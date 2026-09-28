# [DEPRECATED] Task Segmentation + Decision-Point Calibration — Design

> **DEPRECATED 2026-06-09.** Superseded by
> [`trajectory_signals.md`](trajectory_signals.md), which adopts per-turn
> scoring + gradient-based critical-step detection from the literature
> instead of segmentation + typed decision-point extraction. Kept here
> for historical reference. See `trajectory_signals.md` for the change
> rationale.

> Design from brainstorming on 2026-06-07. Two related features:
> (1) detect task/topic segments within a session, and (2) measure agent
> confidence calibration at semantic anchors. The two are paired because
> segmentation gives calibration the per-segment scope it needs to stay
> tractable.

---

## Why these two together

Both ideas address the same root issue surfaced by the [inflection detector
evaluation](../notes/inflection_detector_evaluation.md): purely structural
features over an entire session are too noisy because they conflate logical
units of work and lack semantic grounding. Segmentation gives downstream
analytics a tighter scope; calibration adds semantic anchors that the
structural detector lacks.

Segmentation is the prerequisite — a "decision" in segment 1 has its evidence
in segment 1, not in segment 3. Calibration on top of segments is far more
tractable than on a 250-turn raw session.

---

## Shared prerequisite — Exchange classifier

Both segmentation and the existing inflection detector are corrupted by
harness-injected exchanges (skill injections, `<task-notification>`,
`ScheduleWakeup` polls, context continuation, monitoring loops). The
inflection eval traces ~95% of false positives to these.

The full empirical design is in
[`notes/exchange_classifier_design.md`](../notes/exchange_classifier_design.md)
— per-harness pattern surveys (CC c30450db, Gemini symphony + chess,
Opencode 517fcbdd) and concrete classification rules.

### Taxonomy (single source of truth — from the empirical design)

| `exchange_type` | Harness | Meaning |
|---|---|---|
| `genuine_human` | all | Real user message + agent tool calls |
| `genuine_text` | all | Real user message, no agent tool calls (Q&A / design) |
| `skill_invocation` | CC | `user.startswith("Base directory for this skill:")` |
| `task_notification` | CC | `<task-notification` in user text |
| `context_continuation` | CC | `user.startswith("This session is being continued")` |
| `wakeup_injection` | CC | `ScheduleWakeup` in tool_calls or `scheduledFor` in toolUseResult |
| `agent_continuation` | Gemini | Empty user content (Gemini multi-turn structure) |
| `summary_diff` | Opencode | `user.data.summary.diffs` present, no text parts |

### Architecture

- Each source plugin owns its own classifier (`_classify_exchange`) — patterns
  are harness-specific. Implementations are deterministic string/key checks
  inside `_extract_turn_descriptor()`. Zero LLM cost.
- The **taxonomy enum** lives in `models.py` so all plugins emit the same
  labels and downstream code is harness-agnostic.
- `TurnDescriptor` gets two new fields per the existing design:
  - `exchange_type: str = "genuine_human"`
  - `avg_think_tok_ratio: float = 0.0` (Gemini-only signal; defaults to 0
    elsewhere)
- A shared filter `GENUINE_TYPES = {"genuine_human", "genuine_text"}`. All
  downstream consumers (inflection detector, segmentation, decision-point
  extractor) filter through it. Note `genuine_text` is intentionally
  included — text-only responses can still signal stalls; the entropy=0
  false-positive cluster is fixed because consecutive
  `agent_continuation` / `task_notification` exchanges are filtered out
  before the window fills.

### Why this is a one-time investment

When a new harness is added (Cursor, etc.), only its plugin needs a
classifier; everything downstream just works. The inflection detector
benefits immediately — most of its false-positive load disappears.

---

## Idea 1 — Task/topic segmentation within sessions

### Goal

Long sessions (often 100+ exchanges) span multiple logical units of work
(setup TileDB schema → fix ingestion bug → write tests). Treat the session
as a sequence of coarse-grained **segments**, each representing one unit of
work.

### Use cases (both must be supported)

- **Navigation / UI** — show a session as a labeled timeline so a long trace
  is browsable without reading every chunk.
- **Analytics isolation** — segments become the unit of scope for downstream
  signals (inflection detection, failure density, decision-point extraction).
  Prevents signals from one task contaminating another.

### Boundary detection — hybrid signal

No single signal is reliable on its own; require multiple to agree.

- **Goal-shift cues in user text** — explicit pivot phrases ("ok now",
  "moving on", "next:", "let's switch to") in `human_directive` exchanges.
  High precision, low recall. Regex.
- **Work-area shift** — Jaccard distance between the file/module sets
  touched in adjacent windows. From `turn_descriptors` tool-call targets.
  No LLM.
- **Topical/semantic shift** — embedding distance between adjacent chunk
  windows. Uses existing `record_embeddings`.
- **Idle/gap cues** (if timestamps available) — wall-clock gap between
  exchanges as a soft prior.

Boundaries fire when the weighted combined score crosses a threshold tuned
for **coarse granularity** (3–8 segments per typical long session). Skews
toward precision; some real transitions missed but each emitted segment is
meaningfully distinct.

The detector emits per-signal scores alongside the boundaries so finer
segmentation is possible downstream without re-running.

### Boundary alignment & re-segmentation

- Segment boundaries **always snap to chunk boundaries**. Never split a
  chunk. Simplifies all downstream joins.
- Re-segmentation policy: **overwrite on re-ingest**. Segments are
  derivative data; no value to versioning them.

### Labels

Each segment gets an LLM-generated label (e.g., "Fix ingestion infinite
loop"). One cheap-model call per segment, fed the segment's chunk summaries.
~3–8 calls per session — negligible cost.

Fallback (LLM unavailable / offline mode): extractive label from dominant
entities + first `human_directive` text.

### Schema

```
session_segments (
  id              TEXT PRIMARY KEY,     -- stable hash(session_id, entry_chunk)
  session_id      TEXT NOT NULL,
  segment_index   INTEGER NOT NULL,     -- 0..N within session
  entry_chunk     INTEGER NOT NULL,     -- inclusive
  exit_chunk      INTEGER NOT NULL,     -- inclusive
  entry_turn      INTEGER NOT NULL,
  exit_turn       INTEGER NOT NULL,
  label           TEXT,                 -- LLM-generated, or extractive fallback
  label_method    TEXT,                 -- "llm" | "extractive"
  boundary_score  REAL,                 -- combined signal strength at entry
  signal_detail   TEXT,                 -- JSON: per-signal contributions
  created_at      TEXT NOT NULL
)
```

Stable IDs let `session_inflections`, `decision_points`, and future
analytics reference segments without breaking on re-runs.

---

## Idea 2 — Decision-point calibration

### Why not turn-by-turn Bayesian scoring

Original brainstorming considered three framings from
`bayesian-confidence-research.md` (private notes, not included):

- **A) Belief that task is done / on track** — Martingale on progress
  signals.
- **B) Per-claim confidence with separate observer LLM.**
- **C) Belief that current strategy is working** — Martingale on
  turn-by-turn structural signals.

**C is rejected** because the [inflection detector
evaluation](../notes/inflection_detector_evaluation.md) shows ~95% false
positive rate on the structural signals C would build on. Statistical rigor
on top of noisy features just dresses up the noise. Until harness-injected
exchanges are filtered (the exchange classifier above), no turn-level
statistical metric will be meaningful.

**B is the right direction** but unbounded — every utterance is a "claim".
We reframe to make it tractable.

### Reframe — decision points, not claims

Coding sessions contain ~50 things-said-per-turn but only ~5–15
**load-bearing decisions per session**. A load-bearing decision is the
pattern:

> agent assertion → immediate committing action

Examples:

- "The bug is in `foo.py:42`" → followed by editing `foo.py`
- "Tests pass, task done" → followed by `TodoWrite` complete / final
  summary
- "We should use TileDB here" → followed by writing TileDB code
- "This won't work, switching to X" → followed by a real pivot in tool use
- "I'll assume the data has X" → followed by code that depends on X

These are sparse, semantic, and each has a clear "was this bet justified?"
check.

### Decision-type taxonomy

| Type | Failure when miscalibrated |
|---|---|
| `task_done` | Agent claims done while bugs remain |
| `root_cause` | Agent edits wrong file based on weak evidence |
| `approach_choice` | Agent commits to wrong stack, burns turns |
| `strategy_pivot` | Agent abandons working approach OR keeps doubling down |
| `assumption` | Agent acts on unverified assumption about data/system |

**v1 scope: `task_done` + `root_cause` only.** These are the most
consequential, the easiest to anchor (clear committing actions), and cover
the most-cited failure modes. The other three deferred to v2.

### Pipeline (per session, after segmentation)

1. **Decision-point extractor** — scoped per segment. Light LLM pass
   surfaces decision points anchored to specific exchanges. Yields ~5–15
   per session total.
2. **Evidence bundle** — for each decision, the `evidence_text` from the
   preceding N exchanges *within the same segment*. Token-budget capped
   (e.g., 8K).
3. **Observer LLM** — one call per decision (~10/session). Inputs:
   - The decision's `claim_text` (with hedge words stripped) + decision type
   - The evidence bundle (tool outputs only)
   - No agent reasoning, no chunk summaries
   Returns: `evidence_supports_decision` ∈ [0,1] + 1–3 falsification
   criteria the evidence does/does not address.
4. **Agent-expressed confidence** — extracted from the agent's language at
   the decision point. **Regex/phrase-table** with a 5-level scale:
   `confident | asserted | hedged | tentative | uncertain`. Transparent,
   debuggable, cheap. Upgrade to light LLM if validation shows it's
   brittle.
5. **Calibration delta** = `agent_confidence − observer_confidence`.
   Negative = overconfident. Positive = under-confident.
6. **Outcome signal** — lookforward within the same segment, capped at 15
   turns. Outcome is `refuted` if:
   - User explicitly contradicts the claim, OR
   - For `root_cause`: agent edits a different file/location than claimed,
     OR
   - For `task_done`: agent reopens the task or runs more fixes
   Otherwise `unknown` — and most cases will be `unknown`. That's fine;
   the calibration delta is independently useful.
7. **Martingale score over decision sequence** — Pearson r between
   `agent_confidence_t` and `(agent_confidence_{t+1} − agent_confidence_t)`
   across the decision-point sequence. Positive r = entrenchment.

### Why this is scalable

- ~10 observer LLM calls per session, not 250.
- Anchored in semantic events (committing actions), not noisy structural
  z-scores.
- Evidence bundles are bounded by segment size.
- Outputs are actionable per-decision *and* aggregable per-session.

### Schema

```
decision_points (
  id                  TEXT PRIMARY KEY,
  session_id          TEXT NOT NULL,
  segment_id          TEXT,                -- nullable: pre-segmentation backfill
  exchange_idx        INTEGER NOT NULL,    -- where decision was made
  decision_type       TEXT NOT NULL,       -- v1: "task_done" | "root_cause"
  claim_text          TEXT NOT NULL,
  committing_action   TEXT NOT NULL,       -- JSON: the action that anchored it
  agent_confidence    REAL,                -- from hedging extraction (0..1)
  observer_confidence REAL,                -- from observer LLM
  observer_criteria   TEXT,                -- JSON: falsification criteria
  calibration_delta   REAL,                -- agent − observer
  outcome             TEXT,                -- "supported" | "refuted" | "unknown"
  outcome_evidence    TEXT,                -- what later turn revealed outcome
  failure_mode        TEXT,                -- nullable; categorical failure_mode when outcome=refuted
  features            TEXT,                -- JSON: bucketed evidence features (see below)
  created_at          TEXT NOT NULL
)
```

### Failure-mode taxonomy (added)

When `outcome = "refuted"`, the decision is labeled with a categorical
`failure_mode`. This is what downstream cross-session memory evolution
(separate spec) uses as evidence labels.

v1 taxonomy (deliberately small; extend as patterns emerge):

| failure_mode | Trigger pattern |
|---|---|
| `wrong_target` | `root_cause`: edited file ≠ claimed file/location |
| `premature_done` | `task_done`: user correction or further fixes within lookforward window |
| `evidence_thin` | Observer confidence < 0.4 — claim made on insufficient evidence regardless of realized outcome |
| `tool_misread` | Tool returned signal that contradicts the claim but agent proceeded anyway |
| `unknown` | Refuted but doesn't match any pattern above |

Failure-mode classification runs after observer + outcome detection;
deterministic rules first, with `unknown` as the catch-all.

### Bucketed evidence features (added)

Each decision and each segment is tagged with bucketed structural features
that serve as **categorical evidence dimensions** for the cross-session
memory evolution model (separate spec). Inspired by Bayesian-Agent's
feature-conditioned categorical likelihood.

Buckets applied at the **decision_point** level (preceding evidence window):

| Feature | Buckets |
|---|---|
| `tokens_bucket` | `<2k`, `2-8k`, `8-32k`, `>32k` (tokens in preceding evidence window) |
| `turns_bucket` | `1`, `2-4`, `5-15`, `>15` (exchanges in preceding window) |
| `tool_diversity_bucket` | `1`, `2-3`, `4-6`, `>6` (distinct tool names) |
| `error_rate_bucket` | `0%`, `<25%`, `25-50%`, `>50%` (tool errors in window) |
| `context` | task family label from segment (e.g. "fix_bug", "implement_feature", "debug_session") — extracted from segment label by a cheap classifier |

Buckets applied at the **segment** level (carried on `session_segments`):

| Feature | Buckets |
|---|---|
| `length_bucket` | `<10`, `10-30`, `30-80`, `>80` exchanges |
| `latency_bucket` | `<1m`, `1-5m`, `5-30m`, `>30m` wall-clock (when timestamps available) |
| `dominant_model` | already on `session_metadata`; carried for joinability |

Buckets are chosen to keep the categorical likelihood model tractable (4–5
bins per dimension). Raw values are also stored in `signal_detail` for any
non-Bayesian downstream use. Buckets stored as JSON in
`decision_points.features` and a parallel `session_segments.features`
column.

---

## Harness asymmetry — Evidence text contract

The observer LLM's value hinges on it seeing the same tool outputs the agent
saw. The current per-plugin serialization is asymmetric:

| Harness | `chunk_text` content | Tool result included? |
|---|---|---|
| Claude Code | tool calls + tool_result blocks (truncated to ~200 chars) | Yes, truncated |
| Gemini CLI | tool calls only (name + args); plus `thoughts` blocks | **No — results dropped by `_serialize_exchange`** |
| Opencode | bash/edit tool results in `chunk_text` | Yes (per failure_signals keywords) |

This breaks "evidence = tool outputs" for Gemini sessions.

### Decision: extend the plugin contract

Add a parallel field on `RawChunk`:

```
class RawChunk(BaseModel):
    chunk_index: int
    chunk_text: str          # existing — narrative summary for embeddings/UI
    evidence_text: str = ""  # NEW — verbatim tool outputs, no agent text
    ...
```

- `evidence_text` is the **untruncated concatenation of tool results** for
  the exchange range covered by the chunk, with consistent framing
  (`[tool: name(args)] → result`).
- Each plugin is responsible for surfacing tool results into
  `evidence_text` regardless of how `chunk_text` is summarized.
- Gemini's plugin retains `tc.get("result", [])` instead of dropping it.
- CC's plugin keeps full tool_result content instead of the 200-char cap
  (or uses a longer cap configurable per-pipeline).
- The DB schema adds an `evidence_text` column to `records` mirroring
  `chunk_text`.

This makes the observer harness-agnostic and the contract explicit:
plugins are responsible for surfacing evidence. The work per plugin is
small — each already parses tool results, just needs to write them to a
different field.

---

## Validation strategy

The inflection detector eval revealed that without labeled ground truth,
heuristics are uninspectable. The same risk applies here.

- Extend `label-failures` HTML viewer with a **"decision points" tab**.
- Reviewer can: confirm/refute each extracted decision-point claim, mark
  decision type, and label the realized outcome.
- Build ground truth incrementally over the existing corpus — same workflow
  as inflection labeling.
- Initial validation goal: confirm decision-point extraction precision is
  ≥80% (i.e., when the extractor says "this is a decision point", a human
  agrees) before trusting the observer/calibration numbers.
- Calibration validation: on a labeled subset where outcome is known
  (refuted by user correction, etc.), does `calibration_delta < 0`
  correlate with `outcome = refuted`?

---

## Sequencing

1. **Exchange classifier** (shared prerequisite) — small, immediately
   improves the existing inflection detector.
2. **Plugin `evidence_text` extension** — schema + per-plugin changes;
   prerequisite for the observer.
3. **Segmentation** — boundary detector + labels + `session_segments`
   table. Self-contained, immediately useful in UI.
4. **Decision-point extractor** — operates on segments + `evidence_text`.
5. **Observer + calibration** — operates on extracted decision points.
6. **Outcome detection + Martingale** — operates on observer output.
7. **Label-failures viewer extension** — validation tooling, can run in
   parallel with 5 and 6.

Each step produces a usable artifact and can be evaluated independently.

---

## Non-goals

- Real-time per-turn belief elicitation (cost + LLMs-Bayesian-in-
  expectation-not-realization problem).
- Replacing the inflection detector — calibration is a complement. The
  exchange classifier still benefits the inflection detector independently.
- TXCONFORMAL-style FDR control across hypothesis shortlists. Our setting
  doesn't have a hypothesis shortlist; this idea doesn't port.
- Bayesian Teaching fine-tune — no training data yet.
- v1 extended decision types (`approach_choice`, `strategy_pivot`,
  `assumption`) — deferred until v1 validation succeeds.
- Cross-session memory evolution + Bayesian posterior on procedural
  memories — covered by a separate spec
  ([memory_evolution.md](memory_evolution.md), in design now).
  This spec produces the per-decision and per-segment artifacts that the
  memory-evolution spec consumes as evidence labels and features.

---

## References

- `bayesian-confidence-research.md` (private notes, not included) — survey of Martingale Score, POPPER, TXCONFORMAL etc.
- [inflection_detector_evaluation.md](../notes/inflection_detector_evaluation.md) — empirical motivation for not building on raw structural signals.
- [exchange_classifier_design.md](../notes/exchange_classifier_design.md) — empirical taxonomy and per-harness classification rules; supersedes the placeholder taxonomy initially sketched here.
- [eval_and_benchmark_strategy.md](../notes/eval_and_benchmark_strategy.md) — guiding principle "eval has to be aligned to end use cases"; informs the validation section.
- [trace_to_benchmark_design.md](../notes/trace_to_benchmark_design.md) — pivoted to failure classifier / labeling / synthetic benchmark; relevant for validation strategy.
- [memory_evolution.md](memory_evolution.md) — companion spec; consumes per-decision and per-segment artifacts from here.
- [design_decisions.md](design_decisions.md) — broader project decision log.
