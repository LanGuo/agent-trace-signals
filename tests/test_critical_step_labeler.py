"""Tests for Stage 4 critical-step labeler + precision report."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_trace_signals.annotation.critical_step_labeler import (
    SHIP_GATE_MIN_LABELED,
    compute_precision,
    generate_labels,
)
from agent_trace_signals.annotation.critical_step_viewer import generate as render_html
from agent_trace_signals.models import CriticalStep, StepScore


# ── helpers ────────────────────────────────────────────────────────────────


def _seed_session(store, sid="sess-A"):
    store.conn.execute(
        """INSERT OR REPLACE INTO sessions
           (id, source_type, source_plugin, ingested_at, session_summary,
            embedding_text, raw_facets)
           VALUES (?, 'agent_trace', 'claude_code', '2026-06-01', '', '', '{}')""",
        (sid,),
    )
    store.conn.commit()


def _add_step(store, sid, idx, score, pv=None, cv=None, summary="agent did stuff"):
    ss = StepScore(
        session_id=sid,
        exchange_idx=idx,
        evidence_supports=score,
        progress_vector=json.dumps(pv or {"delta_test": 0.0, "delta_scope": 0.0,
                                          "delta_patch": 0.0, "delta_info": 0.0}),
        cost_vector=json.dumps(cv or {"tokens": 0.3, "redundancy": 0.0}),
        agent_action_summary=summary,
        features=json.dumps({"tokens_bucket": "<2k"}),
    )
    store.insert_step_score(ss)


def _add_critical(store, sid, idx, tag, failure_mode=None, score=0.1, delta=-0.2):
    cs = CriticalStep(
        id=CriticalStep.make_id(sid, idx, tag),
        session_id=sid,
        exchange_idx=idx,
        tag=tag,
        failure_mode=failure_mode,
        delta=delta,
        score=score,
        features="{}",
    )
    store.insert_critical_steps([cs])


def _add_record(store, sid, idx, span_start, span_end, evidence_text=""):
    store.conn.execute(
        """INSERT INTO records (id, session_id, chunk_index, chunk_text,
           evidence_text, embedding_text, span_start, span_end)
           VALUES (?,?,?,?,?,?,?,?)""",
        (f"{sid}-r{idx}", sid, idx, "chunk", evidence_text, "", str(span_start), str(span_end)),
    )
    store.conn.commit()


def _db_path(store) -> str:
    # in-memory conn; for labeler tests we need a file-backed sqlite so a
    # separate connection can read it. Dump the schema + rows to a tmp file.
    return store._tmp_path  # set by fixture


@pytest.fixture
def file_store(tmp_path):
    """A SQLiteStore backed by a tmp file (labeler opens its own connection)."""
    from agent_trace_signals.db.schema import open_db, create_all, migrate
    from agent_trace_signals.db.store import SQLiteStore

    p = tmp_path / "test.db"
    conn = open_db(str(p))
    create_all(conn)
    migrate(conn)
    s = SQLiteStore(conn)
    s._tmp_path = str(p)
    return s


# ── generation ─────────────────────────────────────────────────────────────


def test_generate_labels_writes_records(file_store, tmp_path):
    sid = "sess-A"
    _seed_session(file_store, sid)
    _add_step(file_store, sid, 0, 0.5)
    _add_step(file_store, sid, 1, 0.1, summary="agent loop")
    _add_step(file_store, sid, 2, 0.9)
    _add_record(file_store, sid, 0, 0, 2, evidence_text="[user: do something] then tool calls...")
    _add_critical(file_store, sid, 1, "failure_critical", "evidence_thin", score=0.1, delta=-0.4)
    _add_critical(file_store, sid, 2, "success_critical", None, score=0.9, delta=0.8)

    out = tmp_path / "labels.json"
    n = generate_labels(db_path=file_store._tmp_path, output_path=str(out))
    assert n == 2

    payload = json.loads(out.read_text())
    assert payload["version"] == 1
    assert "thresholds" in payload
    assert len(payload["records"]) == 2

    r0 = payload["records"][0]
    assert r0["session_id"] == sid
    assert r0["tag"] in ("failure_critical", "success_critical")
    assert r0["label"]["is_true_positive"] is None
    assert "context" in r0
    # New per-exchange context structure (replaces old evidence_text_preview)
    ctx = r0["context"]
    assert "substantial_directive" in ctx
    assert "immediate_user_text" in ctx
    assert "this_exchange" in ctx
    assert isinstance(ctx["this_exchange"], dict)
    assert "raw_serialized" in ctx["this_exchange"]
    assert "tool_calls" in ctx["this_exchange"]


def test_generate_labels_preserves_existing(file_store, tmp_path):
    sid = "sess-B"
    _seed_session(file_store, sid)
    _add_step(file_store, sid, 0, 0.5)
    _add_step(file_store, sid, 1, 0.1)
    _add_critical(file_store, sid, 1, "failure_critical", "evidence_thin")

    out = tmp_path / "labels.json"
    generate_labels(db_path=file_store._tmp_path, output_path=str(out))

    # Manually fill in a label
    payload = json.loads(out.read_text())
    payload["records"][0]["label"]["is_true_positive"] = True
    payload["records"][0]["label"]["notes"] = "yep"
    out.write_text(json.dumps(payload))

    # Re-generate — preserve
    generate_labels(db_path=file_store._tmp_path, output_path=str(out))
    payload2 = json.loads(out.read_text())
    assert payload2["records"][0]["label"]["is_true_positive"] is True
    assert payload2["records"][0]["label"]["notes"] == "yep"


# ── compute_precision ──────────────────────────────────────────────────────


def _write_labels(path: Path, recs: list[dict]) -> None:
    path.write_text(json.dumps({
        "version": 1,
        "generated_at": "x",
        "thresholds": {},
        "records": recs,
    }))


def _mk(sid_idx, tag, verdict, mode=None):
    return {
        "session_id": f"s{sid_idx}",
        "exchange_idx": sid_idx,
        "tag": tag,
        "failure_mode": mode,
        "score": 0.1, "delta": -0.2,
        "features": {}, "progress_vector": {}, "cost_vector": {},
        "agent_action_summary": "",
        "context": {"user_text": "", "evidence_text_preview": "",
                    "preceding_score": None, "following_score": None},
        "label": {"is_true_positive": verdict, "should_have_been_tag": None,
                  "should_have_been_failure_mode": None, "notes": ""},
    }


def test_compute_precision_zero(tmp_path):
    p = tmp_path / "l.json"
    _write_labels(p, [_mk(i, "failure_critical", False) for i in range(4)])
    rep = compute_precision(p)
    assert rep["labeled"] == 4
    assert rep["precision_overall"] == 0.0
    assert rep["ship_gate_passed"] is False


def test_compute_precision_half(tmp_path):
    p = tmp_path / "l.json"
    recs = [_mk(i, "failure_critical", True) for i in range(2)] + \
           [_mk(i + 100, "failure_critical", False) for i in range(2)]
    _write_labels(p, recs)
    rep = compute_precision(p)
    assert rep["precision_overall"] == 0.5
    assert rep["ship_gate_passed"] is False  # labeled < 20


def test_compute_precision_80_pct_meets_gate(tmp_path):
    """80% precision over 20+ labeled records passes the gate."""
    p = tmp_path / "l.json"
    recs = [_mk(i, "failure_critical", True) for i in range(16)] + \
           [_mk(i + 100, "failure_critical", False) for i in range(4)]
    _write_labels(p, recs)
    rep = compute_precision(p)
    assert rep["labeled"] == 20
    assert rep["precision_overall"] == pytest.approx(0.8)
    assert rep["ship_gate_passed"] is True


def test_compute_precision_100(tmp_path):
    p = tmp_path / "l.json"
    recs = [_mk(i, "failure_critical", True) for i in range(SHIP_GATE_MIN_LABELED)]
    _write_labels(p, recs)
    rep = compute_precision(p)
    assert rep["precision_overall"] == 1.0
    assert rep["ship_gate_passed"] is True


def test_compute_precision_ship_gate_needs_min_labeled(tmp_path):
    """Even 100% precision with <20 labeled fails the gate."""
    p = tmp_path / "l.json"
    recs = [_mk(i, "failure_critical", True) for i in range(5)]
    _write_labels(p, recs)
    rep = compute_precision(p)
    assert rep["precision_overall"] == 1.0
    assert rep["ship_gate_passed"] is False


def test_compute_precision_by_tag_and_mode(tmp_path):
    p = tmp_path / "l.json"
    recs = [
        _mk(1, "failure_critical", True, mode="evidence_thin"),
        _mk(2, "failure_critical", True, mode="evidence_thin"),
        _mk(3, "failure_critical", False, mode="evidence_thin"),
        _mk(4, "success_critical", True),
    ]
    _write_labels(p, recs)
    rep = compute_precision(p)
    assert rep["precision_by_tag"]["failure_critical"] == pytest.approx(2 / 3)
    assert rep["precision_by_tag"]["success_critical"] == 1.0
    assert rep["precision_by_failure_mode"]["evidence_thin"] == pytest.approx(2 / 3)


# ── HTML viewer ────────────────────────────────────────────────────────────


def test_html_viewer_renders(file_store, tmp_path):
    sid = "sess-C"
    _seed_session(file_store, sid)
    _add_step(file_store, sid, 0, 0.5)
    _add_step(file_store, sid, 1, 0.1)
    _add_critical(file_store, sid, 1, "failure_critical", "evidence_thin")

    out = tmp_path / "labels.json"
    generate_labels(db_path=file_store._tmp_path, output_path=str(out))
    payload = json.loads(out.read_text())

    html = render_html(payload)
    assert len(html) > 1000
    assert "Critical-Step Labeler" in html
    assert 'id="candidate-count"' in html
    assert ">1<" in html  # the candidate count appears
    assert "labels.forEach" in html  # JS labels script present
    assert "failure_critical" in html
