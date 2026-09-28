# Phase 0 Findings: Inflection Context & Ground Truth

> Investigation date: 2026-06-05
> Method: re-parsed source JSONLs for top-5 inflections, examined exchange windows

---

## Summary

Re-parsing source files and slicing by `entry_turn` exchange index works cleanly.
The inflection context is readable and the ground truth (exchange `entry_turn + 1`)
is identifiable. However, the investigation also exposed three schema/persistence
gaps that need to be fixed before the export pipeline is built.

---

## Phase 0 Results: Does the approach work?

### Case 1 — `af9d50d7` | `agent_thrash` | entry=53 | strength=3.59

**Context (exchanges 51–53):**
- Exchange 51 (precipitating): user asks about enabling prompt optimization based on
  extraction outcomes — a design question, no tool calls
- Exchange 52: user gives a directive ("tackle verification mode first, then prompt
  optimization with proper plan"), agent reads and edits several files, runs pytest,
  calls Skill
- Exchange 53 (inflection): a skill injection message triggers the agent to do a
  planning pass — agent reads 6 files back-to-back (Glob + 5× Read), all read-only,
  no synthesis output

**Ground truth (exchange 54):** A context-continuation summary message — the session
ran out of context and was resumed. Agent immediately does Glob + Read + Write
(writes the plan doc it was supposed to produce). Resumes synthesis.

**Assessment:** The failure is clear (pure read loop, no output), but exchange 54 is
a context-continuation boundary, not a human correction. The "ground truth" here is
ambiguous — it's a system artifact, not a deliberate user intervention.

**Lesson:** Need to filter out inflections where `entry_turn + 1` is a
context-continuation message (`type=summary` or content starting with "This session
is being continued"). These are not corrections; they are session scaffolding.

---

### Case 2 — `0c81467c` | `agent_thrash` | entry=13 | strength=3.47

**Context (exchanges 9–13):**
- Exchange 9 (precipitating): user types "A or B" — terse, no tool calls triggered
- Exchanges 10–11: short confirmations ("A, B, or C", "Yes"), task updates
- Exchange 12: user asks a substantive design question about the data structure and
  analytics storage shape
- Exchange 13 (inflection): user asks a pointed design question ("why are raw_facets
  never consumed by analytics?") — agent reads and edits the exploration.md doc,
  minimal tool use

**Ground truth (exchange 14):** User sends a URL to the MemWire repo with a
description. Agent does 8× WebFetch calls — a deliberate research pivot.

**Assessment:** Clean case. The failure (agent thrash on design questions with only
doc edits) and the correction (user redirecting to external research) are both legible.
The ground truth is a genuine user intervention, not a system artifact.
Generalizable: "agent was stuck in documentation-only mode; user provided an external
reference to break the pattern."

---

### Case 3 — `af5ae23` | `loop_entry` | entry=24 | strength=2.90

**Context (exchanges 22–24):**
- Exchange 22 (precipitating): user corrects a misunderstanding ("sorry I meant
  summary_fresh from current exp results") — agent reads the results file
- Exchange 23: user asks two follow-up questions about NER on summaries and chunk
  overlap — agent runs bash queries, no synthesis
- Exchange 24 (inflection): user says "let's first try prompt-tuning for chunk
  summary, what's the current prompt?" — agent produces NO tool calls at all
  (pure text response, action_entropy drops to zero)

**Ground truth (exchange 25):** User immediately follows up with a new prompt:
"what would you prompt if we're aiming for a combined prompt?" — the conversation
shifts from analysis to concrete proposal.

**Assessment:** The inflection is real but subtle — the agent went into a
discussion-only mode (no tool calls, zero action entropy). The "correction" is the
user pushing forward with a more specific question. This is a valid benchmark case:
the model should have proposed something concrete at exchange 24 rather than asking
clarifying questions. The ground truth (exchange 25's user message) shows what
direction was actually wanted.

---

## What works

1. **Re-parsing source files by `entry_turn` is clean.** `_build_exchanges` is
   deterministic — the same exchange index always maps to the same conversation
   segment. No ambiguity.

2. **The window `[precip_turn - 1 : entry_turn + 1]` is readable context.** 4–6
   exchanges is enough to see the failure build up.

3. **Exchange `entry_turn + 1` is usually identifiable as a correction** — either
   user redirects, provides new information, or changes the task framing.

4. **Source files are all on disk** (confirmed via `ingestion_state.abs_path`).
   Re-parsing is feasible at export time.

---

## Gaps & Issues to Fix in Ingestion

### GAP-1: `span_start` / `span_end` on records ✅ FIXED

**Schema:** `records` table has `span_start` and `span_end` columns.
**Status:** Fixed in all three plugins (`claude_code`, `gemini_cli`, `opencode`).
Verified 2026-06-07: 488/488 CC chunks, 94/94 Gemini chunks populated.
Opencode has 9 legacy chunks from before the fix (1-chunk stub sessions).
**Impact:** Exchange index → chunk index mapping is now available without re-parsing
for all sessions ingested after the fix.

### GAP-2: `turn_descriptors` are computed but not persisted

**Code:** `_extract_turn_descriptor` runs on every exchange and builds a list of
`TurnDescriptor` objects. They are passed through `parsed_session.metadata` to
`IngestionPipeline._ingest_session` where inflection detection consumes them.
After detection, they are discarded — not written anywhere in the DB.
**Impact:** At export time there is no way to look up what tool calls occurred at
exchange N, what the error rate was, or which exchanges contributed to the inflection
signal. The only way to get this is to re-parse.
**Fix:** Persist `turn_descriptors` — either as a JSON column on `sessions`
(simplest), or as a separate `session_turns` table keyed by `(session_id, exchange_idx)`
(more queryable). The JSON column approach is lower friction and sufficient for
export. Suggested column: `sessions.turn_descriptors TEXT` (JSON array).

### GAP-3: `sessions.metadata` and `sessions.raw_facets` are always empty `{}`

**Code:** The plugin populates `parsed_session.metadata` with a rich dict (session_id,
cwd, git_branch, turn_descriptors, entity_mentions, away_summary_latest, etc.).
But `Session.raw_facets` is never assigned from this — the ingestion pipeline creates
`Session` objects without forwarding the plugin metadata to `raw_facets`.
**Impact:** Nothing plugin-specific is queryable from the DB without re-parsing.
The `metadata` column exists but is useless.
**Fix:** At session creation time in `IngestionPipeline`, assign relevant plugin
metadata fields to `raw_facets` (or a dedicated column). At minimum: `cwd`,
`git_branch`, `cc_version`, `away_summary_latest`. The `turn_descriptors` fix from
GAP-2 should go here or to its own column.

### GAP-4: Context-continuation messages look like user corrections

**Pattern:** When a Claude Code session runs out of context and is resumed, the
injected continuation summary appears as a `user` message at `entry_turn + 1`.
It begins with "This session is being continued from a previous conversation..."
**Impact:** For inflections near a context boundary, the "correction" ground truth
is actually a system-injected summary, not a human response. Using it as a label
produces a nonsensical benchmark task.
**Fix (at export time, not ingestion):** In the export pipeline, detect and skip
any inflection where `exchanges[entry_turn + 1]` matches the continuation pattern.
Detection: check if the user message content starts with "This session is being
continued" or contains `type=summary`. This is a filter, not a schema fix.

---

## Decision: Option B confirmed

Re-parsing source files at export time (Option B from the design doc) is the correct
approach for ground truth extraction. It is:
- Feasible: source files are on disk, paths stored in `ingestion_state.abs_path`
- Accurate: exchange-level precision, no chunk boundary ambiguity
- Sufficient: `_build_exchanges` is fast and deterministic

GAP-1 and GAP-2 should be fixed in ingestion so that future export runs can avoid
re-parsing — but they do not block the initial prototype.
