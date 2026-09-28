import struct
import sqlite3
import sqlite_vec
import numpy as np
from agent_trace_signals.db.schema import create_all, migrate
from agent_trace_signals.db.store import SQLiteStore


def _make_store(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    migrate(conn)
    create_all(conn)
    return SQLiteStore(conn)


def test_topic_backfill_averages_chunk_embeddings(tmp_path):
    """Verify that after backfill, session_topic_embeddings contains avg of record_embeddings."""
    store = _make_store(tmp_path)
    store.conn.execute(
        "INSERT INTO sessions(id,org_id,workspace_id,app_id,user_id,source_type,source_plugin,ingested_at,session_summary) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("s1","default","default","default","default","cc","claude-code","2026-01-01T00:00:00Z","summary"),
    )
    for i, rid in enumerate(["r1", "r2"]):
        store.conn.execute(
            "INSERT INTO records(id,session_id,chunk_index,chunk_text,evidence_text,embedding_text) VALUES(?,?,?,?,?,?)",
            (rid, "s1", i, f"chunk {i}", "", ""),
        )
    store.conn.commit()

    emb1 = [float(i) for i in range(768)]
    emb2 = [float(i) * 2 for i in range(768)]
    store._vec_upsert("record_embeddings", "record_id", "r1", emb1)
    store._vec_upsert("record_embeddings", "record_id", "r2", emb2)

    # Run backfill logic directly (mirrors what cli does)
    sessions = store.get_sessions_without_topic_embedding()
    assert len(sessions) == 1
    for sess in sessions:
        vecs = store.get_record_embeddings_for_session(sess["id"])
        avg = np.mean(vecs, axis=0).tolist()
        store.upsert_topic_embedding(sess["id"], avg)

    # Verify result
    sessions_after = store.get_sessions_without_topic_embedding()
    assert len(sessions_after) == 0

    # Verify the averaged value
    expected_avg = [(e1 + e2) / 2 for e1, e2 in zip(emb1, emb2)]
    result_rows = store.conn.execute(
        "SELECT embedding FROM session_topic_embeddings WHERE session_id='s1'"
    ).fetchall()
    assert len(result_rows) == 1
    stored_embedding = list(struct.unpack(f"{len(expected_avg)}f", result_rows[0][0]))
    for i, (stored, expected) in enumerate(zip(stored_embedding, expected_avg)):
        assert abs(stored - expected) < 1e-4, f"Embedding mismatch at index {i}: {stored} != {expected}"


def test_topic_backfill_skips_sessions_without_record_embeddings(tmp_path):
    """Session with no record_embeddings should be skipped gracefully."""
    store = _make_store(tmp_path)
    store.conn.execute(
        "INSERT INTO sessions(id,org_id,workspace_id,app_id,user_id,source_type,source_plugin,ingested_at,session_summary) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("s1","default","default","default","default","cc","claude-code","2026-01-01T00:00:00Z","summary"),
    )
    store.conn.commit()

    sessions = store.get_sessions_without_topic_embedding()
    for sess in sessions:
        vecs = store.get_record_embeddings_for_session(sess["id"])
        if not vecs:
            continue  # should skip
        avg = np.mean(vecs, axis=0).tolist()
        store.upsert_topic_embedding(sess["id"], avg)

    # Nothing written — no record embeddings
    rows = store.conn.execute("SELECT session_id FROM session_topic_embeddings").fetchall()
    assert len(rows) == 0
