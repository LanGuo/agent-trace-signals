"""Tests for eval0 confidence filtering."""
import json
from agent_trace_signals.eval.level0 import Level0Evaluator


def _insert_session(conn, session_id):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO sessions
           (id,org_id,workspace_id,source_type,source_plugin,ingested_at,
            session_summary,embedding_text,raw_facets,chunk_count)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (session_id, "default", "default", "agent_trace", "claude_code",
         now, "", "", "{}", 3),
    )


def _insert_record(conn, record_id, session_id, chunk_idx=0):
    conn.execute(
        """INSERT INTO records (id,session_id,chunk_index,chunk_text,embedding_text)
           VALUES (?,?,?,?,?)""",
        (record_id, session_id, chunk_idx, "chunk text", ""),
    )


def _insert_entity(conn, entity_id, name, etype, confidence):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO entities
           (id,org_id,workspace_id,canonical_name,entity_type,degree_count,
            confidence,first_seen,last_seen,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (entity_id, "default", "default", name, etype, 1, confidence, now, now, now, now),
    )


def _insert_occurrence(conn, occ_id, record_id, session_id, entity_id):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO occurrences
           (id,record_id,session_id,entity_id,role,mention_text,context_text,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (occ_id, record_id, session_id, entity_id, "subject", "text", "ctx", now),
    )


def test_eval_no_confidence_filter_counts_all(store, tmp_path):
    """Default (no filter) counts all extracted entities."""
    sid = "s" * 64
    _insert_session(store.conn, sid)
    _insert_record(store.conn, "r1", sid, 0)
    _insert_entity(store.conn, "e1", "Python", "technology", "raw")
    _insert_entity(store.conn, "e2", "SQLite", "technology", "session")
    _insert_occurrence(store.conn, "o1", "r1", sid, "e1")
    _insert_occurrence(store.conn, "o2", "r1", sid, "e2")
    store.conn.commit()

    ann_file = tmp_path / f"{sid}.json"
    ann_file.write_text(json.dumps({
        "session_id": sid, "source_file": "", "source_type": "agent_trace",
        "expected_entities": [
            {"canonical_name": "Python", "entity_type": "technology"},
            {"canonical_name": "SQLite", "entity_type": "technology"},
        ],
        "expected_occurrences": [], "expected_explicit_memories": [],
        "expected_structural_edges": [],
    }))

    evaluator = Level0Evaluator(store)
    report = evaluator.evaluate(ann_file, min_confidence="raw")
    assert report.entity_overall.true_positives == 2


def test_eval_session_confidence_filter_excludes_raw(store, tmp_path):
    """min_confidence='session' excludes 'raw' entities from extracted set."""
    sid = "t" * 64
    _insert_session(store.conn, sid)
    _insert_record(store.conn, "r2", sid, 0)
    _insert_entity(store.conn, "e3", "Python", "technology", "raw")
    _insert_entity(store.conn, "e4", "SQLite", "technology", "session")
    _insert_occurrence(store.conn, "o3", "r2", sid, "e3")
    _insert_occurrence(store.conn, "o4", "r2", sid, "e4")
    store.conn.commit()

    ann_file = tmp_path / f"{sid}.json"
    ann_file.write_text(json.dumps({
        "session_id": sid, "source_file": "", "source_type": "agent_trace",
        "expected_entities": [
            {"canonical_name": "Python", "entity_type": "technology"},
            {"canonical_name": "SQLite", "entity_type": "technology"},
        ],
        "expected_occurrences": [], "expected_explicit_memories": [],
        "expected_structural_edges": [],
    }))

    evaluator = Level0Evaluator(store)
    report = evaluator.evaluate(ann_file, min_confidence="session")
    # Only SQLite ('session') is in extracted set; Python ('raw') is excluded
    # Python is in expected but not extracted → FN; precision is 1.0 (SQLite matches)
    assert report.entity_overall.true_positives == 1
    assert report.entity_overall.false_negatives == 1
    assert report.entity_overall.false_positives == 0
