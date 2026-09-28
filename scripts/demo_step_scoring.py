"""End-to-end demo for per-step observer scoring (Stage 2).

Parses one Claude Code session, writes a minimal session row + records +
turn_descriptors into a fresh DB, then runs step-scoring against the
default Ollama observer.
"""
from __future__ import annotations
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from agent_trace_signals.config import Config
from agent_trace_signals.db.schema import open_db, create_all, migrate
from agent_trace_signals.db.store import SQLiteStore
from agent_trace_signals.models import Record
from agent_trace_signals.pipeline.step_scoring import StepScoringPipeline
from agent_trace_signals.providers import provider_for_model


def _pick_session() -> Path | None:
    root = Path.home() / ".claude" / "projects"
    candidates = []
    for proj_dir in root.iterdir():
        if not proj_dir.is_dir():
            continue
        for jsonl in proj_dir.glob("*.jsonl"):
            try:
                size = jsonl.stat().st_size
            except OSError:
                continue
            if 300_000 < size < 2_000_000:
                candidates.append((size, jsonl))
    candidates.sort()
    return candidates[len(candidates) // 2][1] if candidates else None


def main():
    db_path = "/tmp/ats_step_scoring_demo.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = open_db(db_path)
    create_all(conn)
    migrate(conn)
    store = SQLiteStore(conn)

    src = _pick_session()
    if not src:
        print("No suitable CC session found"); sys.exit(1)
    print(f"[demo] Source session file: {src} ({src.stat().st_size} bytes)")

    cfg = Config.default()
    from agent_trace_signals.plugins.claude_code import ClaudeCodeSource
    plugin = ClaudeCodeSource(cfg.pipeline)
    parsed = plugin.parse(str(src))
    if not parsed or not parsed.chunks:
        print("Parser returned nothing"); sys.exit(1)

    tds = parsed.metadata.get("turn_descriptors", [])
    print(f"[demo] parsed: {len(parsed.chunks)} chunks, {len(tds)} turn_descriptors")

    # Write minimal session + records
    sid = "demo-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    td_json = json.dumps(tds)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO sessions
           (id, org_id, workspace_id, app_id, user_id, source_type, source_plugin,
            ingested_at, session_summary, embedding_text, raw_facets,
            turn_descriptors, chunk_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sid, "default", parsed.workspace_id, "default", "default",
         parsed.source_type, parsed.source_plugin, now, "", "", "{}",
         td_json, len(parsed.chunks)),
    )
    for raw in parsed.chunks:
        rid = Record.make_id(sid, raw.chunk_index)
        conn.execute(
            """INSERT OR REPLACE INTO records
               (id, session_id, chunk_index, chunk_text, evidence_text,
                embedding_text, span_start, span_end)
               VALUES (?,?,?,?,?,?,?,?)""",
            (rid, sid, raw.chunk_index, raw.chunk_text or "",
             raw.evidence_text or "", "", raw.span_start, raw.span_end),
        )
    conn.commit()

    type_counts = Counter(t.get("exchange_type", "?") for t in tds)
    print(f"[demo] exchange-type counts: {dict(type_counts)}")
    scorable_n = sum(
        1 for t in tds
        if t.get("exchange_type")
        in {"genuine_human", "genuine_text", "agent_continuation", "summary_diff"}
    )
    print(f"[demo] SCORABLE exchanges: {scorable_n}")

    provider = provider_for_model(cfg.models.observer_llm, cfg.models)
    sp = StepScoringPipeline(store=store, provider=provider, config=cfg.models)
    t0 = time.time()
    n = sp.score_session(sid, max_workers=4)
    elapsed = time.time() - t0
    print(f"[demo] scored {n} exchanges in {elapsed:.1f}s "
          f"({elapsed/max(n,1):.2f}s/exchange)")

    rows = conn.execute(
        "SELECT exchange_idx, evidence_supports, progress_vector, cost_vector, agent_action_summary "
        "FROM step_scores WHERE session_id=? ORDER BY exchange_idx", (sid,),
    ).fetchall()
    scores = [r[1] for r in rows]
    if not scores:
        print("No scores written"); sys.exit(1)

    quintiles = [0, 0, 0, 0, 0]
    for s in scores:
        i = min(4, int(s * 5))
        quintiles[i] += 1

    print(f"[demo] step_scores rows: {len(scores)}")
    print(f"[demo] evidence_supports — min={min(scores):.3f} max={max(scores):.3f} "
          f"mean={statistics.mean(scores):.3f} median={statistics.median(scores):.3f}")
    print(f"[demo] quintile counts (0-0.2, 0.2-0.4, 0.4-0.6, 0.6-0.8, 0.8-1.0): {quintiles}")

    ranked = sorted(rows, key=lambda r: r[1])
    print("\n[demo] LOW samples:")
    for r in ranked[:2]:
        print(f"  exchange_idx={r[0]} ev={r[1]:.3f}")
        print(f"     pv={r[2]}")
        print(f"     cv={r[3]}")
        print(f"     action: {r[4]}")
    print("\n[demo] HIGH samples:")
    for r in ranked[-2:]:
        print(f"  exchange_idx={r[0]} ev={r[1]:.3f}")
        print(f"     pv={r[2]}")
        print(f"     cv={r[3]}")
        print(f"     action: {r[4]}")

    print(f"\n[demo] sanity: total_exchanges={len(tds)} scorable={scorable_n} rows_written={len(scores)}")
    assert len(scores) == scorable_n, "scorable mismatch — non-scorable types were not skipped"
    print("[demo] OK — non-scorable types skipped as expected.")


if __name__ == "__main__":
    main()
