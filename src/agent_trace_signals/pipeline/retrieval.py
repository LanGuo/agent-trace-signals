"""Hybrid retrieval engine: BM25 + semantic + RRF fusion."""

from __future__ import annotations
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_trace_signals.models import MemoryRetrieval

if TYPE_CHECKING:
    from agent_trace_signals.db.store import SQLiteStore
    from agent_trace_signals.embedder import Embedder

_RRF_K = 60


def _log_memory_retrievals(
    store: "SQLiteStore",
    results: list[dict],
    query: str,
    session_id: str | None,
    exchange_idx: int | None,
) -> None:
    """Persist a MemoryRetrieval row for each surfaced memory. Side-effect only."""
    if not results:
        return
    retrieved_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for rank, mem in enumerate(results, start=1):
        mid = mem.get("id")
        if not mid:
            continue
        rows.append(MemoryRetrieval(
            id=MemoryRetrieval.make_id(mid, retrieved_at, session_id),
            session_id=session_id,
            exchange_idx=exchange_idx,
            memory_id=mid,
            query_text=query,
            relevance_rank=rank,
            relevance_score=mem.get("rrf_score"),
            surfaced=True,
            retrieved_at=retrieved_at,
        ))
    try:
        store.insert_memory_retrievals_batch(rows)
    except Exception:
        # Logging must never break retrieval
        pass


def _rrf(
    bm25_results: list[tuple[str, float]],
    sem_results: list[tuple[str, float]],
    k: int = _RRF_K,
    top_k: int | None = None,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion of two ranked lists. Returns [(id, rrf_score)] desc.

    A candidate that is each leg's #1 result is guaranteed a slot in the
    truncated top_k, even if additive RRF scoring would otherwise rank it
    below several candidates that are only mediocre on *both* legs (e.g. two
    rank-~5-8 placements summing to more than one rank-1 + one absence). This
    is scoped to rank-1 only, the one signal unambiguous enough to override
    fusion — see design/design_decisions.md 2026-07-28 entry for the
    reproduction case and why weaker single-leg placements (e.g. rank 3-4)
    are left to compete normally.
    """
    scores: dict[str, float] = {}
    for rank, (id_, _) in enumerate(bm25_results, 1):
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank)
    for rank, (id_, _) in enumerate(sem_results, 1):
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    if not top_k:
        return ranked

    selected = ranked[:top_k]
    selected_ids = {id_ for id_, _ in selected}
    guaranteed = [r[0][0] for r in (bm25_results, sem_results) if r]

    for gid in guaranteed:
        if gid in selected_ids:
            continue
        for i in range(len(selected) - 1, -1, -1):
            if selected[i][0] not in guaranteed:
                selected.pop(i)
                break
        else:
            continue  # no evictable (non-guaranteed) slot left to make room
        selected.append((gid, scores[gid]))
        selected_ids.add(gid)

    selected.sort(key=lambda x: x[1], reverse=True)
    return selected


class RetrievalEngine:
    """Wraps SQLiteStore + Embedder for hybrid memory/chunk retrieval."""

    # Shared decay applied to every graph-expanded result (memory/chunk/session)
    # relative to the seed that found it — keeps expansions from ever outranking
    # their seed while still letting them compete honestly against each other,
    # instead of the old flat rrf_score=0.0 that made expansion invisible to ranking.
    _GRAPH_EXPANSION_DECAY = 0.5

    # graph_walk()'s per-hop and per-call ceilings. These are a structural
    # backstop, not the primary fix — the primary fix is the shared_memory edge
    # weighting and the eom->leaf clustering change (clustering_analytics.py) that
    # keep the BFS from ever seeing a near-corpus-wide clique in the first place.
    # Kept anyway because `structural`/`workspace` edges are also uncapped cliques
    # (e.g. `workspace` connects every session in a shared workspace) and could
    # still grow large as the corpus grows, independent of clustering quality.
    # See design/design_decisions.md's 2026-07-30 entry for the measured before/after.
    _MAX_GRAPH_NEIGHBOURS_PER_HOP = 10
    _MAX_GRAPH_EXPANSION_MEMORIES = 30

    # `_chunk_graph_expand()`'s per-entity and per-call ceilings, mirroring the
    # graph_walk pattern above but for the entity-pivot hop, not the
    # session_graph_edges one. Measured on the real corpus: entity mention
    # counts follow a smooth long tail (no single eom-style blob), topped by
    # the project's own name/aliases, the LLM models used for extraction, and
    # infrastructure files (README.md, traces.db) mentioned in 15-25% of all
    # chunks — near-universal in this corpus without being any one pathological
    # entity. Inverse-specificity weighting (below) handles that gradient
    # naturally; these caps are the backstop for corpus growth, same role
    # _MAX_GRAPH_NEIGHBOURS_PER_HOP/_MAX_GRAPH_EXPANSION_MEMORIES play for
    # graph_walk. See design/design_decisions.md's dated entry for numbers.
    _MAX_CHUNK_NEIGHBOURS_PER_ENTITY = 10
    _MAX_CHUNK_EXPANSION_RECORDS = 30

    def __init__(self, store: "SQLiteStore", embedder: "Embedder") -> None:
        self.store = store
        self.embedder = embedder

    def recall(
        self,
        query: str,
        memory_type: str | None = None,
        top_k: int = 10,
        include_graph: bool = False,
        method: str = "hybrid",
        session_id: str | None = None,
        exchange_idx: int | None = None,
        workspace_id: str | None = None,
    ) -> list[dict]:
        """BM25, semantic, or hybrid (RRF) retrieval over memories.

        method: 'hybrid' (default) | 'semantic' | 'lexical'
        """
        query_emb = self.embedder.embed(query) if method != "lexical" else None
        bm25 = self.store.search_memories_bm25(query, memory_type=memory_type, top_k=top_k * 5, workspace_id=workspace_id) if method != "semantic" else []
        sem = self.store.search_memories_semantic(query_emb, top_k=top_k * 5, memory_type=memory_type, workspace_id=workspace_id) if query_emb is not None else []
        fused = _rrf(bm25, sem, top_k=top_k)

        if not fused:
            return []

        memory_ids = [mid for mid, _ in fused]
        scores = {mid: score for mid, score in fused}

        placeholders = ",".join("?" * len(memory_ids))
        type_filter = " AND memory_type = ?" if memory_type else ""
        params: list = list(memory_ids) + ([memory_type] if memory_type else [])
        rows = self.store.conn.execute(
            f"SELECT id, content, memory_type, extraction_method, evidence_count, "
            f"entity_id, action_orientation, access_count, status "
            f"FROM memories WHERE id IN ({placeholders}){type_filter} "
            f"AND (status IS NULL OR status != 'superseded')",
            params,
        ).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            d["rrf_score"] = scores.get(d["id"], 0.0)
            results.append(d)

        results.sort(key=lambda x: x["rrf_score"], reverse=True)

        if include_graph:
            seen = {m["id"] for m in results}
            extra: list[dict] = []
            for mem in results[:3]:
                seed_score = mem["rrf_score"]
                for exp in self.graph_walk(mem["id"], depth=1):
                    exp["rrf_score"] = seed_score * self._GRAPH_EXPANSION_DECAY
                    extra.append(exp)
            for mem in extra:
                if mem["id"] not in seen:
                    results.append(mem)
                    seen.add(mem["id"])
            results.sort(key=lambda x: x["rrf_score"], reverse=True)

        for mem in results:
            self.store.increment_access_count(mem["id"])

        _log_memory_retrievals(self.store, results, query, session_id, exchange_idx)

        return results

    def chunk_search(
        self,
        query: str,
        session_id: str | None = None,
        top_k: int = 20,
        method: str = "hybrid",
        include_graph: bool = False,
        workspace_id: str | None = None,
    ) -> list[dict]:
        """BM25, semantic, or hybrid (RRF) retrieval over records (chunks).

        method: 'hybrid' (default) | 'semantic' | 'lexical'
        include_graph: expand the top hits with chunks that mention the same
            entity (via `occurrences`), across sessions regardless of the
            `session_id` filter — this is the cross-session hop the plain
            search can't do on its own.
        """
        query_emb = self.embedder.embed(query) if method != "lexical" else None
        bm25 = self.store.search_records_bm25(query, session_id=session_id, top_k=top_k * 5, workspace_id=workspace_id) if method != "semantic" else []
        sem = self.store.search_records_semantic(query_emb, top_k=top_k * 5, session_id=session_id, workspace_id=workspace_id) if query_emb is not None else []
        fused = _rrf(bm25, sem, top_k=top_k)

        if not fused:
            return []

        record_ids = [rid for rid, _ in fused]
        scores = {rid: score for rid, score in fused}

        placeholders = ",".join("?" * len(record_ids))
        sess_filter = " AND session_id = ?" if session_id else ""
        params2: list = list(record_ids) + ([session_id] if session_id else [])
        rows = self.store.conn.execute(
            f"SELECT id, session_id, chunk_index, chunk_text, chunk_summary "
            f"FROM records WHERE id IN ({placeholders}){sess_filter}",
            params2,
        ).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            d["rrf_score"] = scores.get(d["id"], 0.0)
            results.append(d)
        results.sort(key=lambda x: x["rrf_score"], reverse=True)

        if include_graph:
            seen = {r["id"] for r in results}
            extra: list[dict] = []
            for rec in results[:3]:
                extra.extend(self._chunk_graph_expand(rec["id"], rec["rrf_score"]))
            for rec in extra:
                if rec["id"] not in seen:
                    results.append(rec)
                    seen.add(rec["id"])
            results.sort(key=lambda x: x["rrf_score"], reverse=True)

        return results

    def _chunk_graph_expand(self, record_id: str, seed_score: float) -> list[dict]:
        """One hop from a chunk to other chunks mentioning the same entity.

        Uses `occurrences(record_id, entity_id)` directly — no separate
        chunk-chunk edge table needed, since occurrences already carries the
        chunk-granularity entity link. Score decays from the seed chunk's own
        score so an expanded chunk can never outrank the hit that found it,
        but still competes honestly against other expanded chunks.

        Each matched entity is weighted by inverse specificity
        (`1 / chunks_mentioning_entity`) — the same idea used for
        `workspace`/`structural` session-graph edges — so a near-universal
        entity (the project's own name, an LLM model name used almost
        everywhere, README.md) contributes chunks at a much lower score than
        a genuinely specific one, instead of flooding the pool undifferentiated.
        Per-entity and total-expansion caps are a backstop on top of that, not
        the primary fix — see `_MAX_CHUNK_NEIGHBOURS_PER_ENTITY`/
        `_MAX_CHUNK_EXPANSION_RECORDS` above.
        """
        entity_ids = [
            r[0] for r in self.store.conn.execute(
                "SELECT DISTINCT entity_id FROM occurrences WHERE record_id = ?",
                (record_id,),
            ).fetchall()
        ]
        if not entity_ids:
            return []

        placeholders = ",".join("?" * len(entity_ids))
        entity_counts = dict(self.store.conn.execute(
            f"SELECT entity_id, COUNT(DISTINCT record_id) FROM occurrences "
            f"WHERE entity_id IN ({placeholders}) GROUP BY entity_id",
            entity_ids,
        ).fetchall())

        candidate_weight: dict[str, float] = {}
        for entity_id, n_chunks in entity_counts.items():
            weight = 1.0 / n_chunks
            rows = self.store.conn.execute(
                "SELECT DISTINCT record_id FROM occurrences "
                "WHERE entity_id = ? AND record_id != ? "
                "ORDER BY record_id LIMIT ?",
                (entity_id, record_id, self._MAX_CHUNK_NEIGHBOURS_PER_ENTITY),
            ).fetchall()
            for (rid,) in rows:
                candidate_weight[rid] = max(weight, candidate_weight.get(rid, 0.0))

        if not candidate_weight:
            return []

        ranked = sorted(candidate_weight.items(), key=lambda x: x[1], reverse=True)
        ranked = ranked[: self._MAX_CHUNK_EXPANSION_RECORDS]
        weight_map = dict(ranked)
        neighbour_ids = list(weight_map.keys())

        placeholders2 = ",".join("?" * len(neighbour_ids))
        rows = self.store.conn.execute(
            f"SELECT id, session_id, chunk_index, chunk_text, chunk_summary "
            f"FROM records WHERE id IN ({placeholders2})",
            neighbour_ids,
        ).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            d["rrf_score"] = seed_score * self._GRAPH_EXPANSION_DECAY * weight_map.get(d["id"], 1.0)
            results.append(d)
        return results

    def session_search(
        self,
        query: str,
        top_k: int = 5,
        method: str = "hybrid",
        include_graph: bool = False,
        workspace_id: str | None = None,
    ) -> list[dict]:
        """Rank sessions by relevance to a query using session-level or avg-chunk embeddings.

        method: 'hybrid' (default) | 'semantic' | 'lexical'
        Semantic: cosine similarity of query embedding vs avg of chunk embeddings per session.
        Lexical: BM25 over session_summary text.
        Hybrid: RRF fusion of both.
        include_graph: expand top hits with their session_graph_edges neighbours
            (workspace/structural/shared_memory/structural_similarity, whichever
            exist) — sessions are the graph's own nodes here, so no pivot table
            is needed, unlike recall()'s memory->session->memory hop.
        """
        import struct
        import math

        def _decode(blob: bytes) -> list[float]:
            n = len(blob) // 4
            return list(struct.unpack(f"{n}f", blob))

        def _cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            return dot / (na * nb) if na and nb else 0.0

        sem_ranked: list[tuple[str, float]] = []
        if method != "lexical":
            query_emb = self.embedder.embed(query)
            # Gather avg-chunk embedding per session (fallback when session_embeddings missing)
            if workspace_id:
                rows = self.store.conn.execute(
                    "SELECT r.session_id, re.embedding FROM record_embeddings re "
                    "JOIN records r ON r.id = re.record_id "
                    "JOIN sessions s ON s.id = r.session_id "
                    "WHERE re.embedding IS NOT NULL AND s.workspace_id LIKE ?",
                    (f"%{workspace_id}%",),
                ).fetchall()
            else:
                rows = self.store.conn.execute(
                    "SELECT r.session_id, re.embedding FROM record_embeddings re "
                    "JOIN records r ON r.id = re.record_id WHERE re.embedding IS NOT NULL"
                ).fetchall()
            from collections import defaultdict
            sess_vecs: dict[str, list[list[float]]] = defaultdict(list)
            for sid, blob in rows:
                if blob:
                    sess_vecs[sid].append(_decode(blob))
            scores = {}
            for sid, vecs in sess_vecs.items():
                avg = [sum(v[i] for v in vecs) / len(vecs) for i in range(len(vecs[0]))]
                scores[sid] = _cosine(query_emb, avg)
            sem_ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k * 3]

        bm25_ranked: list[tuple[str, float]] = []
        if method != "semantic":
            bm25_ranked = self.store.search_sessions_bm25(query, top_k=top_k * 3, workspace_id=workspace_id)

        fused = _rrf(bm25_ranked, sem_ranked, top_k=top_k)

        fused_ids = [sid for sid, _ in fused]
        score_map = {sid: score for sid, score in fused}
        if not fused_ids:
            return []

        placeholders = ",".join("?" * len(fused_ids))
        sess_rows = self.store.conn.execute(
            f"SELECT id, source_plugin, workspace_id, session_timestamp, chunk_count, session_summary "
            f"FROM sessions WHERE id IN ({placeholders})",
            fused_ids,
        ).fetchall()

        results = [dict(r) for r in sess_rows]
        for r in results:
            r["score"] = score_map.get(r["id"], 0.0)
        results.sort(key=lambda x: x["score"], reverse=True)

        if include_graph:
            seen = {r["id"] for r in results}
            extra: list[dict] = []
            for sess in results[:3]:
                extra.extend(self._session_graph_expand(sess["id"], sess["score"]))
            for sess in extra:
                if sess["id"] not in seen:
                    results.append(sess)
                    seen.add(sess["id"])
            results.sort(key=lambda x: x["score"], reverse=True)

        return results

    def _session_graph_expand(self, session_id: str, seed_score: float) -> list[dict]:
        """One hop to a session's session_graph_edges neighbours, any edge type.

        Weight (real for structural_similarity's cosine value, 1.0 for the
        other three edge types) further scales the decay, so a strong
        embedding-similarity neighbour ranks higher than a weak one while a
        workspace/structural/shared_memory neighbour keeps the plain decay.
        """
        rows = self.store.conn.execute(
            "SELECT target_session_id, MAX(weight) FROM session_graph_edges "
            "WHERE source_session_id = ? GROUP BY target_session_id",
            (session_id,),
        ).fetchall()
        if not rows:
            return []
        weight_map = {r[0]: r[1] for r in rows}
        neighbour_ids = list(weight_map.keys())

        placeholders = ",".join("?" * len(neighbour_ids))
        sess_rows = self.store.conn.execute(
            f"SELECT id, source_plugin, workspace_id, session_timestamp, chunk_count, session_summary "
            f"FROM sessions WHERE id IN ({placeholders})",
            neighbour_ids,
        ).fetchall()

        results = []
        for row in sess_rows:
            d = dict(row)
            d["score"] = seed_score * self._GRAPH_EXPANSION_DECAY * weight_map.get(d["id"], 1.0)
            results.append(d)
        return results

    def graph_walk(
        self,
        seed_memory_id: str,
        depth: int = 2,
        session_id: str | None = None,
        exchange_idx: int | None = None,
    ) -> list[dict]:
        """BFS from a memory's source sessions over session_graph_edges.

        Each hop keeps only the top `_MAX_GRAPH_NEIGHBOURS_PER_HOP` unvisited
        neighbour sessions by edge weight (not every session past the 0.2
        threshold) — otherwise one low-specificity edge (e.g. a broad
        `shared_memory` cluster, or a large `workspace`) can fan the walk out to
        most of the corpus. The final memory pull is similarly capped to
        `_MAX_GRAPH_EXPANSION_MEMORIES`, ranked by evidence/access count as a
        relevance proxy rather than left unbounded.
        """
        seed_sessions = [
            r[0] for r in self.store.conn.execute(
                "SELECT session_id FROM memory_sources WHERE memory_id = ?",
                (seed_memory_id,),
            ).fetchall()
        ]
        if not seed_sessions:
            return []

        visited_sessions: set[str] = set(seed_sessions)
        frontier = list(seed_sessions)

        for _ in range(depth):
            candidates: dict[str, float] = {}
            for sess in frontier:
                neighbours = self.store.conn.execute(
                    "SELECT target_session_id, MAX(weight) FROM session_graph_edges "
                    "WHERE source_session_id = ? AND weight > 0.2 "
                    "GROUP BY target_session_id",
                    (sess,),
                ).fetchall()
                for ns, w in neighbours:
                    if ns not in visited_sessions:
                        candidates[ns] = max(w, candidates.get(ns, 0.0))
            ranked = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
            next_frontier = [ns for ns, _ in ranked[:self._MAX_GRAPH_NEIGHBOURS_PER_HOP]]
            visited_sessions.update(next_frontier)
            frontier = next_frontier

        neighbour_sessions = list(visited_sessions - set(seed_sessions))
        if not neighbour_sessions:
            return []

        placeholders = ",".join("?" * len(neighbour_sessions))
        rows = self.store.conn.execute(
            f"SELECT DISTINCT m.id, m.content, m.memory_type, m.extraction_method, "
            f"m.evidence_count, m.entity_id, m.action_orientation, m.access_count "
            f"FROM memories m "
            f"JOIN memory_sources ms ON ms.memory_id = m.id "
            f"WHERE ms.session_id IN ({placeholders}) AND m.id != ? "
            f"ORDER BY m.evidence_count DESC, m.access_count DESC "
            f"LIMIT {self._MAX_GRAPH_EXPANSION_MEMORIES}",
            neighbour_sessions + [seed_memory_id],
        ).fetchall()
        results = [dict(r) for r in rows]
        _log_memory_retrievals(
            self.store, results, f"graph_walk:{seed_memory_id}", session_id, exchange_idx,
        )
        return results
