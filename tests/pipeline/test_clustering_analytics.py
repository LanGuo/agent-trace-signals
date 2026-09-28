"""Tests for ClusteringAnalyticsPipeline.tag_session_types."""
import numpy as np

from agent_trace_signals.db.schema import create_all, open_db
from agent_trace_signals.db.store import SQLiteStore


def _make_store(tmp_path, name="test.db"):
    db_path = tmp_path / name
    con = open_db(str(db_path))
    create_all(con)
    return SQLiteStore(con)


def _insert_session(store, sid):
    store.conn.execute(
        "INSERT INTO sessions (id,org_id,workspace_id,app_id,source_type,source_plugin,"
        "session_timestamp,ingested_at,session_summary,embedding_text) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (sid, "o", "w", "a", "agent_trace", "cc", "2026-01-01", "2026-01-01", "test", "test"),
    )
    store.conn.commit()


def _insert_cluster_memory(store, mid, content="test cluster content"):
    store.conn.execute(
        "INSERT INTO memories (id,org_id,workspace_id,app_id,memory_type,"
        "extraction_method,content,created_by_pipeline,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (mid, "o", "w", "a", "cluster", "cluster", content, "clustering_analytics",
         "2026-01-01", "2026-01-01"),
    )


def _insert_cluster_membership(store, cluster_memory_id, member_ids):
    for member_id in member_ids:
        store.conn.execute(
            "INSERT INTO memory_cluster_members (cluster_memory_id, member_memory_id, member_memory_type) "
            "VALUES (?,?,?)",
            (cluster_memory_id, member_id, "procedural"),
        )


def test_tag_session_types_writes_session_type(tmp_path):
    store = _make_store(tmp_path)

    # Insert 6 fake sessions with 2 distinct embedding clusters
    for i in range(6):
        _insert_session(store, f"sess{i:02d}")

    # Insert structural embeddings: 3 near [1,0,...] and 3 near [0,1,...]
    rng = np.random.default_rng(42)
    for i in range(3):
        emb_a = [1.0] + [0.0] * 767
        emb_a[1] = float(rng.normal(0, 0.01))
        store.upsert_structural_embedding(f"sess{i:02d}", emb_a)
    for i in range(3, 6):
        emb_b = [0.0] + [1.0] + [0.0] * 766
        emb_b[2] = float(rng.normal(0, 0.01))
        store.upsert_structural_embedding(f"sess{i:02d}", emb_b)

    from agent_trace_signals.pipeline.clustering_analytics import ClusteringAnalyticsPipeline
    pipeline = ClusteringAnalyticsPipeline(store=store)
    result = pipeline.tag_session_types(min_cluster_size=2)

    # All sessions should have a session_type set
    rows = store.conn.execute("SELECT session_type FROM sessions").fetchall()
    types = [r[0] for r in rows]
    assert all(t is not None for t in types), f"Some session_types are None: {types}"
    assert len(result) >= 1  # at least one cluster found


def test_tag_session_types_returns_empty_when_too_few(tmp_path):
    store = _make_store(tmp_path)
    sid = "sess00"
    _insert_session(store, sid)
    store.upsert_structural_embedding(sid, [1.0] + [0.0] * 767)

    from agent_trace_signals.pipeline.clustering_analytics import ClusteringAnalyticsPipeline
    pipeline = ClusteringAnalyticsPipeline(store=store)
    result = pipeline.tag_session_types(min_cluster_size=3)
    assert result == {}


def test_consolidate_memories_mints_cluster_memory(tmp_path):
    """Clusters with recurrence >= min_recurrence produce a consolidated 'cluster' memory."""
    from unittest.mock import MagicMock

    store = _make_store(tmp_path)

    # HDBSCAN requires two well-separated groups in high-dim space to form clusters.
    # We insert 12 sessions: 6 with "read-before-write" memories near base1,
    # and 6 with "check-tests" memories near base2.
    n_each = 6
    rng = np.random.default_rng(42)
    base1 = np.array([1.0, 0.0] + [0.0] * 766, dtype=np.float32)
    base2 = np.array([0.0, 1.0] + [0.0] * 766, dtype=np.float32)

    for i in range(n_each * 2):
        _insert_session(store, f"s{i}")

    memory_ids_g1, memory_ids_g2 = [], []
    for i in range(n_each):
        # Group 1: read-before-write
        mid = f"mem_a{i}"
        memory_ids_g1.append(mid)
        store.conn.execute(
            "INSERT INTO memories (id,org_id,workspace_id,app_id,memory_type,"
            "extraction_method,content,first_observed,last_observed,created_by_pipeline,"
            "created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, "o", "w", "a", "pattern_strategy", "d_combined",
             f"agent reads files before editing (session {i})", "2026-01-01", "2026-01-01", "ingestion",
             "2026-01-01", "2026-01-01"),
        )
        store.conn.execute(
            "INSERT INTO memory_sources (memory_id,session_id,relevance_score) VALUES (?,?,?)",
            (mid, f"s{i}", 1.0),
        )
        # Group 2: run-tests
        mid2 = f"mem_b{i}"
        memory_ids_g2.append(mid2)
        store.conn.execute(
            "INSERT INTO memories (id,org_id,workspace_id,app_id,memory_type,"
            "extraction_method,content,first_observed,last_observed,created_by_pipeline,"
            "created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid2, "o", "w", "a", "pattern_strategy", "d_combined",
             f"always run tests after changes (session {i})", "2026-01-01", "2026-01-01", "ingestion",
             "2026-01-01", "2026-01-01"),
        )
        store.conn.execute(
            "INSERT INTO memory_sources (memory_id,session_id,relevance_score) VALUES (?,?,?)",
            (mid2, f"s{n_each + i}", 1.0),
        )
    store.conn.commit()

    # Insert embeddings: group 1 near base1, group 2 near base2 (well-separated)
    for mid in memory_ids_g1:
        emb = (base1 + rng.normal(0, 0.001, 768).astype(np.float32)).tolist()
        store._vec_upsert("memory_embeddings", "memory_id", mid, emb)
    for mid in memory_ids_g2:
        emb = (base2 + rng.normal(0, 0.001, 768).astype(np.float32)).tolist()
        store._vec_upsert("memory_embeddings", "memory_id", mid, emb)

    provider = MagicMock()
    provider.complete_json.return_value = {
        "consolidated": "When starting any task, read all relevant files before making changes (read-before-write strategy).",
        "memory_type": "pattern_strategy",
    }

    from agent_trace_signals.pipeline.clustering_analytics import ClusteringAnalyticsPipeline
    pipeline = ClusteringAnalyticsPipeline(store=store)
    n = pipeline.consolidate_memories(min_recurrence=3, provider=provider, model="gemma3:12b")

    assert n >= 1
    rows = store.conn.execute(
        "SELECT memory_type, extraction_method, content FROM memories "
        "WHERE extraction_method='cluster'"
    ).fetchall()
    assert len(rows) >= 1
    assert rows[0][0] == "cluster"


class TestMarkSupersededClusters:
    def test_marks_prior_cluster_superseded_on_majority_overlap(self, tmp_path):
        import json
        store = _make_store(tmp_path)
        _insert_cluster_memory(store, "old")
        _insert_cluster_memory(store, "new")
        _insert_cluster_membership(store, "old", ["m1", "m2", "m3"])
        store.conn.commit()

        from agent_trace_signals.pipeline.clustering_analytics import ClusteringAnalyticsPipeline
        pipeline = ClusteringAnalyticsPipeline(store=store)
        pipeline.mark_superseded_clusters({"m1", "m2", "m3", "m4", "m5"}, "new", "2026-02-01")

        row = store.conn.execute("SELECT status, metadata FROM memories WHERE id='old'").fetchone()
        assert row[0] == "superseded"
        assert json.loads(row[1])["superseded_by"] == "new"

    def test_does_not_supersede_below_threshold(self, tmp_path):
        store = _make_store(tmp_path)
        _insert_cluster_memory(store, "old")
        _insert_cluster_memory(store, "new")
        _insert_cluster_membership(store, "old", ["m1", "m2", "m3", "m4"])
        store.conn.commit()

        from agent_trace_signals.pipeline.clustering_analytics import ClusteringAnalyticsPipeline
        pipeline = ClusteringAnalyticsPipeline(store=store)
        # Only 1 of "old"'s 4 members (25%) is in the new cluster — below the 50% threshold
        pipeline.mark_superseded_clusters({"m1", "m5", "m6"}, "new", "2026-02-01")

        row = store.conn.execute("SELECT status FROM memories WHERE id='old'").fetchone()
        assert row[0] is None

    def test_no_new_members_is_a_noop(self, tmp_path):
        store = _make_store(tmp_path)
        _insert_cluster_memory(store, "old")
        _insert_cluster_membership(store, "old", ["m1"])
        store.conn.commit()

        from agent_trace_signals.pipeline.clustering_analytics import ClusteringAnalyticsPipeline
        pipeline = ClusteringAnalyticsPipeline(store=store)
        pipeline.mark_superseded_clusters(set(), "new", "2026-02-01")

        row = store.conn.execute("SELECT status FROM memories WHERE id='old'").fetchone()
        assert row[0] is None

    def test_already_superseded_cluster_is_not_reassigned(self, tmp_path):
        import json
        store = _make_store(tmp_path)
        _insert_cluster_memory(store, "old")
        _insert_cluster_memory(store, "mid")
        _insert_cluster_memory(store, "new")
        _insert_cluster_membership(store, "old", ["m1", "m2"])
        store.conn.commit()

        from agent_trace_signals.pipeline.clustering_analytics import ClusteringAnalyticsPipeline
        pipeline = ClusteringAnalyticsPipeline(store=store)
        pipeline.mark_superseded_clusters({"m1", "m2"}, "mid", "2026-02-01")
        row = store.conn.execute("SELECT metadata FROM memories WHERE id='old'").fetchone()
        assert json.loads(row[0])["superseded_by"] == "mid"

        # A second, later cluster ("new") also overlaps "old" 100% and would
        # naively re-supersede it pointing at "new" instead — the
        # `status != 'superseded'` guard in the UPDATE keeps "old" pointed at
        # whichever cluster first superseded it ("mid"), not the most recent.
        pipeline.mark_superseded_clusters({"m1", "m2"}, "new", "2026-02-02")
        row = store.conn.execute("SELECT metadata FROM memories WHERE id='old'").fetchone()
        assert json.loads(row[0])["superseded_by"] == "mid"
