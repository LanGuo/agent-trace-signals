"""Generate labels JSON for human review of critical_step detections (Stage 4).

Mirror of failure_labeler.py but for the Stage 3 critical_steps table.

Usage (via CLI):
    uv run ats label-critical-steps [--tag TAG] [--session-id ID] [--limit N]
                                    [--labels labeled_critical_steps.json]

The reviewer judges each record's `label.is_true_positive` (true/false/null).
compute_precision() then reports precision and ship-gate status.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_trace_signals.config import CriticalStepConfig


SHIP_GATE_MIN_PRECISION = 0.8
SHIP_GATE_MIN_LABELED = 20

VALID_TAGS = ("failure_critical", "success_critical", "recovery_critical")
VALID_FAILURE_MODES = (
    "wrong_target",
    "tool_misread",
    "evidence_thin",
    "premature_done",
    "trajectory_inflation",
    "other",
)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _is_substantial_directive(text: str) -> bool:
    """A user message is 'substantial' (i.e. a real directive worth showing)
    if it has > 3 words AND > 12 chars, and isn't an obvious short
    confirmation. Used to walk past acks like 'yes', '1', 'go ahead'."""
    t = (text or "").strip()
    if len(t) < 12:
        return False
    if len(t.split()) <= 3:
        return False
    low = t.lower().rstrip(".!?, ")
    if low in {"yes", "ok", "okay", "go", "do it", "go ahead", "sounds good", "sg", "lgtm", "looks good", "1", "2", "3", "y", "yeah", "yep", "sure", "agreed"}:
        return False
    return True


def _fetch_substantial_directive(
    conn: sqlite3.Connection,
    session_id: str,
    exchange_idx: int,
    cap: int = 1500,
) -> tuple[str, int]:
    """Walk backward from exchange_idx looking for a SUBSTANTIAL
    genuine_human directive (not just an ack like 'yes'). Returns
    (text, exchange_idx_where_found). Falls back to most recent
    genuine_human if no substantial one exists.
    """
    row = conn.execute(
        "SELECT turn_descriptors FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row or not row["turn_descriptors"]:
        return ("", -1)
    try:
        tds = json.loads(row["turn_descriptors"])
    except (TypeError, json.JSONDecodeError):
        return ("", -1)
    if not isinstance(tds, list):
        return ("", -1)
    best_substantial = ("", -1)
    best_fallback = ("", -1)
    for td in tds:
        if not isinstance(td, dict):
            continue
        ex_i = td.get("exchange_idx", -1)
        if ex_i > exchange_idx:
            break
        if td.get("exchange_type") == "genuine_human":
            ut = (td.get("user_text") or "").strip()
            if not ut:
                continue
            best_fallback = (ut, ex_i)
            if _is_substantial_directive(ut):
                best_substantial = (ut, ex_i)
    text, found_at = best_substantial if best_substantial[0] else best_fallback
    return (text[:cap], found_at)


def _fetch_user_directive(
    conn: sqlite3.Connection,
    session_id: str,
    exchange_idx: int,
    cap: int = 800,
) -> str:
    """Fetch the most recent genuine_human user_text at or before exchange_idx.

    Reads sessions.turn_descriptors (JSON list of TurnDescriptor dicts) and
    walks backward from exchange_idx to find the most recent genuine_human
    exchange with non-empty user_text. user_text is populated by all plugins
    as of the Stage 2 user_text patch.
    """
    row = conn.execute(
        "SELECT turn_descriptors FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row or not row["turn_descriptors"]:
        return ""
    try:
        tds = json.loads(row["turn_descriptors"])
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(tds, list):
        return ""
    # Walk backward from exchange_idx (inclusive) looking for genuine_human
    # with non-empty user_text. Fall back to most recent genuine_human at all.
    best_at_or_before = ""
    for td in tds:
        if not isinstance(td, dict):
            continue
        ex_i = td.get("exchange_idx", -1)
        if ex_i > exchange_idx:
            break
        if td.get("exchange_type") == "genuine_human":
            ut = (td.get("user_text") or "").strip()
            if ut:
                best_at_or_before = ut
    return best_at_or_before[:cap]


# ---- Per-exchange detail extraction (re-parses source on demand) ----
# Cached per session so we don't re-parse for every critical_step in that session.
_EXCHANGE_CACHE: dict[str, list[dict]] = {}


def _get_raw_exchanges(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Return raw exchange dicts for a session by re-reading its source file.

    Cached per session. Returns [] if the source path is unknown or unreadable.
    The exchange format varies by plugin (CC: turns list; Gemini: gemini_turns;
    Opencode: assistant_turns) — we call each plugin's _build_exchanges with
    the parsed message list it expects.
    """
    if session_id in _EXCHANGE_CACHE:
        return _EXCHANGE_CACHE[session_id]

    row = conn.execute(
        """SELECT s.source_plugin, i.abs_path
           FROM sessions s LEFT JOIN ingestion_state i ON i.session_id = s.id
           WHERE s.id = ?""",
        (session_id,),
    ).fetchone()
    if not row or not row["abs_path"]:
        _EXCHANGE_CACHE[session_id] = []
        return []

    source_plugin = row["source_plugin"]
    abs_path = row["abs_path"]
    if not Path(abs_path).exists() and "::" not in abs_path:
        _EXCHANGE_CACHE[session_id] = []
        return []

    try:
        if source_plugin == "claude_code":
            messages = _read_jsonl(abs_path)
            from agent_trace_signals.plugins.claude_code import ClaudeCodeSource
            exchanges = ClaudeCodeSource._build_exchanges(messages)
        elif source_plugin == "gemini_cli":
            messages = _read_gemini_messages(abs_path)
            from agent_trace_signals.plugins.gemini_cli import GeminiSource
            exchanges = GeminiSource._build_exchanges(messages)
        elif source_plugin == "opencode":
            # opencode reads from sqlite; messages assembled differently.
            # Skip for v1 — opencode users get the per-step view from the
            # step_score's agent_action_summary only.
            exchanges = []
        else:
            exchanges = []
        _EXCHANGE_CACHE[session_id] = list(exchanges)
        return _EXCHANGE_CACHE[session_id]
    except Exception:
        # Cache empty so we don't retry on every record, but log via the
        # docstring placeholder for debugging.
        _EXCHANGE_CACHE[session_id] = []
        return []


def _read_jsonl(path: str) -> list[dict]:
    """Read a JSONL file into a list of message dicts (CC format)."""
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _read_gemini_messages(path: str) -> list[dict]:
    """Read a Gemini session file (.json or .jsonl) and return its messages list.

    Gemini JSONL has a session header line first then per-message lines.
    Gemini JSON has {"messages": [...]} or similar; we use the same flat list
    convention the plugin expects.
    """
    p = Path(path)
    if p.suffix == ".jsonl":
        out: list[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Skip $set delta records and session header (no 'type' field
                # at the top level usually)
                if isinstance(obj, dict) and ("$set" in obj or not obj.get("type")):
                    continue
                out.append(obj)
        return out
    else:
        # .json — could be a dict with messages, or a flat list
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "messages" in data:
            return [m for m in data["messages"] if isinstance(m, dict)]
        if isinstance(data, list):
            return [m for m in data if isinstance(m, dict)]
        return []


def _compact_exchange_summary(
    conn: sqlite3.Connection,
    session_id: str,
    exchange_idx: int,
    plugin: str,
) -> dict:
    """One-line summary of an exchange for the surrounding-turns timeline.

    Returns {
        "exchange_idx": int,
        "exchange_type": str,
        "user_brief": str,        # first ~80 chars of user text
        "agent_brief": str,       # tool call summary or first ~120 chars of agent text
        "tool_count": int,
        "tool_error_count": int,
    }
    """
    out = {
        "exchange_idx": exchange_idx,
        "exchange_type": "?",
        "user_brief": "",
        "agent_brief": "",
        "tool_count": 0,
        "tool_error_count": 0,
    }
    raw = _get_raw_exchanges(conn, session_id)
    if exchange_idx < 0 or exchange_idx >= len(raw):
        return out
    exchange = raw[exchange_idx]

    # exchange_type from TurnDescriptor
    tds_row = conn.execute("SELECT turn_descriptors FROM sessions WHERE id=?",
                           (session_id,)).fetchone()
    if tds_row and tds_row["turn_descriptors"]:
        try:
            tds = json.loads(tds_row["turn_descriptors"])
            for td in tds:
                if isinstance(td, dict) and td.get("exchange_idx") == exchange_idx:
                    out["exchange_type"] = td.get("exchange_type", "?")
                    out["tool_count"] = len(td.get("tool_calls", []) or [])
                    out["tool_error_count"] = sum(
                        1 for e in (td.get("tool_errors") or []) if e
                    )
                    break
        except Exception:
            pass

    if plugin == "claude_code":
        user = exchange.get("user", {})
        content = user.get("message", {}).get("content", "")
        if isinstance(content, str):
            out["user_brief"] = content[:80]
        elif isinstance(content, list):
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            out["user_brief"] = (" ".join(parts))[:80]
        # Agent brief: first tool call or first text block
        for turn in exchange.get("turns", []):
            if turn.get("type") != "assistant":
                continue
            for block in turn.get("message", {}).get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    inp = block.get("input", {})
                    target = (inp.get("file_path") or inp.get("command", "")
                              or inp.get("query", "") or inp.get("pattern", ""))
                    out["agent_brief"] = f"{name}({str(target)[:60]})"
                    break
                elif block.get("type") == "text":
                    out["agent_brief"] = block.get("text", "")[:120]
                    break
            if out["agent_brief"]:
                break

    elif plugin == "gemini_cli":
        user_content = exchange.get("user", {}).get("content", [])
        ut_parts = [item.get("text", "") for item in user_content
                    if isinstance(item, dict)]
        out["user_brief"] = (" ".join(ut_parts))[:80]
        for turn in exchange.get("gemini_turns", []):
            tcs = turn.get("toolCalls", [])
            if tcs:
                first = tcs[0]
                name = first.get("name", "")
                args = first.get("args", {})
                target = (args.get("file_path") or args.get("command", "")
                          or args.get("query", "") or args.get("prompt", ""))
                out["agent_brief"] = f"{name}({str(target)[:60]})"
                break
            text = turn.get("content", "")
            if isinstance(text, str) and text.strip():
                out["agent_brief"] = text[:120]
                break
    return out


def _fetch_surrounding(
    conn: sqlite3.Connection,
    session_id: str,
    exchange_idx: int,
    window: int = 3,
) -> list[dict]:
    """Return compact summaries for exchanges in [idx-window .. idx+window].

    Each entry is augmented with the relative offset, evidence_supports score
    (if scored), and whether it's also a critical_step.
    """
    plugin_row = conn.execute(
        "SELECT source_plugin FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    plugin = plugin_row["source_plugin"] if plugin_row else ""

    raw = _get_raw_exchanges(conn, session_id)
    if not raw:
        return []
    lo = max(0, exchange_idx - window)
    hi = min(len(raw) - 1, exchange_idx + window)

    # Pre-fetch scores + critical tags for the range
    scores = {}
    for r in conn.execute(
        "SELECT exchange_idx, evidence_supports FROM step_scores "
        "WHERE session_id = ? AND exchange_idx BETWEEN ? AND ?",
        (session_id, lo, hi),
    ):
        scores[r["exchange_idx"]] = float(r["evidence_supports"])
    crits = {}
    for r in conn.execute(
        "SELECT exchange_idx, tag FROM critical_steps "
        "WHERE session_id = ? AND exchange_idx BETWEEN ? AND ?",
        (session_id, lo, hi),
    ):
        crits[r["exchange_idx"]] = r["tag"]

    out = []
    for ex_i in range(lo, hi + 1):
        summary = _compact_exchange_summary(conn, session_id, ex_i, plugin)
        summary["offset"] = ex_i - exchange_idx   # -2, -1, 0, +1, +2
        summary["is_focus"] = (ex_i == exchange_idx)
        summary["score"] = scores.get(ex_i)
        summary["critical_tag"] = crits.get(ex_i)
        out.append(summary)
    return out


def _format_exchange_detail(
    conn: sqlite3.Connection,
    session_id: str,
    exchange_idx: int,
    char_cap: int = 6000,
) -> dict:
    """Extract a per-exchange detail dict for the labeler context.

    Returns:
        {
          "this_user_text": str,          # what the user said THIS exchange (often empty / 'yes')
          "this_agent_text": str,         # agent's response text (thinking + reply) this exchange
          "tool_calls": [                 # tool calls made this exchange with args + result
              {"name": str, "args_summary": str, "result_summary": str, "is_error": bool}
          ],
          "exchange_type": str,           # from TurnDescriptor
          "raw_serialized": str,          # plugin's narrative serialization of THIS exchange
        }
    Falls back to empty fields if source isn't accessible.
    """
    out = {
        "this_user_text": "",
        "this_agent_text": "",
        "tool_calls": [],
        "exchange_type": "",
        "raw_serialized": "",
    }
    raw = _get_raw_exchanges(conn, session_id)
    if exchange_idx < 0 or exchange_idx >= len(raw):
        return out
    exchange = raw[exchange_idx]

    # Determine plugin format
    row = conn.execute("SELECT source_plugin FROM sessions WHERE id = ?",
                       (session_id,)).fetchone()
    plugin = row["source_plugin"] if row else ""

    if plugin == "claude_code":
        user = exchange.get("user", {})
        content = user.get("message", {}).get("content", "")
        if isinstance(content, str):
            out["this_user_text"] = content[:2000]
        elif isinstance(content, list):
            parts = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(b.get("text", ""))
            out["this_user_text"] = " ".join(parts)[:2000]

        agent_parts: list[str] = []
        tool_calls: list[dict] = []
        for turn in exchange.get("turns", []):
            if turn.get("type") != "assistant":
                continue
            for block in turn.get("message", {}).get("content", []):
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    agent_parts.append(block.get("text", ""))
                elif btype == "thinking":
                    agent_parts.append("[thinking] " + (block.get("thinking", "") or "")[:400])
                elif btype == "tool_use":
                    name = block.get("name", "")
                    inp = block.get("input", {})
                    args_summary = str(inp)[:200]
                    tool_calls.append({
                        "name": name,
                        "args_summary": args_summary,
                        "result_summary": "",
                        "is_error": False,
                    })
        # Tool results live in user_type turns with toolUseResult
        for turn in exchange.get("turns", []):
            if turn.get("type") != "user":
                continue
            for block in turn.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    is_err = bool(block.get("is_error", False))
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        result_text = " ".join(
                            b.get("text", "") for b in inner if isinstance(b, dict)
                        )
                    else:
                        result_text = str(inner or "")
                    # Match against tool_calls — use sequence position
                    for tc in tool_calls:
                        if tc["result_summary"] == "":
                            tc["result_summary"] = result_text[:800]
                            tc["is_error"] = is_err
                            break
        out["this_agent_text"] = " ".join(agent_parts)[:3000]
        out["tool_calls"] = tool_calls[:10]

    elif plugin == "gemini_cli":
        user_content = exchange.get("user", {}).get("content", [])
        ut_parts = [item.get("text", "") for item in user_content if isinstance(item, dict)]
        out["this_user_text"] = " ".join(ut_parts)[:2000]
        agent_parts: list[str] = []
        tool_calls: list[dict] = []
        for turn in exchange.get("gemini_turns", []):
            text = turn.get("content", "")
            if isinstance(text, str) and text.strip():
                agent_parts.append(text)
            for thought in turn.get("thoughts", []):
                if isinstance(thought, dict):
                    subj = thought.get("subject", "")
                    desc = thought.get("description", "")
                    if subj or desc:
                        agent_parts.append(f"[thinking:{subj}] {desc[:300]}")
            for tc in turn.get("toolCalls", []):
                name = tc.get("name", "")
                args = tc.get("args", {})
                result_text = ""
                is_err = tc.get("status") == "error"
                for r in tc.get("result", []):
                    if isinstance(r, dict):
                        resp = r.get("functionResponse", {}).get("response", {})
                        out_val = resp.get("output", "")
                        if isinstance(out_val, str):
                            result_text += out_val
                tool_calls.append({
                    "name": name,
                    "args_summary": str(args)[:200],
                    "result_summary": result_text[:800],
                    "is_error": is_err,
                })
        out["this_agent_text"] = " ".join(agent_parts)[:3000]
        out["tool_calls"] = tool_calls[:10]

    # exchange_type from TurnDescriptor
    tds_row = conn.execute("SELECT turn_descriptors FROM sessions WHERE id=?",
                           (session_id,)).fetchone()
    if tds_row and tds_row["turn_descriptors"]:
        try:
            tds = json.loads(tds_row["turn_descriptors"])
            for td in tds:
                if isinstance(td, dict) and td.get("exchange_idx") == exchange_idx:
                    out["exchange_type"] = td.get("exchange_type", "")
                    break
        except Exception:
            pass

    # Build a brief narrative serialization (capped)
    lines = []
    if out["this_user_text"]:
        lines.append(f"USER: {out['this_user_text'][:1000]}")
    if out["this_agent_text"]:
        lines.append(f"AGENT: {out['this_agent_text'][:1500]}")
    for tc in out["tool_calls"][:6]:
        err = " [ERROR]" if tc["is_error"] else ""
        lines.append(f"[tool: {tc['name']}({tc['args_summary'][:120]})]{err}")
        if tc["result_summary"]:
            lines.append(f"  → {tc['result_summary'][:600]}")
    out["raw_serialized"] = "\n".join(lines)[:char_cap]
    return out


def _fetch_chunk_for_exchange(
    conn: sqlite3.Connection,
    session_id: str,
    exchange_idx: int,
) -> str:
    """Return the evidence_text of the chunk whose span_start..span_end contains
    the given exchange_idx. Empty string if not found.
    """
    rows = conn.execute(
        "SELECT span_start, span_end, evidence_text FROM records "
        "WHERE session_id = ? ORDER BY chunk_index",
        (session_id,),
    ).fetchall()
    for r in rows:
        try:
            start = int(r["span_start"])
            end = int(r["span_end"])
        except (TypeError, ValueError):
            continue
        if start <= exchange_idx <= end:
            return r["evidence_text"] or ""
    return ""


def _fetch_step_score(
    conn: sqlite3.Connection,
    session_id: str,
    exchange_idx: int,
) -> dict | None:
    row = conn.execute(
        "SELECT exchange_idx, evidence_supports, progress_vector, cost_vector, "
        "agent_action_summary FROM step_scores "
        "WHERE session_id = ? AND exchange_idx = ?",
        (session_id, exchange_idx),
    ).fetchone()
    if row is None:
        return None
    try:
        pv = json.loads(row["progress_vector"] or "{}")
    except (TypeError, json.JSONDecodeError):
        pv = {}
    try:
        cv = json.loads(row["cost_vector"] or "{}")
    except (TypeError, json.JSONDecodeError):
        cv = {}
    return {
        "exchange_idx": row["exchange_idx"],
        "score": float(row["evidence_supports"]),
        "progress_vector": pv,
        "cost_vector": cv,
        "agent_action_summary": row["agent_action_summary"] or "",
    }


def _fetch_neighbor_score(
    conn: sqlite3.Connection,
    session_id: str,
    exchange_idx: int,
    direction: int,
) -> float | None:
    """Score of the neighbor step (-1 preceding, +1 following)."""
    if direction < 0:
        row = conn.execute(
            "SELECT evidence_supports FROM step_scores "
            "WHERE session_id = ? AND exchange_idx < ? "
            "ORDER BY exchange_idx DESC LIMIT 1",
            (session_id, exchange_idx),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT evidence_supports FROM step_scores "
            "WHERE session_id = ? AND exchange_idx > ? "
            "ORDER BY exchange_idx ASC LIMIT 1",
            (session_id, exchange_idx),
        ).fetchone()
    return float(row["evidence_supports"]) if row else None


def _empty_label() -> dict:
    return {
        "is_true_positive": None,
        "should_have_been_tag": None,
        "should_have_been_failure_mode": None,
        "notes": "",
    }


def _key(session_id: str, exchange_idx: int, tag: str) -> tuple[str, int, str]:
    return (session_id, exchange_idx, tag)


def generate_labels(
    db_path: str,
    output_path: str,
    limit: int | None = None,
    tag: str = "all",
    session_id: str | None = None,
    config: CriticalStepConfig | None = None,
) -> int:
    """Generate / refresh a labels JSON from the critical_steps table.

    Preserves existing label fields where is_true_positive was set, matched on
    (session_id, exchange_idx, tag).
    """
    cfg = config or CriticalStepConfig()
    conn = _connect(db_path)

    query = "SELECT * FROM critical_steps"
    where: list[str] = []
    params: list[Any] = []
    if tag and tag != "all":
        where.append("tag = ?")
        params.append(tag)
    if session_id:
        where.append("session_id = ?")
        params.append(session_id)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY session_id, exchange_idx, tag"
    if limit is not None and limit > 0:
        query += f" LIMIT {int(limit)}"

    rows = conn.execute(query, params).fetchall()

    # Load existing labels to preserve human work
    existing: dict[tuple[str, int, str], dict] = {}
    out = Path(output_path)
    if out.exists():
        try:
            prior = json.loads(out.read_text())
            for rec in prior.get("records", []):
                k = _key(rec["session_id"], int(rec["exchange_idx"]), rec["tag"])
                lab = rec.get("label") or {}
                if lab.get("is_true_positive") is not None:
                    existing[k] = lab
        except (json.JSONDecodeError, KeyError):
            pass

    records: list[dict] = []
    for r in rows:
        sid = r["session_id"]
        ex_idx = int(r["exchange_idx"])
        tag_val = r["tag"]
        try:
            features = json.loads(r["features"] or "{}")
        except (TypeError, json.JSONDecodeError):
            features = {}

        step = _fetch_step_score(conn, sid, ex_idx) or {}
        preceding = _fetch_neighbor_score(conn, sid, ex_idx, -1)
        following = _fetch_neighbor_score(conn, sid, ex_idx, +1)

        # Rich per-exchange context (re-parses source, cached per session)
        detail = _format_exchange_detail(conn, sid, ex_idx)
        substantial_directive, sd_at = _fetch_substantial_directive(conn, sid, ex_idx)
        # Also keep the "most recent at-or-before" directive for cases where
        # the user explicitly said something short like "yes" right before
        # the step — useful to distinguish "user just confirmed" from
        # "agent acting on a stale older instruction".
        immediate_directive = _fetch_user_directive(conn, sid, ex_idx)

        record = {
            "session_id": sid,
            "exchange_idx": ex_idx,
            "tag": tag_val,
            "failure_mode": r["failure_mode"],
            "score": float(r["score"]),
            "delta": float(r["delta"]),
            "features": features,
            "progress_vector": step.get("progress_vector", {}),
            "cost_vector": step.get("cost_vector", {}),
            "agent_action_summary": step.get("agent_action_summary", ""),
            "context": {
                # Substantial intent: most recent user directive >3 words, walking
                # past short confirmations. May be from many exchanges back.
                "substantial_directive": substantial_directive,
                "substantial_directive_at": sd_at,
                # The most recent genuine_human user_text at-or-before this
                # exchange (often "yes" / short confirmation for autonomous work).
                "immediate_user_text": immediate_directive,
                # THIS exchange's content (extracted by re-parsing source):
                "this_exchange": detail,
                # Compact summaries of ±3 surrounding exchanges so reviewer
                # can see what came before/after the critical step.
                "surrounding": _fetch_surrounding(conn, sid, ex_idx, window=3),
                "preceding_score": preceding,
                "following_score": following,
            },
            "label": existing.get(_key(sid, ex_idx, tag_val), _empty_label()),
        }
        records.append(record)

    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": {
            "delta_low": cfg.delta_low,
            "delta_high": cfg.delta_high,
            "delta_change": cfg.delta_change,
            "k": cfg.k,
        },
        "records": records,
    }
    out.write_text(json.dumps(payload, indent=2))
    return len(records)


def compute_precision(labels_path: str | Path) -> dict:
    """Compute precision over a labels JSON file.

    Returns keys: total_candidates, labeled, precision_overall,
    precision_by_tag, precision_by_failure_mode, ship_gate_passed.

    precision = TP / (TP + FP). is_true_positive==None means unlabeled and is
    excluded from precision but counted in total_candidates.

    ship_gate_passed iff precision_overall >= 0.8 AND labeled >= 20.
    """
    p = Path(labels_path)
    if not p.exists():
        return {
            "total_candidates": 0,
            "labeled": 0,
            "precision_overall": None,
            "precision_by_tag": {},
            "precision_by_failure_mode": {},
            "ship_gate_passed": False,
        }

    data = json.loads(p.read_text())
    records = data.get("records", []) if isinstance(data, dict) else []

    tp_overall = fp_overall = 0
    fn_flagged = 0  # count of ⚑-flagged missed events across all cards
    by_tag: dict[str, list[int]] = {}
    by_mode: dict[str, list[int]] = {}

    for rec in records:
        lab = rec.get("label") or {}
        verdict = lab.get("is_true_positive")
        fn_flagged += len(lab.get("fn_flags") or [])
        if verdict is None:
            continue
        tag = rec.get("tag", "")
        mode = rec.get("failure_mode") or ""
        by_tag.setdefault(tag, [0, 0])
        if mode:
            by_mode.setdefault(mode, [0, 0])
        if verdict is True:
            tp_overall += 1
            by_tag[tag][0] += 1
            if mode:
                by_mode[mode][0] += 1
        elif verdict is False:
            fp_overall += 1
            by_tag[tag][1] += 1
            if mode:
                by_mode[mode][1] += 1

    def _prec(tp: int, fp: int) -> float | None:
        return tp / (tp + fp) if (tp + fp) > 0 else None

    labeled = tp_overall + fp_overall
    precision_overall = _prec(tp_overall, fp_overall)
    precision_by_tag = {k: _prec(v[0], v[1]) for k, v in by_tag.items()}
    precision_by_failure_mode = {k: _prec(v[0], v[1]) for k, v in by_mode.items()}

    # Recall lower bound: TP / (TP + FN_flagged).
    # This is a lower bound because only ±3-neighbor windows were visible,
    # so the reviewer could only flag missed events near a candidate card.
    recall_lb = tp_overall / (tp_overall + fn_flagged) if (tp_overall + fn_flagged) > 0 else None

    ship_gate_passed = bool(
        precision_overall is not None
        and precision_overall >= SHIP_GATE_MIN_PRECISION
        and labeled >= SHIP_GATE_MIN_LABELED
    )

    return {
        "total_candidates": len(records),
        "labeled": labeled,
        "precision_overall": precision_overall,
        "precision_by_tag": precision_by_tag,
        "precision_by_failure_mode": precision_by_failure_mode,
        "fn_flagged": fn_flagged,
        "recall_lb": recall_lb,
        "ship_gate_passed": ship_gate_passed,
    }
