"""Tests for Level 1a evaluation — frequency memory quality."""

import json
import pytest
from datetime import datetime, timezone

from agent_trace_signals.db.schema import open_db, create_all, migrate
from agent_trace_signals.db.store import SQLiteStore
from agent_trace_signals.eval.level1a import Level1aEvaluator, Level1aReport


def _now():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def db():
    conn = open_db(":memory:")
    migrate(conn)
    create_all(conn)
    return conn


@pytest.fixture
def store(db):
    return SQLiteStore(db)


def _insert_session(conn, session_id):
    conn.execute(
        """INSERT INTO sessions
           (id,org_id,workspace_id,source_type,source_plugin,ingested_at,
            session_summary,embedding_text,raw_facets,chunk_count)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (session_id, "default", "default", "agent_trace", "claude_code",
         _now(), "", "", "{}", 3),
    )


def _insert_entity(conn, entity_id, name, etype, memory_promoted=None):
    conn.execute(
        """INSERT INTO entities
           (id,org_id,workspace_id,canonical_name,entity_type,degree_count,
            confidence,first_seen,last_seen,created_at,updated_at,memory_promoted)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (entity_id, "default", "default", name, etype, 3,
         "session", "2026-01-01", "2026-05-26", _now(), _now(), memory_promoted),
    )


def _insert_record(conn, record_id, session_id, chunk_index=0):
    conn.execute(
        """INSERT INTO records (id,session_id,chunk_index,chunk_text,embedding_text)
           VALUES (?,?,?,?,?)""",
        (record_id, session_id, chunk_index, "chunk text", ""),
    )


def _insert_occurrence(conn, occ_id, record_id, session_id, entity_id):
    conn.execute(
        """INSERT INTO occurrences
           (id,record_id,session_id,entity_id,role,mention_text,context_text,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (occ_id, record_id, session_id, entity_id, "subject", "mention", "context", _now()),
    )


def _insert_memory(conn, mem_id, extraction_method="frequency", memory_type="procedural",
                   content="test content", evidence_count=1, created_by="light_analytics"):
    conn.execute(
        """INSERT INTO memories
           (id,org_id,workspace_id,app_id,memory_type,extraction_method,content,
            evidence_count,created_at,updated_at,created_by_pipeline)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (mem_id, "default", "default", "default", memory_type, extraction_method,
         content, evidence_count, _now(), _now(), created_by),
    )


def _insert_memory_source(conn, memory_id, session_id):
    conn.execute(
        """INSERT INTO memory_sources (memory_id, session_id)
           VALUES (?, ?)""",
        (memory_id, session_id),
    )


# ============================================================================
# Mode A: Structural quality tests (no annotations needed)
# ============================================================================

def test_eval1a_structural_no_memories(store, tmp_path):
    """With empty DB and no annotation file, report has 0 memories and None for recall/fpr."""
    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate(tmp_path)

    assert report.total_frequency_memories == 0
    assert report.memories_by_type == {}
    assert report.entity_coverage == 0.0
    assert report.cross_session_memory_count == 0
    assert report.trivial_memory_count == 0
    assert report.annotation_recall is None
    assert report.annotation_fpr is None


def test_eval1a_structural_with_single_memory(store):
    """With one frequency memory, counts are correct."""
    _insert_memory(store.conn, "mem1", memory_type="procedural",
                   content="Python is used for testing frameworks across multiple projects worldwide")
    store.conn.commit()

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate("annotations")

    assert report.total_frequency_memories == 1
    assert report.memories_by_type.get("procedural") == 1
    assert report.cross_session_memory_count == 0  # evidence_count=1 by default
    assert report.trivial_memory_count == 0  # Content has 10 words, not trivial


def test_eval1a_structural_type_distribution(store):
    """Type counts are accurate across memory types."""
    _insert_memory(store.conn, "mem1", memory_type="procedural")
    _insert_memory(store.conn, "mem2", memory_type="preference")
    _insert_memory(store.conn, "mem3", memory_type="procedural")
    _insert_memory(store.conn, "mem4", memory_type="episodic")
    store.conn.commit()

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate("annotations")

    assert report.total_frequency_memories == 4
    assert report.memories_by_type.get("procedural") == 2
    assert report.memories_by_type.get("preference") == 1
    assert report.memories_by_type.get("episodic") == 1


def test_eval1a_structural_cross_session_count(store):
    """Memories with evidence_count >= 2 are counted."""
    _insert_memory(store.conn, "mem1", evidence_count=1)  # Single-session
    _insert_memory(store.conn, "mem2", evidence_count=2)  # Cross-session
    _insert_memory(store.conn, "mem3", evidence_count=3)  # Cross-session
    store.conn.commit()

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate("annotations")

    assert report.total_frequency_memories == 3
    assert report.cross_session_memory_count == 2


def test_eval1a_structural_trivial_detection(store):
    """Short content (< 10 words) is flagged as trivial."""
    _insert_memory(store.conn, "mem1", content="hello world")  # 2 words → trivial
    _insert_memory(store.conn, "mem2", content="Python is used for testing")  # 5 words → trivial
    _insert_memory(store.conn, "mem3", content="This is a longer memory that contains more than ten words in it")  # 13 words
    store.conn.commit()

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate("annotations")

    assert report.total_frequency_memories == 3
    assert report.trivial_memory_count == 2


def test_eval1a_structural_entity_coverage(store):
    """Coverage = promoted entities with >=1 frequency memory / total promoted."""
    # Create 2 promoted entities
    _insert_entity(store.conn, "e1", "Python", "technology", memory_promoted=_now())
    _insert_entity(store.conn, "e2", "pytest", "tool", memory_promoted=_now())

    # Create sessions and records
    _insert_session(store.conn, "sess1")
    _insert_session(store.conn, "sess2")
    _insert_record(store.conn, "r1", "sess1")
    _insert_record(store.conn, "r2", "sess2")

    # Create occurrences for e1 (links to both sessions)
    _insert_occurrence(store.conn, "o1", "r1", "sess1", "e1")
    _insert_occurrence(store.conn, "o2", "r2", "sess2", "e1")

    # Create frequency memory linked to e1
    _insert_memory(store.conn, "mem1", content="Python is used for testing")
    _insert_memory_source(store.conn, "mem1", "sess1")

    store.conn.commit()

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate("annotations")

    # Only 1 out of 2 promoted entities has a frequency memory
    assert report.entity_coverage == 0.5


def test_eval1a_structural_entity_coverage_zero(store):
    """If no promoted entities, coverage is 0.0."""
    _insert_entity(store.conn, "e1", "Python", "technology", memory_promoted=None)
    _insert_memory(store.conn, "mem1", content="Python is used")
    store.conn.commit()

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate("annotations")

    assert report.entity_coverage == 0.0


# ============================================================================
# Mode B: Recall and FP with annotation file
# ============================================================================

def test_eval1a_no_annotations_file(store, tmp_path):
    """Without annotation file, recall/FP are None."""
    _insert_memory(store.conn, "mem1")
    store.conn.commit()

    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate(str(annotations_dir))

    assert report.total_frequency_memories == 1
    assert report.annotation_recall is None
    assert report.annotation_fpr is None


def test_eval1a_recall_perfect_match(store, tmp_path):
    """Perfect match: one expected memory found."""
    # Set up entity and memory
    _insert_entity(store.conn, "e1", "pytest", "tool")
    _insert_session(store.conn, "sess1")
    _insert_record(store.conn, "r1", "sess1")
    _insert_occurrence(store.conn, "o1", "r1", "sess1", "e1")

    _insert_memory(store.conn, "mem1", memory_type="procedural",
                   content="pytest is used for test-driven development")
    _insert_memory_source(store.conn, "mem1", "sess1")
    store.conn.commit()

    # Create annotation
    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()
    ann_file = annotations_dir / "level1a.json"
    ann_file.write_text(json.dumps({
        "annotation_type": "level1a",
        "expected_memories": [
            {
                "entity_name": "pytest",
                "entity_type": "tool",
                "memory_type": "procedural",
                "content_hint": "test-driven development"
            }
        ]
    }))

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate(str(annotations_dir))

    assert report.annotation_recall == 1.0
    assert report.annotation_fpr == 0.0  # All memories matched


def test_eval1a_recall_partial_match(store, tmp_path):
    """Partial recall: 1 out of 2 expected memories found."""
    # Set up entities
    _insert_entity(store.conn, "e1", "pytest", "tool")
    _insert_entity(store.conn, "e2", "Django", "technology")

    _insert_session(store.conn, "sess1")
    _insert_record(store.conn, "r1", "sess1")
    _insert_record(store.conn, "r2", "sess1")

    _insert_occurrence(store.conn, "o1", "r1", "sess1", "e1")
    _insert_occurrence(store.conn, "o2", "r2", "sess1", "e2")

    # Only create memory for pytest
    _insert_memory(store.conn, "mem1", memory_type="procedural",
                   content="pytest is used for testing")
    _insert_memory_source(store.conn, "mem1", "sess1")
    store.conn.commit()

    # Expect both pytest and Django
    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()
    ann_file = annotations_dir / "level1a.json"
    ann_file.write_text(json.dumps({
        "annotation_type": "level1a",
        "expected_memories": [
            {
                "entity_name": "pytest",
                "entity_type": "tool",
                "memory_type": "procedural",
                "content_hint": "testing"
            },
            {
                "entity_name": "Django",
                "entity_type": "technology",
                "memory_type": "procedural",
                "content_hint": "web framework"
            }
        ]
    }))

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate(str(annotations_dir))

    assert report.annotation_recall == 0.5  # 1 found out of 2


def test_eval1a_fpr_with_unmatched_memories(store, tmp_path):
    """FP rate increases with unmatched memories in DB."""
    _insert_entity(store.conn, "e1", "pytest", "tool")
    _insert_session(store.conn, "sess1")
    _insert_record(store.conn, "r1", "sess1")
    _insert_occurrence(store.conn, "o1", "r1", "sess1", "e1")

    # Create 2 memories, only 1 will match annotation
    _insert_memory(store.conn, "mem1", memory_type="procedural",
                   content="pytest is used for testing")
    _insert_memory(store.conn, "mem2", memory_type="preference",
                   content="pytest is a testing framework")
    _insert_memory_source(store.conn, "mem1", "sess1")
    _insert_memory_source(store.conn, "mem2", "sess1")
    store.conn.commit()

    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()
    ann_file = annotations_dir / "level1a.json"
    ann_file.write_text(json.dumps({
        "annotation_type": "level1a",
        "expected_memories": [
            {
                "entity_name": "pytest",
                "entity_type": "tool",
                "memory_type": "procedural",
                "content_hint": "testing"
            }
        ]
    }))

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate(str(annotations_dir))

    assert report.annotation_recall == 1.0
    assert report.annotation_fpr == 0.5  # 1 unmatched out of 2


def test_eval1a_case_insensitive_entity_match(store, tmp_path):
    """Entity matching is case-insensitive."""
    _insert_entity(store.conn, "e1", "PyTest", "tool")
    _insert_session(store.conn, "sess1")
    _insert_record(store.conn, "r1", "sess1")
    _insert_occurrence(store.conn, "o1", "r1", "sess1", "e1")

    _insert_memory(store.conn, "mem1", memory_type="procedural",
                   content="pytest is used for testing")
    _insert_memory_source(store.conn, "mem1", "sess1")
    store.conn.commit()

    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()
    ann_file = annotations_dir / "level1a.json"
    ann_file.write_text(json.dumps({
        "annotation_type": "level1a",
        "expected_memories": [
            {
                "entity_name": "pytest",  # lowercase in annotation
                "entity_type": "tool",
                "memory_type": "procedural",
                "content_hint": "testing"
            }
        ]
    }))

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate(str(annotations_dir))

    assert report.annotation_recall == 1.0


def test_eval1a_case_insensitive_content_match(store, tmp_path):
    """Content hint matching is case-insensitive."""
    _insert_entity(store.conn, "e1", "pytest", "tool")
    _insert_session(store.conn, "sess1")
    _insert_record(store.conn, "r1", "sess1")
    _insert_occurrence(store.conn, "o1", "r1", "sess1", "e1")

    _insert_memory(store.conn, "mem1", memory_type="procedural",
                   content="PyTest IS USED FOR TESTING")
    _insert_memory_source(store.conn, "mem1", "sess1")
    store.conn.commit()

    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()
    ann_file = annotations_dir / "level1a.json"
    ann_file.write_text(json.dumps({
        "annotation_type": "level1a",
        "expected_memories": [
            {
                "entity_name": "pytest",
                "entity_type": "tool",
                "memory_type": "procedural",
                "content_hint": "testing"  # lowercase hint
            }
        ]
    }))

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate(str(annotations_dir))

    assert report.annotation_recall == 1.0


def test_eval1a_memory_type_filter(store, tmp_path):
    """Memory type in annotation is used to filter matches."""
    _insert_entity(store.conn, "e1", "pytest", "tool")
    _insert_session(store.conn, "sess1")
    _insert_record(store.conn, "r1", "sess1")
    _insert_occurrence(store.conn, "o1", "r1", "sess1", "e1")

    # Create two memories with different types but same content
    _insert_memory(store.conn, "mem1", memory_type="procedural",
                   content="pytest is used for testing")
    _insert_memory(store.conn, "mem2", memory_type="preference",
                   content="pytest is used for testing")
    _insert_memory_source(store.conn, "mem1", "sess1")
    _insert_memory_source(store.conn, "mem2", "sess1")
    store.conn.commit()

    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()
    ann_file = annotations_dir / "level1a.json"
    ann_file.write_text(json.dumps({
        "annotation_type": "level1a",
        "expected_memories": [
            {
                "entity_name": "pytest",
                "entity_type": "tool",
                "memory_type": "procedural",  # Only procedural should match
                "content_hint": "testing"
            }
        ]
    }))

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate(str(annotations_dir))

    # Should find the procedural one, not the semantic
    assert report.annotation_recall == 1.0


def test_eval1a_no_expected_memories_empty_annotation(store, tmp_path):
    """With empty expected_memories in annotation, recall=0, fpr=1."""
    _insert_memory(store.conn, "mem1")
    store.conn.commit()

    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()
    ann_file = annotations_dir / "level1a.json"
    ann_file.write_text(json.dumps({
        "annotation_type": "level1a",
        "expected_memories": []
    }))

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate(str(annotations_dir))

    assert report.annotation_recall == 0.0
    assert report.annotation_fpr == 1.0


def test_eval1a_passes_targets_true(store):
    """passes_targets() returns True when recall > 0.60 and fpr < 0.25."""
    report = Level1aReport(
        total_frequency_memories=10,
        annotation_recall=0.75,
        annotation_fpr=0.20
    )
    assert report.passes_targets() is True


def test_eval1a_passes_targets_false_low_recall(store):
    """passes_targets() returns False when recall <= 0.60."""
    report = Level1aReport(
        total_frequency_memories=10,
        annotation_recall=0.50,
        annotation_fpr=0.20
    )
    assert report.passes_targets() is False


def test_eval1a_passes_targets_false_high_fpr(store):
    """passes_targets() returns False when fpr >= 0.25."""
    report = Level1aReport(
        total_frequency_memories=10,
        annotation_recall=0.75,
        annotation_fpr=0.30
    )
    assert report.passes_targets() is False


def test_eval1a_passes_targets_none_without_annotations(store):
    """passes_targets() returns None when no annotations."""
    report = Level1aReport(
        total_frequency_memories=10,
        annotation_recall=None,
        annotation_fpr=None
    )
    assert report.passes_targets() is None


# ============================================================================
# CLI integration test
# ============================================================================

def test_eval1a_cli_command_exists():
    from click.testing import CliRunner
    from agent_trace_signals.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["eval1a", "--help"])
    assert result.exit_code == 0
    assert "frequency memory" in result.output.lower()


def test_eval1a_cli_no_crash_empty_db(tmp_path):
    """eval1a on empty DB doesn't crash."""
    from click.testing import CliRunner
    from agent_trace_signals.cli import cli

    runner = CliRunner()
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(cli, ["--db", db_path, "eval1a"])
    assert result.exit_code == 0
    assert "structural metrics" in result.output.lower()


def test_eval1a_cli_with_annotations(tmp_path):
    """eval1a reads annotations/level1a.json if present."""
    from click.testing import CliRunner
    from agent_trace_signals.cli import cli

    runner = CliRunner()
    db_path = str(tmp_path / "test.db")

    # Set up DB with one memory
    from agent_trace_signals.db.schema import open_db, create_all, migrate
    conn = open_db(db_path)
    migrate(conn)
    create_all(conn)
    conn.execute(
        """INSERT INTO memories
           (id,org_id,workspace_id,app_id,memory_type,extraction_method,content,
            evidence_count,created_at,updated_at,created_by_pipeline)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("mem1", "default", "default", "default", "procedural", "frequency",
         "pytest is used for testing", 1, _now(), _now(), "light_analytics"),
    )
    conn.commit()
    conn.close()

    # Create annotations
    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()
    ann_file = annotations_dir / "level1a.json"
    ann_file.write_text(json.dumps({
        "annotation_type": "level1a",
        "expected_memories": []
    }))

    result = runner.invoke(cli, ["--db", db_path, "eval1a",
                                 "--annotations", str(annotations_dir)])
    assert result.exit_code == 0
    assert "annotation-based metrics" in result.output.lower()
