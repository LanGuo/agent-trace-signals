# [DEPRECATED] Exchange Classifier Design (inflection-detector-anchored)

> **DEPRECATED 2026-06-09.** Superseded by
> [`exchange_classifier.md`](exchange_classifier.md), which makes the
> classifier consumer-agnostic (multiple downstream consumers each pick
> their own filter set: inflection detector, per-step observer
> scoring, etc.). This older doc anchors the classifier to the
> inflection detector specifically — the empirical taxonomy is
> preserved in the new doc; this is kept for historical reference.

---

> Exploration of harness-injected exchange patterns across CC, Gemini, Opencode.
> Sessions examined: CC c30450db, Gemini symphony, Opencode 517fcbdd.
> Date: 2026-06-07

---

## Findings per harness

### Claude Code — c30450db (169 exchanges)

| Type | Count | % | Detection |
|------|-------|---|-----------|
| genuine_human | 67 | 40% | has user text + agent tool calls |
| genuine_text | 80 | 47% | has user text, no agent tool calls |
| skill_invocation | 14 | 8% | user text starts with "Base directory for this skill:" |
| context_continuation | 8 | 5% | user text starts with "This session is being continued" |

No task_notification or wakeup_injection in this session. The SLR-gemma4 session
(7610ea24) had task_notifications (turns 148–152) and a different session had
wakeup_injections — so all four CC patterns occur in the corpus, just not all
in one session.

**Full CC injection vocabulary:**
1. `skill_invocation` — `user.startswith("Base directory for this skill:")`
2. `task_notification` — `"<task-notification"` in user text
3. `context_continuation` — `user.startswith("This session is being continued")`
4. `wakeup_injection` — `ScheduleWakeup` in tool_calls OR `scheduledFor` in toolUseResult

All four are deterministic string/key checks. Zero LLM cost.

---

### Gemini — symphony session (144 exchanges)

| Type | Count | % | Detection |
|------|-------|---|-----------|
| genuine_text (real) | 26 | 18% | non-empty user content |
| agent_continuation | 118 | 82% | **empty user content** |

**Gemini's structural difference:** `_build_exchanges` groups by user message, but
Gemini sessions produce many consecutive agent turns with no user prompt between them.
These become exchanges with an empty `user.content` field. This is not a harness
injection — it's Gemini's multi-turn execution structure. The classifier needs to
distinguish `agent_continuation` (empty user, agent produced output) from
`genuine_text` (real user message, no tool calls).

**No skill/task/wakeup injections detected** in this session. Gemini CLI doesn't
use the Claude Code skill/task harness infrastructure.

**Critical finding — Gemini failures are in user-pasted text, not tool errors:**
The symphony session's problems (user recalls it as "quite problematic") are entirely
invisible to the structural detector. The failures appear as error output pasted by
the user into their messages (e.g. `bsrc` command errors at exchanges 54, 58, 65, 75).
The agent produced no tool calls throughout — it's a Q&A + document-writing session.
**No tool error rate to detect. No recurrence to detect. The failure signal is in the
user message text, not the tool call sequence.**

This confirms a hard limit of the structural detector: Gemini research sessions
where the agent only produces text responses are completely opaque to the current
features. A semantic layer reading user message content would be required.

**Gemini tool-use sessions (chess) — detectable, with three gaps to fix:**

The chess session (33 exchanges) shows Gemini *can* produce meaningful structural
signals. Exchange 1: 27 agent turns, 27 tool calls. Exchange 14: 26 turns, 25 tool
calls, 8768 thought tokens. `action_target_recurrence` and `action_entropy` are
computable. `GeminiSource._extract_turn_descriptor` already reads `toolCalls`
correctly.

**Gap 1 — `_READ_ONLY_TOOLS` is mostly CC-centric but mostly OK for Gemini.**
`read_file` and `list_directory` are already in the set. `run_shell_command` is
ambiguous (read and write depending on the command) — not easily classifiable.
Minor issue; no fix needed now.

**Gap 2 — Error detection misses logic errors.** The chess bughouse failure was a
UI rendering bug — no tool call failed, no error keyword in output. Tool-level error
detection on any harness cannot catch logic errors that produce wrong behavior without
a failing tool call. This is a hard limit, not a Gemini-specific gap.

**Gap 3 — `model_turn_count` scale is incompatible with the CC-calibrated detector.**
CC sessions: 1–3 model turns per exchange. Gemini SDE sessions: 1–27. The
`agent_thrash` classifier gates on `model_turn_count > 2.0` — this condition is
almost always true for Gemini SDE sessions, making it meaningless as a discriminator.
Fix: `model_turn_count` z-score already normalises within a session, so the feature
itself is fine. But the absolute-value threshold in `_classify()` (`> 2.0`) needs
to be replaced with a session-relative condition — only flag when `model_turn_count`
z-score is anomalous AND another corroborating feature is also anomalous.

**Gemini-specific signal: `think_tok_ratio`**

Gemini turns include a `tokens.thoughts` field (thought tokens per turn). This is
unique to Gemini and other thinking models. It provides a signal unavailable in CC
or Opencode:

- **Normal pattern:** thought tokens proportional to output tokens — the agent thinks
  to produce output
- **Thrash signal:** thought tokens growing without proportional output tokens, across
  consecutive turns in the same exchange — the agent is deliberating without resolving

`think_tok_ratio = thoughts / max(output, 1)` per turn. High and rising across
turns within an exchange → reasoning-without-resolution. This is particularly
valuable for detecting stalls in text-only Gemini sessions where no other structural
feature is available.

**Implementation:** add `avg_think_tok_ratio: float = 0.0` to `TurnDescriptor`.
Computed in `GeminiSource._extract_turn_descriptor` from `turn.tokens.thoughts` and
`turn.tokens.output`. For CC and Opencode, defaults to 0.0 (not populated).
The inflection detector can then include it as a sixth feature for Gemini sessions,
gated on `avg_think_tok_ratio > 0` (i.e. only active when data is present).

**Text-only Gemini sessions — one weak structural signal:**

`user_turn_length_ratio` could catch "user pasted error output" cases. In the
symphony session, exchanges where the user pasted terminal errors (54, 58, 65, 75)
have much longer user messages than the surrounding Q&A exchanges. This would push
`user_turn_length_ratio` upward, potentially triggering `user_correction`.
However:
- `user_correction` currently fires on *short* user messages (ratio < 0.5), not long ones
- Error pastes produce *long* messages (ratio > 2.0)
- A new `user_error_paste` type gated on `user_turn_length_ratio > 2.0` AND user
  message containing error keywords would catch this pattern

This is a weak, session-type-dependent signal. Not worth implementing until the
classifier and core detector gaps are fixed.

---

### Opencode — 517fcbdd (71 exchanges)

| Type | Count | % | Detection |
|------|-------|---|-----------|
| genuine_human | 49 | 69% | has user text parts + tool calls |
| genuine_text | 19 | 27% | has user text, no tool calls |
| summary_diff | 3 | 4% | user message has `summary.diffs` but no text parts |

**`summary_diff`** is Opencode-specific: the harness injects a message containing
only a diff payload (changed files) to keep the agent context-aware after code
changes. No user text, no tool calls. Detection: `user.data.summary.diffs` exists
AND no text parts.

**Opencode has real tool errors:** 33/71 exchanges (46%) have at least one tool
error; 11/71 (15%) have >50% error rate. These are genuine failures — `bash` and
`edit` operations returning errors. Structural error detection is meaningful here
because Opencode is doing actual SDE work (setting up the ATS codebase, running
ingestion). This is the session type the detector was implicitly designed for.

---

## Summary: what's universal vs. harness-specific

| Pattern | CC | Gemini | Opencode | Detector effect |
|---------|----|--------|----------|-----------------|
| skill_invocation | ✓ | ✗ | ✗ | agent_thrash, loop_entry |
| task_notification | ✓ | ✗ | ✗ | loop_entry cluster |
| context_continuation | ✓ | ✗ | ✗ | agent_thrash |
| wakeup_injection | ✓ | ✗ | ✗ | escalation |
| agent_continuation | ✗ | ✓ | ✗ | loop_entry (entropy=0) |
| summary_diff | ✗ | ✗ | ✓ | loop_entry (empty tools) |
| genuine tool errors | rare | none | common (46%) | escalation ← valid |

---

## Classifier design

### Interface

Two new fields on `TurnDescriptor`. Both computed in each plugin's
`_extract_turn_descriptor()`, no extra pass needed.

```python
class TurnDescriptor(BaseModel):
    exchange_idx: int
    user_word_count: int
    tool_calls: list[dict]
    tool_errors: list[bool]
    model_turn_count: int
    exchange_type: str = "genuine_human"    # NEW — classifier output
    avg_think_tok_ratio: float = 0.0        # NEW — Gemini only; 0.0 for CC/Opencode
```

`avg_think_tok_ratio` = mean of `(thoughts / max(output, 1))` across all turns in
the exchange. Only populated by `GeminiSource`; CC and Opencode default to 0.0.
The inflection detector uses it only when `> 0` (presence check), so it has no
effect on CC/Opencode sessions.

### Per-plugin classification

Each plugin overrides `_classify_exchange(exchange) -> str` in its own
`_extract_turn_descriptor`. Base class provides the `genuine_human` / `genuine_text`
fallback.

**CC (`claude_code.py`):**
```python
def _classify_exchange(exchange):
    ut = user_text(exchange)
    if ut.startswith("Base directory for this skill:"): return "skill_invocation"
    if "<task-notification" in ut:                      return "task_notification"
    if ut.startswith("This session is being continued"): return "context_continuation"
    if any ScheduleWakeup in tool_calls or toolUseResult: return "wakeup_injection"
    if not tool_calls(exchange):                        return "genuine_text"
    return "genuine_human"
```

**Gemini (`gemini_cli.py`):**
```python
def _classify_exchange(exchange):
    ut = user_text(exchange)
    if not ut.strip(): return "agent_continuation"
    if not tool_calls(exchange): return "genuine_text"
    return "genuine_human"
```

**Opencode (`opencode.py`):**
```python
def _classify_exchange(exchange):
    user = exchange["user"]
    has_text_parts = any(p.get("type") == "text" for p in user.get("parts", []))
    has_summary_diff = "diffs" in user.get("data", {}).get("summary", {})
    if has_summary_diff and not has_text_parts: return "summary_diff"
    if not tool_calls(exchange): return "genuine_text"
    return "genuine_human"
```

### Inflection detector filter

After classification, filter TurnDescriptors before passing to the detector:

```python
GENUINE_TYPES = {"genuine_human", "genuine_text"}
genuine_tds = [td for td in turn_descriptors if td.exchange_type in GENUINE_TYPES]
inflections = InflectionDetector().detect(genuine_tds, session_id)
```

Note: `genuine_text` (no tool calls) still enters the detector because text-only
responses can signal stalls. But the entropy=0 false positive is fixed because
consecutive `agent_continuation` or `task_notification` exchanges are filtered,
so the window no longer fills with zero-tool exchanges from harness injections.

### Additional detector fix: remove absolute `model_turn_count` threshold

`_classify()` in `inflection_detector.py` gates `agent_thrash` on:
```python
f.get("model_turn_count", 1) > 2.0
```
This absolute threshold is meaningless for Gemini SDE sessions where 10–27 model
turns per exchange is normal. Replace with: require the `model_turn_count` z-score
itself to exceed the threshold (i.e. it's already anomalous relative to the session
baseline) in addition to `read_only_ratio` being dominant. The z-score normalisation
already handles cross-session scale differences — the absolute check is redundant
and harmful for Gemini.

### What the classifier + detector changes don't fix

1. **Gemini research sessions** (text-only): failures in user-pasted error text are
   invisible to structural features. Requires semantic scanning of user message
   content or the `think_tok_ratio` feature (if the agent was deliberating on the
   problem). Known gap — not blocked on classifier work.

2. **Logic errors with no tool failure** (e.g. chess bughouse UI bug): undetectable
   from tool call sequences on any harness. Requires outcome verification (does the
   output do what was asked?) — a fundamentally different problem.

3. **Opencode `summary_diff` filter may hide context:** `summary_diff` exchanges
   carry file diff context the agent uses for reasoning. Excluding them from
   inflection detection is correct, but their tool call patterns (none) shouldn't
   contaminate the z-score baseline. Confirm the filter handles this correctly.

---

## Keyword and Embedding Signals for Anomaly Detection

The structural z-score features (entropy, recurrence, error_rate) are not the only
available signal. Three complementary sources exist across the keyword and embedding
spaces. Empirically validated on the chess (Gemini, genuine failure) and symphony
(Gemini, text-only) sessions.

---

### Signal 1 — Failure keyword density in chunk_text (available now)

**What:** count of failure-signal words per chunk, z-scored within the session.

**Validated on chess session:**
- Baseline (chunks 0–8, normal development): avg density ~1
- Failure period (chunks 9–19, bughouse bug): rising to peak 22 at chunk 16
- Structural inflection fires at entry_turn=22 ≈ chunk 13
- Keyword signal leads by ~4 chunks — **leading indicator, not lagging**

**Cross-session profile (all sessions with >5 chunks):**
Most sessions sit at avg 0.7–1.8, peaks 4–11. Chess session peak of 22 is 3–4×
any other session — a strong outlier even before z-scoring.

**Keyword families:**

User-signal words (frustration / repetition):
```
"not working", "still not", "still", "broken", "cannot", "again",
"that's wrong", "you're not", "that's not what"
```

Agent-signal words (self-correction / confusion):
```
"I apologize", "unexpected", "let me try", "different approach",
"I was wrong", "I notice I've been", "let me reconsider", "I'm not sure",
"this is strange"
```

**Implementation:** per-chunk keyword count stored as `failure_keyword_density` on
`records`. No LLM call. FTS5 index on `records_fts` already built — can be computed
as a post-ingest SQL scan. Z-scored within session at detection time.

**Coverage:** all three harnesses — chunk_text contains user messages and agent
text for CC/Opencode; Gemini includes both user messages and agent reasoning text.

---

### Signal 2 — Gemini thought block subjects (available now, Gemini only)

**What:** Gemini turns expose chain-of-thought reasoning as structured `thoughts`
blocks. JSONL format: string. JSON format: list of `{subject, description}` dicts.
The `subject` field is a semantic label of the agent's current state, already
written by the model — no embedding or LLM extraction needed.

**Examples from symphony session:**
```
"I've encountered a misstep in my previous approach"
"Exploring Configuration Locations" (neutral)
"Reconsidering Repository Strategy" (pivot signal)
```

**Signal:** keyword match on `subject` field:
```
"misstep", "incorrect", "reconsider", "reconsidering", "issue",
"problem", "error", "failed", "backtrack", "I was wrong"
```

A thought subject containing any of these is explicit agent failure acknowledgment —
the model is labeling its own reasoning state as problematic.

**Why this matters for text-only Gemini sessions:** this is the only structural
signal available when the agent produces no tool calls. The symphony session's
failures (bsrc command errors pasted by user) would be catchable if the agent's
thoughts reflected confusion or backtracking about those errors.

**Implementation:** extract thought subjects in `GeminiSource._extract_turn_descriptor`.
Store as `thought_failure_signal: int` (count of failure-subject turns in exchange)
on `TurnDescriptor`. Alongside `avg_think_tok_ratio`.

**Coverage:** Gemini only. CC and Opencode default to 0.

---

### Signal 3 — Adjacent chunk cosine similarity (needs `ats embed` first)

**What:** cosine similarity between consecutive chunk embeddings within a session.
High similarity (>0.85) = semantically repeating content — same tools, same targets,
same agent reasoning. Embedding-space analog of `action_target_recurrence` but
sensitive to meaning rather than exact tool identity.

**Why complementary:** catches soft loops where the agent rephrases the same
approach rather than literally repeating the same tool+target pair. Also catches
user asking the same question in different words.

**Status:** not yet computable — `record_embeddings` has 0 rows. Requires
`uv run ats embed` to run first. Once embeddings exist, this is a single ordered
scan over chunk pairs per session: no new model calls, just vector math on existing
embeddings.

**Implementation:** `ats analytics light` (or a new `ats analytics signals` step)
computes pairwise adjacent similarity and stores `chunk_repeat_signal: float`
on records. Or computed on-the-fly at detection time from the embedding store.

**Coverage:** all harnesses — embeddings are harness-agnostic.

---

### Combined signal coverage

| Failure type | Structural | Keyword density | Thought subjects | Chunk similarity |
|---|---|---|---|---|
| Tool errors (Opencode SDE) | ✓ escalation | ✓ moderate | — | ✓ if looping |
| Repeated UI bug (chess) | ✗ missed | ✓ **leading** | — (JSONL format) | ✓ if content repeats |
| Text-only confusion (symphony) | ✗ blind | ✓ user pastes | ✓ **primary signal** | ✓ if Q&A loops |
| Skill/wakeup injections (CC) | ✗ false positive | low baseline | — | ✓ would show repetition |

Keyword density is the highest-value near-term addition: works across all harnesses,
already indexed, leading indicator, and requires no new infrastructure. Thought
subjects are the key unlock for Gemini text-only sessions. Chunk similarity is
best-in-class for loop detection but depends on embeddings being computed.

---

### Track 2 input

These signals feed Track 2 in two ways:

1. **Failure candidate selection:** instead of using `session_inflections` (structural
   detector output, currently ~95% false positive rate), use keyword density z-score
   spikes and/or thought failure subjects as the candidate selection criterion for
   the Track 2 extraction window. Lower false positive rate, earlier detection.

2. **Inflection context enrichment:** the Track 2 inflection context tuple
   `(precipitating_action, user_signal, agent_reasoning, interaction_type)` already
   includes `user_signal` and `agent_reasoning`. Keyword density and thought subjects
   provide structured pre-computed versions of these fields, reducing what the Track 2
   LLM extraction prompt needs to infer from raw text.

---

## Scalability

- O(N) string checks per session at ingestion time
- Runs inside `_extract_turn_descriptor` — no extra loop
- `exchange_type` stored in `turn_descriptors` JSON column (GAP-2 fix) → no
  re-parsing needed for future detector runs
- Gemini `agent_continuation` filtering also reduces the effective turn count
  fed to the detector (144 → 26 in the symphony session), cutting z-score noise
  from empty windows
