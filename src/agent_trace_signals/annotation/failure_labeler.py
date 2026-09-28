"""Generate a labels.json for human review of inflection windows.

Usage:
    uv run ats label-failures [--limit N] [--min-signal F] [--output PATH]

For each candidate inflection:
  - re-parses the source file
  - extracts exchanges [precipitating-1 .. entry+1]
  - writes a pre-filled record with blank label fields

Edit labels.json directly to fill in:
  "genuine": true/false
  "failure_description": "1 sentence"
  "recovery_type": "user_redirect|user_correction|agent_self_recovery|session_end|false_positive"
  "notes": ""
"""

from __future__ import annotations
import json
import re
import sqlite3
from pathlib import Path


# Types that most reliably indicate genuine failures — ranked by reliability
_RELIABLE_TYPES = {"escalation", "loop_entry", "agent_thrash", "user_correction", "strategy_pivot"}

_CONTINUATION_PATTERN = re.compile(
    r"This session is being continued|<summary>|conversation that ran out of context",
    re.IGNORECASE,
)


def _turns(exchange: dict) -> list:
    """Return turns regardless of whether the key is 'turns' (Claude Code) or 'gemini_turns'."""
    return exchange.get("turns") or exchange.get("gemini_turns") or []


def _user_text(exchange: dict) -> str:
    user = exchange.get("user", {})
    # Claude Code: user.message.content
    content = user.get("message", {}).get("content") or user.get("content", "")
    if isinstance(content, str):
        return content[:400]
    if isinstance(content, list):
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            # Claude Code text block or Gemini text dict
            if b.get("type") == "text":
                parts.append(b.get("text", "")[:200])
            elif "text" in b and not b.get("type"):
                parts.append(b["text"][:200])
        return " ".join(parts)[:400]
    return ""


def _tool_calls(exchange: dict) -> list[str]:
    calls = []
    for turn in _turns(exchange):
        for block in turn.get("message", {}).get("content", []):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name", "?")
            inp = block.get("input", {})
            target = (
                inp.get("file_path")
                or inp.get("notebook_path")
                or (inp.get("command", "")[:50] if name == "Bash" else "")
                or name
            )
            calls.append(f"{name}({target})")
    return calls


def _is_wakeup_exchange(exchange: dict) -> bool:
    """True if this exchange was injected by ScheduleWakeup rather than a human."""
    for turn in _turns(exchange):
        content = turn.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        # Agent scheduled a wakeup in this exchange
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "ScheduleWakeup":
                return True
        # Wakeup result delivered as a tool result turn
        result = turn.get("toolUseResult")
        if isinstance(result, dict) and "scheduledFor" in result:
            return True
    return False


def _tool_errors(exchange: dict) -> list[str]:
    errors = []
    for turn in _turns(exchange):
        content = turn.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        if turn.get("type") == "user" and turn.get("toolUseResult"):
            for block in content:
                if isinstance(block, dict) and block.get("is_error"):
                    text = ""
                    if isinstance(block.get("content"), list):
                        text = " ".join(
                            c.get("text", "") for c in block["content"]
                            if isinstance(c, dict)
                        )[:200]
                    elif isinstance(block.get("content"), str):
                        text = block["content"][:200]
                    errors.append(text)
    return errors


def _is_continuation(exchange: dict) -> bool:
    return bool(_CONTINUATION_PATTERN.search(_user_text(exchange)))


def _build_exchanges(path: str) -> list[dict]:
    """Re-parse a source file and return exchange list. Supports Claude Code and Gemini CLI."""
    from agent_trace_signals.config import PipelineConfig
    cfg = PipelineConfig()
    p = Path(path)

    if p.suffix == ".jsonl":
        from agent_trace_signals.plugins.claude_code import ClaudeCodeSource
        messages = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return ClaudeCodeSource(cfg)._build_exchanges(messages)

    if p.suffix == ".json":
        from agent_trace_signals.plugins.gemini_cli import GeminiSource
        with open(path) as f:
            raw = json.load(f)
        # Gemini sessions store messages under a "messages" key (list) or as root list
        messages = raw if isinstance(raw, list) else raw.get("messages", [])
        return GeminiSource(cfg)._build_exchanges(messages)

    return []


def generate_labels(
    db_path: str,
    output_path: str,
    limit: int = 30,
    min_signal: float = 2.0,
    window_before: int = 4,
) -> int:
    conn = sqlite3.connect(db_path)

    # Fetch more than limit so deduplication doesn't starve the result set
    rows = conn.execute(
        """
        SELECT i.id, i.session_id, i.inflection_type, i.entry_turn,
               i.exit_turn, i.precipitating_turn, i.signal_strength,
               i.dominant_feature, i.signal_detail, ist.abs_path
        FROM session_inflections i
        JOIN ingestion_state ist ON ist.session_id = i.session_id
        WHERE i.signal_strength >= ?
        ORDER BY i.signal_strength DESC
        LIMIT ?
        """,
        (min_signal, limit * 5),
    ).fetchall()

    # Deduplicate: max 2 inflections per session (highest signal_strength),
    # and among those, skip any whose window overlaps a prior selection.
    max_per_session = 2
    min_gap = window_before + 2
    session_counts: dict[str, int] = {}
    selected_turns: dict[str, list[int]] = {}
    deduped = []
    for row in rows:
        sid, entry_turn = row[1], row[3]
        if session_counts.get(sid, 0) >= max_per_session:
            continue
        prior = selected_turns.get(sid, [])
        if any(abs(entry_turn - t) < min_gap for t in prior):
            continue
        session_counts[sid] = session_counts.get(sid, 0) + 1
        selected_turns.setdefault(sid, []).append(entry_turn)
        deduped.append(row)
        if len(deduped) >= limit:
            break
    rows = deduped

    # Load existing labels so re-runs don't wipe human work.
    # Records may be full (have window/inflection_type) or label-only stubs.
    existing_labels: dict[str, dict] = {}  # inflection_id -> label fields only
    existing_full: dict[str, dict] = {}    # inflection_id -> full record (reuse as-is)
    out = Path(output_path)
    if out.exists():
        for rec in json.loads(out.read_text()):
            iid = rec["inflection_id"]
            if "inflection_type" in rec:
                existing_full[iid] = rec
            else:
                existing_labels[iid] = {
                    k: rec.get(k)
                    for k in ("genuine", "failure_description", "recovery_type", "notes")
                }

    records = []
    skipped = 0

    # Cache parsed exchanges per session (avoid re-parsing same file multiple times)
    exchange_cache: dict[str, list[dict]] = {}

    for row in rows:
        (inf_id, session_id, itype, entry_turn, exit_turn,
         precip_turn, strength, dominant_feature, signal_detail, abs_path) = row

        if inf_id in existing_full and existing_full[inf_id].get("window"):
            records.append(existing_full[inf_id])
            continue

        if not Path(abs_path).exists():
            skipped += 1
            continue

        if abs_path not in exchange_cache:
            try:
                exchange_cache[abs_path] = _build_exchanges(abs_path)
            except Exception:
                skipped += 1
                continue

        exchanges = exchange_cache[abs_path]
        n = len(exchanges)

        window_start = max(0, (precip_turn or entry_turn) - window_before)
        window_end = min(n - 1, entry_turn + 1)

        # Check for context-continuation at correction turn
        correction_is_continuation = (
            entry_turn + 1 <= window_end
            and _is_continuation(exchanges[entry_turn + 1])
        )

        window_records = []
        for idx in range(window_start, window_end + 1):
            ex = exchanges[idx]
            tag = []
            if idx == precip_turn:
                tag.append("PRECIPITATING")
            if idx == entry_turn:
                tag.append("INFLECTION")
            if idx == entry_turn + 1:
                tag.append("CORRECTION" if not correction_is_continuation else "CONTINUATION_BOUNDARY")

            window_records.append({
                "exchange_idx": idx,
                "tags": tag,
                "is_wakeup": _is_wakeup_exchange(ex),
                "user": _user_text(ex),
                "tools": _tool_calls(ex),
                "errors": _tool_errors(ex),
            })

        signal_detail_parsed = {}
        if signal_detail:
            try:
                signal_detail_parsed = json.loads(signal_detail)
            except Exception:
                pass

        saved = existing_labels.get(inf_id, {})
        record = {
            "inflection_id": inf_id,
            "session_id": session_id[:16],
            "inflection_type": itype,
            "dominant_feature": dominant_feature,
            "signal_strength": round(strength, 3),
            "signal_detail": signal_detail_parsed,
            "entry_turn": entry_turn,
            "precip_turn": precip_turn,
            "source_file": Path(abs_path).name,
            "correction_is_continuation": correction_is_continuation,
            "window": window_records,
            "genuine": saved.get("genuine"),
            "failure_description": saved.get("failure_description", ""),
            "recovery_type": saved.get("recovery_type", ""),
            "notes": saved.get("notes", ""),
        }
        records.append(record)

    out.write_text(json.dumps(records, indent=2))
    print(f"Written {len(records)} records to {output_path}")
    if skipped:
        print(f"Skipped {skipped} (source file missing or parse error)")
    unlabeled = sum(1 for r in records if r.get("genuine") is None)
    print(f"Unlabeled: {unlabeled} / {len(records)}")
    return len(records)
