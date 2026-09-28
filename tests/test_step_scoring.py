"""Tests for per-step observer scoring (Stage 2)."""
from __future__ import annotations
import json


from agent_trace_signals.config import ModelConfig
from agent_trace_signals.models import (
    Record, Session, StepScore, TurnDescriptor,
)
from agent_trace_signals.pipeline.step_scoring import (
    StepScoringPipeline,
    compute_cost_vector,
    compute_features,
    derive_evidence_supports,
)
from agent_trace_signals.providers.base import ModelProvider


# ── Fixtures ────────────────────────────────────────────────────────────────

def _td(idx, etype="genuine_human", tools=None, errs=None, words=20):
    return TurnDescriptor(
        exchange_idx=idx,
        user_word_count=words,
        tool_calls=tools or [],
        tool_errors=errs or [],
        exchange_type=etype,
    )


def _tc(name, target="f.py"):
    return {"name": name, "target": target}


class _MockProvider(ModelProvider):
    """Returns deterministic observer JSON per call.

    By default returns a strong-progress vector. Tracks call count.
    """

    def __init__(self, progress=None, summary="mock action"):
        self.progress = progress or {
            "delta_test": 0.5, "delta_scope": 0.5,
            "delta_patch": 0.0, "delta_info": 0.5,
        }
        self.summary = summary
        self.calls = 0

    def complete(self, prompt, *, model=None, schema=None,
                 max_tokens=1024, temperature=0.0):
        self.calls += 1
        return {"progress_vector": self.progress,
                "agent_action_summary": self.summary}


def _seed_session(store, session_id="sess-1", tds=None):
    """Insert a session row with turn_descriptors JSON + a couple of records."""
    tds = tds or []
    td_json = json.dumps([td.model_dump() for td in tds])
    session = Session(
        id=session_id,
        source_type="agent_trace",
        source_plugin="claude_code",
        chunk_count=2,
        turn_descriptors=td_json,
    )
    store.conn.execute(
        """INSERT OR REPLACE INTO sessions
           (id, org_id, workspace_id, app_id, user_id, source_type, source_plugin,
            ingested_at, session_summary, embedding_text, raw_facets,
            turn_descriptors, chunk_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (session.id, session.org_id, session.workspace_id, session.app_id,
         session.user_id, session.source_type, session.source_plugin,
         session.ingested_at, "", "", "{}", session.turn_descriptors,
         session.chunk_count),
    )
    # Minimal records so evidence_text mapping has something
    for i in range(2):
        rid = Record.make_id(session_id, i)
        store.conn.execute(
            """INSERT OR REPLACE INTO records
               (id, session_id, chunk_index, chunk_text, evidence_text,
                embedding_text)
               VALUES (?,?,?,?,?,?)""",
            (rid, session_id, i, f"chunk text {i}", f"evidence {i}", ""),
        )
    store.conn.commit()
    return session_id


# ── Deterministic-function tests ────────────────────────────────────────────

class TestCostVector:
    def test_tokens_no_tools(self):
        td = _td(0, tools=[], words=20)
        cv = compute_cost_vector(td, session_median_tokens=20, preceding_tds=[])
        # 20 words / 20 median = 1.0 -> /3 = 0.333…
        assert 0.30 < cv["tokens"] < 0.35
        assert cv["redundancy"] == 0.0

    def test_tokens_caps_at_three_times_median(self):
        td = _td(0, tools=[_tc("Bash", f"x{i}") for i in range(10)], words=100)
        cv = compute_cost_vector(td, session_median_tokens=10, preceding_tds=[])
        assert cv["tokens"] == 1.0  # capped

    def test_zero_median_safe(self):
        td = _td(0, tools=[], words=20)
        cv = compute_cost_vector(td, session_median_tokens=0, preceding_tds=[])
        assert 0.0 <= cv["tokens"] <= 1.0
        assert cv["redundancy"] == 0.0

    def test_redundancy_full_repeat(self):
        prev = [_td(i, tools=[_tc("Edit", "foo.py")]) for i in range(3)]
        cur = _td(3, tools=[_tc("Edit", "foo.py")])
        cv = compute_cost_vector(cur, session_median_tokens=50, preceding_tds=prev)
        # Capped at 0.5: full repetition is penalised but cannot fully cancel progress signal
        assert cv["redundancy"] == 0.5

    def test_redundancy_partial(self):
        prev = [_td(0, tools=[_tc("Edit", "foo.py")])]
        cur = _td(1, tools=[_tc("Edit", "foo.py"), _tc("Bash", "ls")])
        cv = compute_cost_vector(cur, session_median_tokens=50, preceding_tds=prev)
        assert cv["redundancy"] == 0.5


class TestFeatures:
    def test_buckets_smoke(self):
        td = _td(0, tools=[_tc("Read"), _tc("Edit")], errs=[False, True], words=30)
        feats = compute_features(td, preceding_tds=[])
        assert feats["tokens_bucket"] in {"<2k", "2-8k", "8-32k", ">32k"}
        assert feats["turns_bucket"] in {"1", "2-4", "5-15", ">15"}
        assert feats["tool_diversity_bucket"] in {"1", "2-3", "4-6", ">6"}
        assert feats["error_rate_bucket"] in {"0%", "<25%", "25-50%", ">50%"}
        assert "context" in feats

    def test_error_rate_high(self):
        td = _td(0, tools=[_tc("X"), _tc("Y")], errs=[True, True], words=10)
        feats = compute_features(td, preceding_tds=[])
        assert feats["error_rate_bucket"] == ">50%"


class TestEvidenceSupports:
    def test_clip_low(self):
        progress = {"delta_test": -1, "delta_scope": -1,
                    "delta_patch": -1, "delta_info": -1}
        cost = {"tokens": 1.0, "redundancy": 1.0}
        assert derive_evidence_supports(progress, cost) == 0.0

    def test_clip_high(self):
        progress = {"delta_test": 1, "delta_scope": 1,
                    "delta_patch": 1, "delta_info": 1}
        cost = {"tokens": 0.0, "redundancy": 0.0}
        assert derive_evidence_supports(progress, cost) == 1.0

    def test_mid(self):
        progress = {"delta_test": 0.5, "delta_scope": 0.5,
                    "delta_patch": 0.0, "delta_info": 0.5}
        cost = {"tokens": 0.5, "redundancy": 0.5}
        expected = (
            0.30 * 0.5 + 0.25 * 0.5 + 0.20 * 0.0 + 0.25 * 0.5
            - 0.15 * 0.5 - 0.25 * 0.5
        )
        assert abs(derive_evidence_supports(progress, cost) - max(0.0, min(1.0, expected))) < 1e-9


# ── Integration tests with mock provider ────────────────────────────────────

class TestPipelineIntegration:

    def _build_pipeline(self, store, provider=None):
        cfg = ModelConfig()
        prov = provider or _MockProvider()
        return StepScoringPipeline(store=store, provider=prov, config=cfg), prov

    def test_scores_only_scorable_types(self, store):
        tds = [
            _td(0, etype="genuine_human", tools=[_tc("Read")]),
            _td(1, etype="skill_invocation", tools=[]),         # NON-scorable
            _td(2, etype="agent_continuation", tools=[_tc("Edit")]),
            _td(3, etype="task_notification", tools=[]),        # NON-scorable
            _td(4, etype="genuine_text", tools=[]),
        ]
        sid = _seed_session(store, tds=tds)
        pipeline, mock = self._build_pipeline(store)

        n = pipeline.score_session(sid)
        assert n == 3
        assert mock.calls == 3
        rows = store.conn.execute(
            "SELECT exchange_idx FROM step_scores WHERE session_id=? ORDER BY exchange_idx",
            (sid,),
        ).fetchall()
        assert [r[0] for r in rows] == [0, 2, 4]

    def test_idempotency_default(self, store):
        tds = [_td(0), _td(1, etype="agent_continuation", tools=[_tc("Edit")])]
        sid = _seed_session(store, tds=tds)
        pipeline, mock = self._build_pipeline(store)

        first = pipeline.score_session(sid)
        assert first == 2
        # Second run should score nothing without force
        second = pipeline.score_session(sid)
        assert second == 0
        # mock.calls didn't increment on second run
        assert mock.calls == 2

    def test_force_rescore(self, store):
        tds = [_td(0), _td(1, etype="agent_continuation", tools=[_tc("Edit")])]
        sid = _seed_session(store, tds=tds)
        pipeline, mock = self._build_pipeline(store)
        pipeline.score_session(sid)
        n = pipeline.score_session(sid, force=True)
        assert n == 2
        assert mock.calls == 4

    def test_evidence_supports_in_range(self, store):
        tds = [_td(i, tools=[_tc("Read", f"f{i}.py")]) for i in range(3)]
        sid = _seed_session(store, tds=tds)
        pipeline, _ = self._build_pipeline(store)
        pipeline.score_session(sid)
        rows = store.conn.execute(
            "SELECT evidence_supports, progress_vector, cost_vector FROM step_scores"
        ).fetchall()
        assert len(rows) == 3
        for ev, pv, cv in rows:
            assert 0.0 <= ev <= 1.0
            pv_d = json.loads(pv)
            cv_d = json.loads(cv)
            for k in ("delta_test", "delta_scope", "delta_patch", "delta_info"):
                assert -1.0 <= pv_d[k] <= 1.0
            assert 0.0 <= cv_d["tokens"] <= 1.0
            assert 0.0 <= cv_d["redundancy"] <= 1.0


# ── Store method tests ─────────────────────────────────────────────────────

class TestStoreStepScores:
    def test_insert_and_query(self, store):
        # Seed a session row to satisfy FK
        _seed_session(store, tds=[_td(0)])
        s = StepScore(
            session_id="sess-1", exchange_idx=0, evidence_supports=0.42,
            progress_vector="{}", cost_vector="{}",
            agent_action_summary="x", features="{}", observer_model="mock",
        )
        store.insert_step_score(s)
        assert store.get_existing_step_score_indices("sess-1") == {0}

    def test_insert_batch(self, store):
        _seed_session(store, tds=[_td(i) for i in range(3)])
        scores = [
            StepScore(session_id="sess-1", exchange_idx=i, evidence_supports=0.5,
                      progress_vector="{}", cost_vector="{}", features="{}",
                      observer_model="mock")
            for i in range(3)
        ]
        store.insert_step_scores_batch(scores)
        assert store.get_existing_step_score_indices("sess-1") == {0, 1, 2}
