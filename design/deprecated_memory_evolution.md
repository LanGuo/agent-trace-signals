# [DEPRECATED] Cross-Session Memory Evolution: Bayesian-Scored Procedural Memory

> **DEPRECATED 2026-07-22.** This spec is built entirely on top of
> [`trajectory_signals.md`](trajectory_signals.md) (itself deprecated
> 2026-07-11) — it consumes that doc's per-step artifacts as evidence
> labels and features, and its Track 1/2 framing is the same Track 2
> (action-pattern memory extraction) that was never built and is now
> dead. None of the architectural decisions below (unit-of-skill,
> posterior schema, induction sources) have a live consumer. Kept for
> historical reference. See `design/design_decisions.md` for the
> current extraction/consolidation design.

> Design notes (WIP) from brainstorming on 2026-06-07; updated 2026-06-09
> after re-architecture of the within-session spec. Paired with
> [trajectory_signals.md](trajectory_signals.md) (within-session
> signals), which produces the per-step artifacts this spec consumes as
> evidence labels and features. The previous companion spec —
> [deprecated_segments_and_calibration.md](deprecated_segments_and_calibration.md)
> — has been deprecated; see that doc and the new one for the rationale.
>
> **Status:** Major architectural decisions made (unit-of-skill, posterior
> schema, induction sources, Track 1/2 framing). Remaining open questions
> on rewrite policy, usefulness signal, priors, validation, and lighter
> lifecycles listed at the end.

---

## Why this exists

The agent_trace_signals memory pipeline already extracts memories into
`{episodic, procedural, semantic}` types via D-prompt during ingest plus
frequency promotion during analytics_light. What's missing:

- No cross-session **scoring of whether a memory actually helps** when
  applied — no posterior, no calibration.
- No **lifecycle policy** — memories accumulate, but there's no
  retire/compress/split logic when they go stale or overlap.
- No **structured evidence model** for what conditions a memory is useful
  under — currently retrieved by similarity only.
- The existing **Track 2** (signals: inflections, failure keyword density,
  segments, calibration, exchange classifier) **stops at signals and never
  feeds back into the memory store**. This spec closes that loop.

Inspired by the
[Bayesian-Agent](https://github.com/DataArcTech/Bayesian-Agent) framework
(feature-conditioned categorical likelihood over skills), but reframed for
the existing memory taxonomy rather than introducing a new "skill" object.

---

## Scope

**In scope:**

- Procedural memories (≈ "skills" / SOPs) get a full Bayesian posterior
  over usefulness, conditioned on bucketed evidence features.
- Lifecycle policy: induction triggers, promotion gates, rewrite actions
  (compress / patch / split / retire / explore).
- Extending **Track 2** of ingestion/analytics to mint memories of any
  type (not just procedural) from recurring failure patterns.

**Lighter treatment (not full Bayesian):**

- Episodic memories: consolidation + conflict detection (already partially
  implemented).
- Semantic memories: validity-check against recent sessions (does this
  convention still hold?).

**Inputs** (produced by [`trajectory_signals.md`](trajectory_signals.md)):

- `step_scores.evidence_supports` — per-substantive-exchange scalar.
- `step_scores.progress_vector` + `step_scores.cost_vector` — observer's
  decomposed signal.
- `critical_steps.tag` — `failure_critical` | `success_critical` |
  `recovery_critical`.
- `critical_steps.failure_mode` — categorical when `tag =
  failure_critical`.
- `critical_steps.features` — bucketed evidence dimensions for the
  preceding window.
- `exchange_type` on `TurnDescriptor` — filters harness-injected noise.

---

## Decisions made

### 1. Unit of "skill" = atomic memory + associations (hybrid)

- Each procedural memory row keeps its own posterior. Posteriors are
  fine-grained, retire decisions are atomic, no painful schema migration.
- A new `procedural_associations` table captures the "bundle" structure
  when it emerges in real traces (memory A typically precedes B).
- Bayesian-Agent's "split" rewrite action maps to breaking an
  association; "compress" maps to merging associated memories with high
  co-occurrence and overlapping evidence.

### 2. Posterior schema — parallel `memory_evidence` table

Adds feature-conditioned counts (the core of Bayesian-Agent's categorical
likelihood) without exploding the `memories` schema. Marginal posterior
mean is cached on `memories` for cheap retrieval ranking.

```sql
-- New columns on memories
ALTER TABLE memories ADD COLUMN posterior_mean       REAL;
                                -- marginal P(useful), Laplace-smoothed
ALTER TABLE memories ADD COLUMN posterior_n          INTEGER DEFAULT 0;
                                -- total observations contributing to posterior
ALTER TABLE memories ADD COLUMN lifecycle_state      TEXT DEFAULT 'active';
                                -- 'active' | 'compressed' | 'patched' | 'retired' | 'exploring'
ALTER TABLE memories ADD COLUMN last_evolution_at    TEXT;

-- Feature-conditioned counts; one row per (memory, feature, bucket)
CREATE TABLE memory_evidence (
  memory_id        TEXT NOT NULL REFERENCES memories(id),
  feature_name     TEXT NOT NULL,
                    -- e.g. 'tokens_bucket', 'context', 'failure_mode'
  feature_bucket   TEXT NOT NULL,
                    -- e.g. '8-32k', 'fix_bug', 'premature_done'
  success_count    INTEGER DEFAULT 0,  -- + Laplace α=1 at query time
  failure_count    INTEGER DEFAULT 0,  -- + Laplace α=1 at query time
  last_updated_at  TEXT NOT NULL,
  PRIMARY KEY (memory_id, feature_name, feature_bucket)
);

-- Procedural memory associations (hybrid-C bundle structure)
CREATE TABLE procedural_associations (
  source_memory_id  TEXT NOT NULL REFERENCES memories(id),
  target_memory_id  TEXT NOT NULL REFERENCES memories(id),
  co_occurrence     INTEGER DEFAULT 1,
                     -- # times both retrieved/applied together
  observed_lift     REAL,
                     -- P(success | both) − P(success | source alone)
  last_observed_at  TEXT NOT NULL,
  PRIMARY KEY (source_memory_id, target_memory_id)
);
```

Retrieval queries use `memories.posterior_mean` for ranking;
evolution/scoring queries hit `memory_evidence`.

### 3. Track framing — Track 1 is neutral; Track 2 mints memories from per-step signals

| Track | Purpose | Signal direction | Produces |
|---|---|---|---|
| **Track 1** | Chunk summaries → entities → memories | **Neutral** (doesn't know outcome) | All three memory types via `explicit` (D-prompt) and `frequency` (analytics_light) |
| **Track 2** | Per-step trajectory signals (see [`trajectory_signals.md`](trajectory_signals.md)): exchange classifier → per-step observer scoring → critical-step detection | **Outcome-aware** (success / recovery / failure) | Per-step signals **plus** memories of any type via `success_pattern`, `recovery_pattern`, `failure_response` |

Track 2's signal stack is defined in
[`trajectory_signals.md`](trajectory_signals.md):

1. **Exchange classifier** (see
   [`exchange_classifier.md`](exchange_classifier.md)) — per-plugin
   harness-specific classification of each exchange. Filters out
   harness-injected exchanges before downstream consumption.
2. **Per-step observer LLM scoring** — for each `SCORABLE_TYPES`
   exchange, produces `progress_vector` (LLM-judged) + `cost_vector`
   (deterministic) + scalar `evidence_supports`.
3. **Critical-step detection** — deterministic gradient-and-absolute
   threshold detector adapted from SWE-TRACE; tags critical steps as
   `failure_critical` / `success_critical` / `recovery_critical`.
4. **Within-session candidate mint** — each critical step immediately
   mints a candidate procedural memory via the appropriate induction
   pathway. This is the entry point into this spec.

Note on prior Track 2 framing: the older description referenced
inflections, failure keyword density, segments, and decision-point
calibration. Of those:

- **Inflection detector** still exists but is now a secondary signal
  source (per-step observer scoring is the primary).
- **Failure keyword density** survives as a complementary
  harness-agnostic signal — useful as an adaptive-sampling hint for
  where to spend observer calls (see
  [`trajectory_signals.md`](trajectory_signals.md), adaptive sampling).
- **Segments + decision-point calibration** are deprecated
  ([`deprecated_segments_and_calibration.md`](deprecated_segments_and_calibration.md))
  and replaced by per-step scoring + critical-step detection.

Track 2 previously stopped at signals. This spec extends Track 2 to
feed back into the memory store, closing the loop from "we detected a
notable step" to "we have a memory addressing it."

### 4. Induction sources — all per-session at mint time

| Source | Track | `extraction_method` | Mint trigger |
|---|---|---|---|
| D-prompt during ingest | 1 | `explicit` | Per-chunk (existing) |
| Frequency promotion in analytics_light | 1 | `frequency` | Cross-session frequency (existing) |
| `success_critical` step | **2** | `success_pattern` | **NEW** — per-session, per-step |
| `recovery_critical` step | **2** | `recovery_pattern` | **NEW** — per-session, per-step |
| `failure_critical` step | **2** | `failure_response` | **NEW** — per-session, per-step |

All three Track 2 sources mint a candidate memory **immediately for
each critical step within a session**. No cross-session recurrence
threshold at mint time. This matches the per-trajectory mint pattern
used by Voyager, SkillWeaver, and SWE-TRACE's verbatim-anchor memory
buffer. See
[`trajectory_signals.md`](trajectory_signals.md) for the mint policy
detail.

Cross-session work (clustering, merging, posterior consolidation)
happens in a separate **analytics consolidation pipeline** described
below — analogous to how `analytics_light` consolidates Track 1
memories via frequency promotion.

Why all three: a library with only `failure_response` memories is
reactive ("avoid Z") with no positive playbook. A library with only
`success_pattern` memories may not address recurring failure modes.
`recovery_pattern` sits at the boundary — "when stuck, do Y" — and is
typically sparser but more load-bearing. The full literature review
([notes](../notes/agent_self_improvement_skill_promotion_papers.md))
shows the dominant trend in 2025-2026 is **success-driven** skill
induction (Voyager, SkillWeaver, SWE-TRACE); failure-driven
(Reflexion-style) is the minority. We support both symmetrically.

### 5. Track 2 can mint any memory type (and now from success too)

Track 2's induction LLM is prompted to emit whichever type best fits
the observed cluster, with the cluster's `tag` biasing what to look for:

| Memory type | Track 2 case (failure_critical) | Track 2 case (success_critical) | Lifecycle treatment |
|---|---|---|---|
| `procedural` | Recurring failure with "next time do Y" | Recurring success with "when X, do Y works" | **Full Bayesian posterior** |
| `episodic` | One-shot dramatic failure event as case study | One-shot remarkable success as case study | Consolidation + conflict detection |
| `semantic` | Generalizable underlying truth ("X causes Y") | Generalizable underlying truth ("X enables Y") | Validity-check against recent sessions |

A single Track 2 cluster may emit a paired `procedural + semantic`
(e.g., success cluster: "TileDB consolidation lock prevents parallel
write corruption" + "to write to TileDB in parallel, configure
consolidation lock first"). The induction prompt allows up to two
outputs per cluster.

### Symmetric success/failure distillation — induction prompts

Three prompt templates, one per `critical_steps.tag`:

**`success_pattern`** (from `success_critical` clusters):

> These N successful actions occurred under feature conditions F.
> Extract the transferable principle as a procedural memory in the
> form: when [condition], do [action] because [justification]. Prefer
> principle over instance specifics. Output up to one procedural +
> one semantic memory.

**`recovery_pattern`** (from `recovery_critical`):

> The agent was stuck at low evidence_supports for k steps, then
> recovered. The recovery step's evidence and action are [E, A].
> Extract: when stuck in [state], try [action]. Recovery patterns
> should always be procedural (no semantic-only output).

**`failure_response`** (from `failure_critical`):

> These N refuted actions share failure_mode F under conditions C.
> Extract a procedural memory that would have prevented them; if a
> generalizable causal truth is also apparent, additionally output a
> semantic memory. Prefer principle over instance specifics.

All three prompts share the **principle-level over instance-level**
constraint from the capability-collapse paper (see
[notes](../notes/continual_learning_and_self_improvement_papers.md)).

### 6. Extension vs. mint check (lightweight, at mint time)

When a candidate memory is about to be minted (Track 1 or 2), a fast
local check guards against trivial duplicates:

- **Trivial duplicate** if: cosine similarity of content embeddings
  ≥ **0.92** (high bar — only near-identical text) **AND** ≥1 shared
  feature bucket.
- **Duplicate outcome**: don't mint; instead update the existing
  memory's `memory_evidence` counts for matching feature buckets and
  bump `posterior_n`.
- **Otherwise** → mint new candidate memory in
  `lifecycle_state = 'exploring'` with `extraction_method` from the
  triggering source.

The bar is intentionally high so that **interesting variation is
preserved as separate candidates** at mint time. Looser similarity
merges happen in the consolidation pipeline (below), where an LLM can
inspect content + evidence together and make a more informed
compress/split decision than embedding similarity alone.

---

### 7. Cross-session analytics consolidation pipeline (NEW)

A separate offline pipeline, analogous to `analytics_light`. Runs
periodically (after each ingestion pass, or on a schedule). Operates
on the pool of memories — primarily `lifecycle_state = 'exploring'`
candidates, but also re-evaluates `active` memories.

**Inputs:** all `memories` rows, `memory_evidence`,
`procedural_associations`, recent `memory_retrievals` (when available),
recent `critical_steps` provenance.

**Stages:**

1. **Clustering pass** — for each `memory_type = 'procedural'`,
   group candidates by:
   - content embedding similarity (HDBSCAN or single-link clustering
     at ε ≈ 0.30 distance)
   - **AND** at least one shared feature-bucket value
   Each cluster is a candidate consolidation group.

2. **LLM refinement pass** — for each multi-member cluster, an LLM
   call inspects the members' content + feature buckets + provenance
   sessions. Output options:
   - **`compress`** — cluster represents one principle; merge into a
     canonical memory. Union of feature buckets, sum of
     `memory_evidence` counts, provenance from all sources, retire
     the source memories with a `compressed_into` pointer.
   - **`split`** — cluster mixes distinct principles; LLM proposes a
     finer partition; rerun clustering on the partition.
   - **`patch`** — one canonical member needs textual refinement
     based on the others; update its content; absorb evidence;
     retire the rest.
   - **No action** — cluster is real but the members are distinct
     enough to keep separate.

3. **Posterior promotion pass** — for each memory:
   - If `lifecycle_state = 'exploring'` AND `posterior_n ≥ N_promote`
     AND `posterior_mean ≥ τ_promote` → transition to `active`.
   - If `posterior_n ≥ N_retire` AND `posterior_mean ≤ τ_retire`
     → transition to `retired`.
   - Otherwise leave state alone.
   Thresholds tunable: initial defaults `N_promote=5`, `τ_promote=0.6`,
   `N_retire=10`, `τ_retire=0.3`. The asymmetry (retire needs more
   evidence than promote) reflects the safety-rail principle —
   retiring is harder to reverse in practice.

4. **Association update pass** — recompute `procedural_associations`
   from recent `memory_retrievals`: pairs of memories surfaced in the
   same retrieval get `co_occurrence` increments; observed lift is
   updated based on outcome.

5. **Provenance-quality refresh** — recompute each memory's
   `provenance_quality` from the current evidence_supports distribution
   of its source sessions (some sessions may have been re-scored).

**Cadence:** runs after each `ats ingest` pass, like `analytics_light`.
For larger corpora, can be scoped to "memories minted/updated since
last consolidation run."

**Cost:** dominated by stage 2 LLM calls. One call per multi-member
cluster. Typical corpus: dozens of clusters per run. Cheap model
(Haiku-class). No fixed N-session threshold gating any of this — the
consolidation just looks at what's in the pool and acts.

---

## Remaining open questions

### 1. Rewrite policy mapping (compress / patch / split / retire / explore)

These are the five mutations the consolidation pipeline can apply.
Mapping to our schema:

- **compress** — merge a cluster of similar memories into a canonical
  one. Driven by the clustering + LLM refinement stages of the
  consolidation pipeline. Source memories get
  `lifecycle_state = 'compressed'` with a `metadata.compressed_into`
  pointer; their `memory_evidence` counts are summed into the
  canonical.
- **patch** — refine the content of an existing canonical memory based
  on related candidates (typically a small cluster where one member is
  better-worded). Done by the LLM refinement stage. Change tracked in
  `metadata.patch_history`. Posterior preserved.
- **split** — when the LLM refinement stage judges a cluster mixes
  distinct principles, propose a partition and rerun clustering on
  the partition. Also: break a `procedural_associations` link when
  joint observed_lift ≤ 0 over enough joint observations.
- **retire** — `lifecycle_state = 'retired'`; excluded from retrieval.
  Deterministic in the posterior promotion stage: `posterior_mean ≤
  τ_retire` AND `posterior_n ≥ N_retire`.
- **explore** — every Track 2 candidate is born in `'exploring'` state
  (the default). The explore arm of the policy is implicit in the
  mint-freely / consolidate-later model. Fast-tracked promotion to
  `active` if `posterior_mean ≥ τ_promote` is achieved quickly.

**Decision responsibility:**

- `retire` is deterministic — runs in the posterior promotion stage,
  no LLM judgment needed.
- `compress`, `patch`, `split` are LLM-driven — they need content
  judgment beyond what embedding similarity provides. Run in stage 2
  of the consolidation pipeline.
- `explore` is implicit — every candidate is an explore arm by
  default.

**Safety rails:**

- Never auto-retire a memory in its first 7 days post-mint (let it
  accumulate evidence).
- Never auto-compress across `extraction_method` boundaries without
  LLM confirmation (an `explicit` + `failure_response` merger needs
  judgment about whether they really represent the same principle).
- All mutations write to `metadata.evolution_history` for traceability.

### 2. Memory usefulness signal — "did this memory help?"

Bayesian-Agent assumes a verifier-graded outcome per trajectory. We don't
have that. Options:

- **A) Retrieval-conditioned outcome:** if memory M was surfaced to the
  agent (in a future Retrieval Trace, TBD) and the segment containing that
  retrieval has `decision_points.outcome = 'supported'` (or no refuted
  decisions), count as success; if `refuted`, count as failure.
- **B) Counterfactual proxy:** for sessions where M *would have matched
  on features* but wasn't surfaced, did they fail more often than
  comparable sessions where M was surfaced? Harder to compute; needs a
  retrieval log.
- **C) Manual labeling:** extend the label-failures viewer with a "memory
  helpfulness" judgment.

A is the most tractable; depends on a retrieval log that doesn't exist
yet. Probably a sub-task of this spec.

### 3. Cold-start prior

Laplace α=1 is the default. Open:

- Should procedural memories from high-quality sessions (e.g., a session
  with low calibration delta and `outcome = supported` decisions) get a
  stronger prior?
- Should `extraction_method` affect the prior?
  - `explicit` (agent or user stated) → weaker prior (one observation,
    uncalibrated)
  - `frequency` (cross-session pattern) → moderate prior (k observations)
  - `failure_response` (Track 2) → stronger prior on the failure
    counts, weaker on success (we know it addresses a known failure)

### 4. Validation

- Held-out sessions: bin memories by `posterior_mean` ranges; check that
  retrieved memories with higher predicted usefulness actually correlate
  with downstream `supported` outcomes.
- Calibration plot: predicted P(useful) vs. observed success rate.
- Lifecycle action audit: % of retire actions that were correct (memory
  was indeed stale); % of compress actions that improved retrieval
  precision.

### 5. Episodic and semantic lifecycle details

Out-of-scope for Bayesian scoring, but still need designs:

- **Episodic consolidation:** when do near-duplicate episodic memories
  get merged? Existing `conflicts` table handles contradictions; need a
  duplicate-detection layer.
- **Semantic validity check:** how do we mark a semantic memory stale?
  Probably: if recent sessions consistently contradict it, mark
  `lifecycle_state = 'retired'` with a contradiction provenance row.

---

## Non-goals

- Replacing the existing memory extraction pipeline (D-prompt + frequency
  promotion). This spec adds a scoring/lifecycle layer on top.
- Real-time skill retrieval optimization — focus is on the offline
  evolution loop, not retrieval-time policy.
- Building a separate "skill library" object — the existing `memories`
  table with `memory_type='procedural'` IS the skill library.
- Multi-tenant sharing of memories across orgs/users (SkillClaw territory).

---

## References

- [trajectory_signals.md](trajectory_signals.md) — the within-session
  signals spec that produces this spec's inputs (`step_scores`,
  `critical_steps`, `features`).
- [exchange_classifier.md](exchange_classifier.md) — the per-harness
  classifier whose tags drive Track 2's `SCORABLE_TYPES` filter.
- [deprecated_segments_and_calibration.md](deprecated_segments_and_calibration.md) —
  the predecessor of `trajectory_signals.md`; kept for historical
  reference.
- [../notes/eval_and_benchmark_strategy.md](../notes/eval_and_benchmark_strategy.md)
  — guiding principle: usefulness signal must be aligned to end use
  (retrieval-time outcomes), not isolated component metrics.
- [../notes/trace_to_benchmark_design.md](../notes/trace_to_benchmark_design.md)
  — relevant for memory-evolution validation: failure-classifier and
  labeling pipeline directions feed validation data.
- [Bayesian-Agent](https://github.com/DataArcTech/Bayesian-Agent) —
  feature-conditioned categorical likelihood model; rewrite policy.
- [SkillOS (arXiv 2605.06614)](https://arxiv.org/abs/2605.06614) — RL-
  trained skill curator; less direct overlap.
- [AutoSkill](https://github.com/ECNU-ICALK/AutoSkill) — feedback-
  triggered skill creation; lighter relevance.
- [design_decisions.md](design_decisions.md) — broader project decision log.
