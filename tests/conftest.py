import pytest
from agent_trace_signals.db.schema import open_db, create_all, migrate
from agent_trace_signals.db.store import SQLiteStore


@pytest.fixture
def db():
    conn = open_db(":memory:")
    create_all(conn)
    migrate(conn)
    return conn


@pytest.fixture
def store(db):
    return SQLiteStore(db)
