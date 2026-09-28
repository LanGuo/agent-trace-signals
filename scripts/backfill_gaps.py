"""
Backfill GAP-1, GAP-2, GAP-3 for sessions ingested before the 2026-06-05 fix.

GAP-1: records.span_start / span_end — exchange index range for each chunk
GAP-2: sessions.turn_descriptors — JSON array of TurnDescriptor objects
GAP-3: sessions.raw_facets — cwd, git_branch, cc_version, away_summary
GAP-WS: sessions.workspace_id = 'gemini_cli' (or 'claude_code') instead of
         project directory — caused by archive path not matching original
         plugin workspace_id extraction logic.

Re-parses source files (from ingestion_state.abs_path) for sessions with any
missing column. Does NOT touch entities, occurrences, memories, or inflections.

Usage (from project root):
    uv run python scripts/backfill_gaps.py [--db traces.db] [--dry-run]
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


def needs_backfill(conn: sqlite3.Connection) -> list[dict]:
    """Return sessions that are missing any gap column, including bad workspace_id."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT s.id, s.source_plugin, s.workspace_id, i.abs_path
        FROM sessions s
        JOIN ingestion_state i ON i.session_id = s.id
        WHERE s.turn_descriptors IS NULL
           OR s.raw_facets = '{}'
           OR s.workspace_id = s.source_plugin
           OR EXISTS (
               SELECT 1 FROM records r
               WHERE r.session_id = s.id AND r.span_start IS NULL
               LIMIT 1
           )
    """).fetchall()
    return [dict(r) for r in rows]


def backfill_session(
    conn: sqlite3.Connection,
    session_id: str,
    source_plugin: str,
    abs_path: str,
    dry_run: bool,
) -> bool:
    """Re-parse one source file and patch the three gap columns. Returns True on success."""
    from agent_trace_signals.config import Config
    from agent_trace_signals.plugins.claude_code import ClaudeCodeSource
    from agent_trace_signals.plugins.gemini_cli import GeminiSource
    from agent_trace_signals.plugins.opencode import OpencodeSource

    if not Path(abs_path).exists():
        logger.warning(f"  Source file missing: {abs_path}")
        return False

    config = Config()
    plugin_map = {
        "claude_code": ClaudeCodeSource,
        "gemini_cli":  GeminiSource,
        "opencode":    OpencodeSource,
    }
    plugin_cls = plugin_map.get(source_plugin)
    if plugin_cls is None:
        logger.warning(f"  Unknown plugin {source_plugin!r} — skipping")
        return False

    try:
        plugin = plugin_cls(config.pipeline)
        parsed = plugin.parse(abs_path)
    except Exception as e:
        logger.warning(f"  Parse failed: {e}")
        return False

    # --- GAP-1: span_start/span_end on records ---
    updates_spans = []
    for chunk in parsed.chunks:
        if chunk.span_start is not None or chunk.span_end is not None:
            # Find the record matching this chunk_index in this session
            row = conn.execute(
                "SELECT id FROM records WHERE session_id=? AND chunk_index=?",
                (session_id, chunk.chunk_index)
            ).fetchone()
            if row:
                updates_spans.append((chunk.span_start, chunk.span_end, row[0]))

    # --- GAP-2: turn_descriptors ---
    raw_tds = parsed.metadata.get("turn_descriptors", [])
    tds_json = json.dumps(raw_tds) if raw_tds else None

    # --- GAP-3: raw_facets ---
    plugin_facets = parsed.raw_facets or {}
    plugin_meta = parsed.metadata or {}
    sm = plugin_meta.get("session_metadata") or {}
    if isinstance(sm, dict):
        cwd         = sm.get("cwd")
        git_branch  = sm.get("git_branch")
        cc_version  = sm.get("cc_version")
    else:
        cwd = git_branch = cc_version = None
    away_summary = plugin_meta.get("away_summary_latest")

    raw_facets_dict = {
        **plugin_facets,
        "cwd":          cwd,
        "git_branch":   git_branch,
        "cc_version":   cc_version,
        "away_summary": away_summary,
    }
    raw_facets_dict = {k: v for k, v in raw_facets_dict.items() if v is not None}
    raw_facets_json = json.dumps(raw_facets_dict)

    # --- GAP-WS: workspace_id set to plugin name instead of project dir ---
    correct_workspace_id = parsed.workspace_id  # plugin recomputes from abs_path

    if dry_run:
        logger.info(f"  [DRY-RUN] would update {len(updates_spans)} span rows, "
                    f"turn_descriptors={'YES' if tds_json else 'NO'}, "
                    f"raw_facets={raw_facets_dict}, "
                    f"workspace_id={correct_workspace_id!r}")
        return True

    # Apply updates
    with conn:
        for span_start, span_end, record_id in updates_spans:
            conn.execute(
                "UPDATE records SET span_start=?, span_end=? WHERE id=?",
                (span_start, span_end, record_id)
            )
        conn.execute(
            "UPDATE sessions SET turn_descriptors=?, raw_facets=?, workspace_id=? WHERE id=?",
            (tds_json, raw_facets_json, correct_workspace_id, session_id)
        )

    logger.info(f"  Updated {len(updates_spans)} span rows, "
                f"turn_descriptors={'set' if tds_json else 'empty'}, "
                f"raw_facets keys={list(raw_facets_dict.keys())}, "
                f"workspace_id={correct_workspace_id!r}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="traces.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    # Run migration so turn_descriptors column exists
    from agent_trace_signals.db.schema import migrate, create_all
    migrate(conn)
    create_all(conn)

    sessions = needs_backfill(conn)
    if not sessions:
        logger.info("Nothing to backfill — all sessions are up to date.")
        return

    logger.info(f"Found {len(sessions)} sessions needing backfill"
                + (" (DRY-RUN)" if args.dry_run else ""))

    ok = fail = 0
    for s in sessions:
        logger.info(f"Session {s['id'][:8]} ({s['source_plugin']})  {Path(s['abs_path']).name}")
        if backfill_session(conn, s["id"], s["source_plugin"], s["abs_path"], args.dry_run):
            ok += 1
        else:
            fail += 1

    logger.info(f"\nDone: {ok} updated, {fail} failed")


if __name__ == "__main__":
    main()
