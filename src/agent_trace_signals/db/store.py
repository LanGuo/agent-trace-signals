"""SQLiteStore — reads and writes for all pipeline stages."""

from __future__ import annotations
import re
import sqlite3
import struct
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from agent_trace_signals.models import (
    Session, Record, Entity, Occurrence, Memory,
    MemorySource, SessionGraphEdge, IngestionState, SessionMetadata,
    MemoryRetrieval, StepScore, CriticalStep,
)

# Common English stop words that carry no topical signal for BM25 queries
_STOP_WORDS = frozenset({
    "a","an","the","and","or","but","in","on","at","to","for","of","with",
    "by","from","is","are","was","were","be","been","being","have","has",
    "had","do","does","did","will","would","could","should","may","might",
    "what","how","when","where","who","which","why","i","we","you","he","ai","ml",
    "she","they","it","my","our","your","his","her","their","its",
    "this","that","these","those","not","no","nor","so","yet","both",
    "either","neither","than","then","there","here","about","above",
    "after","before","between","into","through","during","while","as",
    "if","because","since","although","though","me","him","us","them",
})

def _fts_query(query: str) -> str | None:
    """Convert a natural-language query to an FTS5 OR expression.

    Strips stop words and punctuation, joins remaining meaningful terms
    with OR so partial matches rank well via BM25 instead of requiring
    all terms to be present (FTS5 default AND semantics).
    Returns None if no meaningful terms remain.
    """
    tokens = re.findall(r"[a-zA-Z0-9]+", query.lower())
    meaningful = [t for t in tokens if t not in _STOP_WORDS and len(t) > 2]
    if not meaningful:
        return None
    return " OR ".join(meaningful)

if TYPE_CHECKING:
    from agent_trace_signals.models import AnalyticsRun


def _pack(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


_VEC_TABLE_PAIRS = [
    ("record_embeddings",           "record_id",     "records"),
    ("entity_embeddings",           "entity_id",     "entities"),
    ("occurrence_embeddings",       "occurrence_id", "occurrences"),
    ("memory_embeddings",           "memory_id",     "memories"),
    ("session_embeddings",          "session_id",    "sessions"),
    ("session_structural_embeddings", "session_id",  "sessions"),
    ("session_topic_embeddings",    "session_id",    "sessions"),
]


class SQLiteStore:
    """
    All DB interactions for Pipeline 1 and evaluation.
    Uses a single sqlite3.Connection (caller owns the connection lifecycle).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # Ingestion state
    # ------------------------------------------------------------------

    def upsert_ingestion_state(self, state: IngestionState) -> None:
        self.conn.execute(
            """INSERT INTO ingestion_state
               (id, abs_path, file_type, content_hash, session_id,
                last_seen_at, last_ingested_at, status, error, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 content_hash=excluded.content_hash,
                 session_id=excluded.session_id,
                 last_seen_at=excluded.last_seen_at,
                 last_ingested_at=excluded.last_ingested_at,
                 status=excluded.status,
                 error=excluded.error,
                 metadata=excluded.metadata""",
            (state.id, state.abs_path, state.file_type, state.content_hash,
             state.session_id, state.last_seen_at, state.last_ingested_at,
             state.status, state.error, state.metadata),
        )

    def get_ingestion_state(self, file_id: str) -> IngestionState | None:
        row = self.conn.execute(
            "SELECT * FROM ingestion_state WHERE id=?", (file_id,)
        ).fetchone()
        if row is None:
            return None
        return IngestionState(**dict(row))

    def get_ingestion_state_by_path(self, abs_path: str) -> IngestionState | None:
        row = self.conn.execute(
            "SELECT * FROM ingestion_state WHERE abs_path=?", (abs_path,)
        ).fetchone()
        if row is None:
            return None
        return IngestionState(**dict(row))

    def get_ingested_state_by_content_hash(self, content_hash: str) -> IngestionState | None:
        """Find an already-ingested file with this exact content, regardless of path.

        Used by the archive scanner to catch the same underlying session archived
        under two different paths (e.g. a flat copy and a project-subfolder copy) —
        file_id/session_id are path-derived, so a pure path-based dedup check misses
        this case even though the content (and therefore the session) is identical.
        """
        row = self.conn.execute(
            "SELECT * FROM ingestion_state WHERE content_hash=? AND status='ingested' LIMIT 1",
            (content_hash,),
        ).fetchone()
        if row is None:
            return None
        return IngestionState(**dict(row))

    def get_pending_files(self) -> list[IngestionState]:
        rows = self.conn.execute(
            "SELECT * FROM ingestion_state WHERE status='pending'"
        ).fetchall()
        return [IngestionState(**dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Write session + all related data atomically
    # ------------------------------------------------------------------

    def _vec_upsert(self, table: str, pk_col: str, pk_val: str, emb: list[float]) -> None:
        """Delete-then-insert for sqlite-vec vec0 tables (no INSERT OR REPLACE)."""
        self.conn.execute(f"DELETE FROM {table} WHERE {pk_col}=?", (pk_val,))
        cols = f"({pk_col}, embedding)"
        self.conn.execute(f"INSERT INTO {table} {cols} VALUES (?,?)", (pk_val, _pack(emb)))

    def upsert_entity(self, e: "Entity") -> None:
        """Upsert a single entity. Confidence only upgrades, never downgrades."""
        self.conn.execute(
            """INSERT INTO entities
               (id,org_id,workspace_id,canonical_name,entity_type,degree_count,
                confidence,first_seen,last_seen,source_diversity,entity_profile,
                memory_promoted,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 degree_count = degree_count + 1,
                 last_seen    = excluded.last_seen,
                 updated_at   = excluded.updated_at,
                 confidence   = CASE
                   WHEN entities.confidence = 'corpus'      THEN 'corpus'
                   WHEN entities.confidence = 'session'
                    AND excluded.confidence NOT IN ('corpus')            THEN 'session'
                   WHEN entities.confidence = 'llm_verified'
                    AND excluded.confidence NOT IN ('corpus','session')  THEN 'llm_verified'
                   ELSE excluded.confidence
                 END""",
            (e.id, e.org_id, e.workspace_id, e.canonical_name, e.entity_type,
             e.degree_count, e.confidence, e.first_seen, e.last_seen,
             e.source_diversity, e.entity_profile, e.memory_promoted,
             e.created_at, e.updated_at),
        )

    def write_session_batch(
        self,
        session: Session,
        records: list[Record],
        entities: list[Entity],
        occurrences: list[Occurrence],
        memories: list[Memory],
        memory_sources: list[MemorySource],
        edges: list[SessionGraphEdge],
        record_embeddings: dict[str, list[float]],
        session_embedding: list[float] | None,
        entity_embeddings: dict[str, list[float]],
        occurrence_embeddings: dict[str, list[float]],
        memory_embeddings: dict[str, list[float]],
    ) -> None:
        """Write all ingestion output in a single transaction."""
        with self.conn:
            # Session
            self.conn.execute(
                """INSERT OR REPLACE INTO sessions
                   (id,org_id,workspace_id,app_id,user_id,source_type,source_plugin,
                    session_timestamp,ingested_at,session_summary,embedding_text,
                    raw_facets,tags,metadata,turn_descriptors,chunk_count,cluster_id,analytics_run_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (session.id, session.org_id, session.workspace_id, session.app_id,
                 session.user_id, session.source_type, session.source_plugin,
                 session.session_timestamp, session.ingested_at, session.session_summary,
                 session.embedding_text, session.raw_facets, session.tags,
                 session.metadata, session.turn_descriptors, session.chunk_count,
                 session.cluster_id, session.analytics_run_id),
            )
            if session_embedding:
                self._vec_upsert("session_embeddings", "session_id", session.id, session_embedding)
            self.conn.execute(
                "INSERT OR REPLACE INTO sessions_fts(session_id, session_summary, workspace_id) VALUES (?,?,?)",
                (session.id, session.session_summary or "", session.workspace_id or ""),
            )

            # Records
            for r in records:
                self.conn.execute(
                    """INSERT OR REPLACE INTO records
                       (id,session_id,chunk_index,chunk_text,chunk_summary,
                        evidence_text,embedding_text,token_count,span_start,span_end)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (r.id, r.session_id, r.chunk_index, r.chunk_text, r.chunk_summary,
                     r.evidence_text, r.embedding_text, r.token_count, r.span_start, r.span_end),
                )
                emb = record_embeddings.get(r.id)
                if emb:
                    self._vec_upsert("record_embeddings", "record_id", r.id, emb)
                if r.chunk_text or r.chunk_summary:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO records_fts(record_id, chunk_text, chunk_summary) VALUES (?,?,?)",
                        (r.id, r.chunk_text, r.chunk_summary or ""),
                    )

            # Entities (upsert — degree_count incremented, confidence upgrade-only)
            for e in entities:
                self.upsert_entity(e)
                emb = entity_embeddings.get(e.id)
                if emb:
                    self._vec_upsert("entity_embeddings", "entity_id", e.id, emb)

            # Occurrences — deduplicate by id before writing
            seen_occ_ids: set[str] = set()
            for o in occurrences:
                if o.id in seen_occ_ids:
                    continue
                seen_occ_ids.add(o.id)
                self.conn.execute(
                    """INSERT OR REPLACE INTO occurrences
                       (id,record_id,session_id,entity_id,role,mention_text,
                        context_text,span_start,span_end,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (o.id, o.record_id, o.session_id, o.entity_id, o.role,
                     o.mention_text, o.context_text, o.span_start, o.span_end, o.created_at),
                )
                emb = occurrence_embeddings.get(o.id)
                if emb:
                    self._vec_upsert("occurrence_embeddings", "occurrence_id", o.id, emb)

            # Memories
            for m in memories:
                self.conn.execute(
                    """INSERT OR REPLACE INTO memories
                       (id,org_id,workspace_id,app_id,memory_type,extraction_method,
                        content,status,evidence_count,first_observed,last_observed,tags,
                        conflict_status,created_at,updated_at,created_by_pipeline,
                        analytics_run_id,access_count,last_accessed_at,metadata)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (m.id, m.org_id, m.workspace_id, m.app_id, m.memory_type,
                     m.extraction_method, m.content, m.status, m.evidence_count, m.first_observed,
                     m.last_observed, m.tags, m.conflict_status, m.created_at,
                     m.updated_at, m.created_by_pipeline, m.analytics_run_id,
                     m.access_count, m.last_accessed_at, m.metadata),
                )
                emb = memory_embeddings.get(m.id)
                if emb:
                    self._vec_upsert("memory_embeddings", "memory_id", m.id, emb)
                    self.conn.execute(
                        "INSERT OR REPLACE INTO memories_fts(memory_id, content) VALUES (?,?)",
                        (m.id, m.content),
                    )

            # Memory sources
            for ms in memory_sources:
                self.conn.execute(
                    """INSERT OR REPLACE INTO memory_sources
                       (memory_id,session_id,record_id,occurrence_id,relevance_score,span_hint)
                       VALUES (?,?,?,?,?,?)""",
                    (ms.memory_id, ms.session_id, ms.record_id, ms.occurrence_id,
                     ms.relevance_score, ms.span_hint),
                )

            # Session graph edges
            for edge in edges:
                self.conn.execute(
                    """INSERT OR REPLACE INTO session_graph_edges
                       (source_session_id,target_session_id,edge_type,via_entity_id,
                        weight,direction,created_by,created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (edge.source_session_id, edge.target_session_id, edge.edge_type,
                     edge.via_entity_id, edge.weight, edge.direction,
                     edge.created_by, edge.created_at),
                )

    # ------------------------------------------------------------------
    # Session metadata
    # ------------------------------------------------------------------

    def upsert_session_metadata(self, meta: SessionMetadata) -> None:
        """Insert or replace session metadata row."""
        import json as _json
        self.conn.execute(
            """INSERT OR REPLACE INTO session_metadata
               (session_id, dominant_model, models_used, entrypoint, permission_mode,
                user_type, is_sidechain, cc_version, user_turn_count, assistant_turn_count,
                tool_call_count, start_time, end_time, duration_ms,
                total_input_tokens, total_output_tokens, total_cache_read_tokens,
                total_cache_creation_tokens, total_thought_tokens, cwd, git_branch)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                meta.session_id,
                meta.dominant_model,
                _json.dumps(meta.models_used),
                meta.entrypoint,
                meta.permission_mode,
                meta.user_type,
                1 if meta.is_sidechain else 0,
                meta.cc_version,
                meta.user_turn_count,
                meta.assistant_turn_count,
                meta.tool_call_count,
                meta.start_time,
                meta.end_time,
                meta.duration_ms,
                meta.total_input_tokens,
                meta.total_output_tokens,
                meta.total_cache_read_tokens,
                meta.total_cache_creation_tokens,
                meta.total_thought_tokens,
                meta.cwd,
                meta.git_branch,
            ),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def session_exists(self, session_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        return row is not None

    def get_entity_by_id(self, entity_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM entities WHERE id=?", (entity_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_entities_for_name_resolution(
        self, org_id: str, workspace_id: str, entity_type: str
    ) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, canonical_name FROM entities WHERE org_id=? AND workspace_id=? AND entity_type=?",
            (org_id, workspace_id, entity_type),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_entity_by_name(self, org_id: str, workspace_id: str, canonical_name: str) -> dict | None:
        """Look up a structured entity by name only, regardless of type.

        The D-prompt classifies the same real-world entity inconsistently across
        chunks (e.g. `.env` as both `file` and `technology`), and structured entity
        IDs are hashed from (org_id, entity_type, canonical_name) — so without this
        lookup, a type disagreement silently fragments one entity into two DB rows,
        each with its own diluted degree_count. Called before the type-specific
        hash lookup so the first type ever assigned to a name wins.
        """
        row = self.conn.execute(
            "SELECT * FROM entities WHERE org_id=? AND workspace_id=? AND LOWER(canonical_name)=LOWER(?) LIMIT 1",
            (org_id, workspace_id, canonical_name),
        ).fetchone()
        return dict(row) if row else None

    def get_all_entity_embeddings_for_type(
        self, org_id: str, workspace_id: str, entity_type: str
    ) -> list[tuple[str, bytes]]:
        """Return (entity_id, packed_embedding) for concept-type resolution."""
        rows = self.conn.execute(
            """SELECT e.id, ee.embedding
               FROM entities e
               JOIN entity_embeddings ee ON e.id = ee.entity_id
               WHERE e.org_id=? AND e.workspace_id=? AND e.entity_type=?""",
            (org_id, workspace_id, entity_type),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def get_session_stats(self) -> dict[str, int]:
        stats = {}
        for table in ("sessions", "records", "entities", "occurrences", "memories"):
            row = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            stats[table] = row[0]
        return stats

    # ------------------------------------------------------------------
    # Pipeline 2a: Light Analytics — entity promotion and analytics write
    # ------------------------------------------------------------------

    def get_entities_for_promotion(
        self,
        threshold: int = 3,
        occurrence_threshold: int = 20,
        max_session_coverage: float = 0.7,
        max_chunk_coverage: float = 0.7,
        session_occurrence_threshold: int = 0,
        org_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[sqlite3.Row]:
        """Return entities qualifying for promotion via degree_count AND occurrence count.

        Qualifies if BOTH:
          degree_count >= threshold  (seen in enough sessions)
          AND total occurrences >= occurrence_threshold  (total across all sessions)

        threshold=0 disables the session-count gate (degree_count >= 0 is always true),
        leaving occurrence_threshold as the sole inclusion filter. Use threshold=0 for
        single-session projects.

        session_occurrence_threshold > 0: entity must have >= N occurrences in at least
        one session (per-session max). This is more meaningful than the cross-session
        total — it promotes entities that were heavily discussed in a single session,
        rather than lightly mentioned across many. When set, occurrence_threshold still
        applies as an additional cross-session floor.

        Excluded by cross-session IDF if max_session_coverage < 1.0 and total_sessions >= 2:
          (degree_count + 1) / total_sessions > max_session_coverage
        Excluded by within-session chunk IDF if max_chunk_coverage < 1.0:
          distinct_chunks_with_entity / total_chunks > max_chunk_coverage
        Also excludes entities already promoted whose last_seen hasn't advanced.
        org_id / workspace_id filter is skipped when None (corpus-wide).
        """
        filters = [
            "(e.degree_count >= ? AND "
            "(SELECT COUNT(*) FROM occurrences WHERE entity_id = e.id) >= ?)",
            "(e.memory_promoted IS NULL OR e.last_seen > e.memory_promoted)",
        ]
        params: list = [threshold, occurrence_threshold]

        if session_occurrence_threshold > 0:
            filters.append(
                "(SELECT MAX(cnt) FROM ("
                "  SELECT COUNT(*) as cnt FROM occurrences"
                "  WHERE entity_id = e.id GROUP BY session_id"
                ")) >= ?"
            )
            params.append(session_occurrence_threshold)

        if max_session_coverage < 1.0:
            total_sessions = self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            if total_sessions >= 2:
                max_degree = int(total_sessions * max_session_coverage) - 1
                filters.append("e.degree_count <= ?")
                params.append(max_degree)

        if max_chunk_coverage < 1.0:
            total_chunks = self.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            if total_chunks >= 2:
                max_chunks = int(total_chunks * max_chunk_coverage)
                filters.append(
                    "(SELECT COUNT(DISTINCT o.record_id) FROM occurrences o "
                    "WHERE o.entity_id = e.id) <= ?"
                )
                params.append(max_chunks)

        if org_id is not None:
            filters.insert(0, "e.org_id = ?")
            params.insert(0, org_id)
        if workspace_id is not None:
            filters.insert(0, "e.workspace_id = ?")
            params.insert(0, workspace_id)
        where = " AND ".join(filters)
        return self.conn.execute(
            f"SELECT e.* FROM entities e WHERE {where} ORDER BY e.degree_count DESC",
            params,
        ).fetchall()

    def write_inflections(self, inflections: list) -> None:
        """Persist SessionInflection records for a session. Replaces any existing rows."""
        if not inflections:
            return
        with self.conn:
            session_id = inflections[0].session_id
            self.conn.execute(
                "DELETE FROM session_inflections WHERE session_id = ?", (session_id,)
            )
            self.conn.executemany(
                """INSERT OR REPLACE INTO session_inflections
                   (id, session_id, inflection_type, precipitating_turn, entry_turn,
                    exit_turn, signal_strength, dominant_feature, signal_detail, detected_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (i.id, i.session_id, i.inflection_type, i.precipitating_turn,
                     i.entry_turn, i.exit_turn, i.signal_strength, i.dominant_feature,
                     i.signal_detail, i.detected_at)
                    for i in inflections
                ],
            )

    def reset_light_analytics(self) -> None:
        """Removed — analytics light is superseded by ClusteringAnalyticsPipeline."""
        raise NotImplementedError(
            "analytics light is superseded. Use `ats cluster-sessions` / `ats cluster-memories`."
        )

    def delete_entity_light_analytics(self, entity_id: str) -> None:
        """Removed — analytics light is superseded by ClusteringAnalyticsPipeline."""
        raise NotImplementedError(
            "analytics light is superseded. Use `ats cluster-sessions` / `ats cluster-memories`."
        )

    def clean_orphaned_embeddings(self) -> dict[str, int]:
        """Remove vec0 rows whose source rows no longer exist.

        Returns a dict of {table_name: rows_removed}. Safe to call at any time;
        used by drop-session and the clean-orphans CLI command.
        """
        counts: dict[str, int] = {}
        with self.conn:
            for vec_table, pk_col, src_table in _VEC_TABLE_PAIRS:
                before = self.conn.execute(
                    f"SELECT COUNT(*) FROM {vec_table}"
                ).fetchone()[0]
                self.conn.execute(
                    f"DELETE FROM {vec_table} WHERE {pk_col} NOT IN "
                    f"(SELECT id FROM {src_table})"
                )
                after = self.conn.execute(
                    f"SELECT COUNT(*) FROM {vec_table}"
                ).fetchone()[0]
                counts[vec_table] = before - after
        return counts

    def search_memories_bm25(
        self,
        query: str,
        memory_type: str | None = None,
        top_k: int = 50,
        workspace_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """FTS5 BM25 search over memory content. Returns [(memory_id, rank)] best-first."""
        fts = _fts_query(query)
        if not fts:
            return []
        try:
            if memory_type or workspace_id:
                conditions = ["f.content MATCH ?"]
                params: list = [fts]
                if memory_type:
                    conditions.append("m.memory_type = ?")
                    params.append(memory_type)
                if workspace_id:
                    conditions.append("m.workspace_id LIKE ?")
                    params.append(f"%{workspace_id}%")
                params.append(top_k)
                rows = self.conn.execute(
                    "SELECT f.memory_id, f.rank FROM memories_fts f "
                    "JOIN memories m ON m.id = f.memory_id "
                    f"WHERE {' AND '.join(conditions)} ORDER BY f.rank LIMIT ?",
                    params,
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT memory_id, rank FROM memories_fts WHERE content MATCH ? ORDER BY rank LIMIT ?",
                    (fts, top_k),
                ).fetchall()
        except Exception:
            return []
        return [(r[0], r[1]) for r in rows]

    def search_records_bm25(
        self,
        query: str,
        session_id: str | None = None,
        top_k: int = 50,
        workspace_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """FTS5 BM25 search over chunk text. Returns [(record_id, rank)] best-first."""
        fts = _fts_query(query)
        if not fts:
            return []
        try:
            if session_id or workspace_id:
                conditions = ["f.chunk_text MATCH ?"]
                params: list = [fts]
                join = "JOIN records r ON r.id = f.record_id"
                if session_id:
                    conditions.append("r.session_id = ?")
                    params.append(session_id)
                if workspace_id:
                    join += " JOIN sessions s ON s.id = r.session_id"
                    conditions.append("s.workspace_id LIKE ?")
                    params.append(f"%{workspace_id}%")
                params.append(top_k)
                rows = self.conn.execute(
                    f"SELECT f.record_id, f.rank FROM records_fts f "
                    f"{join} "
                    f"WHERE {' AND '.join(conditions)} ORDER BY f.rank LIMIT ?",
                    params,
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT record_id, rank FROM records_fts WHERE chunk_text MATCH ? ORDER BY rank LIMIT ?",
                    (fts, top_k),
                ).fetchall()
        except Exception:
            return []
        return [(r[0], r[1]) for r in rows]

    def search_memories_semantic(
        self,
        query_embedding: list[float],
        top_k: int = 50,
        memory_type: str | None = None,
        workspace_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """sqlite-vec KNN search on memory_embeddings. Returns [(memory_id, distance)] nearest-first."""
        try:
            packed = _pack(query_embedding)
            if memory_type or workspace_id:
                conditions = ["me.embedding MATCH ?", "me.k = ?"]
                params: list = [packed, top_k * 3]
                if memory_type:
                    conditions.append("m.memory_type = ?")
                    params.append(memory_type)
                if workspace_id:
                    conditions.append("m.workspace_id LIKE ?")
                    params.append(f"%{workspace_id}%")
                rows = self.conn.execute(
                    "SELECT me.memory_id, me.distance "
                    "FROM memory_embeddings me "
                    "JOIN memories m ON m.id = me.memory_id "
                    f"WHERE {' AND '.join(conditions)} "
                    "ORDER BY me.distance",
                    params,
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT memory_id, distance FROM memory_embeddings "
                    "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (packed, top_k),
                ).fetchall()
        except Exception:
            return []
        return [(r[0], r[1]) for r in rows[:top_k]]

    def search_records_semantic(
        self,
        query_embedding: list[float],
        top_k: int = 50,
        session_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """sqlite-vec KNN search on record_embeddings. Returns [(record_id, distance)] nearest-first."""
        try:
            packed = _pack(query_embedding)
            if session_id or workspace_id:
                conditions = ["re.embedding MATCH ?", "re.k = ?"]
                params: list = [packed, top_k * 3]
                join = "JOIN records r ON r.id = re.record_id"
                if session_id:
                    conditions.append("r.session_id = ?")
                    params.append(session_id)
                if workspace_id:
                    join += " JOIN sessions s ON s.id = r.session_id"
                    conditions.append("s.workspace_id LIKE ?")
                    params.append(f"%{workspace_id}%")
                rows = self.conn.execute(
                    "SELECT re.record_id, re.distance "
                    "FROM record_embeddings re "
                    f"{join} "
                    f"WHERE {' AND '.join(conditions)} "
                    "ORDER BY re.distance",
                    params,
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT record_id, distance FROM record_embeddings "
                    "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (packed, top_k),
                ).fetchall()
        except Exception:
            return []
        return [(r[0], r[1]) for r in rows[:top_k]]

    def search_sessions_bm25(
        self, query: str, top_k: int = 50, workspace_id: str | None = None
    ) -> list[tuple[str, float]]:
        """FTS5 BM25 search over session summaries and workspace IDs. Returns [(session_id, rank)] best-first."""
        fts = _fts_query(query)
        if not fts:
            return []
        try:
            if workspace_id:
                rows = self.conn.execute(
                    "SELECT session_id, rank FROM sessions_fts "
                    "WHERE sessions_fts MATCH ? AND workspace_id LIKE ? ORDER BY rank LIMIT ?",
                    (fts, f"%{workspace_id}%", top_k),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT session_id, rank FROM sessions_fts WHERE sessions_fts MATCH ? ORDER BY rank LIMIT ?",
                    (fts, top_k),
                ).fetchall()
        except Exception:
            return []
        return [(r[0], r[1]) for r in rows]

    def increment_access_count(self, memory_id: str) -> None:
        """Increment access_count and last_accessed_at for a memory."""
        self.conn.execute(
            "UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), memory_id),
        )
        self.conn.commit()

    def insert_memory_retrieval(self, retrieval: "MemoryRetrieval") -> None:
        """Insert one retrieval row. Idempotent by id."""
        self.conn.execute(
            "INSERT OR IGNORE INTO memory_retrievals "
            "(id, session_id, segment_id, exchange_idx, memory_id, query_text, "
            " query_features, relevance_rank, relevance_score, surfaced, retrieved_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                retrieval.id, retrieval.session_id, retrieval.segment_id,
                retrieval.exchange_idx, retrieval.memory_id, retrieval.query_text,
                retrieval.query_features, retrieval.relevance_rank,
                retrieval.relevance_score, 1 if retrieval.surfaced else 0,
                retrieval.retrieved_at,
            ),
        )
        self.conn.commit()

    def insert_memory_retrievals_batch(self, retrievals: list["MemoryRetrieval"]) -> None:
        """Bulk insert. Skips duplicates on id."""
        if not retrievals:
            return
        self.conn.executemany(
            "INSERT OR IGNORE INTO memory_retrievals "
            "(id, session_id, segment_id, exchange_idx, memory_id, query_text, "
            " query_features, relevance_rank, relevance_score, surfaced, retrieved_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    r.id, r.session_id, r.segment_id, r.exchange_idx, r.memory_id,
                    r.query_text, r.query_features, r.relevance_rank,
                    r.relevance_score, 1 if r.surfaced else 0, r.retrieved_at,
                )
                for r in retrievals
            ],
        )
        self.conn.commit()

    def get_occurrences_for_entity(
        self, entity_id: str, limit: int = 20
    ) -> list[sqlite3.Row]:
        """Return up to `limit` occurrences for an entity, ranked by recency."""
        return self.conn.execute(
            """SELECT o.id, o.session_id, o.context_text, o.role, o.mention_text,
                      o.created_at, s.session_timestamp
               FROM occurrences o
               JOIN sessions s ON s.id = o.session_id
               WHERE o.entity_id = ?
               ORDER BY o.created_at DESC
               LIMIT ?""",
            (entity_id, limit),
        ).fetchall()

    def write_analytics_batch(
        self,
        memories: list["Memory"],
        memory_sources: list["MemorySource"],
        edges: list["SessionGraphEdge"],
        entity_updates: list[tuple[str, str, str]],
        run: "AnalyticsRun | None" = None,
    ) -> None:
        """Write analytics output in a single transaction.

        run: if provided, writes the analytics_runs record. Pass None for
             intermediate flushes during a long run — write run only at the end.
        entity_updates: list of (entity_id, entity_profile, memory_promoted ISO8601)
        """
        with self.conn:
            # Memories
            for m in memories:
                self.conn.execute(
                    """INSERT OR REPLACE INTO memories
                       (id,org_id,workspace_id,app_id,memory_type,extraction_method,
                        content,status,evidence_count,first_observed,last_observed,tags,
                        conflict_status,created_at,updated_at,created_by_pipeline,
                        analytics_run_id,entity_id,access_count,last_accessed_at,metadata)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (m.id, m.org_id, m.workspace_id, m.app_id, m.memory_type,
                     m.extraction_method, m.content, m.status, m.evidence_count,
                     m.first_observed, m.last_observed, m.tags,
                     m.conflict_status, m.created_at, m.updated_at,
                     m.created_by_pipeline, m.analytics_run_id, m.entity_id,
                     m.access_count, m.last_accessed_at, m.metadata),
                )
                self.conn.execute(
                    "INSERT OR REPLACE INTO memories_fts(memory_id, content) VALUES (?,?)",
                    (m.id, m.content),
                )
            # Memory sources
            for src in memory_sources:
                self.conn.execute(
                    """INSERT OR REPLACE INTO memory_sources
                       (memory_id,session_id,record_id,occurrence_id,relevance_score,span_hint)
                       VALUES (?,?,?,?,?,?)""",
                    (src.memory_id, src.session_id, src.record_id,
                     src.occurrence_id, src.relevance_score, src.span_hint),
                )
            # Session graph edges (same_entity)
            for edge in edges:
                self.conn.execute(
                    """INSERT OR REPLACE INTO session_graph_edges
                       (source_session_id,target_session_id,edge_type,via_entity_id,
                        weight,direction,created_by,created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (edge.source_session_id, edge.target_session_id,
                     edge.edge_type, edge.via_entity_id, edge.weight,
                     edge.direction, edge.created_by, edge.created_at),
                )
            # Entity profile + memory_promoted timestamp
            for entity_id, profile_text, promoted_at in entity_updates:
                self.conn.execute(
                    """UPDATE entities
                       SET entity_profile = ?, memory_promoted = ?, updated_at = ?
                       WHERE id = ?""",
                    (profile_text, promoted_at, promoted_at, entity_id),
                )
            # analytics_runs record — only written on final flush
            if run is not None:
                self.conn.execute(
                    """INSERT OR REPLACE INTO analytics_runs
                       (id,pipeline,started_at,completed_at,session_count,memory_count,
                        cluster_count,conflict_count,k_value,embedding_model,labeler_model)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (run.id, run.pipeline, run.started_at, run.completed_at,
                     run.session_count, run.memory_count, run.cluster_count,
                     run.conflict_count, run.k_value, run.embedding_model, run.labeler_model),
                )

    # ------------------------------------------------------------------
    # Deferred embeddings — `ats embed` queries and writes
    # ------------------------------------------------------------------

    def get_unembedded_records(self, limit: int = 2000) -> list[dict]:
        rows = self.conn.execute(
            """SELECT r.id, r.chunk_text FROM records r
               WHERE NOT EXISTS (SELECT 1 FROM record_embeddings re WHERE re.record_id = r.id)
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unembedded_entities(self, limit: int = 2000) -> list[dict]:
        rows = self.conn.execute(
            """SELECT e.id, e.entity_type, e.canonical_name, e.entity_profile FROM entities e
               WHERE NOT EXISTS (SELECT 1 FROM entity_embeddings ee WHERE ee.entity_id = e.id)
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unembedded_occurrences(self, limit: int = 2000) -> list[dict]:
        rows = self.conn.execute(
            """SELECT o.id, o.context_text FROM occurrences o
               WHERE NOT EXISTS (SELECT 1 FROM occurrence_embeddings oe WHERE oe.occurrence_id = o.id)
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unembedded_memories(self, limit: int = 2000) -> list[dict]:
        rows = self.conn.execute(
            """SELECT m.id, m.content FROM memories m
               WHERE NOT EXISTS (SELECT 1 FROM memory_embeddings me WHERE me.memory_id = m.id)
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unembedded_sessions(self, limit: int = 2000) -> list[dict]:
        rows = self.conn.execute(
            """SELECT s.id, s.embedding_text FROM sessions s
               WHERE s.embedding_text IS NOT NULL AND s.embedding_text != ''
               AND NOT EXISTS (SELECT 1 FROM session_embeddings se WHERE se.session_id = s.id)
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def write_embeddings(
        self,
        record_embeddings: "dict[str, list[float]]",
        entity_embeddings: "dict[str, list[float]]",
        occurrence_embeddings: "dict[str, list[float]]",
        memory_embeddings: "dict[str, list[float]]",
        session_embeddings: "dict[str, list[float]]",
    ) -> None:
        """Write embeddings to their respective vec tables in a single transaction."""
        with self.conn:
            for record_id, emb in record_embeddings.items():
                self._vec_upsert("record_embeddings", "record_id", record_id, emb)
            for entity_id, emb in entity_embeddings.items():
                self._vec_upsert("entity_embeddings", "entity_id", entity_id, emb)
            for occurrence_id, emb in occurrence_embeddings.items():
                self._vec_upsert("occurrence_embeddings", "occurrence_id", occurrence_id, emb)
            for memory_id, emb in memory_embeddings.items():
                self._vec_upsert("memory_embeddings", "memory_id", memory_id, emb)
            for session_id, emb in session_embeddings.items():
                self._vec_upsert("session_embeddings", "session_id", session_id, emb)

    def get_frequency_memories_for_annotation(self) -> list[dict]:
        """Return all frequency memories with entity context, for the annotation UI.

        Returns list of dicts with keys:
            memory_id, content, memory_type, evidence_count,
            entity_id, entity_name, entity_type, entity_profile, degree_count
        Ordered by entity_name, memory_type for stable grouping.
        Memories with NULL entity_id are included (entity_* fields will be None).
        """
        rows = self.conn.execute(
            """SELECT m.id AS memory_id, m.content, m.memory_type, m.evidence_count,
                      e.id AS entity_id, e.canonical_name AS entity_name,
                      e.entity_type, e.entity_profile, e.degree_count
               FROM memories m
               LEFT JOIN entities e ON e.id = m.entity_id
               WHERE m.extraction_method = 'frequency'
               ORDER BY COALESCE(e.canonical_name, ''), m.memory_type"""
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Per-step scoring (Stage 2)
    # ------------------------------------------------------------------

    def insert_step_score(self, score: "StepScore") -> None:
        """Insert or replace a single step_scores row, keyed by (session_id, exchange_idx)."""
        self.conn.execute(
            """INSERT OR REPLACE INTO step_scores
               (session_id, exchange_idx, evidence_supports, progress_vector,
                cost_vector, agent_action_summary, features, observer_model, scored_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (score.session_id, score.exchange_idx, score.evidence_supports,
             score.progress_vector, score.cost_vector, score.agent_action_summary,
             score.features, score.observer_model, score.scored_at),
        )
        self.conn.commit()

    def insert_step_scores_batch(self, scores: list["StepScore"]) -> None:
        """Bulk insert/replace step_scores rows."""
        if not scores:
            return
        self.conn.executemany(
            """INSERT OR REPLACE INTO step_scores
               (session_id, exchange_idx, evidence_supports, progress_vector,
                cost_vector, agent_action_summary, features, observer_model, scored_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (s.session_id, s.exchange_idx, s.evidence_supports,
                 s.progress_vector, s.cost_vector, s.agent_action_summary,
                 s.features, s.observer_model, s.scored_at)
                for s in scores
            ],
        )
        self.conn.commit()

    def get_existing_step_score_indices(self, session_id: str) -> set[int]:
        """Return the set of exchange_idx values already scored for this session."""
        rows = self.conn.execute(
            "SELECT exchange_idx FROM step_scores WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return {r[0] for r in rows}

    def get_sessions_with_turn_descriptors(self) -> list[tuple[str, str | None]]:
        """Return [(session_id, turn_descriptors_json)] for all sessions whose
        turn_descriptors column is non-empty."""
        rows = self.conn.execute(
            "SELECT id, turn_descriptors FROM sessions "
            "WHERE turn_descriptors IS NOT NULL AND turn_descriptors != ''"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    # ------------------------------------------------------------------
    # Critical-step detection (Stage 3)
    # ------------------------------------------------------------------

    def insert_critical_steps(self, steps: list["CriticalStep"]) -> None:
        """Bulk insert/replace critical_steps rows, keyed by id."""
        if not steps:
            return
        self.conn.executemany(
            """INSERT OR REPLACE INTO critical_steps
               (id, session_id, exchange_idx, tag, failure_mode, delta, score, features, detected_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (s.id, s.session_id, s.exchange_idx, s.tag, s.failure_mode,
                 s.delta, s.score, s.features, s.detected_at)
                for s in steps
            ],
        )
        self.conn.commit()

    def delete_critical_steps_for_session(self, session_id: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM critical_steps WHERE session_id = ?", (session_id,),
        )
        self.conn.commit()
        return cur.rowcount or 0

    def get_critical_steps(self, session_id: str | None = None) -> list["CriticalStep"]:
        if session_id is None:
            rows = self.conn.execute(
                "SELECT * FROM critical_steps ORDER BY session_id, exchange_idx"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM critical_steps WHERE session_id = ? ORDER BY exchange_idx",
                (session_id,),
            ).fetchall()
        return [CriticalStep(**dict(r)) for r in rows]

    def get_sessions_with_step_scores(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT session_id FROM step_scores ORDER BY session_id"
        ).fetchall()
        return [r[0] for r in rows]

    def has_critical_steps(self, session_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM critical_steps WHERE session_id = ? LIMIT 1", (session_id,),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Structural embeddings and session typing (Task 5)
    # ------------------------------------------------------------------

    def upsert_structural_embedding(self, session_id: str, embedding: list[float]) -> None:
        """Store or update the structural (raw head+tail) embedding for a session."""
        self._vec_upsert("session_structural_embeddings", "session_id", session_id, embedding)

    def set_session_type(self, session_id: str, session_type: str) -> None:
        """Write the cluster-derived session_type label to the sessions row."""
        self.conn.execute(
            "UPDATE sessions SET session_type=? WHERE id=?",
            (session_type, session_id),
        )
        self.conn.commit()

    def get_sessions_without_structural_embedding(self) -> list[dict]:
        """Return sessions that have no structural embedding yet."""
        rows = self.conn.execute(
            """SELECT s.id, s.session_summary
               FROM sessions s
               WHERE NOT EXISTS (
                   SELECT 1 FROM session_structural_embeddings se WHERE se.session_id = s.id
               )
               AND s.session_summary IS NOT NULL AND s.session_summary != ''"""
        ).fetchall()
        return [{"id": r[0], "session_summary": r[1]} for r in rows]

    def upsert_topic_embedding(self, session_id: str, embedding: list[float]) -> None:
        """Store or update the topic (avg-chunk) embedding for a session."""
        self._vec_upsert("session_topic_embeddings", "session_id", session_id, embedding)

    def get_sessions_without_topic_embedding(self) -> list[dict]:
        """Return sessions that have no topic embedding yet."""
        rows = self.conn.execute(
            """SELECT s.id, s.session_summary
               FROM sessions s
               WHERE NOT EXISTS (
                   SELECT 1 FROM session_topic_embeddings te WHERE te.session_id = s.id
               )
               AND s.session_summary IS NOT NULL AND s.session_summary != ''"""
        ).fetchall()
        return [{"id": r[0], "session_summary": r[1]} for r in rows]

    def get_all_structural_embeddings(self) -> list[tuple[str, list[float]]]:
        """Return (session_id, decoded vector) for every session with a structural embedding."""
        rows = self.conn.execute(
            "SELECT session_id, embedding FROM session_structural_embeddings"
        ).fetchall()
        result = []
        for session_id, blob in rows:
            n = len(blob) // 4
            result.append((session_id, list(struct.unpack(f"{n}f", blob))))
        return result

    def replace_session_graph_edges_of_type(self, edge_type: str, edges: list[SessionGraphEdge]) -> None:
        """Atomically replace every session_graph_edges row of one edge_type with a fresh set.

        Used for edge types derived by full recomputation each run (e.g. embedding
        similarity) rather than incremental accumulation — avoids stale edges left
        over from a pair that no longer qualifies (dropped out of top-k, or the
        underlying embedding changed).
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM session_graph_edges WHERE edge_type = ?", (edge_type,)
            )
            for edge in edges:
                self.conn.execute(
                    """INSERT OR REPLACE INTO session_graph_edges
                       (source_session_id,target_session_id,edge_type,via_entity_id,
                        weight,direction,created_by,created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (edge.source_session_id, edge.target_session_id, edge.edge_type,
                     edge.via_entity_id, edge.weight, edge.direction,
                     edge.created_by, edge.created_at),
                )

    def get_record_embeddings_for_session(self, session_id: str) -> list[list[float]]:
        """Return all chunk embedding vectors for a session, in chunk_index order."""
        import struct
        rows = self.conn.execute(
            """SELECT re.embedding
               FROM record_embeddings re
               JOIN records r ON r.id = re.record_id
               WHERE r.session_id = ?
               ORDER BY r.chunk_index""",
            (session_id,),
        ).fetchall()
        result = []
        for (blob,) in rows:
            n = len(blob) // 4
            result.append(list(struct.unpack(f"{n}f", blob)))
        return result
