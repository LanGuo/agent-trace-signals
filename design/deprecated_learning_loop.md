# [DEPRECATED] ATS — Learning Loop Design

> **DEPRECATED 2026-07-22.** Of the three capabilities scoped here: inflection
> point detection shipped and is still live (see `label-failures` in
> `src/agent_trace_signals/cli.py`) but is now documented on its own in
> `design/exchange_classifier.md`, not here; Track 2 memory extraction
> (`action_orientation`) was never built and its design home
> (`design/deprecated_memory_evolution.md`) is itself deprecated; multi-judge
> annotation was re-scoped elsewhere. No section of this doc remains an
> accurate description of current or planned work. Kept for historical
> reference. See `design/design_decisions.md` for current design history.

> As of 2026-05-29. Design doc for the next implementation phase: inflection point
> detection, two-track memory extraction, action_orientation, and multi-judge
> annotation. Supersedes `docs/loop_closure_exploration.md` and
> `docs/multi_judge_annotation.md` (both removed).
>
> For the full data model and pipeline architecture, see `design/exploration.md`.
> For the reasoning behind each decision, see `design/design_decisions.md`.

---

## Scope

This doc covers three new capabilities to implement after Pipeline 2a:

1. **Inflection point detection** — turn-level heuristic detector integrated into
   Pipeline 1 ingestion
2. **Track 2 memory extraction** — action-pattern-centric extraction from inflection
   context, adding `action_orientation` to memories
3. **Multi-judge annotation** — LLM ensemble for memory quality eval and inflection
   validation, reducing manual annotation burden

These are independent enough to implement and evaluate separately, but designed as a
coherent system.

---

## 1. Inflection Point Detection

### What it is

A structural analysis pass over the parsed turn stream that emits `session_inflections`
rows alongside chunking. Zero additional pipeline steps — runs in the same loop as
`_build_records()`.

Inflections are detected as **episodes** with three index points:

```
[normal turns]
      ↓
[precipitating_turn]  ← estimated root cause: last turn before anomaly begins
      ↓
[entry_turn]          ← detectable: structural feature crosses 2σ session baseline
      ↓
[... stuck plateau ...]
      ↓
[exit_turn]           ← resolution: entropy recovers, errors clear, or user intervenes
                        NULL if session ends without resolution
```

### Structural features (primary signals)

Four features computed per-turn from parsed JSONL — no text parsing, no keyword lists:

| Feature | What it captures | Computation |
|---|---|---|
| `action_entropy` | Diversity of tool use in recent turns | Shannon entropy of tool names in last 5 turns |
| `action_target_recurrence` | Fraction of `(tool, target)` pairs that are exact repeats of earlier session history | Repeated pairs in last 5 turns / 5 |
| `error_rate` | Accumulating tool failures | Rolling mean of `is_error` on tool results, last 5 turns |
| `user_turn_length_ratio` | User message length relative to session norm | Current user turn word count / session median user turn word count |

Each feature is z-scored against that session's own running statistics (running mean and
std). No global thresholds, no hardcoded weights. Self-calibrates to session style: a
session where the agent always uses many tools has a higher `action_entropy` baseline.

**A turn is an inflection candidate when any feature deviates >2σ from its session baseline.**

`signal_strength`: z-score magnitude of the dominant (most-deviated) feature at
`entry_turn`. Continuous, session-calibrated — a 3σ deviation is a stronger inflection
than a 2.1σ one.

`dominant_feature`: which feature triggered the episode, used to classify the inflection type:

| `dominant_feature` + direction | Inflection type |
|---|---|
| `action_target_recurrence` ↑ AND `action_entropy` ↓ | `loop_entry` |
| `action_entropy` ↑ or `error_rate` ↓ after a `loop_entry` episode | `loop_exit` |
| `user_turn_length_ratio` ↓ sharply following several tool calls | `user_correction` |
| `error_rate` ↑ monotonically over 5+ turns | `escalation` |
| `action_entropy` ↑ sharply without preceding loop | `strategy_pivot` |

**`precipitating_turn` lookback**: scan backwards from `entry_turn` (max 5 turns) for
the last turn where: (a) `error_rate` first became non-zero in the current rising trend,
OR (b) a new file target appeared that the agent hadn't touched before. That turn is the
estimated root cause. Cap at 5 turns to avoid false attribution.

### Text-based signals (secondary, deferred)

Agent hedging language and user sentiment are a secondary layer — one LLM call per
flagged episode (not per turn) to classify `interaction_type` and confirm type
disambiguation. Not needed for initial implementation; add after structural detection
is validated.

### Schema

```sql
CREATE TABLE session_inflections (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    inflection_type     TEXT NOT NULL,
    precipitating_turn  INT  NOT NULL,
    entry_turn          INT  NOT NULL,
    exit_turn           INT,
    signal_strength     REAL NOT NULL,
    dominant_feature    TEXT NOT NULL,
    signal_detail       TEXT,            -- JSON: feature values at detection time
    detected_at         TEXT NOT NULL
);
```

### Success criteria

| Metric | Target | How to measure |
|---|---|---|
| Detector runs without error on all ingested sessions | 100% | Re-ingest sessions, check for exceptions |
| At least 1 inflection detected per long session (>20 turns) | ≥ 80% of long sessions | `SELECT session_id, COUNT(*) FROM session_inflections GROUP BY session_id` |
| Inflection precision (judge-validated) | > 0.65 | Run inflection judge on 20% sample; count confirmed / (confirmed + false_positive) |
| `loop_entry` → `loop_exit` pairing rate | > 0.50 | Most loops should have a corresponding exit in same session |
| No false positives on trivially short sessions (<5 turns) | 0 | Manual spot-check |
| `signal_strength` distribution — median > 2.0, tail > 3.0 | ✓ | `SELECT AVG(signal_strength), MAX(signal_strength) FROM session_inflections` |

---

## 2. Track 2 Memory Extraction

### What it is

A new extraction path in Pipeline 2a that operates on inflection context tuples rather
than entity occurrence contexts. Produces corrective procedural memories with
`action_orientation` populated.

### Inflection context tuple

For each detected episode, extract:

```python
InflectionContext(
    inflection_type: str,
    precipitating_action: tuple[str, str, str],  # (tool, target, outcome) at precipitating_turn
    user_signal: str,           # verbatim user message at/near exit_turn
    agent_reasoning: str,       # agent text at precipitating_turn
    interaction_type: str,      # 'tool_call' | 'clarifying_exchange' | 'self_correction'
    episode_start_turn: int,    # precipitating_turn index
    episode_end_turn: int,      # exit_turn index, or last session turn if unresolved
)
```

The LLM extraction context spans `episode_start_turn → episode_end_turn` — the full arc
from root cause through failure pattern to resolution. This is richer than a fixed ±3
window: the resolution turn contains the fix that makes a "when X, do Y" memory
actionable, and the precipitating turn captures the assumption that went wrong.

`interaction_type` mapping:
- `tool_call`: inflection follows a tool call with bad outcome
- `clarifying_exchange`: agent asked a question, user answered, then correction
- `self_correction`: agent retraction before user intervened

### action_orientation derivation

| `action_orientation` | Derived from |
|---|---|
| `recovery` | Inflection context near `loop_exit` — this pattern preceded recovery |
| `optimization` | Inflection context near `loop_entry` — this pattern preceded getting stuck |
| `strategy` | Action pattern in turns with no inflections — stable, successful usage |
| `observation` | Inflection context without clear recovery/loop signal |

### LLM extraction prompt (Track 2)

```
Inflection type: <type>
Interaction type: <interaction_type>

Root cause (turn <episode_start_turn>):
  Agent action: <tool>(<target>) → <outcome>
  Agent reasoning: "<agent_reasoning>"

Episode turns <episode_start_turn> → <episode_end_turn>:
<serialized turns spanning the full episode>

Resolution (turn <episode_end_turn>):
  User response: "<user_signal>"
  Resolved by: <loop_exit|user_correction|agent_self_correction|session_end_unresolved>

Extract a corrective procedural memory capturing the lesson from this episode.
The memory should be actionable — "when X happens, do Y" or "avoid Z because W".

Reply as JSON:
{
  "action_orientation": "recovery" | "optimization" | "strategy" | "observation",
  "content": "<memory statement>"
}
```

### Schema change

```sql
ALTER TABLE memories ADD COLUMN action_orientation TEXT;
-- NULL for Track 1 memories (backward compatible)
-- 'recovery' | 'optimization' | 'strategy' | 'observation' for Track 2
```

### Success criteria

| Metric | Target | How to measure |
|---|---|---|
| Track 2 memories extracted per analytics run (14-session corpus) | ≥ 10 | `SELECT COUNT(*) FROM memories WHERE action_orientation IS NOT NULL` |
| `action_orientation` distribution — no single value > 70% | ✓ | `SELECT action_orientation, COUNT(*) FROM memories WHERE action_orientation IS NOT NULL GROUP BY 1` |
| Track 2 memory judge quality (correct + partial) | > 0.60 | Run memory judge on Track 2 memories; (correct+partial) / total |
| Track 2 memories are distinct from Track 1 (no near-duplicates) | < 10% overlap | Cosine similarity > 0.90 between Track 1 and Track 2 memories |
| `interaction_type` distribution covers all three types | ≥ 1 of each | From inflection context extraction |

---

## 3. Multi-Judge Annotation

### What it is

A 3-provider LLM ensemble that auto-labels memories and inflection points, routing
only disagreements to human review.

### Provider configuration

| Provider | Model | Role |
|---|---|---|
| Ollama (local) | `llama3.2` or `olmo-3:7b` | Judge A — never gemma3:12b (extractor) |
| Anthropic | `claude-haiku-4-5` | Judge B |
| Google Gemini | `gemini-2.0-flash` | Judge C — **GeminiProvider not yet built** |

All three judges run in parallel per target. Total latency ≈ slowest provider.

### Surface 1: Memory quality

**Purpose**: automated Level 1a eval ground truth. Replaces manual labeling of
all memories.

**Label space**: `correct` | `partial` | `incorrect`

**Input**: entity name/type/profile + memory type + memory content + evidence count.
(Atomic facts not available — they are ephemeral in current implementation. A future
`source_facts` column on `memories` would improve judge quality.)

**Decision rule**:
- 3/3 agree → auto-label
- 2/3 agree → majority label, 10% spot-check
- 1-1-1 split → human review queue

### Surface 2: Inflection validation

**Purpose**: precision estimation for the heuristic inflection detector.

**Sample rate**: 20% of detected inflections per session (random sample — not all
inflections need judging).

**Input**: 5-turn window (±2 turns around the inflection) + detected type + signal_detail.

**Label space**: `confirmed` | `false_positive` | `reclassify:<new_type>`

### Schema

Columns added to existing tables:

```sql
-- on memories:
ALTER TABLE memories ADD COLUMN judge_label TEXT;
ALTER TABLE memories ADD COLUMN judge_agreement TEXT;   -- 'unanimous'|'majority'|'split'
ALTER TABLE memories ADD COLUMN needs_human_review INT DEFAULT 0;

-- on session_inflections:
ALTER TABLE session_inflections ADD COLUMN judge_label TEXT;
ALTER TABLE session_inflections ADD COLUMN judge_agreement TEXT;
ALTER TABLE session_inflections ADD COLUMN needs_human_review INT DEFAULT 0;
```

Human review UI: extend the existing memory viewer to show judge labels and a
"needs review" filter. Unanimously-agreed items hidden by default.

### GeminiProvider — required dependency

`AnthropicProvider` and `OllamaProvider` exist. `GeminiProvider` needs to be built
using `google-generativeai` SDK. Same interface: `complete(prompt, schema?) → str | dict`.

This is a blocking dependency for multi-judge annotation. Can be bypassed initially
by running with 2 judges (Ollama + Anthropic) — 1-1 tie → human review (higher human
load but functional).

### Success criteria

| Metric | Target | How to measure |
|---|---|---|
| Memory judge unanimous agreement rate | > 60% | `SELECT judge_agreement, COUNT(*) FROM memories GROUP BY 1` |
| Memory judge split rate (human review load) | < 25% | Same |
| Inflection judge false positive rate | < 35% | `confirmed / (confirmed + false_positive)` on sampled inflections |
| Human review queue drains in < 30 min per analytics run | ✓ | Measure at current corpus size |
| GeminiProvider passes same interface test as Ollama/Anthropic | ✓ | Unit test |

---

## Loop closure architecture (full picture)

```
Claude Code session runs
       │
       ▼
Phase 1 Ingest (existing + inflection detection)
  ├── Parse JSONL → turn stream
  ├── Single pass: chunk + inflect_detect simultaneously
  │     → records (chunks) for entity extraction
  │     → session_inflections for Track 2
  ├── Entity extraction → occurrences (Track 1 input)
  └── Explicit memories (Path A, unchanged)
       │
       ▼
Phase 2a Light Analytics
  ├── Track 1: entity promotion → fact compression → memories[episodic/procedural/semantic]
  └── Track 2: inflection context extraction → corrective memories[procedural + action_orientation]
       │
       ▼
Multi-judge annotation
  ├── Surface 1: memory quality (replaces manual Level 1a annotation)
  └── Surface 2: inflection validation (precision estimate for detector)
       │ human reviews splits only
       ▼
Phase 3: MCP Retrieval Server (not yet built)
  ├── Session-start injection: top-K memories by similarity
  │     priority: recovery memories first (most immediately actionable)
  ├── On-demand retrieval mid-session
  └── Log memory_ids retrieved per session → memory_retrievals table
       │
       ▼
New Claude Code session runs with injected memories
  └── Behavior shaped by Track 2 recovery/optimization memories
       │
       ▼
New trace ingested → inflection density measured
  └── Memory weight update:
        memories retrieved before sessions with fewer subsequent inflections → higher weight
        (soft RL approximation — requires memory_retrievals table + memories.weight field)
```

The soft RL layer (weight updates) requires ~100+ sessions to generate reliable signal.
Build it after Phase 3 is live.

---

## Open questions

1. **Inflection detector accuracy before use in Track 2**: if the detector has poor
   precision (many false positives), Track 2 memories will be noisy. Run the inflection
   judge (Surface 2) on a sample before running Track 2 extraction. Decision threshold:
   if inflection precision < 0.50, tune the detector before extracting Track 2 memories.

2. **Atomic facts persistence**: currently ephemeral (in-memory only during analytics).
   Persisting them would improve memory judge quality (judges could distinguish
   extraction failure from corpus sparsity). Proposed: `source_facts TEXT` column on
   `memories`. Add this before building the memory judge if judge quality on memory
   content alone turns out to be insufficient.

3. **Track 2 deduplication**: corrective memories from similar inflection patterns
   across sessions should be merged (EvolveR-style). Currently no deduplication for
   Track 2. Add a merging pass (cosine similarity > 0.90 → LLM merge) after initial
   extraction.

4. **TRAIL error taxonomy tagging**: once Track 2 memories exist, classify each by
   TRAIL error category (reasoning / system_execution / planning_coordination) at
   analytics time. Enables operator reports ("most common error type this month") and
   priority-aware retrieval. Lower priority than getting the detector and extraction
   working first.

5. **Memory lifespan**: recovery memories for environment errors (missing API key,
   wrong Python version) become stale once fixed. Weight decay (from the soft RL layer)
   handles this naturally — stale memories retrieved before sessions that still succeed
   won't get their weight penalized, but they also won't get promoted. A periodic
   pruning pass (weight < 0.1 → archive) keeps the retrieval set clean.
