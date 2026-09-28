# Exchange Classifier

> Generic per-exchange classification, consumer-agnostic. Each
> downstream consumer (inflection detector, per-step observer scoring,
> failure keyword density, etc.) picks its own filter set over the
> shared taxonomy.
>
> Empirical grounding: sessions examined CC c30450db, Gemini symphony +
> chess, Opencode 517fcbdd. Replaces inflection-detector-anchored
> [`notes/deprecated_exchange_classifier_design.md`](../notes/deprecated_exchange_classifier_design.md).
>
> **Harness specificity is fundamental.** Each agent harness (Claude
> Code, Gemini CLI, Opencode, future harnesses like Cursor) has its own
> exchange structure, its own set of harness injection patterns, its
> own conventions for autonomous-agent multi-turn continuation, and its
> own tool result format. The classification *taxonomy* (the label set)
> is shared across harnesses so downstream consumers stay
> harness-agnostic, but the *implementation* of the classifier is
> per-plugin — each `SourcePlugin` owns its own `_classify_exchange`
> method matching its own format. See "Per-harness empirical grounding"
> and "Per-plugin implementation" sections below for what differs.

---

## Purpose

Real production traces (Claude Code, Gemini CLI, Opencode) interleave
substantive agent/human work with harness-injected exchanges (skill
header injections, task notifications, polling loops, context-
continuation markers, compaction messages). Without a classifier:

- The inflection detector's sliding-window z-scores are contaminated
  (95% false-positive rate documented in
  [`notes/inflection_detector_evaluation.md`](../notes/inflection_detector_evaluation.md)).
- Per-step observer scoring wastes calls on non-substantive exchanges
  and produces meaningless evidence_supports values for harness pings.
- Keyword density misattributes harness-text to agent behavior.

The classifier tags every exchange with an `exchange_type`. Downstream
consumers filter on it as they choose.

---

## Taxonomy (single source of truth)

| `exchange_type` | Harness | Detection rule |
|---|---|---|
| `genuine_human` | all | has user text + has agent tool calls |
| `genuine_text` | all | has user text + no agent tool calls |
| `skill_invocation` | CC | `user.startswith("Base directory for this skill:")` |
| `task_notification` | CC | `"<task-notification"` in user text |
| `context_continuation` | CC | `user.startswith("This session is being continued")` |
| `wakeup_injection` | CC | `ScheduleWakeup` in tool_calls OR `scheduledFor` in toolUseResult |
| `agent_continuation` | Gemini | empty user content (Gemini multi-turn autonomous reasoning) |
| `summary_diff` | Opencode | `user.data.summary.diffs` present, no text parts |

The taxonomy enum lives in `models.py` so all plugins emit the same
labels and downstream code is harness-agnostic.

---

## Per-harness empirical grounding

Distribution of types across the sampled sessions.

### Claude Code — c30450db (169 exchanges)

| Type | Count | % |
|------|------:|--:|
| `genuine_human` | 67 | 40% |
| `genuine_text` | 80 | 47% |
| `skill_invocation` | 14 | 8% |
| `context_continuation` | 8 | 5% |

No `task_notification` or `wakeup_injection` in this session — both
were observed in other CC sessions (SLR-gemma4 7610ea24 had
`task_notification` clusters at turns 148–152).

### Gemini CLI — symphony session (144 exchanges)

| Type | Count | % |
|------|------:|--:|
| `genuine_text` | 26 | 18% |
| `agent_continuation` | 118 | **82%** |

**Note the 82%.** Gemini's `_build_exchanges` groups by user message, so
many consecutive agent turns with no user prompt between them become
`exchanges` with empty `user.content`. This is *not* a harness
injection — it's autonomous agent work. Filtering it out (as the
inflection detector does, for its own reasons) would make most
downstream analytics silent on Gemini sessions.

### Opencode — 517fcbdd (71 exchanges)

| Type | Count | % |
|------|------:|--:|
| `genuine_human` | 49 | 69% |
| `genuine_text` | 19 | 27% |
| `summary_diff` | 3 | 4% |

`summary_diff` is Opencode-specific: harness injects a diff payload
to keep the agent context-aware after code changes.

---

## Per-plugin implementation

Each plugin owns its own classifier in `_extract_turn_descriptor`. The
classifier is deterministic string/key matching — zero LLM cost.

**Claude Code** (`plugins/claude_code.py`):
```python
def _classify_exchange(exchange):
    ut = user_text(exchange)
    if ut.startswith("Base directory for this skill:"): return "skill_invocation"
    if "<task-notification" in ut:                      return "task_notification"
    if ut.startswith("This session is being continued"): return "context_continuation"
    if has_wakeup(exchange):                             return "wakeup_injection"
    if not tool_calls(exchange):                         return "genuine_text"
    return "genuine_human"
```

**Gemini** (`plugins/gemini_cli.py`):
```python
def _classify_exchange(exchange):
    ut = user_text(exchange)
    if not ut.strip():                return "agent_continuation"
    if not tool_calls(exchange):      return "genuine_text"
    return "genuine_human"
```

**Opencode** (`plugins/opencode.py`):
```python
def _classify_exchange(exchange):
    user = exchange["user"]
    has_text_parts = any(p.get("type") == "text" for p in user.get("parts", []))
    has_summary_diff = "diffs" in user.get("data", {}).get("summary", {})
    if has_summary_diff and not has_text_parts: return "summary_diff"
    if not tool_calls(exchange):                return "genuine_text"
    return "genuine_human"
```

When a new harness is added (Cursor, etc.), only its plugin needs a
classifier; the taxonomy + downstream filters are reused.

---

## Per-consumer filter sets

Each downstream consumer chooses its own filter. **There is no single
"GENUINE_TYPES" — different consumers have different needs.**

### Inflection detector

```
GENUINE_TYPES = {"genuine_human", "genuine_text"}
```

Why: the inflection detector computes sliding-window z-scores.
Consecutive `agent_continuation` exchanges (Gemini) have zero tool
calls each, which creates a degenerate zero-entropy window that
otherwise fires `loop_entry` falsely. Filtering out
`agent_continuation` and `task_notification` clusters fixes this.

Also requires: removal of the `model_turn_count > 2.0` absolute
threshold (meaningless for Gemini SDE sessions; use z-score only).

### Per-step observer scoring (trajectory_signals.md)

```
SCORABLE_TYPES = {
  "genuine_human", "genuine_text",
  "agent_continuation",  # autonomous agent work counts
  "summary_diff",        # carries information agent reasons about
}
```

Why: we want to score every exchange where the agent is doing
substantive work. The observer scores each step independently with no
window averaging, so the zero-entropy concern doesn't apply. In
Gemini sessions, `agent_continuation` is 82% of exchanges — these
ARE the agent's autonomous reasoning and we want them scored.

### Failure keyword density

No filter. Runs over `records.chunk_text` which is harness-agnostic.
Harness-injected exchanges contribute essentially zero keyword density
naturally, so no pre-filter is needed.

### Future consumers

Track 2 memory induction (within-session candidate mint) reads
`critical_steps`, which are derived from `step_scores` — already
filtered by `SCORABLE_TYPES`. No additional filter needed.

The principle: **the classifier is one source of truth; how each
consumer uses the tag is a per-consumer choice.**

---

## Additional `TurnDescriptor` fields

The classifier work introduced two new fields:

```python
class TurnDescriptor(BaseModel):
    exchange_idx: int
    user_word_count: int
    tool_calls: list[dict]
    tool_errors: list[bool]
    model_turn_count: int
    exchange_type: str = "genuine_human"   # NEW — classifier output
    avg_think_tok_ratio: float = 0.0       # NEW — Gemini only; 0.0 for CC/Opencode
    evidence_text: str = ""                # NEW — verbatim tool outputs (per trajectory_signals.md)
```

`avg_think_tok_ratio` = mean of `(thoughts / max(output, 1))` across
all turns in the exchange. Only populated by `GeminiSource`; CC and
Opencode default to 0.0. Useful as an additional signal for Gemini
text-only sessions where structural features are scarce.

`evidence_text` = verbatim tool outputs for the exchange. Used by per-
step observer scoring as input. Specified in
[`trajectory_signals.md`](trajectory_signals.md).

---

## What the classifier doesn't solve

These are independent gaps documented for awareness:

1. **Gemini text-only research sessions** — failures appear as user-
   pasted error text. No structural signal. Per-step observer can read
   the user text directly; thought-subject keywords are also
   available for Gemini.
2. **Logic errors with no tool failure** (e.g., chess bughouse UI bug).
   No structural detection on any harness. Requires outcome
   verification, fundamentally different problem.
3. **`summary_diff` carries information** the agent uses for reasoning.
   It's filtered out of the inflection detector but kept for per-step
   scoring — different consumers, different decisions.

---

## Scalability

- O(N) string/key checks per session, computed in
  `_extract_turn_descriptor` — no extra pass.
- `exchange_type` persisted in the `turn_descriptors` JSON column on
  `sessions`; no re-parsing needed for downstream consumers.
- Filtering by `exchange_type` is a Python `in` check against a small
  set — trivial cost.

---

## References

- [`notes/deprecated_exchange_classifier_design.md`](../notes/deprecated_exchange_classifier_design.md) —
  predecessor doc; preserved for the inflection-detector-anchored
  framing it originally proposed.
- [`notes/inflection_detector_evaluation.md`](../notes/inflection_detector_evaluation.md) —
  empirical motivation (95% FP from harness injections).
- [`trajectory_signals.md`](trajectory_signals.md) —
  consumes `SCORABLE_TYPES`; the per-step observer scoring design.
- [`memory_evolution.md`](memory_evolution.md) —
  consumes `critical_steps` (already filtered upstream).
