# Eval & Benchmark Strategy

> Working notes — not committed to git. Two separate endeavors documented here.
> Started: 2026-06-05

---

## Guiding Principle

**Eval has to be aligned to end use cases.** Eval for its own sake is waste.

The canonical example of good eval in this project: EXP01–EXP05. These were not
formal benchmarks — they were targeted comparisons of pipeline output quality against
real retrieval use cases (what would actually get recalled? is the memory actionable?).
They had a direct outcome: replaced 4 pipeline steps with the D-prompt in one decision.

The anti-pattern: building a precision/recall harness that measures a pipeline component
in isolation and never connects back to whether retrieval improved.

---

## Endeavor 1 — Evaluating the ATS Pipeline (Internal)

### The existing L0–L3 framework (critical re-evaluation)

The design doc defines four levels:

| Level | What it measures | Threshold |
|-------|-----------------|-----------|
| L0 | Entity recall / resolution precision / explicit memory precision | recall > 0.80, precision > 0.90 |
| L1a | Frequency memory recall / false positive rate | recall > 0.60, FPR < 0.25 |
| L1b | Cluster recovery (Pipeline 2b) | > 0.80 Clio method |
| L2 | Retrieval baseline (no analytics) | — |
| L3 | Analytics boost delta | > 0.05 P@10 |

### What's worth keeping

**L0 is load-bearing** — but only the parts that connect to end use.  
Entity recall matters because entities gate memory promotion and retrieval. If recall is
low, memories are systematically missing topics the user cared about. The threshold
(> 0.80) is meaningful only if it's measured against the kinds of entities that actually
show up in retrieval queries — not just whatever entities happen to be in an annotation.

**L3 (analytics boost delta) is the real end-use signal.** Does having analytics-derived
memories improve what gets recalled? If the delta is < 5%, the analytics pipeline doesn't
earn its complexity (per design principle P6). This is the only eval level that directly
answers "does this help the user?"

### What to question

**L1a (frequency memory FPR) is pipeline-internal.** It measures whether extracted
memories are correct, but not whether correct memories are useful at retrieval time. A
memory can be factually correct and never retrieved. This level only matters as a
sanity check on the extractor, not as a quality gate for the system.

**L1b (cluster recovery) is deferred and may stay deferred.** Pipeline 2b hasn't been
built. Measuring cluster recovery before deciding whether clustering earns its place
is backwards. Earn it with L3 delta first.

**The annotation burden for L0/L1a is high and may not be justified** unless there is
evidence that entity/memory quality is the bottleneck for retrieval. The experiments
(EXP01–EXP05) found the bottleneck and fixed it without a formal annotation harness.

### What "eval" should look like here (revised framing)

The EXP01–EXP05 model is the right model:

1. Pick a real retrieval use case ("find memories about entity extraction pipeline redesign")
2. Run the current pipeline and observe what gets recalled
3. Identify the gap (e.g., summaries contain entities the extractor missed)
4. Test a specific change (e.g., D-prompt) against that gap
5. Decision: adopt or discard

This is qualitative, targeted, and directly tied to the end use. It also produces
decisions, not metrics reports.

Formal annotation harnesses (L0/L1a) are justified only when:
- A qualitative experiment identifies a specific failure and you need to quantify its
  prevalence before investing in a fix
- You're comparing two approaches where qualitative judgment is ambiguous

### Current status (2026-06-05)

- L0 eval framework: implemented (`ats eval0`, `ats annotate-init`, `ats annotate-view`)
- L1a eval framework: implemented (`ats eval1a`, `ats annotate-memories`)
- L1b: not started (Pipeline 2b not built)
- L2/L3: not started (LoCoMo integration planned)

**Recommended next step for internal eval**: run L3 once Pipeline 2a memories are in
place. Use `ats recall` on 10 real queries from your own usage history. Compare results
with and without analytics-promoted memories. That delta tells you whether 2a is earning
its place.

---

## Endeavor 2 — Generating a Kaggle Benchmark from Real Traces

### What this is

An *external* benchmark derived from agent failure patterns in real traces. Evaluates
a *downstream consumer* (an LLM or agent) — not the ATS pipeline itself. Orthogonal
to Endeavor 1.

### Privacy and logistics (from Gemini, 2026-06-05)

**Visibility options:**
- Kaggle supports fully private benchmarks — not searchable, access via invitation only
- Dataset (sanitized traces) can be kept private and only exposed to models during eval
- **Start 100% private.** Only consider public after de-identification pipeline is validated.

**Model vs. harness problem:**
A trace is a recording of a *harness* (Claude Code, Gemini CLI) + *model* combo. You
can't directly benchmark the model because it doesn't have the tools at eval time.

Two approaches:

**Approach A — Static Reasoning ("model only")**
- Present truncated trace to the model
- Model outputs: the exact tool call + arguments that would have fixed the loop/error
- Ground truth: the human-correction turn that resolved the inflection
- Metric: tool selection accuracy + argument correctness
- No live tool execution — pure reasoning test

**Approach B — Simulated Environment ("model + mock tools")**
- Benchmark task acts as a mini-harness with mock tool implementations
- If model calls `read_file('src/main.py')`, harness returns sanitized file content
- Evaluates "loop-exit" capability in a controlled sandbox
- More complex to build; tests the full agentic loop, not just next-action prediction

### ⚠️ Feasibility re-evaluation (2026-06-06)

The recovery-benchmark design is **not feasible** as a rigorous benchmark. Full
analysis in `trace_to_benchmark_design.md`. Short version:

- Agent failures are irreducibly context-dependent. Redaction destroys the context
  that makes them solvable.
- The inflection detector fires on structural anomalies, not true failures. Phase 0
  found skill invocations, deliberate read passes, and text-only responses all
  triggering inflections — false positives before any redaction even happens.
- What the benchmark would actually measure: pattern recognition (which the
  inflection detector already does algorithmically) and prompt-following quality.

**Three revised directions — see `trace_to_benchmark_design.md` for full design:**

**Direction 1 — Failure Classifier:** given a lightly-redacted exchange window, can
a model correctly identify the failure type? Classification, not generation — context
matters much less, labels are objectively verifiable.

**Direction 2 — Real Failure Labeling Pipeline:** use inflection metrics as candidate
signal + human review to build a high-quality labeled dataset of genuine agent
failures. The dataset itself is the artifact. Feeds Direction 1 and 3.

**Direction 3 — Classify real failures from inflection metrics:** use the structural
signals already computed (`signal_strength`, `dominant_feature`, `action_entropy`,
`error_rate`, etc.) as features to train or evaluate a failure classifier. This stays
entirely within the ATS repo — no redaction, no external benchmark. The question:
do the structural features reliably predict what a human would label as a genuine
failure? If yes, the inflection detector can be improved using labeled data. If no,
the features need to be augmented (e.g. with text-level signals from chunk summaries).

**Direction 4 — Synthetic trace benchmark:** use real traces as inspiration to build
fully self-contained synthetic scenarios (mock codebases, canned tool responses).
Rigorous but high-effort. Depends on Directions 1–3 establishing which failure
patterns are worth constructing.

**Recommended sequence:** Direction 2 → Direction 1 → Direction 3 → Direction 4 if warranted.

---

### The one-pass generalizer approach

Proposed workflow:
1. Query `session_inflections` for high `signal_strength` events (agent_thrash,
   loop_entry, escalation)
2. Extract 5–10 preceding chunks + the post-correction turn (ground truth)
3. Local LLM (Ollama gemma3:12b) runs the Generalization Prompt: redact PII/paths/names,
   abstract private APIs, preserve the core failure reasoning
4. Schema-validate the output (required fields: scenario, core_failure, goal,
   ground_truth_action)
5. Human spot-check on 10% of generated tasks
6. Package as benchmark tasks

**The ground truth is already in the DB.** The human-correction turn immediately
following an inflection point is what resolved the failure. This is the label.

### Relationship to Track 2 (action-pattern memory extraction)

The generalization prompt is doing manually what Track 2 (deferred, design_decisions.md
Decision 29) is designed to do as a proper pipeline component: extract action-pattern
memories from inflection contexts. The benchmark export is essentially:
- Take Track 2 output
- Add a sanitization layer
- Add the post-correction turn as ground truth
- Package for external consumption

Track 2 is not a prerequisite for prototyping — you can use raw inflection windows.
But the clean version runs through Track 2.

### Prototype scope

Minimal script to validate the approach:
1. Query top 20 inflection points by signal_strength from ats.db
2. Fetch preceding N chunks per inflection (start with 7)
3. Run generalization prompt via OllamaProvider
4. Output JSON: {scenario, core_failure, goal, ground_truth_action, inflection_type}
5. Manual review to assess de-identification quality and task clarity

Start with Approach A (static reasoning) — simpler to evaluate, no mock harness needed.

### Open questions

- How many chunks to include (context window tradeoff vs. coherence)?
- Does the generalization prompt preserve enough signal across different inflection types
  (agent_thrash vs. escalation vs. loop_entry)?
- What's the right scoring metric for Approach A? Exact tool match is too strict;
  semantic similarity of intent may be more appropriate.
- When does this warrant going beyond private Kaggle to a public benchmark?
  (Threshold: de-id validated on 50+ tasks with human review)
