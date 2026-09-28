"""Tests for session_structural_embeddings vec table and session_type column."""

import sqlite3
import pytest


@pytest.fixture
def db():
    """Create an in-memory DB with full schema."""
    from agent_trace_signals.db.schema import create_all, migrate
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    # Load sqlite-vec
    import sqlite_vec
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    migrate(con)
    create_all(con)
    return con


def test_sessions_table_has_session_type_column(db):
    """Verify session_type column exists on sessions table."""
    cols = [r[1] for r in db.execute("PRAGMA table_info(sessions)").fetchall()]
    assert "session_type" in cols


def test_session_structural_embeddings_table_exists(db):
    """Verify session_structural_embeddings vec0 table exists."""
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "session_structural_embeddings" in tables


def test_upsert_structural_embedding(tmp_path):
    """Test upsert_structural_embedding method."""
    from agent_trace_signals.db.schema import create_all, migrate, open_db
    from agent_trace_signals.db.store import SQLiteStore

    db_path = tmp_path / "test.db"
    con = open_db(str(db_path))
    migrate(con)
    create_all(con)
    con.close()

    store = SQLiteStore(open_db(str(db_path)))
    store.upsert_structural_embedding("sess1", [0.1] * 768)
    # Should not raise
    store.conn.close()


def test_set_session_type(tmp_path):
    """Test set_session_type method."""
    from agent_trace_signals.db.schema import create_all, migrate, open_db
    from agent_trace_signals.db.store import SQLiteStore

    db_path = tmp_path / "test.db"
    con = open_db(str(db_path))
    migrate(con)
    create_all(con)

    # Insert a test session
    con.execute(
        "INSERT INTO sessions (id,org_id,workspace_id,app_id,source_type,source_plugin,"
        "session_timestamp,ingested_at,session_summary,embedding_text) "
        "VALUES ('s1','o','w','a','agent_trace','cc','2026-01-01','2026-01-01','','') "
    )
    con.commit()
    con.close()

    store = SQLiteStore(open_db(str(db_path)))
    store.set_session_type("s1", "tool_heavy")
    row = store.conn.execute("SELECT session_type FROM sessions WHERE id='s1'").fetchone()
    assert row[0] == "tool_heavy"
    store.conn.close()


def test_get_sessions_without_structural_embedding(tmp_path):
    """Test get_sessions_without_structural_embedding method."""
    from agent_trace_signals.db.schema import create_all, migrate, open_db
    from agent_trace_signals.db.store import SQLiteStore

    db_path = tmp_path / "test.db"
    con = open_db(str(db_path))
    migrate(con)
    create_all(con)

    # Insert two sessions
    con.execute(
        "INSERT INTO sessions (id,org_id,workspace_id,app_id,source_type,source_plugin,"
        "session_timestamp,ingested_at,session_summary,embedding_text) "
        "VALUES ('s1','o','w','a','agent_trace','cc','2026-01-01','2026-01-01','summary1','') "
    )
    con.execute(
        "INSERT INTO sessions (id,org_id,workspace_id,app_id,source_type,source_plugin,"
        "session_timestamp,ingested_at,session_summary,embedding_text) "
        "VALUES ('s2','o','w','a','agent_trace','cc','2026-01-01','2026-01-01','summary2','') "
    )
    con.commit()
    con.close()

    store = SQLiteStore(open_db(str(db_path)))

    # Both sessions should be missing embeddings
    results = store.get_sessions_without_structural_embedding()
    assert len(results) == 2
    assert any(r["id"] == "s1" for r in results)
    assert any(r["id"] == "s2" for r in results)

    # Add embedding for s1
    store.upsert_structural_embedding("s1", [0.1] * 768)

    # Now only s2 should be missing
    results = store.get_sessions_without_structural_embedding()
    assert len(results) == 1
    assert results[0]["id"] == "s2"
    assert results[0]["session_summary"] == "summary2"

    store.conn.close()
