"""Round-trip and migration tests for evidence_text (Stage 1, trajectory_signals)."""
from __future__ import annotations


from agent_trace_signals.db.schema import open_db, migrate
from agent_trace_signals.models import Record, Session


def _mk_session():
    return Session(
        id="sess1",
        source_type="agent_trace",
        source_plugin="claude_code",
    )


def test_evidence_text_round_trip(store):
    s = _mk_session()
    rec = Record(
        id=Record.make_id(s.id, 0),
        session_id=s.id,
        chunk_index=0,
        chunk_text="USER: hi\n[TOOL: Bash(ls)]",
        evidence_text="[tool: Bash(command=ls)]\nfile1\nfile2\n\n",
    )
    store.write_session_batch(
        session=s,
        records=[rec],
        entities=[], occurrences=[], memories=[], memory_sources=[], edges=[],
        record_embeddings={}, session_embedding=None,
        entity_embeddings={}, occurrence_embeddings={}, memory_embeddings={},
    )
    row = store.conn.execute(
        "SELECT evidence_text FROM records WHERE id = ?", (rec.id,)
    ).fetchone()
    assert row is not None
    assert row[0] == "[tool: Bash(command=ls)]\nfile1\nfile2\n\n"


def test_migrate_adds_evidence_text_to_legacy_records_table():
    """Open a DB created without evidence_text, run migrate(), confirm column appears
    and existing rows get default ''."""
    conn = open_db(":memory:")
    # Simulate legacy DDL (no evidence_text)
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL DEFAULT 'default',
            workspace_id TEXT NOT NULL DEFAULT 'default',
            app_id TEXT NOT NULL DEFAULT 'default',
            user_id TEXT NOT NULL DEFAULT 'default',
            source_type TEXT NOT NULL,
            source_plugin TEXT NOT NULL,
            session_timestamp TEXT,
            ingested_at TEXT NOT NULL,
            session_summary TEXT NOT NULL DEFAULT '',
            embedding_text TEXT NOT NULL DEFAULT '',
            raw_facets TEXT NOT NULL DEFAULT '{}',
            tags TEXT,
            metadata TEXT,
            chunk_count INTEGER DEFAULT 0,
            cluster_id TEXT,
            analytics_run_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE records (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            chunk_summary TEXT,
            embedding_text TEXT NOT NULL DEFAULT '',
            token_count INTEGER,
            span_start TEXT,
            span_end TEXT
        )
    """)
    # Minimum tables migrate() expects (entities gate)
    conn.execute("""
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL DEFAULT 'default',
            workspace_id TEXT NOT NULL DEFAULT 'default',
            canonical_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            degree_count INTEGER DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            source_diversity TEXT,
            entity_profile TEXT,
            memory_promoted TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO records (id, session_id, chunk_index, chunk_text) VALUES (?,?,?,?)",
        ("r1", "s1", 0, "legacy"),
    )
    conn.commit()

    cols_before = {r[1] for r in conn.execute("PRAGMA table_info(records)").fetchall()}
    assert "evidence_text" not in cols_before

    migrate(conn)

    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(records)").fetchall()}
    assert "evidence_text" in cols_after
    row = conn.execute("SELECT chunk_text, evidence_text FROM records WHERE id='r1'").fetchone()
    assert row[0] == "legacy"  # no data loss
    assert row[1] == ""        # default value
