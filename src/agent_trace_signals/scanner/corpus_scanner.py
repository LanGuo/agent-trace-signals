"""Corpus scanner — discovers session files on disk and tracks ingestion state."""

from __future__ import annotations
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from agent_trace_signals.config import ScannerConfig
from agent_trace_signals.db.store import SQLiteStore
from agent_trace_signals.models import IngestionState


class CorpusScanner:
    """Walks configured scan paths, discovers files, tracks ingestion state."""

    def __init__(self, store: SQLiteStore, config: ScannerConfig) -> None:
        """Initialize scanner with database and configuration.

        Args:
            store: SQLiteStore instance for reading/writing ingestion state
            config: ScannerConfig with scan paths and options
        """
        self.store = store
        self.config = config

    def scan(self) -> list[IngestionState]:
        """Walk all configured scan paths, find files, compare content hashes.

        For each discovered file:
        1. Compute file_id = sha256(abs_path) and content_hash = sha256(file_bytes)
        2. Look up existing state in database
        3. If new or content changed: create/update with status='pending'
        4. If same content and status='ingested': skip (don't update)
        5. Upsert state record

        Returns:
            List of IngestionState records with status='pending' (new or changed files)
        """
        # Process each source type
        self._scan_claude_code_files()
        self._scan_gemini_files()
        self._scan_opencode_sessions()
        self._scan_archive_files()
        self._scan_zoom_files()
        self._scan_otter_files()

        # Return all pending files from the database
        return self.store.get_pending_files()

    def mark_ingested(self, file_id: str, session_id: str) -> None:
        """Mark a file as successfully ingested.

        Args:
            file_id: sha256(abs_path) identifier
            session_id: Session ID for the ingested content
        """
        state = self.store.get_ingestion_state(file_id)
        if state:
            state.status = "ingested"
            state.last_ingested_at = self._now()
            state.session_id = session_id
            self.store.upsert_ingestion_state(state)

    def mark_failed(self, file_id: str, error: str) -> None:
        """Mark a file as failed with error message.

        Args:
            file_id: sha256(abs_path) identifier
            error: Error message explaining the failure
        """
        state = self.store.get_ingestion_state(file_id)
        if state:
            state.status = "failed"
            state.error = error
            self.store.upsert_ingestion_state(state)

    def status_summary(self) -> dict[str, int]:
        """Return counts by status for reporting.

        Returns:
            Dictionary with keys like 'pending', 'ingested', 'failed', 'skipped'
            and their respective counts
        """
        # Query all ingestion states and count by status
        rows = self.store.conn.execute(
            "SELECT status, COUNT(*) FROM ingestion_state GROUP BY status"
        ).fetchall()

        return {row[0]: row[1] for row in rows}

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _scan_claude_code_files(self) -> None:
        """Scan ~/.claude/projects/ for .jsonl files (excluding subagents)."""
        for scan_path in self.config.claude_code_paths:
            path = Path(scan_path)
            if not path.exists():
                continue

            for file_path in path.rglob("*.jsonl"):
                # Skip files under subagents/ directory
                if "subagents" in file_path.parts:
                    if not self.config.include_subagents:
                        continue

                # Extract project directory (the directory name under ~/.claude/projects/)
                try:
                    # Get the relative path from the scan_path to the file
                    rel_path = file_path.relative_to(path)
                    # Project dir is the first component
                    project_dir = rel_path.parts[0]
                except (ValueError, IndexError):
                    project_dir = None

                self._process_file(
                    file_path=file_path,
                    file_type="claude_code",
                    project_dir=project_dir,
                    subdir=project_dir,
                )

    def _scan_gemini_files(self) -> None:
        """Scan ~/.gemini/tmp/*/chats/ for session-*.json and session-*.jsonl files."""
        for scan_path in self.config.gemini_paths:
            path = Path(scan_path)
            if not path.exists():
                continue

            for pattern in ("*/chats/session-*.json", "*/chats/session-*.jsonl"):
                for file_path in path.glob(pattern):
                    try:
                        project_dir = file_path.parts[len(path.parts)]
                    except IndexError:
                        project_dir = None

                    self._process_file(
                        file_path=file_path,
                        file_type="gemini_cli",
                        project_dir=project_dir,
                        subdir=project_dir,
                    )

    def _scan_archive_files(self) -> None:
        """Scan data/archive/ directly for sessions with no live source remaining.

        The live scanners (_scan_claude_code_files, _scan_gemini_files) copy each
        discovered file into archive_dir and rewrite the tracked abs_path to point
        at the archive copy — so archive_dir already holds a superset of every
        live-discovered session. This method additionally picks up files that exist
        ONLY in archive_dir: their live source was deleted or purged (e.g. Claude
        Code's own history rotation), or they arrived from another machine and were
        never live on this one.

        Reuses _process_file, so a file that also has a live source resolves to the
        same abs_path/content_hash/session_id as the live scan and is cleanly
        recognized as already-ingested (session_exists() skip in ingestion.py) —
        no duplicate sessions, no re-running the LLM pipeline.
        """
        if not self.config.archive_dir:
            return
        archive_root = Path(self.config.archive_dir)
        if not archive_root.is_absolute():
            archive_root = Path.cwd() / archive_root

        for file_type, patterns in (
            ("claude_code", ("*.jsonl",)),
            ("gemini_cli", ("*.json", "*.jsonl")),
        ):
            type_root = archive_root / file_type
            if not type_root.exists():
                continue
            for pattern in patterns:
                for file_path in type_root.rglob(pattern):
                    # A live-scan (or an earlier archive-scan) may already track this
                    # exact path under a different file_id (file_id is keyed on the
                    # *original* discovery path, which for live files differs from
                    # this archive path). abs_path is UNIQUE in ingestion_state, so
                    # skip anything already tracked rather than hitting that
                    # constraint — it's already covered.
                    if self.store.get_ingestion_state_by_path(str(file_path.resolve())):
                        continue

                    # Path-based dedup above only catches re-scanning the SAME path.
                    # The archive directory itself can hold the same underlying session
                    # at two different paths (e.g. a flat copy alongside a project-
                    # subfolder copy from an rsync or an earlier archiving pass) —
                    # session_id is derived from abs_path + content_hash, so two paths
                    # with identical content otherwise ingest as two full duplicate
                    # sessions with duplicate chunks/entities/memories. Catch that here
                    # by content hash before archiving/processing a "new" path.
                    content_hash = self._compute_content_hash(str(file_path))
                    dup = self.store.get_ingested_state_by_content_hash(content_hash)
                    if dup:
                        continue

                    try:
                        rel_path = file_path.relative_to(type_root)
                        project_dir = rel_path.parts[0] if len(rel_path.parts) > 1 else None
                    except ValueError:
                        project_dir = None

                    self._process_file(
                        file_path=file_path,
                        file_type=file_type,
                        project_dir=project_dir,
                        subdir=project_dir,
                    )

    def _scan_opencode_sessions(self) -> None:
        """Scan opencode SQLite databases for sessions, emitting one virtual path per session."""
        import sqlite3
        from agent_trace_signals.plugins.opencode import _OPENCODE_DB_NAME, _SEP

        for scan_path in self.config.opencode_paths:
            db_file = Path(scan_path) / _OPENCODE_DB_NAME
            if not db_file.exists():
                continue

            try:
                conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
                rows = conn.execute(
                    "SELECT id, time_updated FROM session WHERE time_archived IS NULL"
                ).fetchall()
                conn.close()
            except Exception:
                continue

            for session_id, time_updated in rows:
                # Virtual path: db_path::session_id
                virtual_path = str(db_file.resolve()) + _SEP + session_id
                file_id = self._compute_file_id(virtual_path)

                # Content hash: sha256 of session_id + time_updated (changes on any edit)
                content_hash = hashlib.sha256(
                    f"{session_id}:{time_updated}".encode()
                ).hexdigest()

                existing_state = self.store.get_ingestion_state(file_id)
                if (existing_state
                        and existing_state.content_hash == content_hash
                        and existing_state.status == "ingested"):
                    continue

                state = IngestionState(
                    id=file_id,
                    abs_path=virtual_path,
                    file_type="opencode",
                    content_hash=content_hash,
                    session_id=existing_state.session_id if existing_state else None,
                    last_seen_at=self._now(),
                    last_ingested_at=existing_state.last_ingested_at if existing_state else None,
                    status="pending",
                    error=None,
                    metadata=json.dumps({"source_type": "opencode", "opencode_session_id": session_id}),
                )
                self.store.upsert_ingestion_state(state)

    def _scan_zoom_files(self) -> None:
        """Scan configured paths for .vtt files."""
        for scan_path in self.config.zoom_paths:
            path = Path(scan_path)
            if not path.exists():
                continue

            for file_path in path.rglob("*.vtt"):
                self._process_file(
                    file_path=file_path,
                    file_type="zoom",
                    project_dir=None,
                )

    def _scan_otter_files(self) -> None:
        """Scan configured paths for .json files."""
        for scan_path in self.config.otter_paths:
            path = Path(scan_path)
            if not path.exists():
                continue

            for file_path in path.rglob("*.json"):
                self._process_file(
                    file_path=file_path,
                    file_type="otter",
                    project_dir=None,
                )

    def _process_file(
        self,
        file_path: Path,
        file_type: str,
        project_dir: str | None,
        subdir: str | None = None,
    ) -> None:
        """Process a single discovered file.

        On first discovery, copies the file to the local archive so it is
        preserved independently of the source (e.g. Claude Code purging
        ~/.claude/projects/). Subsequent scans use the archive path stored in
        ingestion_state, not the original location.

        Args:
            file_path: Path to the file
            file_type: "claude_code", "gemini_cli", "zoom", or "otter"
            project_dir: Project directory name for source-typed files, or None
        """
        abs_path = str(file_path.resolve())

        # file_id is always keyed on the original path so existing records
        # are found correctly even after the source file moves or is deleted.
        file_id = self._compute_file_id(abs_path)
        content_hash = self._compute_content_hash(abs_path)

        existing_state = self.store.get_ingestion_state(file_id)

        if (existing_state
                and existing_state.content_hash == content_hash
                and existing_state.status == "ingested"):
            return

        # Content-hash-across-all-paths guard. This function is the shared entry
        # point for the LIVE scanners (_scan_claude_code_files, _scan_gemini_files),
        # and until 2026-07-24 it only ever checked THIS path's own prior state —
        # _scan_archive_files() has had a get_ingested_state_by_content_hash() guard
        # since the 2026-07-14 duplicate-session fix, but that fix never made it
        # here. Result: a live scan discovering a file whose content already exists
        # under a *different* already-archived path (e.g. a flat copy an earlier
        # archive-only scan found, plus this same content now seen live under its
        # real project-nested path) re-archived and re-ingested it as a second,
        # fully duplicate session — confirmed 2026-07-24: 33 such pairs, 100% of
        # them this exact flat-vs-nested shape, out of a 113-session ingestion run.
        # `dup.id != file_id` still allows a genuine content update to THIS path's
        # own already-tracked file_id to proceed normally.
        dup = self.store.get_ingested_state_by_content_hash(content_hash)
        if dup and dup.id != file_id:
            return

        # Archive on first discovery so the pipeline reads from our copy.
        # Pass project_dir as subdir so workspace_id derivation still works
        # (e.g. data/archive/claude_code/<project_dir>/<file> → parent.name = project_dir).
        stored_path = self._archive_file(file_path, file_type, subdir=project_dir)

        metadata = None
        if file_type in ("claude_code", "gemini_cli") and project_dir:
            metadata_dict: dict[str, Any] = {
                "project_dir": project_dir,
                "source_type": file_type,
            }
            metadata = json.dumps(metadata_dict)

        state = IngestionState(
            id=file_id,
            abs_path=stored_path,       # pipeline reads from archive
            file_type=file_type,
            content_hash=content_hash,
            session_id=existing_state.session_id if existing_state else None,
            last_seen_at=self._now(),
            last_ingested_at=existing_state.last_ingested_at if existing_state else None,
            status="pending",
            error=None,
            metadata=metadata,
        )

        self.store.upsert_ingestion_state(state)

    def _archive_file(self, file_path: Path, file_type: str, subdir: str | None = None) -> str:
        """Copy file to local archive and return the archive path.

        Archive layout: <archive_dir>/<file_type>/<subdir>/<filename>
        subdir is typically the project_dir so that workspace_id derivation
        (which looks at path.parent.name) still returns the correct value.

        If archive_dir is empty or the copy fails, falls back to the original path.
        Skips the copy if the destination already exists with the same content.
        """
        if not self.config.archive_dir:
            return str(file_path.resolve())

        archive_root = Path(self.config.archive_dir)
        if not archive_root.is_absolute():
            archive_root = Path.cwd() / archive_root

        dest_dir = archive_root / file_type / subdir if subdir else archive_root / file_type
        dest = dest_dir / file_path.name
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Skip copy if destination already has identical content
        if dest.exists() and dest.read_bytes() == file_path.read_bytes():
            return str(dest)

        try:
            shutil.copy2(str(file_path), str(dest))
            return str(dest)
        except Exception:
            # Fall back to original path — better than failing the scan
            return str(file_path.resolve())

    @staticmethod
    def _compute_file_id(abs_path: str) -> str:
        """Compute file ID as sha256(abs_path).

        Args:
            abs_path: Absolute file path as string

        Returns:
            SHA256 hex digest of the path
        """
        return hashlib.sha256(abs_path.encode()).hexdigest()

    @staticmethod
    def _compute_content_hash(abs_path: str) -> str:
        """Compute content hash as sha256(file_bytes).

        Args:
            abs_path: Absolute file path as string

        Returns:
            SHA256 hex digest of file contents
        """
        return hashlib.sha256(Path(abs_path).read_bytes()).hexdigest()

    @staticmethod
    def _now() -> str:
        """Return current UTC timestamp in ISO format.

        Returns:
            ISO 8601 formatted datetime string
        """
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
