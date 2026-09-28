# [DEPRECATED] Within-Session Trajectory Signals: Per-Turn Scoring + Critical-Step Detection

> **DEPRECATED.** Soft-deprecated 2026-07-01 (superseded by
> [`2026-07-01-memory-analytics-v2-design.md`](2026-07-01-memory-analytics-v2-design.md));
> hard-deprecated 2026-07-11 — `ats analytics signals/step-scoring/critical-steps/light/reset-light`
> now print deprecation messages and exit, and `store.py`'s light-analytics methods are
> `NotImplementedError` stubs. See `design/design_decisions.md` entries
> "2026-07-01 — Memory analytics v2 architecture" and
> "2026-07-11 — Cleanup: deprecate Track 2 CLI, analytics light, graph walk".
>
> **Why:** the per-exchange observer scoring described below never produced reliable signal —
> cohort-1 validation precision was 0.33 against the ship-gate of ≥0.80, and the
> `step_scores`/`critical_steps` tables have 0 rows in production. It was retired, not fixed.
>
> **What replaced it:** the D-prompt (`chunk_analyzer.py`) now extracts a `patterns[]` field per
> chunk (~800 tokens) with four types — `strategy`, `decision`, `recovery`, `inefficiency` — stored
> as `memory_type ∈ {pattern_strategy, pattern_decision, pattern_recovery, pattern_inefficiency}` in
> the `memories` table. **`pattern_recovery` is the current recovery signal** — semantic,
> chunk-granularity LLM extraction ("stuck state → what was tried → what worked"), not per-step
> numeric scoring. See `design/2026-07-01-memory-analytics-v2-design.md` for the full spec and
> `experiments/exp07_ats_log_audit/` / `experiments/exp08_postfix_reextraction_audit/` for accuracy
> data on `pattern_recovery` specifically.
>
> Kept below for historical reference — the literature review and the gradient-detection reasoning
> may still be useful if per-step signal is revisited, but nothing in this doc describes the current
> pipeline.

> Design from brainstorming on 2026-06-09. Supersedes
> [`deprecated_segments_and_calibration.md`](deprecated_segments_and_calibration.md).
>
> Paired with [`memory_evolution.md`](memory_evolution.md) (cross-session
> memory layer), which consumes the per-turn signals this spec produces.
>
> Companion reading:
> [`notes/swe_trace_findings.md`](../notes/swe_trace_findings.md),
> [`notes/continual_learning_and_self_improvement_papers.md`](../notes/continual_learning_and_self_improvement_papers.md),
> [`notes/agent_self_improvement_skill_promotion_papers.md`](../notes/agent_self_improvement_skill_promotion_papers.md).

---

## Why the change

The previous design (segments + typed decision-point calibration)
predated a careful read of the literature. Reviewing 11 recent papers
on agent self-improvement, trace analysis, and memory (SWE-TRACE,
TELBench/DRIFT, Harness-1, the capability-collapse paper, Reflexion,
Voyager, SkillWeaver, Agent KB, SEAgent, MemoryAgentBench, When
Continual Learning Moves to Memory, SKILLFOUNDRY) surfaced three
findings that changed our architecture:

1. **No paper segments trajectories into task/topic boundaries before
   analysis.** They operate at per-step, per-claim-span, or whole-
   trajectory granularity. Segmentation as a pipeline stage is a
   design choice we invented without literature precedent and without
   strong necessity.
2. **SWE-TRACE's critical-step detection** —
   `K_t = { j : |s_j − s_{j-1}| ≥ δ_chg }` — is a more general and
   less brittle signal extractor than "agent assertion → committing
   action" pattern matching. It needs only a scalar progress score
   per step, no typed taxonomy, no semantic anchor extraction.
3. **DRIFT's claim-tracking** at span level confirms the per-step
   direction: spans are claim-bounded, not topic-bounded.

The previous decision-type taxonomy (`task_done`, `root_cause`, etc.)
imposed a structure that the literature doesn't ratify. It made the
pipeline brittle (a load-bearing extractor that depends on a fixed
typology) without buying signal quality beyond what per-step scoring
provides.

The exchange classifier survives the redesign — it's justified by real
production-log artifacts (skill injections, harness notifications) that
the literature doesn't encounter because they train on synthetic data
or held-out benchmarks.

### What we deprecate

- `session_segments` table and boundary detection pipeline.
- `decision_points` table and the typed decision-point extractor.
- Linguistic hedging extraction as a hard requirement.
- The "agent assertion → committing action" anchor pattern as a
  fundamental unit.

### What we keep

- Exchange classifier (taxonomy from
  [`design/exchange_classifier.md`](exchange_classifier.md)).
- `evidence_text` as a per-plugin contract (verbatim tool outputs).
- The observer LLM concept (now per-step, not per-decision).
- Bucketed evidence features (now per-step, computed over preceding
  window).
- Failure-mode labeling (now as a derived tag on critical steps, not
  on typed decision points).

---

## Why per-turn is the right unit for failure signal

### Literature alignment

| Paper | Unit | Mechanism |
|---|---|---|
| SWE-TRACE | per-step | `S(h, a)` per step; gradient detects critical moments |
| DRIFT / TELBench | claim-bounded span | per-claim evidence support check |
| Harness-1 | per-step semantic decision | externalized state per step |
| Capability collapse | per-step injection | "step-wise injection aligned with intermediate decision states" |
| Reflexion | whole-trajectory | one reflection per failed attempt |
| Voyager | per-skill | skill granularity (≈ multi-step) |

The dominant unit in recent (2025-2026) work is **per-step**. Older
work (Reflexion 2023) is whole-trajectory; newer work is finer. The
trend is clear: finer-grained units capture inflection moments that
trajectory-level signal averages out.

### Granularity tradeoffs

| Unit | Pros | Cons |
|---|---|---|
| Whole trajectory | Cheapest; matches existing Track 1 chunk granularity | Averages out inflections; can't localize where failure started |
| **Per turn (chosen)** | Catches gradients; aligns with existing `TurnDescriptor`; matches literature | Higher observer call volume |
| Sub-turn (per tool call) | Finest granularity | Most actions are atomic at turn level; cost explodes; diminishing returns |
| Segment (task/topic) | UI grouping benefit | No boundary signal is reliable; adds a brittle stage; not in literature |

Per turn matches the unit `TurnDescriptor` already uses, so we're not
introducing a new granularity primitive — we're putting more semantics
on an existing one.

### Why gradients matter

A trajectory-level score (`r_exec ∈ {0,1}`) suffers SWE-TRACE's three
pathologies: reward indifference, trajectory inflation, reward noise.
Per-step scoring with **gradient detection** lets us identify *which*
turn the agent went off-track — not just whether the session ended
poorly. This is the input the memory-evolution layer needs to mint
useful procedural memories ("when conditions X arose at step j and
evidence_supports dropped, the agent did Z and should have done Y").

---

## Architecture

```
        ┌─────────────────────────────────────────────────────────┐
        │  Ingestion (Track 1, unchanged)                          │
        │  raw trace → exchanges → chunks → D-prompt extraction    │
        │  → records, entities, occurrences, neutral memories      │
        └─────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────────────┐
        │  Per-plugin per-exchange artifacts (TurnDescriptor)      │
        │   • exchange_type   (existing exchange classifier)       │
        │   • evidence_text   (verbatim tool outputs, NEW field)   │
        │   • avg_think_tok_ratio (Gemini)                         │
        └─────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────────────┐
        │  Track 2 — per-step scoring                              │
        │   for each scorable exchange (SCORABLE_TYPES — see       │
        │   "Which exchanges get scored" below):                   │
        │     observer LLM returns:                                │
        │       progress_vector = (Δ_test, Δ_scope, Δ_patch,Δ_info)│
        │       cost_vector     = (C_tok, C_red)                   │
        │       evidence_supports ∈ [0,1]                          │
        │       agent_action_summary                               │
        │   stored in: step_scores                                 │
        └─────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────────────┐
        │  Critical-step detection (deterministic; no LLM)         │
        │   K_t = { j : |s_j − s_{j-1}| ≥ δ_chg    OR              │
        │              s_j ≤ δ_low                  OR              │
        │              s_j ≥ δ_high }                              │
        │   → critical_steps                                       │
        │   tagged: failure_critical | success_critical | recovery │
        └─────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────────────┐
        │  Memory-evolution induction (see memory_evolution.md)    │
        │   clusters of failure_critical with matching feature     │
        │     buckets → failure_response procedural memories       │
        │   clusters of success_critical with matching buckets     │
        │     → success_pattern procedural memories                │
        │   recovery_critical → recovery_pattern procedural memory │
        └─────────────────────────────────────────────────────────┘
```

Track 1 produces retrieval-time artifacts. Track 2 produces analytic
signals + drives Track 2 memory induction. Both share the same
`session_id`, `TurnDescriptor` per-exchange unit, and write into the
same `memories` table.

---

## Per-step scoring detail

### Which exchanges get scored (SCORABLE_TYPES)

Observer scoring should fire on any exchange where the agent is doing
substantive work — whether or not a fresh human prompt initiated it.
This is wider than the inflection detector's `GENUINE_TYPES` filter.

```
SCORABLE_TYPES = {
  "genuine_human",       # user + agent contribute
  "genuine_text",        # user spoke, agent responded with text
  "agent_continuation",  # Gemini multi-turn autonomous reasoning
  "summary_diff",        # Opencode diff context the agent reasons about
}

NON_SCORABLE_TYPES = {
  "skill_invocation",    # CC skill header — harness injection
  "task_notification",   # harness ping
  "context_continuation",# compaction marker
  "wakeup_injection",    # polling
}
```

This matters for **autonomous SDE-style sessions** (Gemini multi-turn,
CC `bypassPermissions`, Opencode background runs) where the agent
spends many consecutive exchanges thinking and acting without fresh
human input. The empirical data in
[`design/exchange_classifier.md`](exchange_classifier.md)
shows that `agent_continuation` is **82%** of exchanges in a typical
Gemini session. Filtering them out would make the observer essentially
silent on the most autonomous portions of work, defeating the purpose
of per-step scoring.

**Why this differs from the inflection detector's filter** — the
inflection detector uses `GENUINE_TYPES = {genuine_human,
genuine_text}` because it computes sliding-window z-scores, and
consecutive zero-tool agent_continuation exchanges create a degenerate
zero-entropy window. Per-step observer scoring has no such constraint
— each step is scored independently, no window averaging — so the
broader filter is safe and necessary.

Each downstream consumer picks its own filter:
- Inflection detector → `GENUINE_TYPES`
- Per-step observer scoring → `SCORABLE_TYPES`
- Failure keyword density → all exchanges (already harness-agnostic)

### Observer inputs

- The exchange's user/agent text (already in trace)
- `evidence_text` (verbatim tool outputs from this exchange and a
  short preceding window)
- The agent's stated action (next move / committed change)
- *No* chunk_summary (avoids LLM-on-LLM contamination)
- *No* segment label (we don't have segments)

### Observer outputs

Per substantive exchange:

```
{
  "progress_vector": {
    "delta_test":   float in [-1, +1],  // LLM-judged
    "delta_scope":  float in [-1, +1],  // LLM-judged
    "delta_patch":  float in [-1, +1],  // LLM-judged
    "delta_info":   float in [-1, +1]   // LLM-judged
  },
  "cost_vector": {
    "tokens":       float in [0, +1],   // deterministic, from token counts
    "redundancy":   float in [0, +1]    // deterministic, from action match
  },
  "evidence_supports": float in [0, 1],  // scalar derived from above
  "agent_action_summary": "short structured one-liner"
}
```

### How each value is derived

**Progress dimensions — judged by the observer LLM** from the evidence
bundle. Each scalar in `[-1, +1]`: positive = the step contributed to
progress on that dimension; zero = neutral; negative = the step set
the agent back on that dimension.

| Dimension | What the observer judges | Operational definition for our setting |
|---|---|---|
| `delta_test` | Did the step provide evidence the work is correct? | Tests ran and passed/failed, command exit codes, file content checks. For non-coding sessions: any verification action against an observable signal. SWE-TRACE has an oracle (the test suite); we have observer-judged. |
| `delta_scope` | Did the action target something aligned with the apparent goal? | Compare action's target (file path, command, query) against the **most recent `genuine_human` directive** in the session. +1 = exact match; 0 = plausibly related; -1 = unrelated. For non-SWE traces, this is the most general proxy we have for "scope" without a test-relevance graph. |
| `delta_patch` | For edit/write actions: does the edit match the apparent intent? | Observer compares the proposed edit's content against the goal expressed in recent user messages and the evidence already gathered. For non-edit actions, defaults to 0. |
| `delta_info` | Did the step reveal new useful information? | Observer judges whether the tool output contains content that should change the agent's beliefs about how to proceed. Empty/redundant tool output → 0 or negative; novel informative output → positive. |

Each dimension is grounded in what's in `evidence_text` + the agent's
action; observer is instructed NOT to use chunk_summary, agent's
reasoning text, or hedging language as input to the judgment.

**Cost dimensions — computed deterministically** (no LLM judgment
needed):

| Dimension | Formula | Scale |
|---|---|---|
| `tokens` | `clip(exchange_token_count / session_median_tokens, 0, 3) / 3` | Normalized to `[0, 1]`. Triple the running session median = max 1.0. Captures token-bloated exchanges. |
| `redundancy` | fraction of `(tool_name, primary_target)` pairs in this exchange that match pairs from the preceding K steps in the same session | `[0, 1]`. 0 = all actions novel; 1 = all actions exact repeats. K = 10 by default. |

These don't need observer judgment — they're countable from
`TurnDescriptor` data already in the DB.

### `evidence_supports` — derived scalar

A single `[0, 1]` summary used for critical-step detection and
retrieval ranking:

```
evidence_supports = clip(
  Σ_i λ_i · progress_i  −  Σ_j μ_j · cost_j,
  0, 1
)
```

Default weights (tunable):
- `λ_test = 0.30, λ_scope = 0.25, λ_patch = 0.20, λ_info = 0.25`
- `μ_tokens = 0.15, μ_redundancy = 0.25`

The vectors are kept separately so downstream analytics can use them
directly. E.g., "this session had high `delta_test` but high
`redundancy`" → flagged as `trajectory_inflation` even though
`evidence_supports` is mid-range.

### What the observer is given (prompt inputs)

The observer LLM gets one structured prompt per substantive exchange:

```
INPUTS:
  - exchange_index: int
  - exchange_type: SCORABLE_TYPES enum
  - user_message: str (the most recent genuine_human directive in scope, NOT
    necessarily this exchange's user text — could be from k turns earlier)
  - agent_action: structured one-liner of what the agent did this turn
    (extracted deterministically from tool_calls + content)
  - evidence_text: verbatim tool outputs from this exchange + last 2-3
    preceding exchanges in same session, token-capped at 8K
  - prior_step_score: float | None (this session's previous
    evidence_supports; observer doesn't see the full vector, only the
    scalar, to keep judgments locally anchored without contaminating
    them with prior progress dimensions)

The observer is explicitly NOT given:
  - chunk_summary or D-prompt outputs (avoid LLM-on-LLM contamination)
  - agent's reasoning/thinking text from the trace (avoid the agent's
    confidence leaking into the observer's judgment)
  - segments or topic labels (we don't have those)
  - the cost_vector (computed separately and merged after observer returns)

OUTPUT:
  Strict JSON matching the observer-output schema above.
```

#### Exact system prompt (as of 2026-06-14)

Key change from 2026-06-13: added explicit float-valued intermediate examples for all four dims.
Without these, a thinking model treats the 3-anchor description as a 3-class classifier and
outputs only {-1, 0, +1}, causing binary score jumps from normal mode switches (edit→read).

```
You are an OBSERVER scoring one step of an agent trajectory.
Judge ONLY from the supplied evidence_text and agent_action.
Do NOT use the agent's reasoning text, chunk summaries, or any external knowledge.

Output JSON with two fields:
  progress_vector: {delta_test, delta_scope, delta_patch, delta_info}, each a float in [-1, +1].
  agent_action_summary: a short one-line description of what the agent did.

IMPORTANT: Use the FULL continuous range. Most turns are partial — typical values are 0.3, -0.4, 0.7.
Reserve +1.0 and -1.0 for unambiguous extremes. Do not default to integers.

Operational definitions:

  delta_test — did the tool output provide evidence the work is correct?
    +1.0 = test suite ran and all tests passed, exit code 0 confirmed
    +0.7 = partial pass (some tests passed, output looks correct)
    +0.3 = ran but no clear pass/fail signal (e.g. script executed, no errors)
     0.0 = no verification action taken (read-only turn, text-only response)
    -0.5 = test output shows failures or errors but not catastrophic
    -1.0 = hard failure (exception, test crash, exit 1 with clear error)

  delta_scope — does the agent's action target align with the user's most recent directive?
    +1.0 = action directly addresses the exact file/component the user specified
    +0.6 = action is in the right subsystem but not the precise target
     0.0 = plausible but could be tangential (e.g. reading a config while fixing logic)
    -0.5 = action targets something loosely related but probably not what user meant
    -1.0 = agent is clearly working on something unrelated to the user's directive

  delta_patch — for edit/write actions, how well does the change match the stated goal?
     0.0 = no edit/write in this turn (read-only, shell command, text response) — always 0
    +1.0 = edit precisely implements what was requested, matches goal exactly
    +0.6 = edit moves in the right direction but is incomplete or has minor mismatches
    +0.2 = edit is tangentially related to the goal (scaffolding, cleanup)
    -0.5 = edit partially undoes or contradicts the goal
    -1.0 = edit is clearly wrong or destructive relative to the stated goal

  delta_info — did the tool output reveal NEW useful information?
    +1.0 = unexpected discovery that changes how the problem should be approached
    +0.6 = new concrete data (file contents, error details) that informs next steps
    +0.3 = confirms a hypothesis, useful but expected
     0.0 = expected success confirmation (test passed as predicted, commit succeeded)
    -0.3 = output is repetitive or already known from earlier in the session
    -1.0 = output is empty, misleading, or reveals the approach is fundamentally wrong
```

Followed by a user-turn block:

```
INPUTS:
  exchange_index: {idx}
  exchange_type:  {type}
  user_message:   {last genuine_human directive, capped 800 chars}
  agent_action:   {deterministic one-liner from tool_calls, e.g. "Read(foo.py); Edit(bar.py)"}
  prior_step_score: {float or None}
  evidence_text:
----
{verbatim tool outputs, capped 8000 chars, oldest truncated first}
----

Return STRICT JSON only.
```

#### Token count approximation

The token count used for `cost_vector.tokens` is approximate (no real tokenizer):

```python
exchange_token_count = max(1, user_word_count + 50 * n_tool_calls)
```

`user_word_count` comes from `TurnDescriptor`; `50` is a fixed per-call overhead constant
covering the tool input + output schema. This is monotonic with real cost for our purposes.
The normalization to `[0,1]` is against the session median across scorable exchanges.

This separation is informed by the capability-collapse paper
([notes](../notes/continual_learning_and_self_improvement_papers.md)):
the observer must be a separate, lower-context instance than the
agent that produced the trace.

### Per-turn cost mitigation

- Restrict to `exchange_type ∈ SCORABLE_TYPES` (see above).
  Harness-injected exchanges (`NON_SCORABLE_TYPES`) contribute nothing
  and are skipped — but autonomous-agent exchanges
  (`agent_continuation`, `summary_diff`) are kept.
- Use a cheap observer model (Haiku-class).
- **Adaptive sampling** (optional, v1.5): skip steps where keyword
  density was low AND the prior step's `evidence_supports` was near
  the running median. Spend calls where signals suggest something's
  happening. Cuts call volume ~40-60% on typical sessions.

Net cost likely comparable to the previous design once the (now
removed) decision-point extractor calls are accounted for.

---

## Critical-step detection

### Definition

A **critical step** is an exchange that warrants attention from
downstream analytics — either because the agent is in an intrinsically
notable state (very high or very low evidence support), or because
the step represents a significant transition (large gradient in
evidence support relative to the prior step). Critical steps are the
anchors at which Track 2 mints candidate memories.

### Grounding — adapted from SWE-TRACE

The formula is taken directly from SWE-TRACE's memory-buffer
critical-step detector (their Section 4.1, equation defining `K_t`):

> `K_t = { j ≤ t : s_j^prm ≥ δ_abs   OR   |s_j^prm − s_{j-1}^prm| ≥ δ_chg }`
> where `δ_abs` selects intrinsically important steps and `δ_chg`
> captures decision points that significantly alter progress.

They use this to decide which historical steps to retain as verbatim
anchors in the agent's memory buffer when context overflows.

**Two ideas from SWE-TRACE that we adopt unchanged:**

1. **Absolute threshold (`δ_abs`)** — a step where the progress score
   itself crosses a notable level is critical regardless of how much
   it changed from the prior step. Captures *states*.
2. **Gradient threshold (`δ_chg`)** — a step where the score moves
   meaningfully relative to the prior step is critical regardless of
   the absolute value. Captures *transitions*.

The combination is the key insight: states and transitions are
complementary. A stable low score is critical (the agent is stuck);
a sudden drop is critical (the agent just lost the plot); a stable
high is critical (the agent is doing well in a non-trivial way);
a sudden rise is critical (the agent just recovered).

### Our adaptation

We extend SWE-TRACE's formula in two ways because our setting is
different — we want **symmetric detection of both well-supported and
poorly-supported moments**, not just "which steps to keep as
agent-side memory anchors":

1. **Split the absolute threshold into `δ_high` AND `δ_low`.** SWE-TRACE
   uses one `δ_abs` because they only care about retaining
   high-information steps. We care about both:
   - `s_j ≥ δ_high` → `success_critical` (agent committed to a
     well-supported non-obvious action)
   - `s_j ≤ δ_low` → `failure_critical` (agent committed to a
     poorly-supported action)
2. **Sign the gradient.** SWE-TRACE uses `|s_j − s_{j-1}| ≥ δ_chg`
   (absolute value) because they retain steps with large change in
   either direction. We distinguish the direction:
   - `s_j − s_{j-1} ≤ −δ_chg` → adds to `failure_critical` (negative
     jump; agent fell off)
   - `s_j − s_{j-1} ≥ +δ_chg` with prior k steps at `s ≤ δ_low`
     → `recovery_critical` (positive jump after sustained low; agent
     recovered)

### Combined criterion

```
K_t  =  { j : s_j ≤ δ_low }                            ──► failure_critical
       ∪ { j : s_j − s_{j-1} ≤ −δ_chg }                ──► failure_critical
       ∪ { j : s_j ≥ δ_high  AND  step is non-trivial} ──► success_critical
       ∪ { j : s_j − s_{j-1} ≥ +δ_chg
               AND last k steps had s ≤ δ_low }        ──► recovery_critical
```

| Tag | Condition | What it represents |
|---|---|---|
| `failure_critical` | `s_j ≤ δ_low` OR `s_j − s_{j-1} ≤ −δ_chg` | Agent in / falling into a poorly-supported state |
| `success_critical` | `s_j ≥ δ_high` AND step was non-trivial (cost > 0) | Agent in a well-supported non-obvious state |
| `recovery_critical` | `s_j − s_{j-1} ≥ +δ_chg` AND prior k steps had `s ≤ δ_low` | Agent recovering from a stuck state |

Thresholds (`δ_low`, `δ_high`, `δ_chg`) are tuned on labeled examples.
Initial defaults: `δ_low = 0.35`, `δ_high = 0.8`, `δ_chg = 0.25`,
`k = 3`. The "non-trivial" cost check on `success_critical` is to
avoid flagging trivially obvious actions as successes (e.g., a single
file read with no preceding ambiguity).

The detection is deterministic given the scores — no LLM, fully
reproducible. The same `step_scores` re-run with different thresholds
will deterministically produce different `critical_steps` sets,
making threshold tuning straightforward.

### Why this is the right shape (logic check)

| Pattern | Captured by | Why it matters for our consumers |
|---|---|---|
| Stable high `s_j` | `δ_high` | Reproducible good actions — mint `success_pattern` |
| Stable low `s_j` | `δ_low` | Reproducible bad actions — mint `failure_response` |
| Sudden drop | `−δ_chg` | Inflection: agent just made a bad bet — mint `failure_response` |
| Sudden rise after low | `+δ_chg` after low run | Recovery: agent solved something — mint `recovery_pattern` |
| Mild fluctuation | none of the above | Skipped — normal variation, no memory minted |

The shape covers all four corners of (state × transition). Stable
extremes and inflection moments both fire; mild noise doesn't. This
is the same insight SWE-TRACE uses for in-context memory; we apply it
to cross-session memory induction.

### Failure-mode tagging on critical steps

When a step is `failure_critical`, an additional categorical tag based
on which `progress_vector` component is most negative:

| Dominant negative dim | failure_mode |
|---|---|
| `delta_scope` | `wrong_target` |
| `delta_patch` | `tool_misread` |
| `delta_info` | `evidence_thin` |
| `delta_test` | `premature_done` |
| dominant `cost_vector` | `trajectory_inflation` |

This taxonomy survives from the prior design. Now it's derived from
the score vector rather than being asked of a decision-point extractor.

---

## Within-session candidate memory induction (symmetric across signals)

Every `critical_step` produces a **candidate memory immediately, at the
session it was detected in**. Track 2 induction is per-session, not
cross-session. The cross-session work — clustering, merging,
posterior consolidation — happens in a separate layer described in
[`memory_evolution.md`](memory_evolution.md).

This matches what the literature actually does at the per-trajectory
mint level:

| Paper | Per-trajectory mint mechanism |
|---|---|
| Voyager | Self-verified successful program → added to skill library directly |
| SkillWeaver | Reward-model-confirmed trajectory → Python API via state-action distillation, plus per-skill test/debug |
| SWE-TRACE | Per-step critical anchors (`m_j = (j, a_j, o_j)`) retained in memory buffer for the trajectory; PRM-score gradient detects which steps |

None of them imposes a cross-trajectory recurrence threshold before
storage. Our prior design imposed "≥2 sessions sharing feature buckets"
— that was Bayesian-Agent's specific design choice, applied beyond its
scope. Removing it.

### Three induction pathways (all per-session, all symmetric)

For each `critical_step` detected within a session, an induction LLM
call mints one candidate memory:

**1. `success_pattern`** — from `success_critical` step

The agent committed to a well-supported non-obvious action. Extract:
when [condition derived from preceding feature buckets], do [action
summarized from this step] because [justification from evidence].

**2. `recovery_pattern`** — from `recovery_critical` step

The agent was stuck for k preceding steps then recovered. Extract:
when stuck in [state characterized by the prior-failure feature
buckets], try [recovery action from this step].

**3. `failure_response`** — from `failure_critical` step

The agent committed to a poorly-supported action under the labeled
failure_mode. Extract: when [condition + failure_mode features], avoid
[the action that was taken] because [evidence gap]; instead [the
better action if observer can suggest one].

All three prompts share the **principle-level over instance-level**
constraint from the capability-collapse paper (see
[notes](../notes/continual_learning_and_self_improvement_papers.md)) —
"prefer transferable rule over session-specific detail."

### Mint policy

- **Mint a candidate for every critical step**, not just clusters.
  Initial library will be noisy; the cross-session consolidation
  pipeline cleans up via embedding clustering + LLM-driven merge.
- Candidates start in `lifecycle_state = 'exploring'` (already in the
  schema) and carry full provenance to the originating
  `critical_step.id`.
- Eligible for retrieval immediately, but downweighted by `exploring`
  state until consolidation promotes them.
- A weak prior is assigned (low `posterior_n`, neutral
  `posterior_mean`); provenance_quality weighting (below) adjusts it.

### Noise control — observe first, filter later

We deliberately don't pre-filter at mint time. The plan:

1. Build the per-session mint pipeline with no within-session filter.
2. Run on the existing corpus; measure: candidate volume per session,
   cluster yield in consolidation, ratio of promoted vs. retired
   candidates over time.
3. **Add filters only if measured noise is too high.** Likely
   candidates if needed:
   - Skip critical steps where `agent_action_summary` is a no-op or
     pure tool result echo.
   - Skip steps where the observer's progress vector magnitude is
     below a floor (the gradient was triggered by noise, not signal).
   - Skip when an existing memory already covers the same
     (feature bucket, action) pair with high posterior_n.

The hybrid Voyager/SkillWeaver/SWE-TRACE pattern is "mint freely, let
the posterior sort it out." We follow that and add filters only with
data.

### Provenance-quality weighting (capability collapse mitigation)

From
[continual_learning_and_self_improvement_papers.md](../notes/continual_learning_and_self_improvement_papers.md),
the same risk surface applies symmetrically:

- Candidates from sessions with high overall `evidence_supports`
  distribution get **higher prior weight** (`provenance_quality` high).
- Candidates from sessions with many `failure_critical` steps get
  **lower prior weight** even when the induced lesson is plausible —
  the agent may have been systematically confused.
- Applies to all three pathways: a "success" observed in a generally
  degraded session is just as suspect as a "lesson" from one.

### Same downstream lifecycle

All three induction methods write into the same `memories` table with
the appropriate `extraction_method` ∈ `{success_pattern,
recovery_pattern, failure_response}`. From there, the
[memory_evolution.md](memory_evolution.md) cross-session consolidation
pipeline handles clustering, posterior updates, and rewrite-policy
actions.

---

## Schema

```sql
-- step_scores: per-substantive-exchange observer output
CREATE TABLE step_scores (
  session_id          TEXT NOT NULL REFERENCES sessions(id),
  exchange_idx        INTEGER NOT NULL,
  evidence_supports   REAL NOT NULL,            -- [0,1]
  progress_vector     TEXT NOT NULL,            -- JSON
  cost_vector         TEXT NOT NULL,            -- JSON
  agent_action_summary TEXT,
  features            TEXT NOT NULL,            -- JSON: bucketed evidence features
  observer_model      TEXT,                     -- which model produced this
  scored_at           TEXT NOT NULL,
  PRIMARY KEY (session_id, exchange_idx)
);

CREATE INDEX idx_step_scores_session ON step_scores(session_id);
CREATE INDEX idx_step_scores_low     ON step_scores(evidence_supports);

-- critical_steps: derived from step_scores by gradient + threshold
CREATE TABLE critical_steps (
  id              TEXT PRIMARY KEY,
  session_id      TEXT NOT NULL REFERENCES sessions(id),
  exchange_idx    INTEGER NOT NULL,
  tag             TEXT NOT NULL,        -- 'failure_critical' | 'success_critical' | 'recovery_critical'
  failure_mode    TEXT,                 -- nullable; tagged for failure_critical
  delta           REAL NOT NULL,        -- evidence_supports gradient at this step
  score           REAL NOT NULL,        -- evidence_supports at this step
  features        TEXT NOT NULL,        -- JSON: bucketed features (carried for memory induction)
  detected_at     TEXT NOT NULL
);

CREATE INDEX idx_critical_steps_session ON critical_steps(session_id);
CREATE INDEX idx_critical_steps_tag     ON critical_steps(tag);

-- TurnDescriptor schema additions (already partly in flight)
-- New: evidence_text (verbatim tool outputs for the exchange)
-- Existing per design/exchange_classifier.md:
--   exchange_type, avg_think_tok_ratio
```

The `features` JSON on both tables uses the bucketed dimensions from
the previous design (carried unchanged):

```
{
  "tokens_bucket":      "<2k" | "2-8k" | "8-32k" | ">32k",
  "turns_bucket":       "1" | "2-4" | "5-15" | ">15",
  "tool_diversity_bucket": "1" | "2-3" | "4-6" | ">6",
  "error_rate_bucket":  "0%" | "<25%" | "25-50%" | ">50%",
  "context":            <inferred from preceding turns; cheap classifier>
}
```

Buckets are now computed over the **preceding N-turn window** rather
than per-segment.

---

## Integration with Track 1 (unchanged)

Track 1's chunking, D-prompt extraction, embeddings, and entity
resolution are unchanged. Track 2's per-step pipeline operates on the
same exchanges but at finer granularity:

| | **Track 1** | **Track 2** |
|---|---|---|
| Unit | chunk (~800 tokens) | exchange (per substantive turn) |
| Extraction | D-prompt: summary + entities + memories | observer: progress/cost vector + evidence_supports |
| Storage | `records`, `entities`, `occurrences` | `step_scores`, `critical_steps`, `TurnDescriptor.evidence_text` |
| Memory induction | `explicit` (D-prompt), `frequency` (analytics_light) | `success_pattern`, `recovery_pattern`, `failure_response` |
| Memory writes target | `memories` (shared) | `memories` (shared) |
| Cost | LLM per chunk (~50/session) | small LLM per scorable exchange (~50-300/session for human-driven, can be 200-500 for autonomous SDE; adaptive sampling reduces both) |

Track 1 doesn't need to know Track 2 exists. Track 2 reads chunks for
evidence context when the observer benefits from surrounding tool
output, but otherwise operates on `TurnDescriptor.evidence_text`.

---

## FP suppression (implemented)

Two gates run before critical-step detection to filter sessions/exchanges
where the observer's progress dimensions don't apply cleanly:

**1. Session-level `human_tool_ratio` gate** (`CriticalStepConfig.max_human_tool_ratio = 8.0`)

Computed from `sessions.turn_descriptors` as `genuine_human_turns / n_tool_calls`.
Skip detection for the whole session if `n_tool_calls == 0` or ratio > threshold.
Motivated by embedding EDA (see `experiments/EXP_EMBEDDING_SIGNALS.md`) which found
`human_tool_ratio` has RF importance 0.20 and r=0.75 with the UMAP autonomy axis —
the strongest single separator of implementation vs. conversation sessions.
Replaces the prior `min_tool_using_exchanges` count gate, which was insensitive
to the balance between human turns and tool calls.

| Session type | Typical ratio | Outcome |
|---|---|---|
| Pure conversation (0 tool calls) | ∞ | always skipped |
| Design/exploration (scattered reads) | 15–20 | skipped |
| Implementation (ATS sessions) | 0.5–3.0 | detected |
| Long autonomous coding | 0.15–0.5 | detected |

**2. Exchange-level stub chunk filter** (`ModelConfig.min_evidence_chars = 50`)

In `StepScoringPipeline.score_session`, exchanges are skipped before the observer
call if they have no tool calls AND combined evidence + user content is below 50
chars. Chunk-level embedding EDA found cluster 0 (mean=21 chars) reliably identifies
near-empty stubs that trivially fire `failure_critical` at near-zero cost/progress.
Exchanges with tool calls are always scored even if evidence text is missing
(backfill gap, not a stub).

**3. First-exchange skip** (`CriticalStepConfig.skip_first_exchange = True`)

`exchange_idx == 0` is skipped unconditionally. Resume-after-break artifact: the
first scored exchange in a resumed session looks token-bloated relative to the empty
session median, firing spurious `trajectory_inflation`.

---

## Critical-step detection — calibrated thresholds (as of 2026-06-14)

With session-percentile calibration enabled (`use_session_percentile_thresholds=True`) and
`gemma4:e4b` as observer, the effective thresholds for cc:7 (67 exchanges) are:

| Parameter | Config default | Effective (session-calibrated) |
|-----------|---------------|-------------------------------|
| `eff_low` | `delta_low=0.10` | `max(0.10, p20=0.304) = 0.304` |
| `eff_high` | `delta_high=0.20` | `max(0.20, p80=0.794) = 0.794` |
| `eff_change` | `delta_change=0.10` | `0.10` (not percentile-adjusted) |

The `negative_jump` rule fires when `delta ≤ −0.10`. At that sensitivity, a **mode switch
from an edit turn (`dp=1`) to a read-only turn (`dp=0`)** drops evidence_supports by
`0.20 × 1 = 0.20`, which exceeds the threshold. This is the primary source of FP
`failure_critical` in implementation sessions — it fires on normal read→edit→read rhythms,
not just real regressions.

**Classification rules (exact)**

| Tag | Fires when |
|-----|-----------|
| `failure_critical` | `score ≤ eff_low` (absolute_low) OR `delta ≤ −eff_change` (negative_jump). Pure absolute_low (no jump) additionally requires corroboration: `tokens > 0.3` OR `error_rate_bucket ≠ "0%"` |
| `success_critical` | `score ≥ eff_high` AND `tokens > 0.10` |
| `recovery_critical` | `delta ≥ +eff_change` AND at least `k=3` of the prior `k+1` steps had `score ≤ eff_low` |

`eff_change` is the same threshold for both negative_jump and recovery — it is not
percentile-adjusted.

---

## Score integrity — observer parse-failure handling (as of 2026-06-16)

When the observer LLM call fails (network error, schema mismatch, truncated JSON), the pipeline
must not silently inject a fake zero score that is indistinguishable from a genuine catastrophic
failure. Two defects that a silent zero-fill would cause:

1. **False `failure_critical` detections** — a parse-failure zero looks identical to a real
   floor score, triggering `absolute_low` classification with no real evidence.
2. **Corrupted session-percentile thresholds** — fake zeros drag `p20` down, loosening `eff_low`
   for the entire session, causing the threshold calibration to under-detect real failures.

**Fix (implemented in `step_scoring.py` + `critical_steps.py`):**

```python
# step_scoring.py — _score_one_exchange()
parse_failed = False
try:
    obs = provider.complete(prompt, ...)
    ...
except Exception as e:
    parse_failed = True
    obs = {"progress_vector": {all dims: 0.0}, ...}   # placeholder, NOT a real score
if parse_failed:
    features["observer_parse_failed"] = True           # stored in step_scores.features JSON
```

```python
# critical_steps.py — detection pipeline
def _is_parse_failed(step: _StepRow) -> bool:
    return bool(json.loads(step.features).get("observer_parse_failed"))

def _classify_step(j, steps, ...):
    if _is_parse_failed(steps[j]):
        return None                        # skip — no classification

    # Walk back past any parse-failed predecessor when computing delta,
    # so a fake zero never corrupts a neighboring step's gradient.
    prev_idx = j - 1
    while prev_idx >= 0 and _is_parse_failed(steps[prev_idx]):
        prev_idx -= 1

    # Recovery window excludes parse-failed steps from stuck-count.
    window = [s for s in steps[window_start:j] if not _is_parse_failed(s)]

def _session_thresholds(steps, cfg):
    valid_steps = [s for s in steps if not _is_parse_failed(s)]
    # Uses only valid_steps for p20/p80 percentile calculation.
    ...
```

Parse-failed steps are stored in `step_scores` as before (with placeholder zeros) — they remain
auditable and the flag is queryable. They just produce no `critical_steps` row and don't enter
threshold calibration.

---

## Token cost and redundancy — what they measure

**`cost_vector.tokens`** = `clip(exchange_token_count / session_median, 0, 3) / 3`

Where `exchange_token_count = max(1, user_word_count + 50 × n_tool_calls)`. This means:
- **Pure text exchanges** (0 tool calls): tokens ≈ user_words / session_median. For a typical
  session with median ~5 tool calls (median≈250 tok budget), a text-only reply (50–100 words)
  scores tok ≈ 0.06–0.10.
- **One tool call**: +50 tokens → significant jump in cost score.
- **5+ tool calls**: tok approaches 0.5–1.0.

Low token cost does NOT imply a user pivot or unproductive turn. It means **no tool calls**.
That includes: agent answering a question in prose, reporting results, discussing a design,
confirming a commit. All of these are normal productive exchanges.

**`cost_vector.redundancy`** (as of 2026-06-16, session-relative) = excess of this exchange's
`(tool_name, primary_target)` repeat rate (over the preceding K=10 window) above the session's
own median repeat rate, capped at 0.5.

```python
raw_redundancy = matched_pairs / total_pairs            # uncapped, this exchange
session_baseline = median(raw_redundancy for all scorable exchanges in session)
excess = max(0.0, raw_redundancy - session_baseline)
redundancy = min(0.5, excess)
```

Why session-relative: cc:7 (and most implementation sessions) use only ~5 distinct tools
(`Read`, `Edit`, `Write`, `Bash`, `Agent`) across hundreds of calls on a few dozen files — normal
edit→test→edit iteration naturally re-matches `(tool, target)` pairs within a K=10 window. A
fixed global cap of 0.5 treated this normal iteration as a near-constant `−0.125` penalty on
every exchange (`−0.25 × 0.5`), saturating almost all exchanges and providing no discrimination.
Session-relative excess judges each session against its own normal repetition rate — only
redundancy *above* what's typical for that session (e.g. genuinely re-reading the same file 3×
in a row with no new action in between) gets penalized. Still capped at 0.5 so excess redundancy
alone can't cancel `delta_info` entirely.

Redundancy is **not a classification gate** — it feeds only into `evidence_supports` via the
formula and is not checked directly in `_classify_step()`.

---

## Open questions

1. **Observer scoring calibration** — ~~observer outputs integer-like values {−1, 0, +1}~~ **RESOLVED 2026-06-14.**
   The old 3-anchor prompt caused thinking models to treat scoring as 3-class classification,
   outputting only integers. Fixed by adding explicit float intermediate-value examples for all
   four dims (e.g. "+0.7 = partial pass", "-0.3 = output is repetitive") and an instruction:
   "IMPORTANT: Use the FULL continuous range. Most turns are partial — typical values are 0.3,
   -0.4, 0.7. Reserve +1.0 and -1.0 for unambiguous extremes. Do not default to integers."
   Verified via direct observer calls on 4 known exchanges (ex7, ex16, ex17, ex32) showing
   genuine float outputs. See "Exact system prompt" section above.
2. **Adaptive sampling thresholds** — at what `evidence_supports` and
   gradient values do we skip steps? Tune on labeled corpus after
   Stage 4 ship-gate is passed.
3. **`delta_scope` operationalization for non-SWE traces** — SWE-TRACE
   has a test-relevance graph defining scope. For general agent
   traces (design discussion, debugging, refactoring), scope needs a
   more general proxy: alignment between the user's expressed intent
   in the most recent `human_directive` and the agent's action target.
4. **Context for `delta_test`** — `test` here generalizes to "any
   verification the agent could run." For non-SWE sessions, this
   collapses to `delta_info` often. Worth explicitly redefining the
   four progress dimensions for non-coding contexts.
5. **Failure-mode taxonomy v1 vs v2** — currently 5 modes. Watch for
   `unknown` becoming dominant; that signals a missing category.
6. **Validation** — Stage 4 labeling viewer (HTML, TP/FP buttons, FN
   flag buttons on ±3 surrounding turns, live precision + recall lower
   bound sidebar). Ship-gate: `precision ≥ 0.80 AND ≥ 20 labeled`.
   Current status: cohort-1 precision=0.33 (FAILED — CC discussion
   sessions dominated); cohort-2 in progress with FP suppression fixes.
7. **Recall measurement** — current FN flagging via ⚑ buttons on the
   ±3 surrounding window gives a lower bound only (events outside
   the window are invisible). Full recall enumeration requires either
   labeling ALL exchanges as critical/not (expensive) or a held-out
   session with known ground truth.
8. **Raw-trace session embeddings** — EDA showed summary embeddings
   (current `ats embed`) capture semantic topic; raw head+tail
   embeddings capture harness structure (tool density, autonomy).
   Adding a second session embedding to the pipeline would enable
   automated session routing beyond `human_tool_ratio` heuristics.
   See `experiments/EXP_EMBEDDING_SIGNALS.md` for the proof-of-concept.
9. **Stage 5 / 7 (pending ship-gate)** — within-session candidate
   memory mint and cross-session consolidation pipeline are gated on
   Stage 4 reaching the ship-gate. Not yet built.

---

## Non-goals

- Real-time per-turn observer scoring during agent execution.
  Offline analytics only.
- Replacing Track 1's chunking or D-prompt extraction.
- Resurrecting segmentation as a hard pipeline stage. (Derived views
  that *cluster* critical steps into UI groupings are fine —
  presentation, not analytics.)
- Per-tool-call sub-turn granularity. Most actions are atomic at the
  turn level; further granularity adds cost without payoff.
- TXCONFORMAL / FDR shortlisting (no hypothesis shortlist in our
  setting).

---

## References

- [`notes/swe_trace_findings.md`](../notes/swe_trace_findings.md) —
  SWE-TRACE's per-step oracle + critical-step gradient detection +
  rubric structure. The direct precedent for this design.
- [`notes/continual_learning_and_self_improvement_papers.md`](../notes/continual_learning_and_self_improvement_papers.md) —
  DRIFT/TELBench, Harness-1, capability collapse. Confirms per-step
  direction and surfaces the provenance-quality mitigation.
- [`notes/agent_self_improvement_skill_promotion_papers.md`](../notes/agent_self_improvement_skill_promotion_papers.md) —
  the 8-paper map. Supports symmetric success/failure distillation.
- [`design/exchange_classifier.md`](exchange_classifier.md) —
  prerequisite component, unchanged.
- [`notes/inflection_detector_evaluation.md`](../notes/inflection_detector_evaluation.md) —
  the empirical motivation for not relying on raw structural z-scores.
- [`memory_evolution.md`](memory_evolution.md) — companion spec;
  consumes critical-steps + features as induction inputs.
- [`deprecated_segments_and_calibration.md`](deprecated_segments_and_calibration.md) —
  the prior design this supersedes.
