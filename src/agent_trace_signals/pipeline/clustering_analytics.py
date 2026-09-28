"""Clustering analytics pipeline — session type tagging via HDBSCAN."""

from __future__ import annotations
import itertools
import json
import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from agent_trace_signals.db.store import SQLiteStore

logger = logging.getLogger(__name__)

# A prior cluster memory is marked superseded when at least this fraction of its
# own members are also members of the new cluster being minted — see the
# supersession block in _cluster_and_mint().
_SUPERSEDE_OVERLAP_THRESHOLD = 0.5


class ClusteringAnalyticsPipeline:
    """Phase 1: Tag session types via HDBSCAN over structural embeddings.
    Phase 2: Cluster memory+pattern rows and consolidate recurring clusters (not yet implemented).
    """

    def __init__(self, store: "SQLiteStore") -> None:
        self.store = store

    def tag_session_types(self, min_cluster_size: int = 3) -> dict[str, int]:
        """Run HDBSCAN over session structural embeddings, write session_type tags.

        Returns:
            dict mapping cluster label (e.g. "cluster_0") to session count.
            Noise sessions (HDBSCAN label -1) get session_type "noise".
            Returns {} if fewer than min_cluster_size * 2 sessions have embeddings.
        """
        import hdbscan

        # Fetch all structural embeddings
        rows = self.store.conn.execute(
            "SELECT session_id, embedding FROM session_structural_embeddings"
        ).fetchall()

        if len(rows) < min_cluster_size * 2:
            logger.warning(
                f"Only {len(rows)} structural embeddings — need at least {min_cluster_size * 2} to cluster. "
                "Skipping session type tagging."
            )
            return {}

        session_ids = [r[0] for r in rows]
        # sqlite-vec stores embeddings as blobs; deserialize
        embeddings = np.array([
            np.frombuffer(r[1], dtype=np.float32) for r in rows
        ])

        # cluster_selection_method="leaf" (not the default "eom"/excess-of-mass):
        # eom strongly prefers fewer, larger, more "stable" clusters, which on this
        # corpus collapsed ~70% of sessions (all substantial multi-chunk work) into
        # a single blob. leaf yields much finer, more even granularity — empirically
        # verified on this corpus: eom produced one 87-session cluster vs. 6 real
        # clusters; leaf produces 11 clusters with a max size of 16.
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            metric="euclidean",
            cluster_selection_method="leaf",
        )
        labels = clusterer.fit_predict(embeddings)

        counts: dict[str, int] = {}
        for session_id, label in zip(session_ids, labels):
            tag = "noise" if label == -1 else f"cluster_{label}"
            self.store.set_session_type(session_id, tag)
            counts[tag] = counts.get(tag, 0) + 1

        logger.info(f"Session type tagging: {len(session_ids)} sessions → {counts}")
        return counts

    def label_session_types(self, provider=None, model: str = "gemma3:12b", max_tokens: int = 512) -> int:
        """Generate a short human-readable label + description for each session_type cluster.

        tag_session_types() only ever wrote opaque HDBSCAN labels like "cluster_8" —
        no semantic meaning, nothing to show in the UI beyond a raw id. This samples
        each cluster's member sessions and asks an LLM to characterize the shared
        INTERACTION SHAPE (turn rhythm, tool density, session length) — explicitly
        not topic, since structural embeddings are built from raw head+tail text and
        routinely group sessions on completely different subjects that just "feel"
        similar structurally (verified: sampled two sessions in the same cluster, one
        about a Kaggle benchmark pivot, one about ingestion pipeline debugging — same
        cluster, unrelated topics).

        Run after tag_session_types(). Skips "noise" (not a real cluster, no shared
        pattern to describe — labeled with a fixed generic string instead of an LLM call).

        Returns:
            Number of clusters labeled (including the fixed "noise" label).
        """
        from agent_trace_signals.models import _now

        rows = self.store.conn.execute(
            "SELECT session_type, id, session_summary, chunk_count FROM sessions "
            "WHERE session_type IS NOT NULL"
        ).fetchall()
        by_type: dict[str, list[tuple]] = {}
        for stype, sid, summary, chunks in rows:
            by_type.setdefault(stype, []).append((sid, summary, chunks))

        now = _now()
        labeled = 0
        for stype, members in by_type.items():
            n = len(members)
            if stype == "noise":
                self.store.conn.execute(
                    "INSERT OR REPLACE INTO session_type_labels "
                    "(session_type, label, description, n_sessions, created_at) VALUES (?,?,?,?,?)",
                    (stype, "No clear pattern", "Sessions that didn't fit any dense structural cluster.", n, now),
                )
                labeled += 1
                continue

            avg_chunks = sum(m[2] for m in members) / n
            samples = [m[1] for m in members if m[1]][:4]
            if not samples:
                # No session_summary on any member — typically an all-trivial-session
                # cluster (1-chunk sessions don't get a summary generated). No text
                # to characterize, so use a stats-only label instead of skipping
                # the cluster entirely.
                self.store.conn.execute(
                    "INSERT OR REPLACE INTO session_type_labels "
                    "(session_type, label, description, n_sessions, created_at) VALUES (?,?,?,?,?)",
                    (stype, "Minimal / trivial sessions",
                     f"No session summary available (avg {avg_chunks:.1f} chunks) — likely short, "
                     f"low-content sessions.", n, now),
                )
                labeled += 1
                continue
            samples_text = "\n\n".join(f"Session {i+1}: {s[:600]}" for i, s in enumerate(samples))
            prompt = (
                f"These {n} agent coding sessions were grouped together because they share a similar "
                f"INTERACTION SHAPE (tool-call rhythm, turn pacing, session length — avg {avg_chunks:.1f} "
                f"content chunks) — NOT because they're about the same topic. The summaries below are "
                f"probably about different subjects; ignore the subject matter and describe the shared "
                f"working STYLE instead (e.g. 'long iterative multi-topic engineering sessions', "
                f"'short single-question exchanges', 'heavy tool-use debugging sessions').\n\n"
                f"{samples_text}\n\n"
                f'Return JSON: {{"label": "2-5 word phrase", "description": "one sentence describing the interaction style"}}'
            )
            schema = {
                "type": "object",
                "properties": {"label": {"type": "string"}, "description": {"type": "string"}},
                "required": ["label", "description"],
            }
            try:
                result = provider.complete_json(prompt, schema=schema, model=model, max_tokens=max_tokens)
                label = result.get("label", "").strip()
                description = result.get("description", "").strip()
                if not label:
                    continue
            except Exception as e:
                logger.warning(f"Session-type labeling failed for {stype}: {e}")
                continue

            self.store.conn.execute(
                "INSERT OR REPLACE INTO session_type_labels "
                "(session_type, label, description, n_sessions, created_at) VALUES (?,?,?,?,?)",
                (stype, label, description, n, now),
            )
            self.store.conn.commit()
            labeled += 1
            logger.info(f"Labeled {stype} ({n} sessions): {label} — {description}")

        return labeled

    def consolidate_within_session(
        self,
        min_cluster_size: int = 2,
        provider=None,
        model: str = "gemma4:e4b",
        max_tokens: int = 2048,
    ) -> int:
        """Level 1: consolidate recurring memories within each session independently.

        Unlike consolidate_memories() (the cross-session pass), this never claims
        cross-session corroboration — distinct_sessions is trivially 1 by
        construction here, so the LLM framing says "repeated N times within this
        single agent session," not "extracted from N different sessions." This is
        the fix for the gap identified in design_decisions.md 2026-07-21/22: a
        claim repeated 3x within one long-running session (some ATS sessions span
        21+ days) could satisfy consolidate_memories()'s old min_recurrence=1 gate
        while still being told to the LLM as "different sessions" framing,
        overstating corroboration that never happened.

        Runs one HDBSCAN pass per session rather than one global pass — a
        session's own memory count is usually small, so min_cluster_size defaults
        lower (2) than the cross-session pass's default (3).

        Args:
            min_cluster_size: HDBSCAN density param, scoped per-session.
            provider: ModelProvider instance for LLM consolidation calls.
            model: Model for the consolidation call.
            max_tokens: num_predict budget for the consolidation call.

        Returns:
            Total number of consolidated memories minted across all sessions.
        """
        session_ids = [
            r[0] for r in self.store.conn.execute(
                """SELECT DISTINCT ms.session_id
                   FROM memories m JOIN memory_sources ms ON ms.memory_id = m.id
                   WHERE m.extraction_method NOT IN ('cluster', 'cluster_within_session')
                   AND EXISTS (SELECT 1 FROM memory_embeddings me WHERE me.memory_id = m.id)"""
            ).fetchall()
        ]

        total = 0
        for session_id in session_ids:
            rows = self.store.conn.execute(
                """SELECT m.id, m.memory_type, m.content, ms.session_id
                   FROM memories m JOIN memory_sources ms ON ms.memory_id = m.id
                   WHERE ms.session_id = ?
                   AND m.extraction_method NOT IN ('cluster', 'cluster_within_session')
                   AND EXISTS (SELECT 1 FROM memory_embeddings me WHERE me.memory_id = m.id)""",
                (session_id,),
            ).fetchall()
            if len(rows) < min_cluster_size:
                continue
            total += self._cluster_and_mint(
                rows, min_cluster_size=min_cluster_size, min_recurrence=1,
                provider=provider, model=model, max_tokens=max_tokens,
                extraction_method="cluster_within_session", framing="within_session",
                write_graph_edges=False,
            )
        logger.info(f"Within-session consolidation: {total} consolidated memories minted "
                    f"across {len(session_ids)} sessions.")
        return total

    def consolidate_memories(
        self,
        min_cluster_size: int = 3,
        min_recurrence: int = 2,
        provider=None,
        model: str = "gemma4:31b",
        max_tokens: int = 2048,
    ) -> int:
        """Level 2: cluster memory+pattern rows ACROSS sessions; consolidate recurring clusters.

        Args:
            min_cluster_size: HDBSCAN's density parameter — minimum number of
                               memories (not sessions) to form a cluster at all.
            min_recurrence: Floor on DISTINCT SESSIONS before minting. Defaults to
                            2 — this pass exists specifically to claim cross-session
                            corroboration ("extracted from N different agent
                            sessions"), so a cluster that never left one session
                            doesn't belong here; see consolidate_within_session()
                            for that case instead. Raise higher for a stricter
                            "confirmed in the wild" filter.
            provider: ModelProvider instance for LLM consolidation calls.
            model: Model name to use for consolidation LLM call. Default gemma4:31b —
                   a bigger/more capable local model than the gemma3:12b used elsewhere
                   in ingestion, since consolidation quality directly determines what
                   shows up as the corpus's "recurring insight" summaries.
            max_tokens: num_predict budget for the consolidation call. gemma4:31b is a
                       thinking model (like gemma4:e4b) — reasoning tokens count against
                       this budget before any output text, so it needs much more headroom
                       than the 256 that was fine for non-thinking gemma3:12b.

        Returns:
            Number of consolidated memories minted.
        """
        # Fetch all memory rows with session sources
        rows = self.store.conn.execute(
            """SELECT m.id, m.memory_type, m.content, ms.session_id
               FROM memories m
               JOIN memory_sources ms ON ms.memory_id = m.id
               WHERE m.extraction_method NOT IN ('cluster', 'cluster_within_session')
               AND EXISTS (
                   SELECT 1 FROM memory_embeddings me WHERE me.memory_id = m.id
               )"""
        ).fetchall()

        if len(rows) < min_cluster_size:
            logger.info(f"Only {len(rows)} embedded memories — skipping consolidation.")
            return 0

        return self._cluster_and_mint(
            rows, min_cluster_size=min_cluster_size, min_recurrence=min_recurrence,
            provider=provider, model=model, max_tokens=max_tokens,
            extraction_method="cluster", framing="cross_session", write_graph_edges=True,
        )

    def mark_superseded_clusters(self, new_member_ids: set[str], new_mem_id: str, now: str) -> None:
        """Supersede prior cluster memories a newly-minted one has outgrown.

        `_cluster_and_mint`'s resume-safety check only skips a cluster whose
        members are >=80% already covered by an existing cluster memory — below
        that, a brand new cluster memory is minted from scratch even though most
        of its members already had an older cluster memory. Without this, that
        older, less-evidenced row lingers forever and surfaces in retrieval as if
        it were a separate, distinct insight. A prior cluster counts as
        superseded when a majority of ITS OWN members now belong to the new
        cluster — i.e. the new one is a genuine evolution of it, not just a
        loosely related neighbour.
        """
        if not new_member_ids:
            return
        placeholders = ",".join("?" * len(new_member_ids))
        prior_cluster_ids = {
            r[0] for r in self.store.conn.execute(
                f"SELECT DISTINCT cluster_memory_id FROM memory_cluster_members "
                f"WHERE member_memory_id IN ({placeholders}) AND cluster_memory_id != ?",
                list(new_member_ids) + [new_mem_id],
            ).fetchall()
        }
        for old_id in prior_cluster_ids:
            old_members = {
                r[0] for r in self.store.conn.execute(
                    "SELECT member_memory_id FROM memory_cluster_members WHERE cluster_memory_id = ?",
                    (old_id,),
                ).fetchall()
            }
            if not old_members:
                continue
            old_overlap = len(old_members & new_member_ids) / len(old_members)
            if old_overlap >= _SUPERSEDE_OVERLAP_THRESHOLD:
                self.store.conn.execute(
                    "UPDATE memories SET status='superseded', metadata=?, updated_at=? "
                    "WHERE id=? AND (status IS NULL OR status != 'superseded')",
                    (json.dumps({"superseded_by": new_mem_id}), now, old_id),
                )

    def _cluster_and_mint(
        self,
        rows: list[tuple],
        min_cluster_size: int,
        min_recurrence: int,
        provider,
        model: str,
        max_tokens: int,
        extraction_method: str,
        framing: str,
        write_graph_edges: bool,
    ) -> int:
        """Shared HDBSCAN-cluster + LLM-consolidate + DB-write core for both consolidation levels.

        rows: (memory_id, memory_type, content, session_id) — one row per
              (memory, session) pair; a memory linked to multiple sessions
              appears multiple times and gets deduped below.
        framing: "cross_session" (the multi-session pass, claims corroboration)
                 or "within_session" (single-session pass, no such claim).
        """
        import hdbscan
        from collections import defaultdict, Counter
        from agent_trace_signals.models import _uuid, _now

        # A memory can have multiple memory_sources rows (linked to >1 session);
        # dedupe to one entry per memory_id before clustering, tracking all its
        # session ids so distinct_sessions is counted correctly.
        by_id: dict[str, tuple[str, str, list[str]]] = {}
        for mid, mtype, content, sid in rows:
            if mid not in by_id:
                by_id[mid] = (mtype, content, [sid])
            else:
                by_id[mid][2].append(sid)

        # Fetch embeddings
        memory_ids = list(by_id.keys())
        placeholders = ",".join("?" * len(memory_ids))
        emb_rows = self.store.conn.execute(
            f"SELECT memory_id, embedding FROM memory_embeddings WHERE memory_id IN ({placeholders})",
            memory_ids,
        ).fetchall()
        emb_map = {r[0]: np.frombuffer(r[1], dtype=np.float32) for r in emb_rows}

        # Filter to memories that have embeddings
        valid = [
            (mid, mtype, content, sids)
            for mid, (mtype, content, sids) in by_id.items()
            if mid in emb_map
        ]
        if len(valid) < min_cluster_size:
            return 0

        embeddings = np.array([emb_map[v[0]] for v in valid])
        # cluster_selection_method="leaf" (not "eom"): same fix already proven in
        # tag_session_types() above for the identical failure mode, applied here too.
        # Verified on the real corpus: eom collapsed 2926 of 2986 embedded memories
        # (98%) into one incoherent 75-session "cluster" (sampled content spanned
        # biomedical research setup, an unrelated game-state bug, WANDS re-ranking,
        # and Ollama CLI usage — no shared theme) plus one 3-member cluster; leaf
        # produced 134 clusters, max 20 members / 7 sessions, and sampled clusters
        # were thematically coherent (e.g. one cluster was entirely SLR-agent HITL
        # checkpointing, another entirely hybrid-retrieval/RRF fusion). This also
        # shrinks shared_memory session-graph edges at the source, since edge count
        # per cluster is O(n^2) in its distinct-session count — see the shared_memory
        # weight comment below and design/design_decisions.md's 2026-07-30 entry.
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            metric="euclidean",
            cluster_selection_method="leaf",
        )
        labels = clusterer.fit_predict(embeddings)

        # Group by cluster label
        clusters: dict[int, list[tuple]] = defaultdict(list)
        for item, label in zip(valid, labels):
            if label >= 0:
                clusters[label].append(item)

        # Resume support: if this DB already has cluster memberships from a prior
        # (possibly killed/interrupted) run, skip any cluster whose members are
        # already covered rather than re-minting a near-duplicate. HDBSCAN is
        # deterministic for fixed input+params, so a re-run over unchanged data
        # reproduces the same clusters — this only matters when resuming after
        # a kill, not on a normal fresh run (memory_cluster_members is empty then).
        already_covered_ids = {
            r[0] for r in self.store.conn.execute(
                "SELECT DISTINCT member_memory_id FROM memory_cluster_members"
            ).fetchall()
        }

        def _prepare(label: int, members: list[tuple]) -> dict | None:
            """Build the consolidation prompt for one cluster. Pure — no DB I/O."""
            distinct_sessions = len({sid for m in members for sid in m[3]})
            if distinct_sessions < min_recurrence:
                return None

            member_ids = {m[0] for m in members}
            overlap = len(member_ids & already_covered_ids) / len(member_ids)
            if overlap >= 0.8:
                logger.debug(f"Cluster {label} already covered by a prior run ({overlap:.0%}) — skipping.")
                return None

            # Used only to bias the LLM's phrasing register (e.g. "recovery" vs
            # "strategy" tone) — NOT stored as the memory's type. All consolidated
            # memories are stored as memory_type='cluster'; the real per-member
            # type breakdown is preserved in memory_cluster_members below, since
            # clusters routinely mix types describing the same underlying insight
            # (verified empirically — content stays coherent even when type
            # composition is close to a 50/50 split).
            dominant_type = Counter(m[1] for m in members).most_common(1)[0][0]

            # Order members closest-to-centroid first (not arbitrary DB-fetch order)
            # so the most representative entries lead the prompt — but include ALL
            # members, not a truncated sample, so the consolidation reflects the
            # full cluster (largest cluster on this corpus is 32 members, ~170
            # chars avg content — comfortably within ollama_num_ctx=8192).
            member_embs = [emb_map[m[0]] for m in members]
            centroid = np.mean(member_embs, axis=0)
            ranked = sorted(
                zip(members, member_embs),
                key=lambda pair: np.linalg.norm(pair[1] - centroid),
            )
            member_contents = [m[2] for m, _ in ranked]
            ranked_member_ids = [m[0] for m, _ in ranked]
            contents_text = "\n".join(f"[{idx}] {c}" for idx, c in enumerate(member_contents))
            if framing == "within_session":
                source_desc = f"repeated {len(member_contents)} times within this single agent session"
            else:
                source_desc = (
                    f"extracted from {distinct_sessions} different agent sessions "
                    f"({len(member_contents)} entries total)"
                )
            consolidation_prompt = (
                f"The following memory entries were {source_desc} and are semantically similar "
                f"(embedding-clustered), meaning they're about the same underlying topic — but being "
                f"about the same topic does not mean they agree.\n\n"
                f"Entries:\n{contents_text}\n\n"
                f"Write a single consolidated memory that captures the essential, reusable insight "
                f"from all of these entries. Be concise (1-2 sentences). Abstract away "
                f"session-specific details. The dominant framing among these entries is: {dominant_type}.\n\n"
                f"Separately, check whether any two entries actually CONTRADICT each other — not just "
                f"phrase the same fact differently, but assert incompatible things (e.g. a config value "
                f"changed between two claims, a decision was made one way then reported the opposite way, "
                f"a since-reverted approach still being cited as current). If so, still write the best "
                f"single consolidated memory you can (favor the more recent or more specific claim), but "
                f"flag it and name the two conflicting entries by their [index].\n\n"
                f'Return JSON: {{"consolidated": "...", "conflict": true|false, '
                f'"conflict_type": "short label or empty string", "conflict_indices": [i, j] or []}}'
            )
            return {
                "label": label, "members": members,
                "distinct_sessions": distinct_sessions, "prompt": consolidation_prompt,
                "ranked_member_ids": ranked_member_ids,
            }

        jobs = [j for j in (_prepare(label, members) for label, members in clusters.items()) if j is not None]
        logger.info(f"Consolidating {len(jobs)} eligible clusters ({len(clusters)} found, "
                    f"{len(clusters) - len(jobs)} skipped: below min_recurrence or already minted)")

        schema = {
            "type": "object",
            "properties": {
                "consolidated": {"type": "string"},
                "conflict": {"type": "boolean"},
                "conflict_type": {"type": "string"},
                "conflict_indices": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["consolidated"],
        }

        def _consolidate_one(job: dict) -> dict:
            """LLM call only — no DB I/O — so this is safe to run concurrently."""
            try:
                result = provider.complete_json(
                    job["prompt"], schema=schema, model=model, max_tokens=max_tokens,
                )
                job["consolidated_content"] = (result.get("consolidated") or "").strip()
                job["conflict"] = bool(result.get("conflict"))
                job["conflict_type"] = (result.get("conflict_type") or "").strip()
                job["conflict_indices"] = result.get("conflict_indices") or []
            except Exception as e:
                logger.warning(f"Consolidation LLM call failed for cluster {job['label']}: {e}")
                job["consolidated_content"] = None
            return job

        from concurrent.futures import ThreadPoolExecutor, as_completed
        minted = 0
        conflicts_found = 0
        run_id = _uuid()  # groups every conflict row written by this _cluster_and_mint() call
        workers = min(4, len(jobs)) or 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_consolidate_one, job) for job in jobs]
            for i, fut in enumerate(as_completed(futures), start=1):
                job = fut.result()
                consolidated_content = job.get("consolidated_content")
                if not consolidated_content:
                    continue
                label, members, distinct_sessions = job["label"], job["members"], job["distinct_sessions"]

                # Mint consolidated memory (DB writes stay single-threaded/sequential)
                now = _now()
                mem_id = _uuid()
                self.store.conn.execute(
                    "INSERT INTO memories (id,org_id,workspace_id,app_id,memory_type,"
                    "extraction_method,content,evidence_count,first_observed,last_observed,"
                    "created_by_pipeline,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mem_id, "default", "default", "default", "cluster", extraction_method,
                     consolidated_content, distinct_sessions, now, now, "clustering_analytics", now, now),
                )
                self.store.conn.execute(
                    "INSERT OR REPLACE INTO memories_fts(memory_id, content) VALUES (?,?)",
                    (mem_id, consolidated_content),
                )
                # Link source sessions via memory_sources
                seen_sessions = set()
                for _, _, _, sids in members:
                    for sid in sids:
                        if sid not in seen_sessions:
                            seen_sessions.add(sid)
                            self.store.conn.execute(
                                "INSERT OR IGNORE INTO memory_sources (memory_id,session_id,relevance_score) VALUES (?,?,?)",
                                (mem_id, sid, 1.0),
                            )
                # Persist which original memories fed this cluster, and their
                # original types — otherwise this membership is unrecoverable
                # after this run (HDBSCAN's label is never stored anywhere else).
                for member_id, member_type, _, _ in members:
                    self.store.conn.execute(
                        "INSERT OR IGNORE INTO memory_cluster_members "
                        "(cluster_memory_id, member_memory_id, member_memory_type) VALUES (?,?,?)",
                        (mem_id, member_id, member_type),
                    )
                new_member_ids = {member_id for member_id, _, _, _ in members}
                self.mark_superseded_clusters(new_member_ids, mem_id, now)
                # Session graph edges: every pair of sessions that both contributed
                # to this cluster gets a "shared_memory" edge, via_entity_id=mem_id
                # (so multiple shared clusters between the same pair create distinct
                # rows, same pattern as multiple shared-file structural edges).
                # Inserted both directions — see ingestion.py for why (edges are
                # conceptually undirected but only source_session_id is queried).
                # Skipped for the within-session pass — a single-session cluster has
                # no session pair to link.
                #
                # Weight is 1/distinct_sessions, not a flat 1.0: a cluster is a
                # weaker relevance signal the more sessions it spans (a 2-session
                # cluster means "these two sessions share a specific recurring
                # insight"; a 50-session cluster of a broad recurring theme means
                # much less about any one pair). This deliberately lines up with
                # graph_walk()'s existing `weight > 0.2` BFS threshold: a 2-session
                # cluster (0.5) or 3-session cluster (0.33) clears it, a 5+-session
                # cluster (<=0.2) doesn't — no separate magic number needed. Ties
                # into the leaf-vs-eom clustering fix above (see its comment): with
                # eom, distinct_sessions could reach ~75, making this weighting the
                # only thing standing between one bad cluster and a near-corpus-wide
                # BFS; with leaf it's now a defense-in-depth layer, not the sole guard.
                if write_graph_edges:
                    shared_memory_weight = 1.0 / distinct_sessions
                    for sid_a, sid_b in itertools.combinations(sorted(seen_sessions), 2):
                        for src, tgt in ((sid_a, sid_b), (sid_b, sid_a)):
                            self.store.conn.execute(
                                "INSERT OR REPLACE INTO session_graph_edges "
                                "(source_session_id,target_session_id,edge_type,via_entity_id,"
                                "weight,direction,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
                                (src, tgt, "shared_memory", mem_id, shared_memory_weight, "undirected",
                                 "clustering_analytics", now),
                            )
                # Conflict detection: the consolidation call already reads every member's
                # content to write the merged summary, so asking it to also flag a real
                # contradiction (not just "these are about the same topic") is free —
                # no extra LLM call. conflict_indices map into ranked_member_ids, the same
                # ordering the prompt's [index] entries used.
                ranked_ids = job.get("ranked_member_ids", [])
                indices = job.get("conflict_indices") or []
                if job.get("conflict") and len(indices) == 2:
                    i_a, i_b = indices
                    if 0 <= i_a < len(ranked_ids) and 0 <= i_b < len(ranked_ids) and i_a != i_b:
                        self.store.conn.execute(
                            "INSERT INTO conflicts (id,memory_id_a,memory_id_b,conflict_type,"
                            "detected_at,analytics_run_id) VALUES (?,?,?,?,?,?)",
                            (_uuid(), ranked_ids[i_a], ranked_ids[i_b],
                             job.get("conflict_type") or "unspecified", now, run_id),
                        )
                        conflicts_found += 1

                self.store.conn.commit()
                minted += 1
                logger.info(
                    f"[{i}/{len(jobs)}] Minted consolidated memory from cluster {label} "
                    f"({distinct_sessions} sessions, {len(members)} memories): {consolidated_content[:80]}"
                )

        if conflicts_found:
            logger.info(f"Flagged {conflicts_found} conflicting memory pair(s) during this pass.")
        return minted
