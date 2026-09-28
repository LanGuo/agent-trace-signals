"""Backfill trajectory_signals fields for sessions ingested before Stages 0-2.

What this refreshes (per existing session):
  - records.evidence_text   — verbatim tool outputs (Stage 1)
  - sessions.turn_descriptors with the new fields populated:
      * exchange_type        — Stage 0 (classifier output)
      * user_text            — Stage 2 patch (real user directive)
      * avg_think_tok_ratio  — Stage 0 (Gemini-only)
      * evidence_text on RawChunk (Stage 1)

What this does NOT touch:
  - entities, occurrences, memories, conflicts, memory_sources
  - records.chunk_text, chunk_summary, embedding_text
  - record_embeddings, session_embeddings, FTS5 indexes
  - inflection_detector outputs

Strategy: re-parse each session's archived source via its plugin, then
UPDATE the per-chunk evidence_text and the session's turn_descriptors JSON.
No LLM calls — pure parser work. Sessions are processed sequentially;
safe to interrupt and resume.

Usage:
    uv run python scripts/backfill_trajectory_signals.py [--db traces.db]
                                                          [--dry-run]
                                                          [--session-id PREFIX]
                                                          [--force]
"""
from __future__ import annotations
import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_trace_signals.config import PipelineConfig
from agent_trace_signals.plugins.claude_code import ClaudeCodeSource
from agent_trace_signals.plugins.gemini_cli import GeminiSource
from agent_trace_signals.plugins.opencode import OpencodeSource


def _pick_plugin(source_plugin: str, cfg: PipelineConfig):
    if source_plugin == "claude_code":
        return ClaudeCodeSource(cfg)
    if source_plugin == "gemini_cli":
        return GeminiSource(cfg)
    if source_plugin == "opencode":
        return OpencodeSource(cfg)
    raise ValueError(f"Unknown source_plugin: {source_plugin}")


def _needs_backfill(conn: sqlite3.Connection, session_id: str) -> bool:
    """Return True if any record in this session has empty evidence_text
    AND the session has tool calls (otherwise empty is correct)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM records WHERE session_id = ? AND evidence_text != ''",
        (session_id,),
    ).fetchone()
    populated = row[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM records WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    if total == 0:
        return False
    # If at least one record has non-empty evidence_text, assume already backfilled.
    return populated == 0


def backfill_session(
    conn: sqlite3.Connection,
    session_id: str,
    source_plugin: str,
    abs_path: str,
    cfg: PipelineConfig,
    dry_run: bool,
) -> tuple[int, int]:
    """Re-parse and update. Returns (records_updated, td_count)."""
    plugin = _pick_plugin(source_plugin, cfg)
    if not Path(abs_path).exists() and "::" not in abs_path:
        logger.warning(f"  source missing on disk: {abs_path}")
        return (0, 0)

    parsed = plugin.parse(abs_path)
    chunks = parsed.chunks
    tds = parsed.metadata.get("turn_descriptors") or []

    # Map chunk_index -> evidence_text from re-parse
    new_evidence: dict[int, str] = {c.chunk_index: (c.evidence_text or "") for c in chunks}

    # Update records (only evidence_text — keep everything else)
    rows = conn.execute(
        "SELECT chunk_index FROM records WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    updated = 0
    if not dry_run:
        for (chunk_idx,) in rows:
            if chunk_idx in new_evidence:
                conn.execute(
                    "UPDATE records SET evidence_text = ? WHERE session_id = ? AND chunk_index = ?",
                    (new_evidence[chunk_idx], session_id, chunk_idx),
                )
                updated += 1

        # Update turn_descriptors JSON on sessions
        conn.execute(
            "UPDATE sessions SET turn_descriptors = ? WHERE id = ?",
            (json.dumps(tds), session_id),
        )
        conn.commit()
    else:
        updated = sum(1 for (ci,) in rows if ci in new_evidence)

    return (updated, len(tds))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="traces.db")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--session-id", default=None, help="Process one session (full ID or prefix).")
    ap.add_argument("--force", action="store_true",
                    help="Re-backfill even sessions that already have evidence_text populated.")
    args = ap.parse_args()

    db = Path(args.db).resolve()
    if not db.exists():
        logger.error(f"DB not found: {db}")
        sys.exit(1)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    cfg = PipelineConfig()

    # Find candidate sessions
    sql = """
      SELECT s.id, s.source_plugin, i.abs_path
        FROM sessions s
        JOIN ingestion_state i ON i.session_id = s.id
       WHERE i.abs_path IS NOT NULL
    """
    params: list = []
    if args.session_id:
        sql += " AND s.id LIKE ?"
        params.append(args.session_id + "%")
    sql += " ORDER BY s.session_timestamp DESC"

    sessions = conn.execute(sql, params).fetchall()
    logger.info(f"Found {len(sessions)} session(s) with known source paths")

    total_updated_records = 0
    skipped = 0
    processed = 0
    failed = 0

    for s in sessions:
        sid = s["id"]
        if not args.force and not _needs_backfill(conn, sid):
            skipped += 1
            continue

        logger.info(f"Processing {sid[:12]}... ({s['source_plugin']})")
        try:
            updated, tdc = backfill_session(
                conn, sid, s["source_plugin"], s["abs_path"], cfg, args.dry_run
            )
            logger.info(f"  -> updated {updated} record evidence_text, {tdc} turn_descriptors")
            total_updated_records += updated
            processed += 1
        except Exception as e:
            logger.error(f"  FAILED: {type(e).__name__}: {e}")
            failed += 1

    mode = "DRY RUN" if args.dry_run else "DONE"
    logger.info(
        f"{mode}: processed {processed}, skipped {skipped}, failed {failed}; "
        f"total records updated: {total_updated_records}"
    )


if __name__ == "__main__":
    main()
