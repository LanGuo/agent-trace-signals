# Inflection Detector Evaluation

> Based on manual labeling of 19 candidates (labeled_failures.json).
> Date: 2026-06-06

---

## Result: 1 true positive out of 19 labeled

Only the chess bughouse escalation (session `682b4d8d`, Gemini CLI) was a genuine
agent failure. All others were false positives.

---

## False positive categories

### 1. Skill invocations (subagent-driven-development, brainstorming, writing-plans)
Triggers: `agent_thrash`
Why: Skills always begin with a Glob + many sequential Read calls. This pattern is
structurally identical to thrash but is expected and intentional behavior.
Fix needed: exclude exchanges where the user message contains a skill injection header
(starts with "Base directory for this skill:" or similar harness-injected prefix).

### 2. ScheduleWakeup monitoring loops
Triggers: `escalation`, `loop_entry`
Why: Repeated SSH polling commands return exit code 2 (SSH forwarding non-zero remote
exit) even when the remote job is running fine. Error rate spikes on legitimate polls.
Fix needed: already flagged in viewer; should be excluded from inflection detection
entirely (exchange is harness-injected, not human-driven).

### 3. Context-continuation boundaries
Triggers: `agent_thrash`
Why: When a session resumes after context compaction, the agent reads many files to
rebuild context. Same structural signature as thrash.
Fix needed: exclude exchanges starting with "This session is being continued…"

### 4. Confirmatory user turns ("Go with your recommendation", "1-3 yes, no 4")
Triggers: `strategy_pivot`, `loop_entry`
Why:
- Short confirmatory turns → low `user_turn_length_ratio` → fires `user_correction`
- Agent responding to confirmation with diversified tool use → entropy spike →
  fires `strategy_pivot`
- Agent responding with focused reads → entropy drop → fires `loop_entry`
The signal is in the *agent's response pattern*, which varies legitimately based on
what the user asked for. No anomaly.
Fix needed: `strategy_pivot` and `user_correction` types are too noisy without
semantic grounding. Should be disabled or gated on additional criteria.

### 5. Normal design/discussion exchanges with few tool calls
Triggers: `loop_entry` (via action_entropy drop to near-zero)
Why: An exchange where the agent responds with a long text answer and no tool calls
has entropy = 0, which is anomalous relative to a session full of tool calls. But
a text-only response to a design question is correct behavior.
Fix needed: gate `loop_entry` on a minimum tool call count in the window; entropy
of an empty tool list is meaningless.

---

## What the z-score features actually capture

All six features are **within-session relative** (z-score against running mean).
This means they detect deviation from *this session's own baseline*, not from any
absolute definition of failure. Problems:

- A session that does mostly reads has a low entropy baseline → a brief writing burst
  fires `strategy_pivot`
- A session with many skill invocations (all read-heavy) has a high `read_only_ratio`
  baseline → a non-skill read pass doesn't deviate → detector misses it AND other
  sessions without skills fire `agent_thrash` on the same pattern
- The 5-turn sliding window conflates causes and effects: the user's confirmatory
  turn and the agent's response are in the same window

---

## What holds up

`escalation` (error_rate > 50%) is the most structurally reliable type. But:
- Only 2 instances in the current corpus (limited by corpus size, not detector quality)
- The EC2 monitoring case shows it still fires on legitimate polling failures

Real errors (tool failures that actually block progress) need to be distinguished
from transient errors (exit codes from monitoring commands, network retries).
A threshold on *consecutive* error exchanges would be more precise than a
window-level rate.

---

## Comparison: bypassPermissions (SDE-style) sessions

Queried 3 substantive `bypassPermissions` sessions from traces.db.

**Sessions `0c81467c` and `c30450db`: 0 inflections each.**
Both have >67 user turns despite being bypassPermissions — they're actually
interactive sessions (design discussions, Q&A) despite the permission mode.
The detector simply found nothing to flag.

**Session `7610ea24` (SLR-gemma4, 251 turns, slr-gemma4 project): 19 inflections.**
This is the most autonomous session in the corpus. Results:

| Cluster | Turns | Type | Actual cause |
|---------|-------|------|-------------|
| 5, 9 | agent_thrash | read_only_ratio | Normal read pass |
| 10 | loop_entry | entropy=0 | Skill invocation response (no tools) |
| 24, 26 | loop_entry | recurrence=1.0 | "accept" / "1" → Skill invocations. TaskUpdate repeated same targets |
| 53, 54 | agent_thrash | read_only_ratio | Post-skill read pass |
| 136, 137 | agent_thrash | read_only_ratio | Post-skill read pass (high recurrence) |
| 148–152 | loop_entry | entropy=0 | **5 consecutive `<task-notification>` harness messages** — agent produced no tool calls in response to each |
| 244–248 | loop_entry | entropy=0 | Same: consecutive harness notifications with no agent tool output |

**Conclusion: the SDE-style session fares no better.** All 19 inflections are false
positives driven by the same two root causes:
1. Skill invocations and their read-heavy follow-up passes
2. Harness-injected messages (`<task-notification>`, `<task-id>`) producing text-only
   agent responses → entropy drops to 0 → sustained `loop_entry` cluster

The detector is not producing useful signal regardless of session type. The problem
is not "research vs SDE" — it is that the detector has no concept of harness-injected
exchanges (skill injections, task notifications, ScheduleWakeup, context continuation)
which make up a significant fraction of turns in Claude Code sessions and produce
systematic false positives across all session types.

**Gathering SDE traces from external sources** (SWE-bench trajectories, etc.) is still
worth exploring as a comparison, but the internal evidence already shows the exclusion
rules are the prerequisite — without them, external SDE traces would produce the same
false-positive clusters on skill invocations and harness messages.

---

## Research context

Recent work on agent trajectory failure detection (all 2025-2026):

**[TrajAD](https://arxiv.org/abs/2602.06443)** (Feb 2026)
Key finding: general-purpose LLMs with zero-shot prompting can't reliably detect
trajectory anomalies. Structural features alone are insufficient — logical
dependencies between steps matter. Proposes training a specialized verifier on
synthetically perturbed trajectories.

**[TRAIL](https://arxiv.org/pdf/2505.08638)** — Trace Reasoning and Agentic Issue
Localization. Focuses on localizing *where* in a trace a failure occurred.

**[Trajectory Guard](https://arxiv.org/pdf/2601.00516)** (Jan 2026) — lightweight
sequence-aware model for real-time anomaly detection. Uses tool-call sequences +
semantic content. Key insight: sequence matters more than individual turn features.

**[Agents Failure Attribution](https://github.com/ag2ai/Agents_Failure_Attribution)**
(ICML 2025 Spotlight) — benchmark for automated failure attribution in multi-agent
systems. Most relevant to Direction 1 (failure classifier).

**[Detecting Silent Failures in Multi-Agentic Trajectories](https://arxiv.org/pdf/2511.04032)**
— specifically addresses failures that don't surface as errors (no exit code, no
obvious structural anomaly). Relevant to the "stuck in design discussion" pattern
where no tool errors occur but the agent is not making progress.

---

## Implications for detector redesign

### Short-term: exclusion rules (filter before detection)
These can be implemented without changing the detector logic:
1. Skip exchanges where user message matches skill injection header pattern
2. Skip exchanges where any turn contains a ScheduleWakeup tool use or result
3. Skip exchanges where user message matches context-continuation pattern
4. Disable `strategy_pivot` and `user_correction` types (too noisy)
5. Gate `loop_entry` (entropy drop) on minimum window tool call count ≥ 3

### Medium-term: semantic layer
The structural features need a semantic pre-filter: classify each exchange as
one of {skill_invocation, monitoring_poll, design_discussion, implementation,
debugging} before computing z-scores. Anomaly thresholds would then be
type-conditional. This is the approach Trajectory Guard takes.

### Longer-term: trained verifier
Per TrajAD's finding, a specialized verifier trained on labeled failure/non-failure
trajectories substantially outperforms heuristic detection. The labeling pipeline
(label-failures command) is the data collection mechanism for this.
The current corpus is too small (1 confirmed positive out of 19) to train on;
need 50+ confirmed positives across diverse failure types.

---

## Sessions to examine for genuine failures

**Symphony setup — Gemini CLI** (`session-2026-05-30T06-05-9599c04e.jsonl`)
NOT yet ingested into traces.db. File exists at:
`data/archive/gemini_cli/session-2026-05-30T06-05-9599c04e.jsonl`
User recalls this as a problematic session — good candidate for manual annotation
as a seed for the labeled dataset.

**Chess app — Gemini CLI** (`session-2026-03-05T03-28-0134aafc.json`)
Ingested (session `682b4d8d`). The bughouse case (entry_turn=22, escalation) is the
one confirmed true positive so far. Worth examining more of this session — the user
recalls it as broadly problematic, so there may be additional genuine failures at
other turns.
