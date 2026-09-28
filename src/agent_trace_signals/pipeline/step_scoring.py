"""Per-step observer scoring — Stage 2 of trajectory_signals.

[SOFT-DEPRECATED as of 2026-07-01] Track 2 (per-exchange observer scoring) is no longer invested in.
The patterns[] field in D-prompt covers the same semantic territory at chunk granularity with lower cost.

See design/trajectory_signals.md for the full spec.

For each scorable exchange (exchange_type in SCORABLE_TYPES), an observer LLM
produces a progress_vector + agent_action_summary. The cost_vector and
bucketed features are computed deterministically. evidence_supports is the
derived [0,1] scalar.
"""

from __future__ import annotations
import json
import logging
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from agent_trace_signals.config import ModelConfig
from agent_trace_signals.db.store import SQLiteStore
from agent_trace_signals.models import (
    SCORABLE_TYPES, StepScore, TurnDescriptor,
)
from agent_trace_signals.providers.base import ModelProvider

logger = logging.getLogger(__name__)


# ── Weights for evidence_supports derivation (per spec defaults) ────────────
_LAMBDA_TEST = 0.30
_LAMBDA_SCOPE = 0.25
_LAMBDA_PATCH = 0.20
_LAMBDA_INFO = 0.25
_MU_TOKENS = 0.15
_MU_REDUNDANCY = 0.25

# Window sizes
_REDUNDANCY_K = 10
_FEATURE_WINDOW = 10
_EVIDENCE_CHAR_CAP = 8000

# Observer JSON schema (used by Ollama / Anthropic providers)
_OBSERVER_SCHEMA = {
    "type": "object",
    "properties": {
        "progress_vector": {
            "type": "object",
            "properties": {
                "delta_test":  {"type": "number"},
                "delta_scope": {"type": "number"},
                "delta_patch": {"type": "number"},
                "delta_info":  {"type": "number"},
            },
            "required": ["delta_test", "delta_scope", "delta_patch", "delta_info"],
        },
        "agent_action_summary": {"type": "string"},
    },
    "required": ["progress_vector", "agent_action_summary"],
}


# ── Deterministic helpers ──────────────────────────────────────────────────

def _exchange_token_count(td: TurnDescriptor) -> int:
    """Rough proxy: user words + per-tool-call constant + per-error constant.

    TurnDescriptor doesn't carry token counts; we approximate with user_word_count
    plus a fixed weight per tool call. This is monotonic with real token cost
    for our purposes (cost_vector.tokens is normalized to session median).
    """
    return max(1, int(td.user_word_count) + 50 * len(td.tool_calls))


def _primary_target(tc: dict) -> str:
    return str(tc.get("target") or "")


def _tool_pairs(td: TurnDescriptor) -> list[tuple[str, str]]:
    return [(tc.get("name", ""), _primary_target(tc)) for tc in td.tool_calls]


def _raw_redundancy(td: TurnDescriptor, preceding_tds: list[TurnDescriptor]) -> float:
    """Uncapped fraction of (tool, target) pairs in this exchange that appeared
    in the preceding K=10 SCORABLE exchanges of the same session."""
    pairs = _tool_pairs(td)
    if not pairs:
        return 0.0
    window = preceding_tds[-_REDUNDANCY_K:]
    seen: set[tuple[str, str]] = set()
    for prev in window:
        for p in _tool_pairs(prev):
            seen.add(p)
    matched = sum(1 for p in pairs if p in seen)
    return matched / len(pairs)


def compute_session_baseline_redundancy(
    tds: list[TurnDescriptor], scorable_tds: list[TurnDescriptor]
) -> float:
    """Median raw redundancy across all scorable exchanges in the session.

    Iterative implementation sessions (few distinct tools, tight edit-test loops)
    have a naturally high baseline redundancy that isn't a failure signal. We
    penalize only the *excess* above this session's own baseline rather than a
    fixed global cap, so a session that's normally repetitive isn't punished for
    being itself.
    """
    raws: list[float] = []
    for i, td in enumerate(scorable_tds):
        preceding = scorable_tds[:i]
        raws.append(_raw_redundancy(td, preceding))
    if not raws:
        return 0.0
    return statistics.median(raws)


def compute_cost_vector(
    td: TurnDescriptor,
    session_median_tokens: float,
    preceding_tds: list[TurnDescriptor],
    session_baseline_redundancy: float = 0.0,
) -> dict:
    """Compute deterministic cost vector for this exchange.

    cost_vector.tokens = clip(exchange_token_count / session_median, 0, 3) / 3
    cost_vector.redundancy = excess of this exchange's (tool, target) repeat rate
      over the session's own baseline repeat rate, capped at 0.5. Session-relative
      rather than a fixed global cap: a session that's inherently iterative
      (low tool diversity, tight edit-test loops) is judged against its own normal
      repetition rate, and only redundancy *above* what's typical for that session
      is penalized.
    """
    tok = _exchange_token_count(td)
    median = session_median_tokens if session_median_tokens > 0 else float(tok)
    raw_ratio = tok / median if median > 0 else 0.0
    tokens_cost = max(0.0, min(3.0, raw_ratio)) / 3.0

    raw_redundancy = _raw_redundancy(td, preceding_tds)
    excess = max(0.0, raw_redundancy - session_baseline_redundancy)
    # Still cap at 0.5: even excess redundancy shouldn't be able to cancel
    # delta_info entirely (uncapped excess=1.0 would zero the score outright).
    redundancy = min(0.5, excess)

    return {"tokens": round(tokens_cost, 4), "redundancy": round(redundancy, 4)}


def _bucket_tokens(n: int) -> str:
    if n < 2000:    return "<2k"
    if n < 8000:    return "2-8k"
    if n < 32000:   return "8-32k"
    return ">32k"


def _bucket_turns(n: int) -> str:
    if n <= 1:  return "1"
    if n <= 4:  return "2-4"
    if n <= 15: return "5-15"
    return ">15"


def _bucket_tool_diversity(n: int) -> str:
    if n <= 1:  return "1"
    if n <= 3:  return "2-3"
    if n <= 6:  return "4-6"
    return ">6"


def _bucket_error_rate(rate: float) -> str:
    if rate == 0.0:    return "0%"
    if rate < 0.25:    return "<25%"
    if rate <= 0.50:   return "25-50%"
    return ">50%"


def compute_features(td: TurnDescriptor, preceding_tds: list[TurnDescriptor]) -> dict:
    """Bucketed evidence features over the preceding N-turn window."""
    window = preceding_tds[-_FEATURE_WINDOW:] + [td]
    total_tokens = sum(_exchange_token_count(t) for t in window)
    total_turns = len(window)
    tool_names = {tc.get("name", "") for t in window for tc in t.tool_calls}
    total_tool_calls = sum(len(t.tool_calls) for t in window)
    total_errors = sum(sum(1 for e in t.tool_errors if e) for t in window)
    err_rate = (total_errors / total_tool_calls) if total_tool_calls > 0 else 0.0
    return {
        "tokens_bucket":         _bucket_tokens(total_tokens),
        "turns_bucket":          _bucket_turns(total_turns),
        "tool_diversity_bucket": _bucket_tool_diversity(len(tool_names)),
        "error_rate_bucket":     _bucket_error_rate(err_rate),
        "context":               "unknown",
    }


def derive_evidence_supports(progress: dict, cost: dict) -> float:
    """Combine vectors into the clipped [0,1] scalar per spec defaults."""
    dt = float(progress.get("delta_test", 0.0))
    ds = float(progress.get("delta_scope", 0.0))
    dp = float(progress.get("delta_patch", 0.0))
    di = float(progress.get("delta_info", 0.0))
    ct = float(cost.get("tokens", 0.0))
    cr = float(cost.get("redundancy", 0.0))
    raw = (
        _LAMBDA_TEST * dt + _LAMBDA_SCOPE * ds + _LAMBDA_PATCH * dp + _LAMBDA_INFO * di
        - _MU_TOKENS * ct - _MU_REDUNDANCY * cr
    )
    return max(0.0, min(1.0, raw))


def _agent_action_oneliner(td: TurnDescriptor) -> str:
    """Deterministic one-liner from tool_calls; falls back to text response."""
    if not td.tool_calls:
        return "text-only response"
    parts = []
    for tc in td.tool_calls[:4]:
        name = tc.get("name", "tool")
        tgt = tc.get("target") or ""
        parts.append(f"{name}({tgt})" if tgt else name)
    if len(td.tool_calls) > 4:
        parts.append(f"... +{len(td.tool_calls) - 4} more")
    return "; ".join(parts)


# ── Session-level helpers ──────────────────────────────────────────────────

@dataclass
class _SessionContext:
    session_id: str
    tds: list[TurnDescriptor]                 # ALL exchanges (incl. non-scorable)
    evidence_by_idx: dict[int, str]           # exchange_idx -> evidence_text
    last_human_directive_by_idx: dict[int, str]  # exchange_idx -> recent user msg text
    median_tokens: float
    baseline_redundancy: float                # session's own median raw redundancy


def _load_session_context(store: SQLiteStore, session_id: str, td_json: str) -> _SessionContext | None:
    """Build a _SessionContext from stored turn_descriptors + records."""
    try:
        raw_tds = json.loads(td_json)
    except (TypeError, json.JSONDecodeError):
        return None
    tds = [TurnDescriptor(**td) if isinstance(td, dict) else td for td in raw_tds]
    if not tds:
        return None

    # Median tokens across SCORABLE exchanges
    scorable_tds = [td for td in tds if td.exchange_type in SCORABLE_TYPES]
    scorable_tokens = [_exchange_token_count(td) for td in scorable_tds]
    median = statistics.median(scorable_tokens) if scorable_tokens else 1.0
    baseline_redundancy = compute_session_baseline_redundancy(tds, scorable_tds)

    # Last genuine_human user directive. As of Stage 2 follow-up patch,
    # TurnDescriptor.user_text carries the real text (capped at 2000 chars per plugin);
    # we propagate the most recent one to subsequent exchanges as `current_directive`
    # so the observer can judge delta_scope against the real user intent.
    last_human_by_idx: dict[int, str] = {}
    current_directive = "(no prior user directive)"
    for td in tds:
        if td.exchange_type == "genuine_human" and getattr(td, "user_text", "").strip():
            # Cap at 800 chars in the observer prompt to keep it bounded
            current_directive = td.user_text[:800]
        elif td.exchange_type == "genuine_human" and td.user_word_count > 0:
            # Fallback if user_text wasn't populated (older sessions)
            current_directive = f"[~{td.user_word_count}-word user directive at exchange {td.exchange_idx}]"
        last_human_by_idx[td.exchange_idx] = current_directive

    # Evidence text — pull from records joined by span where possible. As a fallback
    # (when records have no span_start/end populated), we use chunk_text from the
    # k-nearest records by chunk_index proportional to exchange position.
    evidence_by_idx: dict[int, str] = _build_evidence_map(store, session_id, tds)

    return _SessionContext(
        session_id=session_id,
        tds=tds,
        evidence_by_idx=evidence_by_idx,
        last_human_directive_by_idx=last_human_by_idx,
        median_tokens=float(median),
        baseline_redundancy=baseline_redundancy,
    )


def _build_evidence_map(
    store: SQLiteStore, session_id: str, tds: list[TurnDescriptor]
) -> dict[int, str]:
    """Map exchange_idx -> evidence_text from the records table.

    Strategy: if records have span_start/end metadata referring to exchange_idx,
    use that. Otherwise fall back to chunk_text segmented proportionally.
    """
    rows = store.conn.execute(
        "SELECT chunk_index, chunk_text, evidence_text, span_start, span_end "
        "FROM records WHERE session_id=? ORDER BY chunk_index",
        (session_id,),
    ).fetchall()
    if not rows:
        return {td.exchange_idx: "" for td in tds}

    # Try span-based mapping first
    span_map: dict[int, list[str]] = {td.exchange_idx: [] for td in tds}
    span_used = False
    for r in rows:
        sstart, send = r["span_start"], r["span_end"]
        if sstart is None or send is None or sstart == "" or send == "":
            continue
        try:
            s, e = int(sstart), int(send)
        except (ValueError, TypeError):
            continue
        span_used = True
        text = r["evidence_text"] or r["chunk_text"] or ""
        for td in tds:
            if s <= td.exchange_idx <= e:
                span_map[td.exchange_idx].append(text)

    if span_used:
        return {idx: _truncate(" ".join(parts)) for idx, parts in span_map.items()}

    # Fallback: distribute records proportionally across exchanges
    n_records = len(rows)
    n_tds = len(tds)
    if n_tds == 0:
        return {}
    by_idx: dict[int, list[str]] = {td.exchange_idx: [] for td in tds}
    for i, r in enumerate(rows):
        td_pos = int(i * n_tds / n_records)
        td_pos = min(td_pos, n_tds - 1)
        ex_idx = tds[td_pos].exchange_idx
        by_idx[ex_idx].append(r["evidence_text"] or r["chunk_text"] or "")
    return {idx: _truncate(" ".join(parts)) for idx, parts in by_idx.items()}


def _truncate(text: str, cap: int = _EVIDENCE_CHAR_CAP) -> str:
    if len(text) <= cap:
        return text
    # Keep tail (most recent) — trim oldest first
    return "...[truncated]...\n" + text[-cap:]


# ── Observer prompt + LLM call ─────────────────────────────────────────────

_OBSERVER_SYSTEM = (
    "You are an OBSERVER scoring one step of an agent trajectory.\n"
    "Judge ONLY from the supplied evidence_text and agent_action.\n"
    "Do NOT use the agent's reasoning text, chunk summaries, or any external knowledge.\n\n"
    "Output JSON with two fields:\n"
    "  progress_vector: {delta_test, delta_scope, delta_patch, delta_info}, each a float in [-1, +1].\n"
    "  agent_action_summary: a short one-line description of what the agent did.\n\n"
    "IMPORTANT: Use the FULL continuous range. Most turns are partial — typical values are 0.3, -0.4, 0.7.\n"
    "Reserve +1.0 and -1.0 for unambiguous extremes. Do not default to integers.\n\n"
    "Operational definitions:\n\n"
    "  delta_test — did the tool output provide evidence the work is correct?\n"
    "    +1.0 = test suite ran and all tests passed, exit code 0 confirmed\n"
    "    +0.7 = partial pass (some tests passed, output looks correct)\n"
    "    +0.3 = ran but no clear pass/fail signal (e.g. script executed, no errors)\n"
    "     0.0 = no verification action taken (read-only turn, text-only response)\n"
    "    -0.5 = test output shows failures or errors but not catastrophic\n"
    "    -1.0 = hard failure (exception, test crash, exit 1 with clear error)\n\n"
    "  delta_scope — does the agent's action target align with the user's most recent directive?\n"
    "    +1.0 = action directly addresses the exact file/component the user specified\n"
    "    +0.6 = action is in the right subsystem but not the precise target\n"
    "     0.0 = plausible but could be tangential (e.g. reading a config while fixing logic)\n"
    "    -0.5 = action targets something loosely related but probably not what user meant\n"
    "    -1.0 = agent is clearly working on something unrelated to the user's directive\n\n"
    "  delta_patch — for edit/write actions, how well does the change match the stated goal?\n"
    "     0.0 = no edit/write in this turn (read-only, shell command, text response) — always 0\n"
    "    +1.0 = edit precisely implements what was requested, matches goal exactly\n"
    "    +0.6 = edit moves in the right direction but is incomplete or has minor mismatches\n"
    "    +0.2 = edit is tangentially related to the goal (scaffolding, cleanup)\n"
    "    -0.5 = edit partially undoes or contradicts the goal\n"
    "    -1.0 = edit is clearly wrong or destructive relative to the stated goal\n\n"
    "  delta_info — did the tool output reveal NEW useful information?\n"
    "    +1.0 = unexpected discovery that changes how the problem should be approached\n"
    "    +0.6 = new concrete data (file contents, error details) that informs next steps\n"
    "    +0.3 = confirms a hypothesis, useful but expected\n"
    "     0.0 = expected success confirmation (test passed as predicted, commit succeeded)\n"
    "    -0.3 = output is repetitive or already known from earlier in the session\n"
    "    -1.0 = output is empty, misleading, or reveals the approach is fundamentally wrong\n"
)


def _build_prompt(
    exchange_idx: int,
    exchange_type: str,
    user_message: str,
    agent_action: str,
    evidence_text: str,
    prior_step_score: float | None,
) -> str:
    prior = f"{prior_step_score:.3f}" if prior_step_score is not None else "None"
    return (
        f"{_OBSERVER_SYSTEM}\n"
        f"INPUTS:\n"
        f"  exchange_index: {exchange_idx}\n"
        f"  exchange_type:  {exchange_type}\n"
        f"  user_message:   {user_message}\n"
        f"  agent_action:   {agent_action}\n"
        f"  prior_step_score: {prior}\n"
        f"  evidence_text:\n----\n{evidence_text or '(no evidence available)'}\n----\n\n"
        f"Return STRICT JSON only."
    )


# ── Pipeline ───────────────────────────────────────────────────────────────


class StepScoringPipeline:
    """Per-step observer scoring pipeline (Stage 2)."""

    def __init__(
        self,
        store: SQLiteStore,
        provider: ModelProvider,
        config: ModelConfig,
    ) -> None:
        self.store = store
        self.provider = provider
        self.config = config
        self.observer_model = config.observer_llm

    # ----- public ----------------------------------------------------------

    def score_session(
        self,
        session_id: str,
        *,
        force: bool = False,
        max_workers: int = 4,
        turn_descriptors_json: str | None = None,
    ) -> int:
        """Score all scorable exchanges for one session. Returns rows written.

        Idempotent: with force=False, exchanges already in step_scores are skipped.
        """
        if turn_descriptors_json is None:
            row = self.store.conn.execute(
                "SELECT turn_descriptors FROM sessions WHERE id = ?", (session_id,),
            ).fetchone()
            turn_descriptors_json = row[0] if row else None
        if not turn_descriptors_json:
            logger.info("session %s has no turn_descriptors — skipping", session_id)
            return 0

        ctx = _load_session_context(self.store, session_id, turn_descriptors_json)
        if ctx is None:
            return 0

        existing = set() if force else self.store.get_existing_step_score_indices(session_id)

        # Build list of scorable items (preserving order) along with each item's
        # preceding scorable TDs (for redundancy / features) and the running
        # prior_score chain. Score sequentially per session to keep the prior
        # score deterministic; parallelism happens across multiple sessions in
        # score_all if desired. But within a session we can still parallelize
        # observer calls — we sacrifice the prior_step_score chain for speed.

        # Per-spec: prior_step_score is observer's previous evidence_supports.
        # We compute it in two passes to permit concurrent observer calls:
        # 1) prepare deterministic inputs + observer prompts per scorable idx
        # 2) call observer in parallel (prior_step_score uses pre-pass estimate,
        #    derived from cost-only baseline of the immediately preceding score
        #    if available in DB; else None). This preserves correctness in the
        #    common single-pass case.

        # Pre-fetch the prior_score chain from any existing rows so threads
        # don't hit SQLite concurrently. For freshly scored exchanges in this
        # run, prior_score stays as the last DB-known value (acceptable: spec
        # says prior_step_score "may be None for first" — locally anchored hint
        # only, not load-bearing for the math).
        prior_rows = self.store.conn.execute(
            "SELECT exchange_idx, evidence_supports FROM step_scores "
            "WHERE session_id = ? ORDER BY exchange_idx",
            (session_id,),
        ).fetchall()
        prior_lookup = {int(r[0]): float(r[1]) for r in prior_rows}

        scorable_items = []
        preceding_scorable: list[TurnDescriptor] = []
        min_len = self.config.min_evidence_chars if self.config else 50
        for td in ctx.tds:
            if td.exchange_type not in SCORABLE_TYPES:
                continue
            if td.exchange_idx in existing:
                preceding_scorable.append(td)
                continue
            # Stub chunk filter: skip near-empty exchanges that can't carry
            # meaningful progress signal. Chunk-level embedding EDA (2026-06-13)
            # found cluster 0 (mean=21 chars) reliably identifies stubs that
            # fire spurious failure_critical at near-zero cost+progress.
            # Only applies when there are also no tool calls — an exchange with
            # tool calls but missing evidence text is a backfill gap, not a stub.
            evidence = ctx.evidence_by_idx.get(td.exchange_idx, "")
            has_tool_calls = bool(td.tool_calls)
            has_user_content = len(td.user_text) >= min_len or td.user_word_count > 3
            if not has_tool_calls and not has_user_content and len(evidence) < min_len:
                logger.debug(
                    "skipping stub exchange %d (evidence=%d chars, user_text=%d chars, no tool calls)",
                    td.exchange_idx, len(evidence), len(td.user_text),
                )
                preceding_scorable.append(td)
                continue
            # Snapshot prior score (largest known < this idx) at scheduling time
            prior_keys = [k for k in prior_lookup if k < td.exchange_idx]
            prior = prior_lookup[max(prior_keys)] if prior_keys else None
            scorable_items.append((td, list(preceding_scorable), prior))
            preceding_scorable.append(td)

        if not scorable_items:
            return 0

        results: list[StepScore] = []

        def _score_one(item):
            td, preceding, prior = item
            return self._score_one_exchange(ctx, td, preceding, prior_score=prior)

        workers = max(1, min(max_workers, len(scorable_items)))
        if workers == 1:
            for item in scorable_items:
                try:
                    results.append(_score_one(item))
                except Exception as e:
                    logger.warning("observer failed for %s/%d: %s",
                                   session_id, item[0].exchange_idx, e)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                fut_map = {pool.submit(_score_one, item): item for item in scorable_items}
                for fut in as_completed(fut_map):
                    item = fut_map[fut]
                    try:
                        results.append(fut.result())
                    except Exception as e:
                        logger.warning("observer failed for %s/%d: %s",
                                       session_id, item[0].exchange_idx, e)

        self.store.insert_step_scores_batch(results)
        return len(results)

    def score_all(self, *, force: bool = False, max_workers: int = 4) -> dict[str, int]:
        """Score every session that has turn_descriptors. Returns {session_id: rows_written}."""
        sessions = self.store.get_sessions_with_turn_descriptors()
        out: dict[str, int] = {}
        for sid, td_json in sessions:
            n = self.score_session(
                sid, force=force, max_workers=max_workers,
                turn_descriptors_json=td_json,
            )
            out[sid] = n
        return out

    # ----- internal --------------------------------------------------------

    def _score_one_exchange(
        self,
        ctx: _SessionContext,
        td: TurnDescriptor,
        preceding_scorable: list[TurnDescriptor],
        prior_score: float | None = None,
    ) -> StepScore:
        cost = compute_cost_vector(
            td, ctx.median_tokens, preceding_scorable, ctx.baseline_redundancy
        )
        features = compute_features(td, preceding_scorable)
        action = _agent_action_oneliner(td)
        evidence = ctx.evidence_by_idx.get(td.exchange_idx, "")
        user_msg = ctx.last_human_directive_by_idx.get(td.exchange_idx, "")

        prompt = _build_prompt(
            exchange_idx=td.exchange_idx,
            exchange_type=td.exchange_type,
            user_message=user_msg,
            agent_action=action,
            evidence_text=evidence,
            prior_step_score=prior_score,
        )
        parse_failed = False
        try:
            obs = self.provider.complete(
                prompt,
                model=self.observer_model,
                schema=_OBSERVER_SCHEMA,
                max_tokens=self.config.observer_max_tokens,
                temperature=0.0,
            )
            if isinstance(obs, str):
                obs = json.loads(obs)
            if not isinstance(obs, dict) or not obs.get("progress_vector"):
                raise ValueError("observer response missing progress_vector")
        except Exception as e:
            # A parse/schema failure produces NO signal about this exchange — it must
            # not be conflated with a genuine zero-progress score. Silently injecting
            # {0,0,0,0} here previously made failed observer calls indistinguishable
            # from real catastrophic failures, polluting the floor of the score
            # distribution and firing spurious failure_critical detections.
            logger.warning("observer JSON parse failed (%s); flagging as parse_failed", e)
            parse_failed = True
            obs = {"progress_vector": {"delta_test": 0.0, "delta_scope": 0.0,
                                       "delta_patch": 0.0, "delta_info": 0.0},
                   "agent_action_summary": action}

        progress = obs.get("progress_vector") or {}
        # Defensive clipping to [-1, +1]
        progress = {
            k: max(-1.0, min(1.0, float(progress.get(k, 0.0))))
            for k in ("delta_test", "delta_scope", "delta_patch", "delta_info")
        }
        summary = obs.get("agent_action_summary") or action
        if parse_failed:
            features = {**features, "observer_parse_failed": True}

        ev_supp = derive_evidence_supports(progress, cost)

        return StepScore(
            session_id=ctx.session_id,
            exchange_idx=td.exchange_idx,
            evidence_supports=round(ev_supp, 4),
            progress_vector=json.dumps(progress),
            cost_vector=json.dumps(cost),
            agent_action_summary=summary[:500],
            features=json.dumps(features),
            observer_model=self.observer_model,
        )

    def _lookup_prior_score(self, session_id: str, exchange_idx: int) -> float | None:
        row = self.store.conn.execute(
            "SELECT evidence_supports FROM step_scores "
            "WHERE session_id = ? AND exchange_idx < ? "
            "ORDER BY exchange_idx DESC LIMIT 1",
            (session_id, exchange_idx),
        ).fetchone()
        return float(row[0]) if row else None
