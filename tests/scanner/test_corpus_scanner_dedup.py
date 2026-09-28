"""Regression test for the 2026-07-24 duplicate-session gap in CorpusScanner._process_file().

_scan_archive_files() has had a content-hash-across-all-paths guard since the
2026-07-14 duplicate-session fix (design_decisions.md), but that guard was never
applied to _process_file() — the shared entry point for the LIVE scanners
(_scan_claude_code_files, _scan_gemini_files). A file discovered live whose
content already exists under a different already-archived path (e.g. a flat
copy an earlier archive-only scan found) re-archived and re-ingested as a
second, fully duplicate session. Confirmed 2026-07-24: 33 such pairs out of a
113-session ingestion run, 100% the same flat-vs-nested shape.
"""
from __future__ import annotations

from agent_trace_signals.config import ScannerConfig
from agent_trace_signals.db.schema import create_all, open_db
from agent_trace_signals.db.store import SQLiteStore
from agent_trace_signals.models import IngestionState
from agent_trace_signals.scanner.corpus_scanner import CorpusScanner


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def test_process_file_skips_content_already_ingested_at_a_different_path(tmp_path):
    """A live-discovered file must not re-ingest content already archived elsewhere."""
    conn = open_db(str(tmp_path / "test.db"))
    create_all(conn)
    store = SQLiteStore(conn)
    scanner = CorpusScanner(store, ScannerConfig(archive_dir=str(tmp_path / "archive")))

    # Simulate an already-ingested file at a "flat" archive path (as if a prior
    # archive-only scan found it with no project_dir subfolder).
    flat_path = tmp_path / "archive" / "claude_code" / "session-abc.jsonl"
    flat_path.parent.mkdir(parents=True)
    flat_path.write_text('{"type": "user", "message": {"content": "hello"}}\n')
    content_hash = scanner._compute_content_hash(str(flat_path))
    store.upsert_ingestion_state(IngestionState(
        id=scanner._compute_file_id(str(flat_path.resolve())),
        abs_path=str(flat_path.resolve()),
        file_type="claude_code",
        content_hash=content_hash,
        session_id="already-ingested-session",
        status="ingested",
        last_ingested_at=_now(),
    ))

    # Now "discover" the SAME content live, at a different (project-nested) path —
    # this is what _scan_claude_code_files() would pass to _process_file().
    live_path = tmp_path / "live_source" / "-Users-x-src-foo" / "session-abc.jsonl"
    live_path.parent.mkdir(parents=True)
    live_path.write_text(flat_path.read_text())

    scanner._process_file(live_path, file_type="claude_code", project_dir="-Users-x-src-foo")

    # The live path must NOT have created a new pending/ingested state — the
    # content is already covered under the flat path's file_id.
    live_file_id = scanner._compute_file_id(str(live_path.resolve()))
    assert store.get_ingestion_state(live_file_id) is None

    # Only the original flat-path state should exist for this content.
    rows = conn.execute(
        "SELECT COUNT(*) FROM ingestion_state WHERE content_hash=?", (content_hash,)
    ).fetchone()
    assert rows[0] == 1


def test_process_file_still_updates_its_own_already_tracked_path(tmp_path):
    """A content change to an already-tracked path must still update normally,
    not get skipped by the cross-path dedup guard (dup.id == file_id case)."""
    conn = open_db(str(tmp_path / "test.db"))
    create_all(conn)
    store = SQLiteStore(conn)
    scanner = CorpusScanner(store, ScannerConfig(archive_dir=str(tmp_path / "archive")))

    live_path = tmp_path / "live_source" / "proj" / "session-xyz.jsonl"
    live_path.parent.mkdir(parents=True)
    live_path.write_text('{"type": "user", "message": {"content": "v1"}}\n')

    scanner._process_file(live_path, file_type="claude_code", project_dir="proj")
    file_id = scanner._compute_file_id(str(live_path.resolve()))
    state = store.get_ingestion_state(file_id)
    assert state is not None
    assert state.status == "pending"

    # Mark ingested (as the real ingest pipeline would), then change content and rescan.
    state.status = "ingested"
    store.upsert_ingestion_state(state)
    live_path.write_text('{"type": "user", "message": {"content": "v2"}}\n')

    scanner._process_file(live_path, file_type="claude_code", project_dir="proj")
    updated = store.get_ingestion_state(file_id)
    assert updated.status == "pending"  # content changed, re-queued for ingestion
    assert updated.content_hash != state.content_hash
