"""SQLite DDL. All tables + vec0 virtual tables + FTS5 tables in one file."""

import sqlite3
import sqlite_vec

STRUCTURED_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id               TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL DEFAULT 'default',
    workspace_id     TEXT NOT NULL DEFAULT 'default',
    app_id           TEXT NOT NULL DEFAULT 'default',
    user_id          TEXT NOT NULL DEFAULT 'default',
    source_type      TEXT NOT NULL,
    source_plugin    TEXT NOT NULL,
    session_timestamp TEXT,
    ingested_at      TEXT NOT NULL,
    session_summary  TEXT NOT NULL DEFAULT '',
    embedding_text   TEXT NOT NULL DEFAULT '',
    raw_facets       TEXT NOT NULL DEFAULT '{}',
    tags             TEXT,
    metadata         TEXT,
    turn_descriptors TEXT,
    chunk_count      INTEGER DEFAULT 0,
    cluster_id       TEXT,
    analytics_run_id TEXT,
    session_type     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_org_ws   ON sessions(org_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_sessions_plugin   ON sessions(source_plugin);
CREATE INDEX IF NOT EXISTS idx_sessions_ts       ON sessions(session_timestamp);

CREATE TABLE IF NOT EXISTS records (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES sessions(id),
    chunk_index    INTEGER NOT NULL,
    chunk_text     TEXT NOT NULL,
    chunk_summary  TEXT,
    evidence_text  TEXT NOT NULL DEFAULT '',
    embedding_text TEXT NOT NULL DEFAULT '',
    token_count    INTEGER,
    span_start     TEXT,
    span_end       TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_session ON records(session_id);
CREATE INDEX IF NOT EXISTS idx_records_session_idx ON records(session_id, chunk_index);

CREATE TABLE IF NOT EXISTS entities (
    id               TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL DEFAULT 'default',
    workspace_id     TEXT NOT NULL DEFAULT 'default',
    canonical_name   TEXT NOT NULL,
    entity_type      TEXT NOT NULL,
    degree_count     INTEGER DEFAULT 0,
    confidence       TEXT NOT NULL DEFAULT 'raw',
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    source_diversity TEXT,
    entity_profile   TEXT,
    memory_promoted  TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_org_type ON entities(org_id, workspace_id, entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_degree   ON entities(degree_count);
CREATE INDEX IF NOT EXISTS idx_entities_confidence ON entities(confidence);

CREATE TABLE IF NOT EXISTS occurrences (
    id           TEXT PRIMARY KEY,
    record_id    TEXT NOT NULL REFERENCES records(id),
    session_id   TEXT NOT NULL REFERENCES sessions(id),
    entity_id    TEXT NOT NULL REFERENCES entities(id),
    role         TEXT NOT NULL,
    mention_text TEXT NOT NULL,
    context_text TEXT NOT NULL,
    span_start   TEXT,
    span_end     TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_occ_entity  ON occurrences(entity_id);
CREATE INDEX IF NOT EXISTS idx_occ_session ON occurrences(session_id);
CREATE INDEX IF NOT EXISTS idx_occ_record  ON occurrences(record_id);

CREATE TABLE IF NOT EXISTS session_graph_edges (
    source_session_id TEXT NOT NULL REFERENCES sessions(id),
    target_session_id TEXT NOT NULL REFERENCES sessions(id),
    edge_type         TEXT NOT NULL,
    via_entity_id     TEXT NOT NULL DEFAULT '',
    weight            REAL DEFAULT 1.0,
    direction         TEXT DEFAULT 'undirected',
    created_by        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (source_session_id, target_session_id, edge_type, via_entity_id)
);

CREATE TABLE IF NOT EXISTS memories (
    id                  TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL DEFAULT 'default',
    workspace_id        TEXT NOT NULL DEFAULT 'default',
    app_id              TEXT NOT NULL DEFAULT 'default',
    memory_type         TEXT NOT NULL CHECK(memory_type IN (
        'episodic','procedural','preference',
        'pattern_strategy','pattern_recovery','pattern_inefficiency',
        'cluster'
    )),
    extraction_method   TEXT NOT NULL,
    content             TEXT NOT NULL,
    status              TEXT,
    evidence_count      INTEGER DEFAULT 1,
    first_observed      TEXT,
    last_observed       TEXT,
    tags                TEXT,
    conflict_status     TEXT DEFAULT 'none',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    created_by_pipeline TEXT NOT NULL,
    analytics_run_id    TEXT,
    entity_id           TEXT,
    access_count        INTEGER DEFAULT 0,
    last_accessed_at    TEXT,
    metadata            TEXT
);

CREATE TABLE IF NOT EXISTS memory_sources (
    memory_id       TEXT NOT NULL REFERENCES memories(id),
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    record_id       TEXT,
    occurrence_id   TEXT,
    relevance_score REAL DEFAULT 1.0,
    span_hint       TEXT,
    PRIMARY KEY (memory_id, session_id)
);

CREATE TABLE IF NOT EXISTS memory_cluster_members (
    cluster_memory_id  TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    member_memory_id   TEXT NOT NULL,
    member_memory_type TEXT NOT NULL,
    PRIMARY KEY (cluster_memory_id, member_memory_id)
);
CREATE INDEX IF NOT EXISTS idx_cluster_members_cluster ON memory_cluster_members(cluster_memory_id);

CREATE TABLE IF NOT EXISTS session_type_labels (
    session_type TEXT PRIMARY KEY,
    label        TEXT NOT NULL,
    description  TEXT NOT NULL,
    n_sessions   INTEGER NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conflicts (
    id               TEXT PRIMARY KEY,
    memory_id_a      TEXT NOT NULL REFERENCES memories(id),
    memory_id_b      TEXT NOT NULL REFERENCES memories(id),
    conflict_type    TEXT NOT NULL,
    detected_at      TEXT NOT NULL,
    resolution       TEXT,
    resolved_at      TEXT,
    resolver         TEXT,
    analytics_run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clusters (
    id               TEXT PRIMARY KEY,
    analytics_run_id TEXT NOT NULL,
    label            TEXT NOT NULL,
    description      TEXT,
    level            INTEGER NOT NULL,
    parent_id        TEXT,
    member_count     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cluster_edges (
    source_cluster_id TEXT NOT NULL,
    target_cluster_id TEXT NOT NULL,
    edge_type         TEXT NOT NULL,
    weight            REAL NOT NULL,
    direction         TEXT NOT NULL,
    analytics_run_id  TEXT NOT NULL,
    PRIMARY KEY (source_cluster_id, target_cluster_id, edge_type, analytics_run_id)
);

CREATE TABLE IF NOT EXISTS analytics_runs (
    id              TEXT PRIMARY KEY,
    pipeline        TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    session_count   INTEGER,
    memory_count    INTEGER,
    cluster_count   INTEGER,
    conflict_count  INTEGER,
    k_value         INTEGER,
    embedding_model TEXT NOT NULL,
    labeler_model   TEXT
);

CREATE TABLE IF NOT EXISTS session_inflections (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES sessions(id),
    inflection_type     TEXT NOT NULL,
    precipitating_turn  INTEGER NOT NULL,
    entry_turn          INTEGER NOT NULL,
    exit_turn           INTEGER,
    signal_strength     REAL NOT NULL,
    dominant_feature    TEXT NOT NULL,
    signal_detail       TEXT,
    detected_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inflections_session ON session_inflections(session_id);

CREATE TABLE IF NOT EXISTS step_scores (
    session_id           TEXT NOT NULL REFERENCES sessions(id),
    exchange_idx         INTEGER NOT NULL,
    evidence_supports    REAL NOT NULL,
    progress_vector      TEXT NOT NULL,
    cost_vector          TEXT NOT NULL,
    agent_action_summary TEXT,
    features             TEXT NOT NULL DEFAULT '{}',
    observer_model       TEXT,
    scored_at            TEXT NOT NULL,
    PRIMARY KEY (session_id, exchange_idx)
);
CREATE INDEX IF NOT EXISTS idx_step_scores_session ON step_scores(session_id);
CREATE INDEX IF NOT EXISTS idx_step_scores_low     ON step_scores(evidence_supports);

CREATE TABLE IF NOT EXISTS critical_steps (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    exchange_idx    INTEGER NOT NULL,
    tag             TEXT NOT NULL,
    failure_mode    TEXT,
    delta           REAL NOT NULL,
    score           REAL NOT NULL,
    features        TEXT NOT NULL DEFAULT '{}',
    detected_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_critical_steps_session ON critical_steps(session_id);
CREATE INDEX IF NOT EXISTS idx_critical_steps_tag     ON critical_steps(tag);

CREATE TABLE IF NOT EXISTS ingestion_state (
    id               TEXT PRIMARY KEY,
    abs_path         TEXT NOT NULL UNIQUE,
    file_type        TEXT NOT NULL,
    content_hash     TEXT NOT NULL,
    session_id       TEXT,
    last_seen_at     TEXT NOT NULL,
    last_ingested_at TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    error            TEXT,
    metadata         TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_status ON ingestion_state(status);

CREATE TABLE IF NOT EXISTS session_metadata (
    session_id               TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    dominant_model           TEXT,
    models_used              TEXT,
    entrypoint               TEXT,
    permission_mode          TEXT,
    user_type                TEXT,
    is_sidechain             INTEGER DEFAULT 0,
    cc_version               TEXT,
    user_turn_count          INTEGER DEFAULT 0,
    assistant_turn_count     INTEGER DEFAULT 0,
    tool_call_count          INTEGER DEFAULT 0,
    start_time               TEXT,
    end_time                 TEXT,
    duration_ms              INTEGER,
    total_input_tokens       INTEGER DEFAULT 0,
    total_output_tokens      INTEGER DEFAULT 0,
    total_cache_read_tokens  INTEGER DEFAULT 0,
    total_cache_creation_tokens INTEGER DEFAULT 0,
    total_thought_tokens     INTEGER DEFAULT 0,
    cwd                      TEXT,
    git_branch               TEXT
);

CREATE TABLE IF NOT EXISTS memory_retrievals (
    id              TEXT PRIMARY KEY,
    session_id      TEXT REFERENCES sessions(id),
    segment_id      TEXT,
    exchange_idx    INTEGER,
    memory_id       TEXT NOT NULL REFERENCES memories(id),
    query_text      TEXT,
    query_features  TEXT NOT NULL DEFAULT '{}',
    relevance_rank  INTEGER NOT NULL,
    relevance_score REAL,
    surfaced        INTEGER NOT NULL DEFAULT 1,
    retrieved_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_ret_session ON memory_retrievals(session_id);
CREATE INDEX IF NOT EXISTS idx_mem_ret_memory  ON memory_retrievals(memory_id);
CREATE INDEX IF NOT EXISTS idx_mem_ret_time    ON memory_retrievals(retrieved_at);
"""

FTS5_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
    USING fts5(memory_id UNINDEXED, content, tokenize='porter ascii');

CREATE VIRTUAL TABLE IF NOT EXISTS records_fts
    USING fts5(record_id UNINDEXED, chunk_text, chunk_summary, tokenize='porter ascii');

CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts
    USING fts5(session_id UNINDEXED, session_summary, workspace_id, tokenize='porter ascii');
"""

VECTOR_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS session_embeddings
    USING vec0(session_id TEXT PRIMARY KEY, embedding FLOAT[768]);

CREATE VIRTUAL TABLE IF NOT EXISTS session_structural_embeddings
    USING vec0(session_id TEXT PRIMARY KEY, embedding FLOAT[768]);

CREATE VIRTUAL TABLE IF NOT EXISTS session_topic_embeddings
    USING vec0(session_id TEXT PRIMARY KEY, embedding FLOAT[768]);

CREATE VIRTUAL TABLE IF NOT EXISTS record_embeddings
    USING vec0(record_id TEXT PRIMARY KEY, embedding FLOAT[768]);

CREATE VIRTUAL TABLE IF NOT EXISTS entity_embeddings
    USING vec0(entity_id TEXT PRIMARY KEY, embedding FLOAT[768]);

CREATE VIRTUAL TABLE IF NOT EXISTS occurrence_embeddings
    USING vec0(occurrence_id TEXT PRIMARY KEY, embedding FLOAT[768]);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings
    USING vec0(memory_id TEXT PRIMARY KEY, embedding FLOAT[768]);

CREATE VIRTUAL TABLE IF NOT EXISTS cluster_embeddings
    USING vec0(cluster_id TEXT PRIMARY KEY, embedding FLOAT[768]);
"""


def open_db(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with sqlite-vec loaded and WAL mode enabled."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_all(conn: sqlite3.Connection) -> None:
    """Create all tables, indexes, FTS5 tables, and vector tables."""
    for stmt in STRUCTURED_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    for stmt in FTS5_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    for stmt in VECTOR_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


def migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after initial schema creation. Safe to call on any DB.

    Must be called BEFORE create_all() on existing DBs so that indexes on new
    columns don't fail. Returns immediately if the entities table doesn't exist yet.
    """
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='entities'"
    ).fetchone()
    if not table_exists:
        return  # new DB — create_all() will create with the full schema
    existing = {r[1] for r in conn.execute("PRAGMA table_info(entities)").fetchall()}
    if "confidence" not in existing:
        conn.execute(
            "ALTER TABLE entities ADD COLUMN confidence TEXT NOT NULL DEFAULT 'raw'"
        )
        conn.commit()

    # Recreate session_graph_edges with via_entity_id in PRIMARY KEY
    edge_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='session_graph_edges'"
    ).fetchone()
    if edge_sql and "via_entity_id" not in edge_sql[0].split("PRIMARY KEY")[1]:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_graph_edges_new (
                source_session_id TEXT NOT NULL REFERENCES sessions(id),
                target_session_id TEXT NOT NULL REFERENCES sessions(id),
                edge_type         TEXT NOT NULL,
                via_entity_id     TEXT NOT NULL DEFAULT '',
                weight            REAL DEFAULT 1.0,
                direction         TEXT DEFAULT 'undirected',
                created_by        TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                PRIMARY KEY (source_session_id, target_session_id, edge_type, via_entity_id)
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO session_graph_edges_new
            SELECT source_session_id, target_session_id, edge_type,
                   COALESCE(via_entity_id, ''), weight, direction, created_by, created_at
            FROM session_graph_edges
        """)
        conn.execute("DROP TABLE session_graph_edges")
        conn.execute("ALTER TABLE session_graph_edges_new RENAME TO session_graph_edges")
        conn.commit()

    # Add entity_id to memories for proper attribution tracking
    mem_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
    ).fetchone()
    if mem_table:
        mem_cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        if "entity_id" not in mem_cols:
            conn.execute("ALTER TABLE memories ADD COLUMN entity_id TEXT")
            conn.commit()
        if "action_orientation" not in mem_cols:
            conn.execute("ALTER TABLE memories ADD COLUMN action_orientation TEXT")
            conn.commit()

    # Create session_metadata table if missing
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='session_metadata'"
    ).fetchone():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_metadata (
                session_id               TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                dominant_model           TEXT,
                models_used              TEXT,
                entrypoint               TEXT,
                permission_mode          TEXT,
                user_type                TEXT,
                is_sidechain             INTEGER DEFAULT 0,
                cc_version               TEXT,
                user_turn_count          INTEGER DEFAULT 0,
                assistant_turn_count     INTEGER DEFAULT 0,
                tool_call_count          INTEGER DEFAULT 0,
                start_time               TEXT,
                end_time                 TEXT,
                duration_ms              INTEGER,
                total_input_tokens       INTEGER DEFAULT 0,
                total_output_tokens      INTEGER DEFAULT 0,
                total_cache_read_tokens  INTEGER DEFAULT 0,
                total_cache_creation_tokens INTEGER DEFAULT 0,
                total_thought_tokens     INTEGER DEFAULT 0,
                cwd                      TEXT,
                git_branch               TEXT
            )
        """)
        conn.commit()

    # Create session_inflections table if missing
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='session_inflections'"
    ).fetchone():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_inflections (
                id                  TEXT PRIMARY KEY,
                session_id          TEXT NOT NULL REFERENCES sessions(id),
                inflection_type     TEXT NOT NULL,
                precipitating_turn  INTEGER NOT NULL,
                entry_turn          INTEGER NOT NULL,
                exit_turn           INTEGER,
                signal_strength     REAL NOT NULL,
                dominant_feature    TEXT NOT NULL,
                signal_detail       TEXT,
                detected_at         TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inflections_session ON session_inflections(session_id)"
        )
        conn.commit()

    # Add turn_descriptors column to sessions if missing
    if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
    ).fetchone():
        sessions_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "turn_descriptors" not in sessions_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN turn_descriptors TEXT")
            conn.commit()
        # Add session_type column to sessions if missing
        if "session_type" not in sessions_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN session_type TEXT")
            conn.commit()

    # Create sessions_fts and backfill from existing sessions if missing
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions_fts'"
    ).fetchone():
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts
                USING fts5(session_id UNINDEXED, session_summary, workspace_id, tokenize='porter ascii')
        """)
        sessions_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        ).fetchone()
        if sessions_exists:
            conn.execute("""
                INSERT INTO sessions_fts(session_id, session_summary, workspace_id)
                SELECT id, COALESCE(session_summary, ''), COALESCE(workspace_id, '') FROM sessions
            """)
        conn.commit()

    # Add evidence_text to records if missing
    records_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='records'"
    ).fetchone()
    if records_table:
        records_cols = {r[1] for r in conn.execute("PRAGMA table_info(records)").fetchall()}
        if "evidence_text" not in records_cols:
            conn.execute(
                "ALTER TABLE records ADD COLUMN evidence_text TEXT NOT NULL DEFAULT ''"
            )
            conn.commit()

    # Rebuild records_fts if chunk_summary column is missing (added to index chunk summaries)
    records_fts_info = conn.execute("PRAGMA table_info(records_fts)").fetchall()
    records_fts_cols = {row[1] for row in records_fts_info}
    if records_fts_info and "chunk_summary" not in records_fts_cols:
        conn.execute("DROP TABLE records_fts")
        conn.execute("""
            CREATE VIRTUAL TABLE records_fts
                USING fts5(record_id UNINDEXED, chunk_text, chunk_summary, tokenize='porter ascii')
        """)
        conn.execute("""
            INSERT INTO records_fts(record_id, chunk_text, chunk_summary)
            SELECT id, COALESCE(chunk_text, ''), COALESCE(chunk_summary, '') FROM records
        """)
        conn.commit()

    # Create memory_retrievals table if missing
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_retrievals'"
    ).fetchone():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_retrievals (
                id              TEXT PRIMARY KEY,
                session_id      TEXT REFERENCES sessions(id),
                segment_id      TEXT,
                exchange_idx    INTEGER,
                memory_id       TEXT NOT NULL REFERENCES memories(id),
                query_text      TEXT,
                query_features  TEXT NOT NULL DEFAULT '{}',
                relevance_rank  INTEGER NOT NULL,
                relevance_score REAL,
                surfaced        INTEGER NOT NULL DEFAULT 1,
                retrieved_at    TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_ret_session ON memory_retrievals(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_ret_memory  ON memory_retrievals(memory_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_ret_time    ON memory_retrievals(retrieved_at)")
        conn.commit()

    # Create step_scores table if missing (Stage 2 — per-step observer scoring)
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='step_scores'"
    ).fetchone():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS step_scores (
                session_id           TEXT NOT NULL REFERENCES sessions(id),
                exchange_idx         INTEGER NOT NULL,
                evidence_supports    REAL NOT NULL,
                progress_vector      TEXT NOT NULL,
                cost_vector          TEXT NOT NULL,
                agent_action_summary TEXT,
                features             TEXT NOT NULL DEFAULT '{}',
                observer_model       TEXT,
                scored_at            TEXT NOT NULL,
                PRIMARY KEY (session_id, exchange_idx)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_step_scores_session ON step_scores(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_step_scores_low     ON step_scores(evidence_supports)")
        conn.commit()

    # Create critical_steps table if missing (Stage 3 — critical step detection)
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='critical_steps'"
    ).fetchone():
        conn.execute("""
            CREATE TABLE IF NOT EXISTS critical_steps (
                id              TEXT PRIMARY KEY,
                session_id      TEXT NOT NULL REFERENCES sessions(id),
                exchange_idx    INTEGER NOT NULL,
                tag             TEXT NOT NULL,
                failure_mode    TEXT,
                delta           REAL NOT NULL,
                score           REAL NOT NULL,
                features        TEXT NOT NULL DEFAULT '{}',
                detected_at     TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_critical_steps_session ON critical_steps(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_critical_steps_tag     ON critical_steps(tag)")
        conn.commit()

    # Backfill memories_fts if empty (analytics light memories were not indexed before this fix)
    memories_fts_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    ).fetchone()
    memories_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
    ).fetchone()
    if memories_fts_exists and memories_exists:
        fts_count = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        mem_count  = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if mem_count > 0 and fts_count < mem_count:
            conn.execute("DELETE FROM memories_fts")
            conn.execute("INSERT INTO memories_fts(memory_id, content) SELECT id, COALESCE(content,'') FROM memories")
            conn.commit()
