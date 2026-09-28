"""Critical-step detection — Stage 3 of trajectory_signals.

[SOFT-DEPRECATED as of 2026-07-01] Track 2 (critical step detection) is no longer invested in.
The patterns[] field in D-prompt covers the same semantic territory at chunk granularity with lower cost.

Pure deterministic stage. Reads step_scores rows for a session, applies
threshold + gradient rules, writes critical_steps rows.

Combined criterion (see design/trajectory_signals.md):

    K = { j : s_j ≤ δ_low }                              → failure_critical  (absolute_low)
      ∪ { j : s_j − s_{j-1} ≤ −δ_change }                → failure_critical  (negative_jump)
      ∪ { j : s_j ≥ δ_high  AND step is non-trivial }    → success_critical
      ∪ { j : s_j − s_{j-1} ≥ +δ_change AND
              ≥ k of the prior k+1 steps had s ≤ δ_low } → recovery_critical

Tag precedence when a step satisfies multiple conditions (only one row written
per (session_id, exchange_idx)): recovery_critical > failure_critical > success_critical.

Threshold tunability — IMPORTANT:
    The default thresholds (delta_low=0.35, delta_high=0.8, delta_change=0.25,
    k=3) are calibrated for a capable observer such as Anthropic Haiku.
    The local Ollama observer (gemma3:12b) produces conservative scores —
    evidence_supports rarely exceeds ~0.25 on healthy SDE work. When running
    with the Ollama default observer, lower delta_high to ~0.20 so any
    success_critical signals surface. Pass --delta-high 0.20 on the CLI or
    configure via CriticalStepConfig.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from agent_trace_signals.config import CriticalStepConfig
from agent_trace_signals.db.store import SQLiteStore
from agent_trace_signals.models import CriticalStep

logger = logging.getLogger(__name__)


# ── failure_mode derivation ────────────────────────────────────────────────

_PROGRESS_DIM_TO_MODE = {
    "delta_scope": "wrong_target",
    "delta_patch": "tool_misread",
    "delta_info":  "evidence_thin",
    "delta_test":  "premature_done",
}


def derive_failure_mode(progress_vector: dict, cost_vector: dict) -> str | None:
    """Pick failure_mode given a step's progress + cost vectors.

    Rules:
    - If any progress dim is negative, return the mode for the most-negative dim.
    - Else if cost_vector.tokens or .redundancy > 0.7, return 'trajectory_inflation'.
    - Else None.
    """
    dims = {k: float(progress_vector.get(k, 0.0)) for k in _PROGRESS_DIM_TO_MODE}
    most_neg_dim = min(dims, key=lambda k: dims[k])
    if dims[most_neg_dim] < 0:
        return _PROGRESS_DIM_TO_MODE[most_neg_dim]

    tokens = float(cost_vector.get("tokens", 0.0))
    redundancy = float(cost_vector.get("redundancy", 0.0))
    if max(tokens, redundancy) > 0.7:
        return "trajectory_inflation"
    return None


# ── detection rules ────────────────────────────────────────────────────────

@dataclass
class _StepRow:
    exchange_idx: int
    score: float
    progress_vector: dict
    cost_vector: dict
    features: str


def _load_steps_for_session(store: SQLiteStore, session_id: str) -> list[_StepRow]:
    rows = store.conn.execute(
        "SELECT exchange_idx, evidence_supports, progress_vector, cost_vector, features "
        "FROM step_scores WHERE session_id = ? ORDER BY exchange_idx",
        (session_id,),
    ).fetchall()
    out: list[_StepRow] = []
    for r in rows:
        try:
            pv = json.loads(r["progress_vector"] or "{}")
        except (TypeError, json.JSONDecodeError):
            pv = {}
        try:
            cv = json.loads(r["cost_vector"] or "{}")
        except (TypeError, json.JSONDecodeError):
            cv = {}
        out.append(_StepRow(
            exchange_idx=int(r["exchange_idx"]),
            score=float(r["evidence_supports"]),
            progress_vector=pv,
            cost_vector=cv,
            features=r["features"] or "{}",
        ))
    return out


def _is_nontrivial(cost_vector: dict) -> bool:
    return float(cost_vector.get("tokens", 0.0)) > 0.1


def _session_thresholds(
    steps: list[_StepRow],
    cfg: CriticalStepConfig,
) -> tuple[float, float, float]:
    """Return effective (delta_low, delta_high, delta_change) for this session.

    Within-session calibration: when the observer's scores are compressed into
    a narrow range (e.g. all steps score 0.0–0.25 due to the Ollama observer's
    conservative bias), the absolute thresholds fire on almost every exchange.
    We compute the session p20 and p80 and use those as the effective thresholds,
    subject to a floor/ceiling so we never go below the absolute config values.

    delta_low  = max(cfg.delta_low,  p20)  — only tighten, never loosen
    delta_high = max(cfg.delta_high, p80)  — only tighten, never loosen
    delta_change stays absolute (it's a gradient, less affected by compression)

    If the session has fewer than 5 steps, fall back to absolute thresholds.
    Parse-failed steps are excluded — their placeholder 0.0 scores would drag
    p20 down artificially, loosening eff_low for the whole session.
    """
    valid_steps = [s for s in steps if not _is_parse_failed(s)]
    if not cfg.use_session_percentile_thresholds or len(valid_steps) < 5:
        return cfg.delta_low, cfg.delta_high, cfg.delta_change

    scores = sorted(s.score for s in valid_steps)
    n = len(scores)
    p20 = scores[max(0, int(n * 0.20) - 1)]
    p80 = scores[min(n - 1, int(n * 0.80))]

    eff_low  = max(cfg.delta_low,  p20)
    eff_high = max(cfg.delta_high, p80)
    return eff_low, eff_high, cfg.delta_change


def _is_parse_failed(step: _StepRow) -> bool:
    """True if this step's score is a placeholder from a failed observer call."""
    try:
        feats = json.loads(step.features) if isinstance(step.features, str) else step.features
    except (TypeError, json.JSONDecodeError):
        feats = {}
    return bool(feats.get("observer_parse_failed"))


def _has_corroboration(cur: _StepRow) -> bool:
    """Return True if there is evidence of real effort in this exchange.

    Used to gate the pure absolute_low failure_critical rule (score ≤ delta_low
    AND delta ≈ 0). Without this gate, neutral exchanges (doc update, git commit,
    research discussion) that score 0 because there's no SWE-progress also fire.
    The negative_jump rule already supplies its own corroboration (a score drop
    from a higher value), so this check is only needed for the flat-zero case.

    Corroboration criteria:
    - cost_vector.tokens > 0.3: exchange did substantial work (many tool calls
      or a large user directive) — but still scored 0, suggesting real failure.
    - features.error_rate_bucket != "0%": observer detected tool errors.
    """
    tokens = float(cur.cost_vector.get("tokens", 0.0))
    if tokens > 0.3:
        return True
    try:
        feats = json.loads(cur.features) if isinstance(cur.features, str) else cur.features
    except (TypeError, json.JSONDecodeError):
        feats = {}
    return feats.get("error_rate_bucket", "0%") != "0%"


def _classify_step(
    j: int,
    steps: list[_StepRow],
    cfg: CriticalStepConfig,
    eff_low: float,
    eff_high: float,
    eff_change: float,
) -> tuple[str, str | None] | None:
    """Return (tag, failure_mode) or None if no rule matches.

    Implements precedence: recovery > failure > success.
    Uses session-calibrated thresholds (eff_low/eff_high/eff_change).
    """
    cur = steps[j]

    # A step where the observer call failed to parse carries no real signal —
    # its score is a placeholder, not evidence of failure. Skip classification
    # entirely rather than let it fire as a false failure_critical or pollute
    # a neighboring negative_jump/recovery calculation.
    if _is_parse_failed(cur):
        return None

    # Walk back past any parse_failed predecessor so its placeholder score
    # never enters the delta calculation (it would look like a fake drop/jump).
    prev_idx = j - 1
    while prev_idx >= 0 and _is_parse_failed(steps[prev_idx]):
        prev_idx -= 1
    prev_score = steps[prev_idx].score if prev_idx >= 0 else cur.score
    delta = cur.score - prev_score

    # Recovery: positive jump AND ≥ k of immediately preceding (k+1) steps were stuck.
    # Parse-failed steps carry no signal — excluded from both the window and the count.
    if delta >= eff_change and j > 0:
        window_start = max(0, j - (cfg.k + 1))
        window = [s for s in steps[window_start:j] if not _is_parse_failed(s)]
        if len(window) >= cfg.k:
            stuck_count = sum(1 for s in window if s.score <= eff_low)
            if stuck_count >= cfg.k:
                return ("recovery_critical", None)

    is_negative_jump = j > 0 and delta <= -eff_change
    is_absolute_low = cur.score <= eff_low

    # Failure: absolute low OR negative jump
    if is_absolute_low or is_negative_jump:
        # For pure absolute_low (score was already at the bottom — no new drop),
        # require corroboration so neutral maintenance exchanges don't fire.
        if (
            cfg.absolute_low_requires_corroboration
            and is_absolute_low
            and not is_negative_jump
            and not _has_corroboration(cur)
        ):
            return None
        mode = derive_failure_mode(cur.progress_vector, cur.cost_vector)
        return ("failure_critical", mode)

    # Success: absolute high AND non-trivial cost
    if cur.score >= eff_high and _is_nontrivial(cur.cost_vector):
        return ("success_critical", None)

    return None


# ── session-level harness feature helpers ─────────────────────────────────


def _compute_human_tool_ratio(store: SQLiteStore, session_id: str) -> tuple[float, int]:
    """Return (human_tool_ratio, n_tool_calls) from stored turn_descriptors.

    human_tool_ratio = genuine_human_turns / max(n_tool_calls, 1).
    Returns (inf, 0) when the session has no tool calls at all.
    """
    row = store.conn.execute(
        "SELECT turn_descriptors FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row or not row[0]:
        return (float("inf"), 0)
    try:
        tds = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return (float("inf"), 0)

    genuine_human = 0
    n_tool_calls = 0
    for td in tds:
        if td.get("exchange_type") == "genuine_human":
            genuine_human += 1
        # tool_calls is a list of dicts; count total calls across all exchanges
        n_tool_calls += len(td.get("tool_calls") or [])

    if n_tool_calls == 0:
        return (float("inf"), 0)
    return (genuine_human / n_tool_calls, n_tool_calls)


# ── pipeline ───────────────────────────────────────────────────────────────


class CriticalStepDetectionPipeline:
    """Deterministic critical-step detection over step_scores rows."""

    def __init__(self, store: SQLiteStore, config: CriticalStepConfig | None = None):
        self.store = store
        self.config = config or CriticalStepConfig()

    def detect_session(self, session_id: str, force: bool = False) -> int:
        """Detect critical steps for one session. Returns rows written.

        Idempotent — skips if critical_steps already exist for this session,
        unless force=True (then deletes existing rows and re-runs).
        """
        if self.store.has_critical_steps(session_id):
            if not force:
                logger.info("session %s already has critical_steps — skipping", session_id)
                return 0
            self.store.delete_critical_steps_for_session(session_id)

        steps = _load_steps_for_session(self.store, session_id)
        if not steps:
            return 0

        # FP-suppression rule: human_tool_ratio gate.
        # Embedding experiments (2026-06-13) showed human_tool_ratio is the
        # strongest single predictor separating discussion sessions from
        # implementation sessions (RF importance 0.20, r=0.75 with UMAP-y).
        # A pure conversation has n_tool_calls=0 (ratio → ∞); an implementation
        # session has ratio well below the threshold. We compute from
        # turn_descriptors so no new data is needed.
        if self.config.max_human_tool_ratio is not None:
            ratio, n_tool_calls = _compute_human_tool_ratio(self.store, session_id)
            if n_tool_calls == 0 or ratio > self.config.max_human_tool_ratio:
                logger.info(
                    "session %s skipped: human_tool_ratio=%.2f (n_tool_calls=%d, threshold=%.1f)",
                    session_id, ratio, n_tool_calls, self.config.max_human_tool_ratio,
                )
                return 0

        eff_low, eff_high, eff_change = _session_thresholds(steps, self.config)
        if eff_low != self.config.delta_low or eff_high != self.config.delta_high:
            logger.info(
                "session %s: percentile thresholds → low=%.3f (abs=%.3f) high=%.3f (abs=%.3f)",
                session_id, eff_low, self.config.delta_low, eff_high, self.config.delta_high,
            )

        results: list[CriticalStep] = []
        for j, cur in enumerate(steps):
            # FP-suppression rule: skip the first exchange in a session
            # (resume-after-break artifact; cost normalized vs empty
            # session median erroneously trips trajectory_inflation).
            if self.config.skip_first_exchange and cur.exchange_idx == 0:
                continue

            outcome = _classify_step(j, steps, self.config, eff_low, eff_high, eff_change)
            if outcome is None:
                continue
            tag, failure_mode = outcome
            prev_score = steps[j - 1].score if j > 0 else cur.score
            delta = 0.0 if j == 0 else cur.score - prev_score
            cs = CriticalStep(
                id=CriticalStep.make_id(session_id, cur.exchange_idx, tag),
                session_id=session_id,
                exchange_idx=cur.exchange_idx,
                tag=tag,
                failure_mode=failure_mode,
                delta=round(delta, 4),
                score=round(cur.score, 4),
                features=cur.features or "{}",
            )
            results.append(cs)

        self.store.insert_critical_steps(results)
        return len(results)

    def detect_all(self, force: bool = False) -> dict[str, int]:
        """Iterate all sessions with step_scores. Returns {session_id: rows_written}."""
        out: dict[str, int] = {}
        for sid in self.store.get_sessions_with_step_scores():
            out[sid] = self.detect_session(sid, force=force)
        return out
