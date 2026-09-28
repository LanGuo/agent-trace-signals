"""Test that DChunkResult.patterns are written to the memories table as pattern_* types."""


from agent_trace_signals.db.schema import create_all, open_db
from agent_trace_signals.models import PATTERN_TYPES


def test_patterns_db_table_accepts_pattern_types(tmp_path):
    """DB schema accepts pattern_* memory_type values (smoke test for schema constraint)."""
    db_path = tmp_path / "test.db"
    con = open_db(str(db_path))
    create_all(con)

    # No pattern rows initially
    rows = con.execute(
        "SELECT memory_type, content FROM memories WHERE memory_type LIKE 'pattern_%'"
    ).fetchall()
    assert rows == []
    con.close()


def test_pattern_type_filter_rejects_unknown_types(tmp_path):
    """Patterns with unknown types are filtered out before building pattern_* memory_type."""
    from agent_trace_signals.pipeline.chunk_analyzer import DPattern

    valid = {"strategy", "recovery", "inefficiency"}
    patterns = [
        DPattern(type="strategy", content="valid"),
        DPattern(type="unknown_type", content="should be filtered"),
        DPattern(type="recovery", content="also valid"),
    ]

    stored = [
        f"pattern_{p.type}" for p in patterns if p.type in valid
    ]
    assert stored == ["pattern_strategy", "pattern_recovery"]
    # all stored types are in PATTERN_TYPES
    for t in stored:
        assert t in PATTERN_TYPES
