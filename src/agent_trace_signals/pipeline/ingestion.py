"""Pipeline 1: Full ingestion orchestrator."""

from __future__ import annotations
import hashlib
import json
import logging
from typing import TYPE_CHECKING

from agent_trace_signals.config import Config
from agent_trace_signals.db.store import SQLiteStore
from agent_trace_signals.models import (
    ParsedSession, Record, Session, SessionGraphEdge, Memory, MemorySource,
)
from agent_trace_signals.pipeline.chunk_analyzer import DChunkAnalyzer
from agent_trace_signals.pipeline.entity_resolver import EntityResolver
from agent_trace_signals.pipeline.occurrence_extractor import OccurrenceExtractor
from agent_trace_signals.pipeline.summarizer import Summarizer
from agent_trace_signals.pipeline.utils import format_workspace

if TYPE_CHECKING:
    from agent_trace_signals.embedder import Embedder

logger = logging.getLogger(__name__)



def _now() -> str:
    """Get current timestamp in ISO8601 format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class IngestionPipeline:
    """Orchestrate Pipeline 1: ingestion of sessions with summaries, entities, and memories."""

    def __init__(
        self,
        store: SQLiteStore,
        summarizer: Summarizer,
        embedder: "Embedder",
        entity_resolver: EntityResolver,
        occurrence_extractor: OccurrenceExtractor,
        config: Config,
        skip_memories: bool = False,
        skip_summaries: bool = False,
    ) -> None:
        """Initialize ingestion pipeline.

        Args:
            store: SQLite database store
            summarizer: Text summarizer
            embedder: Text embedder
            entity_resolver: Entity resolution module
            occurrence_extractor: Occurrence extraction module
            config: Full configuration
            skip_memories: Skip memory extraction
            skip_summaries: Skip summary generation
        """
        self.store = store
        self.summarizer = summarizer
        self.embedder = embedder
        self.entity_resolver = entity_resolver
        self.occurrence_extractor = occurrence_extractor
        self.config = config
        self.skip_memories = skip_memories
        self.skip_summaries = skip_summaries
        self.max_workers = 4  # cap parallel chunk threads; lower on memory-constrained machines

        # Initialize D-prompt analyzer
        from agent_trace_signals.providers.ollama import OllamaProvider
        provider = OllamaProvider(config.models)
        self.d_analyzer = DChunkAnalyzer(provider, config.models, config.pipeline)

    def run(
        self,
        parsed_session: ParsedSession,
        abs_path: str,
        content_hash: str,
    ) -> Session:
        """Run full Pipeline 1 ingestion.

        Args:
            parsed_session: Parsed session from source plugin
            abs_path: Absolute path to source file
            content_hash: SHA256 hash of file contents

        Returns:
            Session object that was written to database
        """
        # Step 1: Dedup check
        session_id = self._compute_session_id(
            parsed_session.source_plugin, abs_path, content_hash
        )

        if self.store.session_exists(session_id):
            logger.info(f"Session {session_id} already ingested, skipping")
            # Return a placeholder session (caller should handle this)
            return Session(
                id=session_id,
                source_type=parsed_session.source_type,
                source_plugin=parsed_session.source_plugin,
                workspace_id=parsed_session.workspace_id,
            )

        logger.info(f"Ingesting session {session_id}")

        # Step 2: Build Records
        records = self._build_records(parsed_session, session_id)

        if not records:
            raise ValueError(f"Session {session_id} produced no chunks after parsing")

        org_id = "default"  # TODO: derive from parsed_session or config
        workspace_id = parsed_session.workspace_id
        session_timestamp = parsed_session.session_timestamp
        # Create temp session for memory extraction
        temp_session = Session(
            id=session_id,
            org_id="default",
            workspace_id=workspace_id,
            source_type=parsed_session.source_type,
            source_plugin=parsed_session.source_plugin,
            session_timestamp=session_timestamp,
        )

        # D-prompt path: Combined analysis
        logger.info(f"Using D-prompt combined analysis for {session_id}")
        entities, occurrences, memories, memory_sources = self._run_d_prompt_path(
            records, temp_session, org_id, workspace_id, session_timestamp
        )

        # Structural edges (applies to both paths).
        # Edges are conceptually undirected (see SessionGraphEdge.direction), so every
        # edge is inserted as BOTH (session_id, other_id) and (other_id, session_id).
        # Related-Sessions lookups only filter on source_session_id, so a one-directional
        # insert would make the connection visible only from whichever session happened
        # to be ingested later — invisible from the earlier session's own detail view.
        edges = []

        def _add_edge(other_id: str, edge_type: str, via_entity_id: str, weight: float = 1.0) -> None:
            edges.append(SessionGraphEdge(
                source_session_id=session_id, target_session_id=other_id,
                edge_type=edge_type, via_entity_id=via_entity_id, weight=weight,
            ))
            edges.append(SessionGraphEdge(
                source_session_id=other_id, target_session_id=session_id,
                edge_type=edge_type, via_entity_id=via_entity_id, weight=weight,
            ))

        for entity in entities:
            if entity.entity_type.lower() in self.config.pipeline.structural_entity_types:
                # Query for other sessions referencing this entity
                try:
                    rows = self.store.conn.execute(
                        """SELECT DISTINCT session_id FROM occurrences
                           WHERE entity_id=? AND session_id!=?""",
                        (entity.id, session_id),
                    ).fetchall()
                    if not rows:
                        continue
                    # Weight is 1/session_count referencing this entity (this session
                    # plus every other one) — same specificity principle as
                    # shared_memory/workspace above: a shared reference to a rare,
                    # specific file is a strong signal, a shared reference to a file
                    # nearly every session touches (e.g. this project's own
                    # README.md/design_decisions.md) is not. Verified empirically:
                    # without this, one seed session had 35 structural candidates all
                    # tied at flat weight=1.0, filling graph_walk's entire per-hop cap
                    # before any shared_memory candidate got a chance to compete.
                    structural_weight = 1.0 / (len(rows) + 1)
                    for row in rows:
                        _add_edge(row[0], "structural", entity.id, weight=structural_weight)
                except Exception as e:
                    logger.warning(f"Failed to create structural edges: {e}")

        # Deterministic same-workspace edges. This does NOT depend on LLM entity
        # classification — it's a direct comparison of session.workspace_id, which
        # is set at parse time from the actual working directory. Different
        # harnesses encode the same project differently (e.g. Claude Code:
        # "-Users-x-src-foo", opencode: "foo"), so we compare via
        # format_workspace() rather than raw equality.
        normalized_ws = format_workspace(workspace_id) if workspace_id else ""
        if normalized_ws:
            try:
                rows = self.store.conn.execute(
                    "SELECT id, workspace_id FROM sessions "
                    "WHERE id != ? AND workspace_id IS NOT NULL AND workspace_id != ''",
                    (session_id,),
                ).fetchall()
                same_ws_ids = [other_id for other_id, other_ws in rows if format_workspace(other_ws) == normalized_ws]
                # Weight is 1/sibling_count (this session plus every other session
                # already in the same workspace), same specificity principle as
                # shared_memory's clustering_analytics.py weighting: "same project"
                # is a weak relevance signal on its own, weaker the bigger the
                # project's session count, and graph_walk's BFS should treat a
                # 30-session workspace clique as noise the same way it now treats a
                # 30-session shared_memory cluster as noise. A snapshot at ingestion
                # time, not recomputed as the workspace grows — the same tradeoff
                # `structural` edges already accept (verified empirically: without
                # this, workspace's still-flat weight=1.0 out-ranked a genuinely
                # relevant but appropriately-downweighted shared_memory cluster in
                # graph_walk's candidate ordering — see design_decisions.md 2026-07-30).
                workspace_weight = 1.0 / (len(same_ws_ids) + 1) if same_ws_ids else 1.0
                for other_id in same_ws_ids:
                    _add_edge(other_id, "workspace", "", weight=workspace_weight)
            except Exception as e:
                logger.warning(f"Failed to create workspace edges: {e}")

        # Step 10: Session summary
        chunk_summaries = [r.chunk_summary for r in records if r.chunk_summary]
        source_type_label = "agent session" if parsed_session.source_type == "agent_trace" else "meeting"

        session = Session(
            id=session_id,
            org_id="default",
            workspace_id=workspace_id,
            source_type=parsed_session.source_type,
            source_plugin=parsed_session.source_plugin,
            session_timestamp=session_timestamp,
            ingested_at=_now(),
            chunk_count=len(records),
        )

        # Forward plugin metadata to session raw_facets
        plugin_meta = parsed_session.metadata or {}
        plugin_facets = parsed_session.raw_facets or {}
        session_meta_dict = plugin_meta.get("session_metadata", {})
        if not isinstance(session_meta_dict, dict):
            session_meta_dict = {}

        raw_facets_dict = {
            **plugin_facets,
            "cwd": session_meta_dict.get("cwd"),
            "git_branch": session_meta_dict.get("git_branch"),
            "cc_version": session_meta_dict.get("cc_version"),
            "away_summary": plugin_meta.get("away_summary_latest"),
        }
        # Remove None values
        raw_facets_dict = {k: v for k, v in raw_facets_dict.items() if v is not None}
        session.raw_facets = json.dumps(raw_facets_dict)

        if chunk_summaries and not self.skip_summaries:
            session.session_summary = self.summarizer.summarize_session(
                chunk_summaries, source_type_label
            )
        else:
            # Fall back to the latest away_summary written by Claude Code (free, no LLM call)
            session.session_summary = parsed_session.metadata.get("away_summary_latest") or ""

        session.embedding_text = session.session_summary

        # Step 11: Write (embeddings are deferred — run `ats embed` after ingestion)
        self.store.write_session_batch(
            session=session,
            records=records,
            entities=entities,
            occurrences=occurrences,
            memories=memories,
            memory_sources=memory_sources,
            edges=edges,
            record_embeddings={},
            session_embedding=None,
            entity_embeddings={},
            occurrence_embeddings={},
            memory_embeddings={},
        )

        # Step 11b: Compute and store structural embedding (sampled raw chunk texts)
        from agent_trace_signals.pipeline.utils import build_structural_text
        raw_texts = [r.chunk_text for r in records if r.chunk_text]
        if raw_texts:
            structural_text = build_structural_text(raw_texts)
            if structural_text:
                structural_emb = self.embedder.embed(structural_text)
                self.store.upsert_structural_embedding(session.id, structural_emb)

        # Step 11d: Write session metadata if extracted by plugin
        raw_sm = parsed_session.metadata.get("session_metadata")
        if raw_sm:
            try:
                from agent_trace_signals.models import SessionMetadata
                # Override session_id to use the pipeline-computed one (not plugin's raw sessionId)
                raw_sm_copy = dict(raw_sm)
                raw_sm_copy["session_id"] = session_id
                meta = SessionMetadata(**raw_sm_copy)
                self.store.upsert_session_metadata(meta)
            except Exception as e:
                logger.warning(f"Failed to write session metadata for {session_id}: {e}")

        # Step 12: Inflection detection (structural features, no LLM)
        raw_tds = parsed_session.metadata.get("turn_descriptors", [])
        if raw_tds:
            from agent_trace_signals.models import TurnDescriptor as _TD
            from agent_trace_signals.pipeline.inflection_detector import InflectionDetector
            turn_descriptors = [_TD(**td) if isinstance(td, dict) else td for td in raw_tds]
            inflections = InflectionDetector().detect(turn_descriptors, session_id)
            if inflections:
                self.store.write_inflections(inflections)
                logger.info(f"  Inflections detected: {len(inflections)}")

            # Persist turn_descriptors on the session for export use
            self.store.conn.execute(
                "UPDATE sessions SET turn_descriptors = ? WHERE id = ?",
                (json.dumps([td if isinstance(td, dict) else td.model_dump() for td in raw_tds]), session_id)
            )
            self.store.conn.commit()

        # NOTE (GAP-4): Context-continuation messages ("This session is being continued...")
        # appear as user messages at entry_turn+1 near context boundaries.
        # These are NOT genuine corrections — they are system-injected scaffolding.
        # Filter them out at export time when building ground truth labels.
        # Detection: exchanges[entry_turn+1] user content starts with
        # "This session is being continued"

        logger.info(
            f"Ingested session {session_id}: {len(records)} records, "
            f"{len(entities)} entities, {len(occurrences)} occurrences, "
            f"{len(memories)} memories"
        )

        # Step 13: Return
        return session

    def _run_d_prompt_path(
        self,
        records: list[Record],
        session: Session,
        org_id: str,
        workspace_id: str,
        session_timestamp: str | None,
    ) -> tuple[list, list, list, list]:
        """Run the D-prompt combined analysis path.

        Args:
            records: List of records to analyze
            session: Session object for context
            org_id: Organization ID
            workspace_id: Workspace ID
            session_timestamp: Session timestamp

        Returns:
            Tuple of (entities, occurrences, memories, memory_sources)
        """
        # Step 3: D-prompt analysis on all chunks
        d_results = self.d_analyzer.analyze_batch(records, session, max_workers=self.max_workers)

        # Set chunk summaries from D results
        chunk_summaries = []
        all_d_entities = []
        all_d_memories = []
        chunk_index_to_d_entities = {}

        for record, d_result in zip(records, d_results):
            record.chunk_summary = d_result.summary
            chunk_summaries.append(d_result.summary) if d_result.summary else None
            all_d_entities.extend(d_result.entities)
            all_d_memories.extend(d_result.memories)
            chunk_index_to_d_entities[record.chunk_index] = d_result.entities

        # Step 4: Entity resolution (D-entities only)
        resolved_entities = self.entity_resolver.resolve_d_entities(
            all_d_entities, org_id, workspace_id, session_timestamp
        )

        # Deduplicate entities by id
        unique_entities = {e.id: e for e in resolved_entities}.values()
        entities = list(unique_entities)

        # For D-entities, set confidence based on chunk frequency
        # (simpler than the original approach since D already filters)
        for entity in entities:
            entity.confidence = "session"  # D-extracted entities are pre-filtered

        # Step 5: Occurrence extraction from D entities
        occurrences = []
        for record in records:
            record_d_entities = chunk_index_to_d_entities.get(record.chunk_index, [])
            record_occurrences = self.occurrence_extractor.extract_from_d(
                record, entities, record_d_entities
            )
            occurrences.extend(record_occurrences)

        # Step 6: Store D memories
        memories = []
        memory_sources = []
        if not self.skip_memories:
            for record, d_result in zip(records, d_results):
                for d_memory in d_result.memories:
                    memory = Memory(
                        org_id=org_id,
                        workspace_id=workspace_id,
                        app_id="default",
                        memory_type=d_memory.type,
                        extraction_method="d_combined",
                        content=d_memory.content,
                        status=d_memory.status or None,
                        first_observed=session_timestamp,
                        last_observed=session_timestamp,
                        created_by_pipeline="ingestion",
                    )
                    memories.append(memory)

                    # Create memory source
                    memory_source = MemorySource(
                        memory_id=memory.id,
                        session_id=session.id,
                        record_id=record.id,
                        relevance_score=1.0,
                    )
                    memory_sources.append(memory_source)

                for d_preference in d_result.preferences:
                    memory = Memory(
                        org_id=org_id,
                        workspace_id=workspace_id,
                        app_id="default",
                        memory_type="preference",
                        extraction_method="d_combined",
                        content=d_preference.content,
                        first_observed=session_timestamp,
                        last_observed=session_timestamp,
                        created_by_pipeline="ingestion",
                    )
                    memories.append(memory)
                    memory_sources.append(MemorySource(
                        memory_id=memory.id,
                        session_id=session.id,
                        record_id=record.id,
                        relevance_score=1.0,
                    ))

        # Step 7: Store D patterns as memory rows with pattern_* types
        _valid_pattern_types = {"strategy", "recovery", "inefficiency"}
        if not self.skip_memories:
            for record, d_result in zip(records, d_results):
                for d_pattern in d_result.patterns:
                    if d_pattern.type not in _valid_pattern_types:
                        continue
                    pattern_type = f"pattern_{d_pattern.type}"
                    memory = Memory(
                        org_id=org_id,
                        workspace_id=workspace_id,
                        app_id="default",
                        memory_type=pattern_type,
                        extraction_method="d_combined",
                        content=d_pattern.content,
                        first_observed=session_timestamp,
                        last_observed=session_timestamp,
                        created_by_pipeline="ingestion",
                    )
                    memories.append(memory)
                    memory_sources.append(MemorySource(
                        memory_id=memory.id,
                        session_id=session.id,
                        record_id=record.id,
                        relevance_score=1.0,
                    ))

        return entities, occurrences, memories, memory_sources

    def _compute_session_id(
        self, source_plugin: str, abs_path: str, content_hash: str
    ) -> str:
        """Compute session ID from source info.

        Args:
            source_plugin: Source plugin name
            abs_path: Absolute file path
            content_hash: SHA256 hash of file contents

        Returns:
            Session ID (SHA256)
        """
        key = f"{source_plugin}:{abs_path}:{content_hash}"
        return hashlib.sha256(key.encode()).hexdigest()

    def _build_records(self, parsed_session: ParsedSession, session_id: str) -> list[Record]:
        """Build Record objects from parsed chunks.

        Args:
            parsed_session: Parsed session
            session_id: Session ID

        Returns:
            List of Record objects
        """
        records = []
        for chunk in parsed_session.chunks:
            record = Record(
                id=Record.make_id(session_id, chunk.chunk_index),
                session_id=session_id,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                evidence_text=chunk.evidence_text,
                embedding_text=chunk.chunk_text,  # For now
                token_count=chunk.token_count,
                span_start=chunk.span_start,
                span_end=chunk.span_end,
            )
            records.append(record)

        return records
