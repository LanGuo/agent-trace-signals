"""Tests for annotate-memories store method, HTML generator, CLI, and eval update."""
import pytest
from agent_trace_signals.db.schema import open_db, create_all, migrate
from agent_trace_signals.db.store import SQLiteStore
from datetime import datetime, timezone


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


def _insert_entity(conn, entity_id, name, etype, degree_count=2, profile=""):
    conn.execute(
        """INSERT INTO entities
           (id,org_id,workspace_id,canonical_name,entity_type,degree_count,
            confidence,first_seen,last_seen,created_at,updated_at,entity_profile)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (entity_id, "default", "default", name, etype, degree_count,
         "session", "2026-01-01", "2026-05-27", _now(), _now(), profile),
    )


def _insert_memory(conn, memory_id, entity_id, mtype="procedural", content="content here is long enough"):
    conn.execute(
        """INSERT INTO memories
           (id,org_id,workspace_id,app_id,memory_type,extraction_method,
            content,evidence_count,created_at,updated_at,created_by_pipeline,entity_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (memory_id, "default", "default", "default", mtype, "frequency",
         content, 2, _now(), _now(), "light_analytics", entity_id),
    )


def test_get_frequency_memories_for_annotation_returns_memories(store):
    """Returns frequency memories with entity info attached."""
    _insert_entity(store.conn, "e1", "pytest", "technology", degree_count=2,
                   profile="pytest is used for testing")
    _insert_memory(store.conn, "m1", "e1", "procedural", "pytest is used with TDD consistently")
    _insert_memory(store.conn, "m2", "e1", "preference", "pytest supports parameterized test cases")
    store.conn.commit()

    rows = store.get_frequency_memories_for_annotation()
    assert len(rows) == 2
    assert all(r["entity_name"] == "pytest" for r in rows)
    assert all(r["entity_type"] == "technology" for r in rows)
    assert {r["memory_id"] for r in rows} == {"m1", "m2"}


def test_get_frequency_memories_for_annotation_excludes_non_frequency(store):
    """Explicit memories (extraction_method='explicit') are excluded."""
    _insert_entity(store.conn, "e1", "pytest", "technology")
    _insert_memory(store.conn, "m1", "e1")
    store.conn.execute(
        "INSERT INTO memories (id,org_id,workspace_id,app_id,memory_type,extraction_method,"
        "content,evidence_count,created_at,updated_at,created_by_pipeline) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("m2", "default", "default", "default", "procedural", "explicit",
         "explicit memory", 1, _now(), _now(), "ingestion"),
    )
    store.conn.commit()

    rows = store.get_frequency_memories_for_annotation()
    assert len(rows) == 1
    assert rows[0]["memory_id"] == "m1"


def test_get_frequency_memories_for_annotation_empty(store):
    """Returns empty list when no frequency memories exist."""
    rows = store.get_frequency_memories_for_annotation()
    assert rows == []


def test_generate_memory_html_returns_string(store):
    """generate() returns a non-empty HTML string."""
    from agent_trace_signals.annotation.memory_viewer import generate
    _insert_entity(store.conn, "e1", "pytest", "technology")
    _insert_memory(store.conn, "m1", "e1", "procedural", "pytest is used for TDD consistently")
    store.conn.commit()

    memories = store.get_frequency_memories_for_annotation()
    html = generate(memories, existing_annotation={})
    assert isinstance(html, str)
    assert len(html) > 100
    assert "pytest" in html
    assert "m1" in html


def test_generate_memory_html_pre_checks_labels(store):
    """Memories with existing labels show their label pre-selected in the HTML."""
    from agent_trace_signals.annotation.memory_viewer import generate
    _insert_entity(store.conn, "e1", "pytest", "technology")
    _insert_memory(store.conn, "m1", "e1", "procedural", "pytest is used for TDD consistently")
    store.conn.commit()

    memories = store.get_frequency_memories_for_annotation()
    existing = {
        "labeled_memories": [
            {"memory_id": "m1", "label": "correct", "entity_name": "pytest",
             "entity_type": "technology", "memory_type": "procedural",
             "content": "pytest is used for TDD consistently"}
        ]
    }
    html = generate(memories, existing_annotation=existing)
    assert '"m1"' in html
    assert '"correct"' in html


def test_generate_memory_html_empty_memories():
    """generate() handles empty memory list without crashing."""
    from agent_trace_signals.annotation.memory_viewer import generate
    html = generate([], existing_annotation={})
    assert "No frequency memories" in html


def test_generate_memory_html_preserves_expected_memories(store):
    """generate() includes existing expected_memories in the embedded JS so Save preserves them."""
    from agent_trace_signals.annotation.memory_viewer import generate
    _insert_entity(store.conn, "e1", "pytest", "technology")
    _insert_memory(store.conn, "m1", "e1", "procedural", "pytest is used for TDD consistently")
    store.conn.commit()

    memories = store.get_frequency_memories_for_annotation()
    existing = {
        "expected_memories": [
            {"entity_name": "pytest", "entity_type": "technology",
             "memory_type": "procedural", "content_hint": "TDD approach"}
        ],
        "labeled_memories": [],
    }
    html = generate(memories, existing_annotation=existing)
    # EXPECTED_MEMORIES must be embedded in the HTML so saveAnnotation can re-emit it
    assert "EXPECTED_MEMORIES" in html
    assert "TDD approach" in html


def test_annotate_memories_command_exists():
    from click.testing import CliRunner
    from agent_trace_signals.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["annotate-memories", "--help"])
    assert result.exit_code == 0
    assert "annotations" in result.output.lower()


def test_annotate_memories_creates_html(tmp_path):
    """annotate-memories writes an HTML file and prints its path."""
    from click.testing import CliRunner
    from agent_trace_signals.cli import cli
    from agent_trace_signals.db.schema import open_db, create_all, migrate
    from pathlib import Path
    from datetime import datetime, timezone

    def _now():
        return datetime.now(timezone.utc).isoformat()

    db_file = str(tmp_path / "test.db")
    conn = open_db(db_file)
    migrate(conn)
    create_all(conn)
    conn.execute(
        """INSERT INTO entities
           (id,org_id,workspace_id,canonical_name,entity_type,degree_count,
            confidence,first_seen,last_seen,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("e1", "default", "default", "pytest", "technology", 2,
         "session", "2026-01-01", "2026-05-27", _now(), _now()),
    )
    conn.execute(
        """INSERT INTO memories
           (id,org_id,workspace_id,app_id,memory_type,extraction_method,
            content,evidence_count,created_at,updated_at,created_by_pipeline,entity_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("m1", "default", "default", "default", "procedural", "frequency",
         "pytest is used for TDD consistently", 2, _now(), _now(), "light_analytics", "e1"),
    )
    conn.commit()

    runner = CliRunner()
    ann_dir = str(tmp_path / "annotations")
    result = runner.invoke(cli, ["--db", db_file, "annotate-memories", "--annotations", ann_dir])
    assert result.exit_code == 0, result.output
    html_files = list(Path(ann_dir).glob("*.html"))
    assert len(html_files) == 1
    html = html_files[0].read_text()
    assert "pytest" in html
    assert "m1" in html


def test_eval1a_uses_labeled_memories_for_fpr(tmp_path):
    """When labeled_memories present, FPR = incorrect / (correct + incorrect)."""
    from agent_trace_signals.db.schema import open_db, create_all, migrate
    from agent_trace_signals.db.store import SQLiteStore
    from agent_trace_signals.eval.level1a import Level1aEvaluator
    import json
    from datetime import datetime, timezone

    def _now():
        return datetime.now(timezone.utc).isoformat()

    conn = open_db(":memory:")
    migrate(conn)
    create_all(conn)
    store = SQLiteStore(conn)

    conn.execute(
        """INSERT INTO entities
           (id,org_id,workspace_id,canonical_name,entity_type,degree_count,
            confidence,first_seen,last_seen,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("e1", "default", "default", "pytest", "technology", 2,
         "session", "2026-01-01", "2026-05-27", _now(), _now()),
    )
    conn.execute(
        """INSERT INTO memories
           (id,org_id,workspace_id,app_id,memory_type,extraction_method,
            content,evidence_count,created_at,updated_at,created_by_pipeline,entity_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("m1", "default", "default", "default", "procedural", "frequency",
         "pytest is used with TDD consistently across sessions", 2, _now(), _now(), "light_analytics", "e1"),
    )
    conn.execute(
        """INSERT INTO memories
           (id,org_id,workspace_id,app_id,memory_type,extraction_method,
            content,evidence_count,created_at,updated_at,created_by_pipeline,entity_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("m2", "default", "default", "default", "preference", "frequency",
         "pytest supports fixtures and parameterized tests well", 2, _now(), _now(), "light_analytics", "e1"),
    )
    conn.commit()

    # 1 correct, 1 incorrect → FPR = 1/2 = 0.5
    ann = {
        "annotation_type": "level1a",
        "labeled_memories": [
            {"memory_id": "m1", "label": "correct", "entity_name": "pytest",
             "entity_type": "technology", "memory_type": "procedural",
             "content": "pytest is used with TDD consistently across sessions"},
            {"memory_id": "m2", "label": "incorrect", "entity_name": "pytest",
             "entity_type": "technology", "memory_type": "preference",
             "content": "pytest supports fixtures and parameterized tests well"},
        ],
    }
    ann_path = tmp_path / "level1a.json"
    ann_path.write_text(json.dumps(ann))

    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate(str(tmp_path))

    assert abs(report.annotation_fpr - 0.5) < 1e-9
