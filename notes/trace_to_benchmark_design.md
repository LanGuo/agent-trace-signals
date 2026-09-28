# Trace → Benchmark: Design & Plan

> Working doc — not committed to git.
> Started: 2026-06-05
> Revised: 2026-06-06 — original approach found infeasible; pivoted to three new directions

---

## ⚠️ Feasibility Re-evaluation (2026-06-06)

The original design — redact real traces, abstract context, evaluate whether a model
can recover from the failure — is **not feasible as a rigorous benchmark**.

### Why the original approach fails

Agent trace failures are irreducibly context-dependent. The "correct" action at
any given exchange is only correct given: the full codebase state, what every prior
tool call returned, implicit project goals built up over hundreds of turns, and the
human's unstated preferences.

Redaction destroys exactly the information that makes the failure solvable. The
ground truth suffers the same problem: "user sent a URL and the agent did 8 WebFetch
calls" abstracted to "user redirected agent to do research" is not a learnable label.

What the original benchmark would actually measure:
- **Pattern recognition** — which the inflection detector already does algorithmically
- **Generic strategy advice** ("try a different approach") — not differentiating
- **Prompt-following quality** — artifact quality, not model capability

Phase 0 also showed that the inflection detector fires on *structural anomalies*,
not true failures — skill invocations, deliberate read passes, and text-only responses
all trigger it. The signal is too noisy for failure-recovery benchmarking.

### Three revised directions

See full discussion below. In priority order:

1. **Failure classifier** — use traces to train/eval a model that labels failure types
   from lightly-redacted exchange windows. Classification, not generation.
2. **Real failure labeling pipeline** — use inflection metrics + manual review to
   build a high-quality labeled dataset of real agent failures. The dataset is the
   artifact, not a Kaggle harness.
3. **Synthetic trace benchmark** — use real traces as inspiration to construct fully
   self-contained synthetic scenarios (mock codebases, canned tool responses).
   Much more work; produces something rigorous like SWE-bench.

---

## Original design (superseded — kept for reference)

Two repos, one interface.

**`agent-trace-signals` (this repo)** — extracts and sanitizes benchmark tasks from real
traces. Output: a JSON file of anonymized task records.

**`agent-trace-benchmark` (new repo)** — consumes that JSON, defines the eval harness,
hosts the Kaggle benchmark. Output: a scored leaderboard.

The interface between them is `tasks.json`.

---

## Part 1 — ATS Repo: Extraction & Generalization

### What it does

1. Queries `session_inflections` for high-signal failure events
2. Fetches the surrounding conversation window (preceding chunks + post-correction turn)
3. Runs a local LLM generalization pass to anonymize and abstract
4. Validates output structure
5. Writes `tasks.json`

### Pipeline stages

```
session_inflections (signal_strength >= threshold)
    ↓
fetch_window(inflection)
    → preceding N chunks from records table
    → post-correction turn (first record after inflection.end_turn)
    ↓
generalize(window)  [local LLM, never leaves machine]
    → scenario: str          # generalized problem description
    → core_failure: str      # what went wrong / why the agent got stuck
    → goal: str              # what a correct agent should do
    → ground_truth_action: str   # the corrective action taken (from post-correction turn)
    → inflection_type: str   # preserved as-is (loop_entry / agent_thrash / escalation / …)
    → difficulty: str        # easy / medium / hard — LLM self-assessed
    ↓
validate(task)
    → required fields present
    → no PII patterns (path regex, email, real names heuristic)
    → min length on scenario + core_failure
    ↓
tasks.json
```

### Window size

- Default: 7 chunks preceding the inflection window + the post-correction turn
- Rationale: inflection window is 5 turns; 7 chunks gives ~2 turns of pre-signal context
- Configurable: `--window-before N`, `--include-correction/--no-correction`

The post-correction turn (ground truth) is included in extraction but written to a
separate `ground_truth` field — kept out of the task prompt presented to evaluated models.

### Generalization prompt (v1)

```
You are a Technical Anonymizer. Transform this raw agent trace excerpt into a
generalized programming task for a benchmark.

Rules:
1. REDACT: Replace all usernames, home directory paths (e.g. /Users/<name> →
   /workdir), project names, and company names with generic placeholders.
2. ABSTRACT: If code references a private API or internal tool, describe its
   function instead (e.g. "an internal search API that returns ranked results").
3. PRESERVE: The core technical failure — the logic error, the repeated action,
   the wrong strategy — must remain clearly visible in the abstracted version.
4. DO NOT INVENT: Do not add failures or context that are not present in the trace.

Output JSON with these exact fields:
{
  "scenario": "...",        // 2–4 sentence description of the task context
  "core_failure": "...",    // 1–2 sentences: what went wrong and why
  "goal": "...",            // 1 sentence: what a correct agent should accomplish
  "ground_truth_action": "...", // the action that resolved the failure (from the
                                //   post-correction turn — paraphrase, don't quote)
  "difficulty": "easy|medium|hard"
}
```

### Validation pass

After LLM output, run a deterministic validator before accepting a task:

```python
PII_PATTERNS = [
    r'/Users/\w+',           # home dirs
    r'\b[A-Z][a-z]+guo\b',  # surname heuristic — extend as needed
    r'\b\w+@\w+\.\w+\b',    # emails
]

REQUIRED_FIELDS = ['scenario', 'core_failure', 'goal', 'ground_truth_action', 'difficulty']
MIN_WORDS = {'scenario': 20, 'core_failure': 10, 'goal': 8}
```

Failed validation → log warning, skip task. Do not silently include a task that fails PII check.

### `tasks.json` schema

```json
[
  {
    "id": "task_<sha8>",
    "inflection_type": "agent_thrash",
    "signal_strength": 3.4,
    "scenario": "...",
    "core_failure": "...",
    "goal": "...",
    "ground_truth_action": "...",
    "difficulty": "medium",
    "source_session_hash": "<sha256 of session_id>",  // one-way hash, not the real ID
    "generated_at": "2026-06-05T..."
  }
]
```

`source_session_hash` allows deduplication across runs without exposing the real session ID.

### CLI command

```
uv run ats export-benchmark \
    --min-signal 2.5 \
    --inflection-types agent_thrash loop_entry escalation \
    --window-before 7 \
    --output tasks.json \
    --limit 100
```

### Module location (this repo)

```
src/agent_trace_signals/
  export/
    __init__.py
    benchmark_exporter.py   # orchestrates the pipeline above
    generalizer.py          # wraps OllamaProvider with the generalization prompt
    validator.py            # PII check + field validation
```

---

## Part 2 — New Repo: `agent-trace-benchmark`

### Purpose

Hosts the benchmark harness, the published (sanitized) task dataset, and the scoring
logic. Has no dependency on ATS, Ollama, or SQLite. Consumes `tasks.json`.

### Structure

```
agent-trace-benchmark/
  data/
    tasks.json              # generated by ATS export, committed after human review
    tasks_sample.json       # 10-task public preview (for README)
  harness/
    __init__.py
    task_runner.py          # loads tasks, calls model, scores
    scorer.py               # scoring logic per approach
    mock_tools.py           # Approach B: mock tool implementations
  tasks/
    static_reasoning.py     # Approach A: next-action prediction tasks
    simulated_env.py        # Approach B: mock-harness tasks (deferred)
  eval/
    run_eval.py             # entry point: python -m eval.run_eval --model gpt-4o
    metrics.py              # precision, semantic similarity, loop-exit rate
  README.md
  pyproject.toml
```

### Evaluation approaches

**Approach A — Static Reasoning (build first)**

Present the model with the sanitized scenario + core_failure. Ask it to output:
- Which tool to call next
- What arguments to pass
- One-sentence rationale

Score against `ground_truth_action`:
- **Exact tool match**: did the model call the right tool category?
- **Semantic similarity**: cosine similarity between model rationale and ground truth
  (using a small embedding model — no Ollama dependency, use sentence-transformers)
- **Avoidance rate**: did the model avoid repeating the failed action?

```python
# harness/task_runner.py sketch
def run_task_a(task: dict, model_fn: Callable) -> dict:
    prompt = TASK_A_PROMPT.format(
        scenario=task['scenario'],
        core_failure=task['core_failure'],
        goal=task['goal'],
    )
    response = model_fn(prompt)
    return score_response(response, task['ground_truth_action'])
```

**Approach B — Simulated Environment (deferred)**

The harness acts as a mock tool server. Model calls tools; harness returns sanitized
responses. Evaluates loop-exit rate (did the agent break out of the failure pattern
within K turns?). Requires mock_tools.py to implement read_file, search, etc. with
canned responses derived from the trace window.

Build after Approach A is validated end-to-end.

### Scoring summary

| Metric | Approach | How measured |
|--------|----------|-------------|
| Tool selection accuracy | A | Exact category match vs. ground truth |
| Rationale similarity | A | Cosine sim, threshold > 0.65 = correct |
| Avoidance rate | A | Does model repeat the failed action? |
| Loop-exit rate | B | Did agent resolve within K turns? |
| Recovery efficiency | B | Turns to resolution vs. original trace |

---

## Implementation Plan

### Phase 0 — Validate the approach ✅ DONE (2026-06-05)

Re-parsed source JSONLs for top-5 inflections. Full findings in
`notes/phase0_findings.md`. Key results:

- Re-parsing by `entry_turn` exchange index is clean and deterministic. Option B
  confirmed: export pipeline re-parses source files via `ingestion_state.abs_path`.
- 2 of 3 cases inspected have clean, usable ground truth (exchange `entry_turn + 1`
  is a genuine user correction). 1 case is a context-continuation boundary — need
  to filter these out at export time.
- Context window `[precip_turn - 1 : entry_turn + 1]` (~5 exchanges) is readable
  and contains the failure buildup.

**Schema gaps found (fix in ingestion before scaling export):**
- GAP-1: `records.span_start` / `span_end` ✅ FIXED — all three plugins now write exchange index ranges
- GAP-2: `turn_descriptors` computed but not persisted to DB (discarded after detection)
- GAP-3: `sessions.metadata` and `raw_facets` always empty `{}` — plugin metadata
  not forwarded to Session object
- GAP-4 (export-time filter): context-continuation messages at `entry_turn + 1`
  must be detected and skipped (start with "This session is being continued…")

### Phases 1–4 (superseded)

Original phases assumed the recovery-benchmark design. Superseded by the three
revised directions below.

---

## Revised Directions (2026-06-06)

### Direction 1 — Failure Classifier (recommended starting point)

**What:** Given a lightly-redacted exchange window (real tool names, real error text,
PII stripped), can a model correctly identify the failure type and root cause?

**Why this works where the original didn't:** Classification doesn't require the model
to know the solution — only to recognize the pattern. Context matters much less. The
label (inflection type + dominant feature + manual confirmation) is objectively
verifiable, not context-dependent.

**What we're actually evaluating:** Does the model understand agent failure modes?
Can it distinguish a loop from a thrash from an escalation from normal behavior?
This is a meaningful capability question.

**Dataset construction:**
1. Take inflection windows from `session_inflections` (re-parse source files via
   Option B approach confirmed in Phase 0)
2. Strip PII (usernames, paths, project names) — light redaction, preserve tool names
   and error text
3. Manual review: confirm each is a genuine failure (not a skill invocation or
   deliberate read pass — the Phase 0 false-positive problem)
4. Label: `{inflection_type, dominant_feature, is_genuine_failure: bool, notes}`

**Scoring:** Accuracy of failure type classification. Secondary: can the model
identify the precipitating exchange?

**Effort to prototype:** ~1 day for dataset construction (20 windows), 1 day for
eval harness.

---

### Direction 2 — Real Failure Labeling Pipeline (high value, feeds Direction 1)

**What:** Build a labeled dataset of real agent failures — the dataset itself is the
artifact, not a Kaggle harness. This can feed Direction 1 (classifier eval) and
Direction 3 (synthetic generation) and has standalone research value.

**The key insight:** The inflection detector gives *candidate* failure moments.
Human review confirms which are genuine. The combination — structural signal + human
judgment — produces high-quality labels at reasonable annotation cost.

**Labeling schema per instance:**
```
exchange_window: [exchange_idx-4 ... exchange_idx+1]
inflection_type: agent_thrash | loop_entry | escalation | ...
dominant_feature: read_only_ratio | action_entropy | error_rate | ...
is_genuine_failure: bool          # human judgment
failure_description: str          # 1 sentence, human-written
recovery_type: user_redirect | user_correction | agent_self_recovery | session_end
recovery_description: str         # 1 sentence, what actually resolved it
```

**Why `recovery_type` matters:** Phase 0 revealed that "corrections" vary: some are
genuine user interventions, some are context-continuation boundaries, some are the
user just moving on. Labeling the recovery type makes the dataset more useful.

**Pipeline in this repo:**
- Query `session_inflections` filtered by type and signal_strength threshold
- Re-parse source file, extract window
- Strip PII
- Render to a review UI (extend existing `annotation/` module or a simple HTML viewer)
- Human labels saved to `labeled_failures/` JSON

**Scale:** 50 labeled instances is enough to be meaningful. With ~9 sessions in
`traces.db` averaging ~15 inflections each, there are ~135 candidates — a 50-instance
labeled set is ~37% yield after filtering false positives.

---

### Direction 3 — Synthetic Trace Benchmark (most rigorous, most effort)

**What:** Use real traces as inspiration to construct fully self-contained synthetic
scenarios: mock codebases, canned tool responses, known-correct exit conditions.
The real traces tell you which failure patterns are worth constructing; the actual
benchmark instances are synthetic.

**Why this works:** Full context is provided (synthetic codebase, canned tool
responses). The model can actually solve the problem because all necessary information
is present. Scoring is verifiable (did the agent exit the loop? did it produce the
right output?).

**Relationship to SWE-bench:** Same principle — give the model everything it needs,
verify against a concrete spec. SWE-bench uses real repos + real bugs; this uses
synthetic repos + synthetic failure patterns drawn from real traces.

**Effort:** High. Each synthetic scenario requires: a mock codebase, a set of canned
tool responses for ~10 exchanges, a verifiable success condition. Realistic scope is
5–10 hand-crafted scenarios as a v1.

**Dependency:** Directions 1 and 2 first — need to know which failure patterns are
real and worth constructing before investing in synthetic scenarios.

---

## Recommended sequence

```
Direction 2 (labeling pipeline) → Direction 1 (classifier eval) → Direction 3 (synthetic, if warranted)
```

Direction 2 produces the labeled dataset that grounds everything else. Direction 1
is a fast, meaningful eval that uses it directly. Direction 3 is only worth building
after Direction 1 shows which failure patterns are hard to classify — those are the
ones worth constructing synthetic scenarios for.

---

## Open Questions (revised)

1. **Inflection detector signal quality**: Phase 0 showed that `agent_thrash` and
   `loop_entry` frequently fire on non-failures. Is `escalation` (error_rate > 50%)
   the most reliable type for seeding the labeled dataset? Or is manual review
   sufficient to filter any type?

2. **Labeling cost**: How long does it take to label one instance (review the window,
   decide genuine/not, write two sentences)? If >5 min each, 50 instances is a
   significant commitment — may need to reduce scope or automate pre-filtering.

3. **Classifier baseline**: What's the expected difficulty of Direction 1? If a simple
   heuristic (count read-only calls, check error rate) gets 80% accuracy, the LLM
   classifier doesn't add value. Run the heuristic baseline first to set the bar.

4. **Privacy for Direction 1 dataset**: Light redaction (strip PII, keep tool names
   and error text) may still expose enough to identify the project from tool call
   patterns (e.g. a sequence of `Edit(src/agent_trace_signals/...)` calls). Decide
   whether the dataset is private-only or whether heavier abstraction is needed for
   any public release.
