"""Tests for the memory_retrievals log (Stage 6)."""
from __future__ import annotations


from agent_trace_signals.models import MemoryRetrieval
from agent_trace_signals.pipeline.retrieval import RetrievalEngine


def _insert_session(conn, sid: str = "s1") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sessions"
        "(id,source_type,source_plugin,ingested_at,session_timestamp,embedding_text)"
        " VALUES (?,?,?,?,?,?)",
        (sid, "agent_trace", "cc", "2026-01-01", "2026-01-01", ""),
    )


def _insert_memory(conn, mid: str, content: str, session_id: str = "s1") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO memories"
        "(id,org_id,workspace_id,app_id,memory_type,extraction_method,"
        "content,created_at,updated_at,created_by_pipeline)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (mid, "default", "default", "default", "preference", "frequency",
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


# ---------------------------------------------------------------------------
# Store-level
# ---------------------------------------------------------------------------

def test_insert_memory_retrieval_roundtrip(store, db):
    _insert_session(db, "s1")
    _insert_memory(db, "m1", "alpha beta")
    r = MemoryRetrieval(
        id=MemoryRetrieval.make_id("m1", "2026-06-10T00:00:00+00:00", "s1"),
        session_id="s1",
        memory_id="m1",
        query_text="alpha",
        relevance_rank=1,
        relevance_score=0.5,
        surfaced=True,
        retrieved_at="2026-06-10T00:00:00+00:00",
    )
    store.insert_memory_retrieval(r)
    rows = db.execute("SELECT * FROM memory_retrievals").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["memory_id"] == "m1"
    assert row["relevance_rank"] == 1
    assert row["relevance_score"] == 0.5
    assert row["surfaced"] == 1
    assert row["query_features"] == "{}"


def test_insert_memory_retrievals_batch_idempotent(store, db):
    _insert_session(db, "s1")
    _insert_memory(db, "m1", "alpha")
    _insert_memory(db, "m2", "beta")
    rows = [
        MemoryRetrieval(
            id=MemoryRetrieval.make_id("m1", "t0", "s1"),
            session_id="s1", memory_id="m1", relevance_rank=1, retrieved_at="t0",
        ),
        MemoryRetrieval(
            id=MemoryRetrieval.make_id("m2", "t0", "s1"),
            session_id="s1", memory_id="m2", relevance_rank=2, retrieved_at="t0",
        ),
    ]
    store.insert_memory_retrievals_batch(rows)
    store.insert_memory_retrievals_batch(rows)  # duplicate
    assert db.execute("SELECT COUNT(*) FROM memory_retrievals").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# Engine-level (logging side effect)
# ---------------------------------------------------------------------------

class _StubEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.0] * 768


def test_recall_logs_memory_retrievals(store, db):
    _insert_session(db, "s1")
    _insert_memory(db, "m1", "pdf rendering bug")
    _insert_memory(db, "m2", "pdf parsing error")

    engine = RetrievalEngine(store, _StubEmbedder())
    results = engine.recall("pdf", method="lexical", top_k=10)
    assert len(results) >= 1

    rows = db.execute(
        "SELECT memory_id, relevance_rank, relevance_score, surfaced, query_text "
        "FROM memory_retrievals ORDER BY relevance_rank"
    ).fetchall()
    assert len(rows) == len(results)
    ranks = [r["relevance_rank"] for r in rows]
    assert ranks == sorted(ranks)
    assert ranks[0] == 1
    assert all(r["surfaced"] == 1 for r in rows)
    assert all(r["query_text"] == "pdf" for r in rows)
    assert all(r["relevance_score"] is not None for r in rows)


def test_recall_with_session_id_logs_anchor(store, db):
    _insert_session(db, "s1")
    _insert_memory(db, "m1", "alpha keyword")
    engine = RetrievalEngine(store, _StubEmbedder())
    engine.recall("alpha", method="lexical", top_k=5, session_id="s1", exchange_idx=3)
    row = db.execute(
        "SELECT session_id, exchange_idx FROM memory_retrievals"
    ).fetchone()
    assert row["session_id"] == "s1"
    assert row["exchange_idx"] == 3
