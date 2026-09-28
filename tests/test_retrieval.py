"""Tests for retrieval store methods, RRF fusion, and RetrievalEngine."""
import struct
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pack(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _insert_session(conn, sid: str = "s1") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sessions"
        "(id,source_type,source_plugin,ingested_at,session_timestamp,embedding_text)"
        " VALUES (?,?,?,?,?,?)",
        (sid, "agent_trace", "cc", "2026-01-01", "2026-01-01", ""),
    )


def _insert_memory(conn, mid: str, content: str, mtype: str = "preference",
                   session_id: str = "s1") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO memories"
        "(id,org_id,workspace_id,app_id,memory_type,extraction_method,"
        "content,created_at,updated_at,created_by_pipeline)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (mid, "default", "default", "default", mtype, "frequency",
         content, "2026-01-01", "2026-01-01", "test"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO memories_fts(memory_id,content) VALUES (?,?)",
        (mid, content),
    )
    conn.execute(
        "INSERT OR IGNORE INTO memory_sources(memory_id,session_id) VALUES (?,?)",
        (mid, session_id),
    )


def _insert_record(conn, rid: str, text: str, session_id: str = "s1",
                   chunk_index: int = 0) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO records"
        "(id,session_id,chunk_index,chunk_text,embedding_text)"
        " VALUES (?,?,?,?,?)",
        (rid, session_id, chunk_index, text, text),
    )
    conn.execute(
        "INSERT OR IGNORE INTO records_fts(record_id,chunk_text) VALUES (?,?)",
        (rid, text),
    )


def _insert_memory_emb(conn, mid: str, vec: list[float]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO memory_embeddings(memory_id,embedding) VALUES (?,?)",
        (mid, _pack(vec)),
    )


def _insert_record_emb(conn, rid: str, vec: list[float]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO record_embeddings(record_id,embedding) VALUES (?,?)",
        (rid, _pack(vec)),
    )


def _insert_entity(conn, entity_id: str, name: str, etype: str = "technology") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities"
        "(id,canonical_name,entity_type,first_seen,last_seen,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (entity_id, name, etype, "2026-01-01", "2026-01-01", "2026-01-01", "2026-01-01"),
    )


def _insert_occurrence(conn, occ_id: str, record_id: str, session_id: str, entity_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO occurrences"
        "(id,record_id,session_id,entity_id,role,mention_text,context_text,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (occ_id, record_id, session_id, entity_id, "subject", "x", "ctx", "2026-01-01"),
    )


def _insert_structural_emb(conn, session_id: str, vec: list[float]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO session_structural_embeddings(session_id,embedding) VALUES (?,?)",
        (session_id, _pack(vec)),
    )


def _insert_session_summary(conn, session_id: str, summary: str) -> None:
    """Update session_summary AND its FTS5 shadow row (sessions_fts isn't a
    content-linked table — store.py populates it manually at write time, so
    tests inserting sessions via raw SQL must sync it too for BM25 to find them).
    """
    conn.execute("UPDATE sessions SET session_summary=? WHERE id=?", (summary, session_id))
    conn.execute(
        "INSERT OR REPLACE INTO sessions_fts(session_id, session_summary, workspace_id) VALUES (?,?,?)",
        (session_id, summary, ""),
    )


def _insert_session_graph_edge(conn, source: str, target: str, edge_type: str = "workspace",
                                weight: float = 1.0) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO session_graph_edges "
        "(source_session_id,target_session_id,edge_type,via_entity_id,weight,"
        "direction,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (source, target, edge_type, "", weight, "undirected", "test", "2026-01-01"),
    )


# ---------------------------------------------------------------------------
# BM25 search
# ---------------------------------------------------------------------------

class TestSearchMemoriesBM25:
    def test_returns_matching_memory(self, db, store):
        _insert_session(db)
        _insert_memory(db, "m1", "PDF rendering blocked by JavaScript")
        _insert_memory(db, "m2", "pytest fixtures reset state between tests")
        db.commit()
        results = store.search_memories_bm25("PDF rendering", top_k=5)
        ids = [r[0] for r in results]
        assert "m1" in ids

    def test_top_k_limits_results(self, db, store):
        _insert_session(db)
        for i in range(5):
            _insert_memory(db, f"m{i}", f"entity extraction step {i} uses GLiNER")
        db.commit()
        results = store.search_memories_bm25("entity extraction GLiNER", top_k=2)
        assert len(results) <= 2

    def test_empty_query_returns_empty(self, db, store):
        assert store.search_memories_bm25("", top_k=5) == []

    def test_no_match_returns_empty(self, db, store):
        _insert_session(db)
        _insert_memory(db, "m1", "PDF rendering blocked")
        db.commit()
        assert store.search_memories_bm25("xyzzy nonexistent", top_k=5) == []


class TestSearchRecordsBM25:
    def test_returns_matching_record(self, db, store):
        _insert_session(db)
        _insert_record(db, "r1", "entity extraction uses GLiNER for concept detection")
        _insert_record(db, "r2", "analytics pipeline promotes frequent entities")
        db.commit()
        results = store.search_records_bm25("GLiNER concept", top_k=5)
        ids = [r[0] for r in results]
        assert "r1" in ids

    def test_session_filter(self, db, store):
        _insert_session(db, "s1")
        _insert_session(db, "s2")
        _insert_record(db, "r1", "retrieval engine uses BM25", "s1")
        _insert_record(db, "r2", "retrieval engine uses BM25", "s2")
        db.commit()
        results = store.search_records_bm25("retrieval BM25", session_id="s1", top_k=10)
        ids = [r[0] for r in results]
        assert "r1" in ids
        assert "r2" not in ids

    def test_empty_query_returns_empty(self, db, store):
        assert store.search_records_bm25("", top_k=5) == []


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------

class TestSearchMemoriesSemantic:
    def test_returns_nearest_memory(self, db, store):
        _insert_session(db)
        _insert_memory(db, "m1", "PDF rendering blocked")
        _insert_memory(db, "m2", "entity extraction approach")
        vec_a = [1.0] + [0.0] * 767
        vec_b = [0.0] + [1.0] + [0.0] * 766
        _insert_memory_emb(db, "m1", vec_a)
        _insert_memory_emb(db, "m2", vec_b)
        db.commit()
        results = store.search_memories_semantic(vec_a, top_k=2)
        assert results[0][0] == "m1"

    def test_memory_type_filter(self, db, store):
        _insert_session(db)
        _insert_memory(db, "m1", "procedural memory content", mtype="procedural")
        _insert_memory(db, "m2", "semantic memory content", mtype="preference")
        vec = [1.0] + [0.0] * 767
        _insert_memory_emb(db, "m1", vec)
        _insert_memory_emb(db, "m2", vec)
        db.commit()
        results = store.search_memories_semantic(vec, top_k=10, memory_type="procedural")
        ids = [r[0] for r in results]
        assert "m1" in ids
        assert "m2" not in ids

    def test_empty_table_returns_empty(self, db, store):
        results = store.search_memories_semantic([0.0] * 768, top_k=5)
        assert results == []


class TestSearchRecordsSemantic:
    def test_returns_nearest_record(self, db, store):
        _insert_session(db)
        _insert_record(db, "r1", "chunk about retrieval")
        _insert_record(db, "r2", "chunk about ingestion")
        vec_a = [1.0] + [0.0] * 767
        vec_b = [0.0] + [1.0] + [0.0] * 766
        _insert_record_emb(db, "r1", vec_a)
        _insert_record_emb(db, "r2", vec_b)
        db.commit()
        results = store.search_records_semantic(vec_a, top_k=2)
        assert results[0][0] == "r1"

    def test_session_filter(self, db, store):
        _insert_session(db, "s1")
        _insert_session(db, "s2")
        _insert_record(db, "r1", "same text", "s1")
        _insert_record(db, "r2", "same text", "s2")
        vec = [1.0] + [0.0] * 767
        _insert_record_emb(db, "r1", vec)
        _insert_record_emb(db, "r2", vec)
        db.commit()
        results = store.search_records_semantic(vec, top_k=10, session_id="s1")
        ids = [r[0] for r in results]
        assert "r1" in ids
        assert "r2" not in ids

    def test_empty_table_returns_empty(self, db, store):
        results = store.search_records_semantic([0.0] * 768, top_k=5)
        assert results == []


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------

class TestRRF:
    def test_item_in_both_lists_ranks_first(self):
        from agent_trace_signals.pipeline.retrieval import _rrf
        bm25 = [("a", -1.5), ("b", -0.8)]
        sem  = [("b", 0.1),  ("c", 0.3)]
        result = _rrf(bm25, sem)
        assert result[0][0] == "b"

    def test_all_ids_included(self):
        from agent_trace_signals.pipeline.retrieval import _rrf
        bm25 = [("a", -1.0), ("b", -0.5)]
        sem  = [("b", 0.1),  ("c", 0.3)]
        ids = {r[0] for r in _rrf(bm25, sem)}
        assert ids == {"a", "b", "c"}

    def test_top_k_truncates(self):
        from agent_trace_signals.pipeline.retrieval import _rrf
        bm25 = [("a", -1.0), ("b", -0.5), ("c", -0.3)]
        sem  = [("d", 0.1),  ("e", 0.2)]
        assert len(_rrf(bm25, sem, top_k=2)) == 2

    def test_empty_inputs(self):
        from agent_trace_signals.pipeline.retrieval import _rrf
        assert _rrf([], []) == []
        result = _rrf([("a", -1.0)], [])
        assert len(result) == 1
        assert result[0][0] == "a"

    def test_scores_are_positive(self):
        from agent_trace_signals.pipeline.retrieval import _rrf
        result = _rrf([("a", -1.0)], [("a", 0.2)])
        assert result[0][1] > 0

    def test_leg_top1_guaranteed_slot_survives_truncation(self):
        """A candidate that's #1 on one leg but absent from the other must
        not be truncated out of top_k just because several other candidates
        are mediocre-but-present on both legs and out-sum it additively."""
        from agent_trace_signals.pipeline.retrieval import _rrf
        # "z" is semantic-only #1 (bm25 finds nothing for it).
        sem = [("z", 0.9), ("x1", 0.1)]
        # Five other candidates rank respectably on both legs, so their
        # summed reciprocal scores would otherwise bury "z" past top_k=5.
        bm25 = [(f"c{i}", -1.0) for i in range(1, 6)]
        sem += [(f"c{i}", 0.05) for i in range(1, 6)]
        result = _rrf(bm25, sem, top_k=5)
        ids = [r[0] for r in result]
        assert "z" in ids
        assert len(result) == 5

    def test_both_legs_top1_guaranteed_when_different(self):
        from agent_trace_signals.pipeline.retrieval import _rrf
        bm25 = [("bm25_only", -1.0)] + [(f"c{i}", -1.0) for i in range(1, 6)]
        sem = [("sem_only", 0.9)] + [(f"c{i}", 0.05) for i in range(1, 6)]
        result = _rrf(bm25, sem, top_k=5)
        ids = [r[0] for r in result]
        assert "bm25_only" in ids
        assert "sem_only" in ids

    def test_guaranteed_slot_noop_when_already_present(self):
        """When both legs agree on the same top pick, no eviction needed —
        result is unaffected by the guarantee logic."""
        from agent_trace_signals.pipeline.retrieval import _rrf
        bm25 = [("a", -1.0), ("b", -0.5)]
        sem = [("a", 0.9), ("b", 0.1)]
        result = _rrf(bm25, sem, top_k=2)
        assert [r[0] for r in result] == ["a", "b"]

    def test_guaranteed_slot_scores_unchanged(self):
        """The guarantee only affects which ids survive truncation, not the
        RRF score attached to them."""
        from agent_trace_signals.pipeline.retrieval import _rrf
        sem = [("z", 0.9)] + [(f"c{i}", 0.05) for i in range(1, 6)]
        bm25 = [(f"c{i}", -1.0) for i in range(1, 6)]
        full = dict(_rrf(bm25, sem, top_k=None))
        truncated = dict(_rrf(bm25, sem, top_k=5))
        assert truncated["z"] == full["z"]


# ---------------------------------------------------------------------------
# RetrievalEngine (stubbed embedder)
# ---------------------------------------------------------------------------

class _FakeEmbedder:
    """Returns a deterministic unit vector from query text length."""
    def embed(self, text: str) -> list[float]:
        v = [0.0] * 768
        v[len(text) % 768] = 1.0
        return v


class TestRetrievalEngine:
    def test_recall_returns_memories(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db)
        _insert_memory(db, "m1", "PDF rendering blocked by JavaScript rendering engine")
        _insert_memory(db, "m2", "entity extraction uses GLiNER for NER")
        vec = [1.0] + [0.0] * 767
        _insert_memory_emb(db, "m1", vec)
        _insert_memory_emb(db, "m2", vec)
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())
        results = engine.recall("PDF rendering", top_k=10)
        assert len(results) >= 1
        assert all("rrf_score" in r for r in results)
        assert all("content" in r for r in results)

    def test_recall_with_type_filter(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db)
        _insert_memory(db, "m1", "procedural pattern for using pytest", mtype="procedural")
        _insert_memory(db, "m2", "semantic fact about Python", mtype="preference")
        vec = [1.0] + [0.0] * 767
        _insert_memory_emb(db, "m1", vec)
        _insert_memory_emb(db, "m2", vec)
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())
        results = engine.recall("pytest pattern", memory_type="procedural", top_k=10)
        types = {r["memory_type"] for r in results}
        assert types <= {"procedural"}

    def test_recall_increments_access_count(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db)
        _insert_memory(db, "m1", "PDF rendering blocked by JavaScript")
        vec = [1.0] + [0.0] * 767
        _insert_memory_emb(db, "m1", vec)
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())
        engine.recall("PDF rendering", top_k=5)
        count = db.execute(
            "SELECT access_count FROM memories WHERE id='m1'"
        ).fetchone()[0]
        assert count >= 1

    def test_chunk_search_returns_records(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db)
        _insert_record(db, "r1", "entity extraction pipeline uses GLiNER for concept NER")
        _insert_record(db, "r2", "analytics light promotes frequent entities to memories")
        vec = [1.0] + [0.0] * 767
        _insert_record_emb(db, "r1", vec)
        _insert_record_emb(db, "r2", vec)
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())
        results = engine.chunk_search("GLiNER entity", top_k=10)
        assert len(results) >= 1
        assert all("rrf_score" in r for r in results)
        assert all("chunk_text" in r for r in results)

    def test_recall_empty_db_returns_empty(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        engine = RetrievalEngine(store, _FakeEmbedder())
        assert engine.recall("anything", top_k=5) == []

    def test_results_sorted_by_rrf_score_desc(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db)
        # m1 will rank high in BM25 (exact match) and semantic
        _insert_memory(db, "m1", "PDF rendering blocked by JavaScript rendering engine")
        _insert_memory(db, "m2", "unrelated topic about database indexing strategies")
        vec_close = [1.0] + [0.0] * 767
        vec_far   = [0.0] * 767 + [1.0]
        _insert_memory_emb(db, "m1", vec_close)
        _insert_memory_emb(db, "m2", vec_far)
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())
        results = engine.recall("PDF rendering", top_k=10)
        scores = [r["rrf_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_include_graph_decays_score_relative_to_seed(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db, "s1")
        _insert_session(db, "s2")
        _insert_memory(db, "m1", "PDF rendering blocked by JavaScript rendering engine", session_id="s1")
        _insert_memory(db, "m2", "completely unrelated content about something else", session_id="s2")
        vec = [1.0] + [0.0] * 767
        _insert_memory_emb(db, "m1", vec)
        _insert_session_graph_edge(db, "s1", "s2", "workspace", 1.0)
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())

        without_graph = engine.recall("PDF rendering", top_k=10, include_graph=False)
        assert "m2" not in {r["id"] for r in without_graph}

        with_graph = engine.recall("PDF rendering", top_k=10, include_graph=True)
        by_id = {r["id"]: r["rrf_score"] for r in with_graph}
        assert "m2" in by_id
        assert 0 < by_id["m2"] < by_id["m1"]

    def test_superseded_memory_excluded_from_recall(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db)
        _insert_memory(db, "m1", "PDF rendering blocked by JavaScript rendering engine")
        vec = [1.0] + [0.0] * 767
        _insert_memory_emb(db, "m1", vec)
        db.execute("UPDATE memories SET status='superseded' WHERE id='m1'")
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())
        results = engine.recall("PDF rendering", top_k=10)
        assert "m1" not in {r["id"] for r in results}


# ---------------------------------------------------------------------------
# session_search graph expansion (session_graph_edges, direct)
# ---------------------------------------------------------------------------

class TestSessionSearchGraphExpansion:
    def test_expands_to_edge_neighbour(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db, "s1")
        _insert_session(db, "s2")
        _insert_session_summary(db, "s1", "PDF rendering pipeline issue")
        _insert_session_summary(db, "s2", "completely unrelated summary text")
        _insert_session_graph_edge(db, "s1", "s2", "workspace", 1.0)
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())

        without_graph = engine.session_search("PDF rendering", top_k=10, include_graph=False)
        assert "s2" not in {r["id"] for r in without_graph}

        with_graph = engine.session_search("PDF rendering", top_k=10, include_graph=True)
        by_id = {r["id"]: r["score"] for r in with_graph}
        assert "s1" in by_id and "s2" in by_id
        assert 0 < by_id["s2"] < by_id["s1"]

    def test_structural_similarity_weight_scales_expansion_score(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db, "s1")
        _insert_session(db, "s2")
        _insert_session(db, "s3")
        _insert_session_summary(db, "s1", "PDF rendering pipeline issue")
        _insert_session_summary(db, "s2", "other text")
        _insert_session_summary(db, "s3", "other text too")
        _insert_session_graph_edge(db, "s1", "s2", "structural_similarity", 0.9)
        _insert_session_graph_edge(db, "s1", "s3", "structural_similarity", 0.3)
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())

        results = engine.session_search("PDF rendering", top_k=10, include_graph=True)
        by_id = {r["id"]: r["score"] for r in results}
        assert by_id["s2"] > by_id["s3"]

    def test_no_expansion_without_edge(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db, "s1")
        _insert_session(db, "s2")
        _insert_session_summary(db, "s1", "PDF rendering pipeline issue")
        _insert_session_summary(db, "s2", "unrelated")
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())
        results = engine.session_search("PDF rendering", top_k=10, include_graph=True)
        assert "s2" not in {r["id"] for r in results}


# ---------------------------------------------------------------------------
# chunk_search graph expansion (occurrences self-join)
# ---------------------------------------------------------------------------

class TestChunkSearchGraphExpansion:
    def test_expands_to_chunk_sharing_entity_in_other_session(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db, "s1")
        _insert_session(db, "s2")
        _insert_record(db, "r1", "entity extraction pipeline uses GLiNER for NER", "s1")
        _insert_record(db, "r2", "unrelated chunk about test fixtures", "s2")
        _insert_entity(db, "e1", "GLiNER")
        _insert_occurrence(db, "o1", "r1", "s1", "e1")
        _insert_occurrence(db, "o2", "r2", "s2", "e1")
        vec = [1.0] + [0.0] * 767
        _insert_record_emb(db, "r1", vec)
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())

        without_graph = engine.chunk_search("GLiNER entity", top_k=10, include_graph=False)
        assert "r2" not in {r["id"] for r in without_graph}

        with_graph = engine.chunk_search("GLiNER entity", top_k=10, include_graph=True)
        ids = {r["id"] for r in with_graph}
        assert "r1" in ids
        assert "r2" in ids

    def test_expanded_chunk_scores_below_its_seed(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db, "s1")
        _insert_session(db, "s2")
        _insert_record(db, "r1", "entity extraction pipeline uses GLiNER for NER", "s1")
        _insert_record(db, "r2", "unrelated chunk about test fixtures", "s2")
        _insert_entity(db, "e1", "GLiNER")
        _insert_occurrence(db, "o1", "r1", "s1", "e1")
        _insert_occurrence(db, "o2", "r2", "s2", "e1")
        vec = [1.0] + [0.0] * 767
        _insert_record_emb(db, "r1", vec)
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())

        results = engine.chunk_search("GLiNER entity", top_k=10, include_graph=True)
        by_id = {r["id"]: r["rrf_score"] for r in results}
        assert by_id["r2"] < by_id["r1"]
        assert by_id["r2"] > 0

    def test_no_expansion_without_shared_entity(self, db, store):
        from agent_trace_signals.pipeline.retrieval import RetrievalEngine
        _insert_session(db, "s1")
        _insert_session(db, "s2")
        _insert_record(db, "r1", "entity extraction pipeline uses GLiNER for NER", "s1")
        _insert_record(db, "r2", "completely unrelated chunk", "s2")
        vec = [1.0] + [0.0] * 767
        _insert_record_emb(db, "r1", vec)
        db.commit()
        engine = RetrievalEngine(store, _FakeEmbedder())

        results = engine.chunk_search("GLiNER entity", top_k=10, include_graph=True)
        assert "r2" not in {r["id"] for r in results}


# ---------------------------------------------------------------------------
# structural_similarity session edges (store methods)
# ---------------------------------------------------------------------------

class TestStructuralSimilarityEdges:
    def test_get_all_structural_embeddings_roundtrip(self, db, store):
        _insert_session(db, "s1")
        _insert_session(db, "s2")
        vec_a = [1.0] + [0.0] * 767
        vec_b = [0.0] + [1.0] + [0.0] * 766
        _insert_structural_emb(db, "s1", vec_a)
        _insert_structural_emb(db, "s2", vec_b)
        db.commit()
        pairs = dict(store.get_all_structural_embeddings())
        assert set(pairs.keys()) == {"s1", "s2"}
        assert pairs["s1"] == pytest.approx(vec_a)

    def test_replace_edges_of_type_is_a_full_replace(self, db, store):
        from agent_trace_signals.models import SessionGraphEdge
        _insert_session(db, "s1")
        _insert_session(db, "s2")
        _insert_session(db, "s3")
        db.commit()
        old_edge = SessionGraphEdge(
            source_session_id="s1", target_session_id="s2",
            edge_type="structural_similarity", via_entity_id="",
            weight=0.9, created_by="embed", created_at="2026-01-01",
        )
        store.replace_session_graph_edges_of_type("structural_similarity", [old_edge])
        assert db.execute(
            "SELECT COUNT(*) FROM session_graph_edges WHERE edge_type='structural_similarity'"
        ).fetchone()[0] == 1

        new_edge = SessionGraphEdge(
            source_session_id="s1", target_session_id="s3",
            edge_type="structural_similarity", via_entity_id="",
            weight=0.8, created_by="embed", created_at="2026-01-02",
        )
        store.replace_session_graph_edges_of_type("structural_similarity", [new_edge])
        rows = [tuple(r) for r in db.execute(
            "SELECT source_session_id, target_session_id FROM session_graph_edges "
            "WHERE edge_type='structural_similarity'"
        ).fetchall()]
        assert rows == [("s1", "s3")]

    def test_replace_edges_of_type_does_not_touch_other_edge_types(self, db, store):
        _insert_session(db, "s1")
        _insert_session(db, "s2")
        db.commit()
        db.execute(
            "INSERT INTO session_graph_edges "
            "(source_session_id,target_session_id,edge_type,via_entity_id,weight,"
            "direction,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("s1", "s2", "workspace", "", 1.0, "undirected", "ingestion", "2026-01-01"),
        )
        db.commit()
        store.replace_session_graph_edges_of_type("structural_similarity", [])
        assert db.execute(
            "SELECT COUNT(*) FROM session_graph_edges WHERE edge_type='workspace'"
        ).fetchone()[0] == 1
