"""Test pattern memory types and schema constraints."""

import sqlite3
from agent_trace_signals.db.schema import STRUCTURED_DDL, FTS5_DDL


def test_pattern_memory_types_in_models():
    from agent_trace_signals.models import PATTERN_TYPES, MEMORY_TYPES
    assert "pattern_strategy" in PATTERN_TYPES
    assert "pattern_recovery" in PATTERN_TYPES
    assert "pattern_inefficiency" in PATTERN_TYPES
    assert "pattern_decision" not in PATTERN_TYPES
    assert PATTERN_TYPES.issubset(MEMORY_TYPES)


def test_memory_insert_with_pattern_type():
    """Pattern memory types can be written to the DB without constraint errors."""
    db = sqlite3.connect(":memory:")

    # Create schema
    for stmt in STRUCTURED_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            db.execute(stmt)
    for stmt in FTS5_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            db.execute(stmt)
    db.commit()

    # Insert pattern memory type
    db.execute(
        "INSERT INTO memories (id, org_id, workspace_id, app_id, memory_type, "
        "extraction_method, content, first_observed, last_observed, created_by_pipeline, "
        "created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("m1","o","w","a","pattern_strategy","d_combined","test content",
         "2026-01-01","2026-01-01","ingestion","2026-01-01T00:00:00","2026-01-01T00:00:00"),
    )
    db.commit()
    row = db.execute("SELECT memory_type FROM memories WHERE id='m1'").fetchone()
    assert row[0] == "pattern_strategy"
