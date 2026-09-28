from agent_trace_signals.db.schema import migrate
from agent_trace_signals.models import Entity
from agent_trace_signals.config import PipelineConfig


def test_entity_default_confidence():
    e = Entity(
        id="abc123",
        canonical_name="Python",
        entity_type="technology",
        first_seen="2026-01-01",
        last_seen="2026-01-01",
    )
    assert e.confidence == "raw"


def test_entity_confidence_field_persists(store):
    e = Entity(
        id="abc123",
        canonical_name="Python",
        entity_type="technology",
        confidence="session",
        first_seen="2026-01-01",
        last_seen="2026-01-01",
    )
    store.conn.execute(
        """INSERT INTO entities
           (id,org_id,workspace_id,canonical_name,entity_type,degree_count,
            confidence,first_seen,last_seen,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (e.id, e.org_id, e.workspace_id, e.canonical_name, e.entity_type,
         e.degree_count, e.confidence, e.first_seen, e.last_seen,
         e.created_at, e.updated_at),
    )
    row = store.conn.execute(
        "SELECT confidence FROM entities WHERE id=?", (e.id,)
    ).fetchone()
    assert row[0] == "session"


def test_schema_has_confidence_column(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(entities)").fetchall()]
    assert "confidence" in cols


def test_migrate_adds_column_to_existing_db():
    import sqlite3
    import sqlite_vec
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("""CREATE TABLE entities (
        id TEXT PRIMARY KEY, org_id TEXT NOT NULL DEFAULT 'default',
        workspace_id TEXT NOT NULL DEFAULT 'default',
        canonical_name TEXT NOT NULL, entity_type TEXT NOT NULL,
        degree_count INTEGER DEFAULT 0, first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    conn.commit()
    cols_before = [r[1] for r in conn.execute("PRAGMA table_info(entities)").fetchall()]
    assert "confidence" not in cols_before
    migrate(conn)
    cols_after = [r[1] for r in conn.execute("PRAGMA table_info(entities)").fetchall()]
    assert "confidence" in cols_after


def test_config_session_promotion_min_chunks():
    cfg = PipelineConfig()
    assert cfg.session_promotion_min_chunks == 2
    cfg2 = PipelineConfig(session_promotion_min_chunks=3)
    assert cfg2.session_promotion_min_chunks == 3


def _insert_entity(store, entity_id, name, etype, confidence):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    store.conn.execute(
        """INSERT INTO entities
           (id,org_id,workspace_id,canonical_name,entity_type,degree_count,
            confidence,first_seen,last_seen,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (entity_id, "default", "default", name, etype, 1, confidence, now, now, now, now),
    )
    store.conn.commit()


def test_upsert_upgrades_confidence(store):
    e_raw = Entity(
        id="eid1", canonical_name="Python", entity_type="technology",
        confidence="raw", first_seen="2026-01-01", last_seen="2026-01-01",
    )
    e_session = Entity(
        id="eid1", canonical_name="Python", entity_type="technology",
        confidence="session", first_seen="2026-01-01", last_seen="2026-01-02",
    )
    store.upsert_entity(e_raw)
    store.upsert_entity(e_session)
    row = store.conn.execute("SELECT confidence FROM entities WHERE id='eid1'").fetchone()
    assert row[0] == "session"


def test_upsert_no_downgrade(store):
    e_session = Entity(
        id="eid2", canonical_name="Bash", entity_type="tool",
        confidence="session", first_seen="2026-01-01", last_seen="2026-01-01",
    )
    e_raw = Entity(
        id="eid2", canonical_name="Bash", entity_type="tool",
        confidence="raw", first_seen="2026-01-01", last_seen="2026-01-02",
    )
    store.upsert_entity(e_session)
    store.upsert_entity(e_raw)
    row = store.conn.execute("SELECT confidence FROM entities WHERE id='eid2'").fetchone()
    assert row[0] == "session"


def test_upsert_corpus_never_downgraded(store):
    e_corpus = Entity(
        id="eid3", canonical_name="SQLite", entity_type="technology",
        confidence="corpus", first_seen="2026-01-01", last_seen="2026-01-01",
    )
    e_llm = Entity(
        id="eid3", canonical_name="SQLite", entity_type="technology",
        confidence="llm_verified", first_seen="2026-01-01", last_seen="2026-01-02",
    )
    store.upsert_entity(e_corpus)
    store.upsert_entity(e_llm)
    row = store.conn.execute("SELECT confidence FROM entities WHERE id='eid3'").fetchone()
    assert row[0] == "corpus"
