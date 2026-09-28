import sqlite3
import sqlite_vec
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


def test_session_topic_embeddings_table_exists(tmp_path):
    store = _make_store(tmp_path)
    # vec0 tables show up in sqlite_master as tables
    tables = [r[0] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "session_topic_embeddings" in tables


def test_upsert_and_get_topic_embedding(tmp_path):
    store = _make_store(tmp_path)
    store.conn.execute(
        "INSERT INTO sessions(id,org_id,workspace_id,app_id,user_id,source_type,source_plugin,ingested_at,session_summary) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("sess1","default","default","default","default","cc","claude-code","2026-01-01T00:00:00Z","summary"),
    )
    store.conn.commit()
    emb = [0.1] * 768
    store.upsert_topic_embedding("sess1", emb)
    rows = store.conn.execute("SELECT session_id FROM session_topic_embeddings").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "sess1"


def test_get_sessions_without_topic_embedding(tmp_path):
    store = _make_store(tmp_path)
    for sid in ("s1", "s2"):
        store.conn.execute(
            "INSERT INTO sessions(id,org_id,workspace_id,app_id,user_id,source_type,source_plugin,ingested_at,session_summary) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (sid,"default","default","default","default","cc","claude-code","2026-01-01T00:00:00Z","summary"),
        )
    store.conn.commit()
    store.upsert_topic_embedding("s1", [0.1] * 768)
    result = store.get_sessions_without_topic_embedding()
    ids = [r["id"] for r in result]
    assert "s2" in ids
    assert "s1" not in ids


def test_get_record_embeddings_for_session(tmp_path):
    store = _make_store(tmp_path)
    store.conn.execute(
        "INSERT INTO sessions(id,org_id,workspace_id,app_id,user_id,source_type,source_plugin,ingested_at,session_summary) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("sess1","default","default","default","default","cc","claude-code","2026-01-01T00:00:00Z","summary"),
    )
    store.conn.execute(
        "INSERT INTO records(id,session_id,chunk_index,chunk_text,evidence_text,embedding_text) VALUES(?,?,?,?,?,?)",
        ("r1","sess1",0,"chunk text","",""),
    )
    store.conn.commit()
    emb1 = [0.1] * 768
    store._vec_upsert("record_embeddings", "record_id", "r1", emb1)
    vecs = store.get_record_embeddings_for_session("sess1")
    assert len(vecs) == 1
    assert len(vecs[0]) == 768
    assert abs(vecs[0][0] - 0.1) < 1e-4
