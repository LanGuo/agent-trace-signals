"""Tests for Stage 3 critical-step detection."""
from __future__ import annotations
import json

import pytest

from agent_trace_signals.config import CriticalStepConfig
from agent_trace_signals.models import StepScore
from agent_trace_signals.pipeline.critical_steps import (
    CriticalStepDetectionPipeline,
    derive_failure_mode,
)


# ── helpers ────────────────────────────────────────────────────────────────

DEFAULT_PV = {"delta_test": 0.0, "delta_scope": 0.0, "delta_patch": 0.0, "delta_info": 0.0}
DEFAULT_CV = {"tokens": 0.3, "redundancy": 0.0}

# These tests were written against the original spec defaults (delta_low=0.35,
# delta_high=0.80, delta_change=0.25). The production defaults have since
# been retuned for the default Ollama observer. Tests still verify the rule
# logic, so we pin to the original thresholds explicitly here.
_TEST_CONFIG = CriticalStepConfig(
    delta_low=0.35, delta_high=0.8, delta_change=0.25, k=3,
    # Tests use synthetic fixtures that intentionally exercise exchange_idx=0
    # and short sessions; disable production-only FP-suppression gates so the
    # rule logic is verified independently of them.
    skip_first_exchange=False,
    max_human_tool_ratio=None,               # disable session-level gate in unit tests
    use_session_percentile_thresholds=False, # tests use exact threshold assertions
    absolute_low_requires_corroboration=False,  # tests exercise rule logic directly
)


def _seed_session(store, sid="sess-1"):
    store.conn.execute(
        """INSERT OR REPLACE INTO sessions
           (id, source_type, source_plugin, ingested_at, session_summary,
            embedding_text, raw_facets)
           VALUES (?, 'agent_trace', 'claude_code', '2026-06-01', '', '', '{}')""",
        (sid,),
    )
    store.conn.commit()


def _add_score(store, sid, idx, score, pv=None, cv=None):
    ss = StepScore(
        session_id=sid, exchange_idx=idx, evidence_supports=score,
        progress_vector=json.dumps(pv or DEFAULT_PV),
        cost_vector=json.dumps(cv or DEFAULT_CV),
        agent_action_summary="x",
        features=json.dumps({"tokens_bucket": "<2k"}),
    )
    store.insert_step_score(ss)


def _tags(store, sid):
    return [(c.exchange_idx, c.tag, c.failure_mode) for c in store.get_critical_steps(sid)]


# ── detection rules ────────────────────────────────────────────────────────

def test_absolute_low_failure_critical(store):
    _seed_session(store)
    _add_score(store, "sess-1", 0, 0.5)
    _add_score(store, "sess-1", 1, 0.2)   # ≤ delta_low=0.35
    pipe = CriticalStepDetectionPipeline(store, _TEST_CONFIG)
    n = pipe.detect_session("sess-1")
    assert n == 1
    rows = _tags(store, "sess-1")
    assert (1, "failure_critical", None) in [(r[0], r[1], r[2]) for r in rows]


def test_negative_jump_failure_critical(store):
    _seed_session(store)
    _add_score(store, "sess-1", 0, 0.9, pv={"delta_test": 0.8, "delta_scope": 0.0, "delta_patch": 0.0, "delta_info": 0.0})
    _add_score(store, "sess-1", 1, 0.5)   # drop of 0.4 ≥ 0.25
    pipe = CriticalStepDetectionPipeline(store, _TEST_CONFIG)
    pipe.detect_session("sess-1")
    rows = _tags(store, "sess-1")
    # idx 0 should be success_critical (high + nontrivial); idx 1 failure_critical (negative jump)
    by_idx = {r[0]: r for r in rows}
    assert by_idx[1][1] == "failure_critical"


def test_success_critical_nontrivial(store):
    _seed_session(store)
    _add_score(store, "sess-1", 0, 0.9, cv={"tokens": 0.5, "redundancy": 0.0})
    pipe = CriticalStepDetectionPipeline(store, _TEST_CONFIG)
    pipe.detect_session("sess-1")
    rows = _tags(store, "sess-1")
    assert rows == [(0, "success_critical", None)]


def test_success_high_but_trivial_cost_not_critical(store):
    _seed_session(store)
    _add_score(store, "sess-1", 0, 0.9, cv={"tokens": 0.05, "redundancy": 0.0})
    pipe = CriticalStepDetectionPipeline(store, _TEST_CONFIG)
    n = pipe.detect_session("sess-1")
    assert n == 0


def test_recovery_critical(store):
    _seed_session(store)
    # 4 stuck steps then a big positive jump
    _add_score(store, "sess-1", 0, 0.2)
    _add_score(store, "sess-1", 1, 0.1)
    _add_score(store, "sess-1", 2, 0.2)
    _add_score(store, "sess-1", 3, 0.2)
    _add_score(store, "sess-1", 4, 0.6)   # +0.4 jump after stuck window
    pipe = CriticalStepDetectionPipeline(store, _TEST_CONFIG)
    pipe.detect_session("sess-1")
    rows = {r[0]: r for r in _tags(store, "sess-1")}
    assert rows[4][1] == "recovery_critical"


def test_positive_jump_without_prior_stuck_not_recovery(store):
    _seed_session(store)
    _add_score(store, "sess-1", 0, 0.5)
    _add_score(store, "sess-1", 1, 0.5)
    _add_score(store, "sess-1", 2, 0.5)
    _add_score(store, "sess-1", 3, 0.9, cv={"tokens": 0.5})   # jump but no stuck prior
    pipe = CriticalStepDetectionPipeline(store, _TEST_CONFIG)
    pipe.detect_session("sess-1")
    rows = {r[0]: r for r in _tags(store, "sess-1")}
    assert rows.get(3, (3, None, None))[1] != "recovery_critical"
    # should still be success_critical due to absolute high + nontrivial
    assert rows[3][1] == "success_critical"


def test_tag_precedence_recovery_over_failure(store):
    """If a step satisfies both failure (low absolute) AND recovery
    (positive jump + prior stuck), recovery wins."""
    _seed_session(store)
    _add_score(store, "sess-1", 0, 0.05)
    _add_score(store, "sess-1", 1, 0.05)
    _add_score(store, "sess-1", 2, 0.05)
    _add_score(store, "sess-1", 3, 0.05)
    # Jump of +0.3 lands at 0.35 — STILL ≤ delta_low=0.35 (failure_critical),
    # but also a positive jump after sustained stuck → recovery wins.
    _add_score(store, "sess-1", 4, 0.35)
    pipe = CriticalStepDetectionPipeline(store, _TEST_CONFIG)
    pipe.detect_session("sess-1")
    rows = {r[0]: r for r in _tags(store, "sess-1")}
    assert rows[4][1] == "recovery_critical"


# ── failure-mode derivation ────────────────────────────────────────────────

@pytest.mark.parametrize("dim,expected", [
    ("delta_scope", "wrong_target"),
    ("delta_patch", "tool_misread"),
    ("delta_info",  "evidence_thin"),
    ("delta_test",  "premature_done"),
])
def test_failure_mode_progress_dims(dim, expected):
    pv = dict(DEFAULT_PV)
    pv[dim] = -0.5
    # make sure it's strictly most-negative
    assert derive_failure_mode(pv, {"tokens": 0.0, "redundancy": 0.0}) == expected


def test_failure_mode_trajectory_inflation():
    pv = {"delta_test": 0.0, "delta_scope": 0.0, "delta_patch": 0.0, "delta_info": 0.0}
    cv = {"tokens": 0.3, "redundancy": 0.9}
    assert derive_failure_mode(pv, cv) == "trajectory_inflation"


def test_failure_mode_none_when_nothing_dominant():
    pv = {"delta_test": 0.1, "delta_scope": 0.1, "delta_patch": 0.0, "delta_info": 0.0}
    cv = {"tokens": 0.2, "redundancy": 0.2}
    assert derive_failure_mode(pv, cv) is None


# ── idempotency ────────────────────────────────────────────────────────────

def test_idempotent_rerun_no_force(store):
    _seed_session(store)
    _add_score(store, "sess-1", 0, 0.1)
    _add_score(store, "sess-1", 1, 0.1)
    pipe = CriticalStepDetectionPipeline(store, _TEST_CONFIG)
    n1 = pipe.detect_session("sess-1")
    n2 = pipe.detect_session("sess-1")  # no force → skip
    assert n1 > 0 and n2 == 0


def test_force_rerun_deletes_and_redetects(store):
    _seed_session(store)
    _add_score(store, "sess-1", 0, 0.1)
    _add_score(store, "sess-1", 1, 0.1)
    pipe = CriticalStepDetectionPipeline(store, _TEST_CONFIG)
    n1 = pipe.detect_session("sess-1")
    n2 = pipe.detect_session("sess-1", force=True)
    assert n1 == n2 and n2 > 0
    # Still only the expected rows, not doubled
    assert len(store.get_critical_steps("sess-1")) == n2


# ── integration ────────────────────────────────────────────────────────────

def test_integration_mixed_session(store):
    """Synthetic step trajectory hits all four rule branches."""
    _seed_session(store)
    # idx 0: high + nontrivial → success_critical
    _add_score(store, "sess-1", 0, 0.9, cv={"tokens": 0.5, "redundancy": 0.0},
               pv={"delta_test": 0.8, "delta_scope": 0.2, "delta_patch": 0.0, "delta_info": 0.2})
    # idx 1: drop → negative_jump failure_critical
    _add_score(store, "sess-1", 1, 0.5,
               pv={"delta_test": 0.0, "delta_scope": -0.3, "delta_patch": 0.0, "delta_info": 0.0})
    # idx 2,3,4: stuck low → absolute_low failure_critical
    _add_score(store, "sess-1", 2, 0.1)
    _add_score(store, "sess-1", 3, 0.1)
    _add_score(store, "sess-1", 4, 0.1)
    # idx 5: recovery (jump after 3 prior stuck)
    _add_score(store, "sess-1", 5, 0.5)

    pipe = CriticalStepDetectionPipeline(store, _TEST_CONFIG)
    n = pipe.detect_session("sess-1")
    rows = {r[0]: r for r in _tags(store, "sess-1")}
    assert rows[0][1] == "success_critical"
    assert rows[1][1] == "failure_critical"
    assert rows[2][1] == "failure_critical"
    assert rows[5][1] == "recovery_critical"
    # failure_mode derivation: idx 1 has most-negative delta_scope → wrong_target
    assert rows[1][2] == "wrong_target"
    # idx 3,4 are also low → failure_critical
    assert rows[3][1] == "failure_critical"
    assert rows[4][1] == "failure_critical"
    assert n == 6
