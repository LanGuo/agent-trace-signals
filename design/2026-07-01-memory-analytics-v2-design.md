# Memory Analytics Layer v2 — Design Spec

> **Date:** 2026-07-01  
> **Status:** Draft — prompt-fix experiments now complete, see update below  
> **Supersedes:** `design/exploration.md` (architecture sections), `design/trajectory_signals.md` (Track 2 sections)  
> **Companion docs:** `design/design_decisions.md` (history), `design/deprecated_memory_evolution.md` (memory lifecycle, deprecated)
>
> **Update 2026-07-22**: the prompt-fix experiments this doc was waiting on are done — see
> `design/design_decisions.md`'s 2026-07-15/16 entry (four production extraction bugs fixed),
> 2026-07-20/21 entries (audits of extraction quality vs. the design log, with a temporal-validity
> correction), and 2026-07-21/22 entry (experimental taxonomy v2 redesign, not yet merged to
> production). This doc's own proposals have not been reconciled against those findings yet — still
> a draft, not promoted to accepted.

---

## Primary Use Case

**"Chat with all your agent traces"** — query the full history of your coding agent sessions in natural language.

Example queries:
- *"What was I working on when I got stuck on the auth bug?"*
- *"What patterns show up in how I approach new codebases?"*
- *"When did I last work with async DB migrations, and what came up?"*
- *"What decisions have I made about database schema design?"*

**Secondary use case:** Shared memory across coding agents (Claude Code, Gemini CLI, opencode, Codex). Same pipeline, pluggable source parsers.

---

## Architecture

```
Ingestion (per chunk, runs at ingest time):
  chunk_text → D-prompt → {summary, entities[], memories[], patterns[]}
  Each memory + pattern row stored with embedding

Analytics (offline, per session batch):
  1. Session structural embedding (raw head+tail) → HDBSCAN cluster → session_type tag
  2. Cluster all memory+pattern rows by embedding similarity across sessions
  3. Clusters with recurrence ≥ N → LLM consolidation → higher-order memory minted

Retrieval (at query time):
  Query → semantic similarity over memories + patterns rows
  + optional filter: session_type matches current session's structural cluster
```

---

## Ingestion Pipeline

### D-prompt extraction (per chunk, ~800 tokens)

One LLM call per chunk produces four fields:

**`summary`** — 3–5 sentences: what was being worked on, what problem was identified, what decision or action resulted and why.

**`entities[]`** — named things worth remembering across sessions. Each has `name`, `type` (technology / concept / model / dataset / org / person / tool / file / commit / pr), and `role` (one-phrase significance in this chunk). Entity resolution and deduplication continues via `entity_resolver.py`.

**`memories[]`** — durable facts, typed:
- `episodic` — specific event with a clear outcome or resolution
- `procedural` — reusable how-to with enough detail to act on
- `semantic` — stable fact about a named system or concept

**`patterns[]`** — agent reasoning and strategy observations, typed:
- `strategy` — HOW the agent approached the problem (meta-level mindset, not steps). Distinct from procedural memories which capture object-level instructions.
- `decision` — a notable decision point: what was decided, why, and whether it was well-supported or premature. Must be abstracted away from session-specific names.
- `recovery` — only when agent demonstrably got stuck (repeated failures, explicit errors, backtracking) and then found a solution: stuck state → what was tried → what worked.
- `inefficiency` — only when a shorter/faster path existed: what was done, what would have been better.

**Extraction rules for `patterns[]`:**
- Max 2 per chunk (quality over quantity)
- Skip if nothing notable is observable — empty array is correct and expected
- Must be non-ephemeral: abstract away session-specific names, file paths, model names
- Skip if a pattern substantially restates a procedural memory already in `memories[]`

### Storage

`memories` and `patterns` share the same `memories` table, distinguished by `memory_type`:
- Existing types: `episodic`, `procedural`, `semantic`
- New types: `pattern_strategy`, `pattern_decision`, `pattern_recovery`, `pattern_inefficiency`
- All rows get embeddings stored in `memory_embeddings` vec table

### Session structural embedding

In addition to the existing content embedding (from `session_summary`), each session stores a **structural embedding** from raw head+tail text (first 4k + last 2k chars of the session). This captures harness structure (tool density, human_tool_ratio, autonomy level) rather than semantic topic.

Stored in a new `session_structural_embeddings` vec table. Used for session type clustering (see Analytics), not retrieval.

---

## Analytics Pipeline (replaces `light_analytics.py`)

### Phase 1: Session type tagging (low risk, ship first)

1. Compute structural embedding for each session lacking one
2. HDBSCAN cluster over all structural embeddings
3. Write cluster label as `session_type` tag on `sessions` row
4. Session types roughly correspond to: discussion-heavy, tool-heavy-coding, mixed, short/sparse

This immediately enables routing — e.g., suppress `pattern_strategy` extraction for discussion sessions where no agent reasoning is observable.

### Phase 2: Cross-session memory consolidation (replaces entity-frequency promotion)

Runs as an offline batch job after Phase 1:

1. Embed all `memories` and `patterns` rows lacking embeddings
2. HDBSCAN cluster over memory+pattern embeddings across sessions
3. For each cluster with recurrence ≥ N sessions (N=3 default): call LLM to consolidate cluster members into a single higher-order memory
4. Write consolidated memory to `memories` with `extraction_method='cluster'`, linked to source rows via `memory_sources`

**Why this replaces entity-frequency promotion:**
- Entity anchoring was the gate; embedding recurrence is the new gate
- Two memories about semantically equivalent things cluster correctly regardless of name variation
- Patterns (which have no entity anchor) participate equally in clustering

---

## Retrieval

Unchanged mechanism, broader scope:

- Semantic similarity search over all `memories` rows (including patterns)
- Optional filter: `session_type` cluster matches current session's structural cluster (finds patterns from structurally similar sessions)
- BM25 + semantic hybrid search via existing RRF scoring
- Graph walk via `session_graph_edges` (same-entity edges) still available

---

## Component Verdicts

| Component | Verdict | Change |
|---|---|---|
| `ingestion.py` | MODIFY | Wire `patterns[]` from D-prompt; compute + store structural session embedding; remove legacy path |
| `chunk_analyzer.py` | MODIFY | Add `patterns[]` field to prompt, schema, `DChunkResult`; apply 3 prompt fixes (see below) |
| `light_analytics.py` | REPLACE | Rewrite as two-phase `ClusteringAnalyticsPipeline` |
| `step_scoring.py` | SOFT-DEPRECATE | Leave in place; no new investment; gated on Stage 4 ≥ 80% precision which has not shipped |
| `critical_steps.py` | SOFT-DEPRECATE | Follows step_scoring |
| `entity_extractor.py` | DEPRECATE | Dead code — only reachable via removed legacy path |
| `entity_resolver.py` | MODIFY→SHRINK | Remove fuzzy matching; keep simple "store entity if not exists" |
| `occurrence_extractor.py` | DEPRECATE | Occurrences table loses its only consumer (light_analytics) |
| `memory_extractor.py` | DEPRECATE | Replaced by D-prompt; promote `_is_trivial()` to shared util |
| `summarizer.py` | MODIFY | Drop `summarize_chunk()` (replaced by D-prompt); keep `summarize_session()` |
| `inflection_detector.py` | SOFT-DEPRECATE | Track 2 Stage 5 not yet built; patterns[] covers same semantic territory |
| `db/schema.py` + `store.py` | MODIFY | Add pattern types to memories; add `session_structural_embeddings` vec table; add `session_type` to sessions |

---

## D-prompt: `patterns[]` field specification

### Prompt text

```
4. patterns — agent reasoning and strategy observations worth remembering for future sessions.
   ONLY extract if something notable is observable in this chunk. Return [] if nothing stands out.
   MAX 2 per chunk. Must be non-ephemeral — abstract away session-specific names, paths, model names.
   Skip ONLY if the pattern is substantially identical (same steps, same action verbs) to a
   PROCEDURAL memory already in field 3. Overlap with episodic or semantic memories does NOT
   trigger suppression — those capture facts, not reasoning approaches.
   Patterns capture HOW the agent reasoned (meta-level mindset).
   Procedural memories capture WHAT to do step-by-step (object-level). If they overlap, keep the memory.

   - strategy: HOW the agent approached the problem (mindset/method, not steps).
     BAD: "agent ran several read commands before editing"
     GOOD: "agent read all affected files before making any edits (read-before-write strategy)"

   - decision: what was decided and WHY, abstracting away specific tool/model/file names.
     BAD: "agent selected gemma3:12b over gemma4:e4b for review regeneration"
     GOOD: "when multiple local models are available, chose a mid-size instruction-following model
            over the largest model (which may be optimized for embeddings, not generation)"

   - recovery: ONLY if agent demonstrably got stuck (repeated failures, errors, backtracking)
     then found a solution. State: stuck state → what was tried → what worked.
     GOOD: "JSON parsing failed repeatedly due to markdown fences in LLM output; agent added
            fence-stripping as a preprocessing step — addresses a recurring LLM output format issue"

   - inefficiency: ONLY if a shorter/faster path existed. State: what was done, what would be better.
     GOOD: "agent iterated remove_from_cart(item_id) N times; could have used empty_cart() —
            avoids O(n) API calls for an O(1) operation"
```

### Validation results (experiments 2026-07-01)

Two rounds of experiments run (v1 prompt and v2 with 3 fixes). 9 chunks across 3 sessions (tool-heavy ML coding, discussion/design, mixed analysis). Model: gemma4:e4b.

**v1 findings:**
- 5/9 correct abstentions — model is appropriately conservative ✅
- `strategy` patterns non-ephemeral and distinct from `memories[]` ✅
- One `decision` pattern leaked specific model names → Fix 1
- One chunk produced memory+pattern overlap (both captured same thing) → Fix 2
- `recovery` and `inefficiency` never fired (correct: those sessions had none) → Fix 3 adds examples for future cases

**v2 fix assessment:**

| Fix | Effect | Final verdict |
|-----|--------|---------------|
| Fix 1: stronger decision BAD/GOOD example | No decision patterns with leaked names appeared; no regressions | **Keep** |
| Fix 2: skip pattern if overlaps memory | Too aggressive — dropped two valid non-overlapping patterns in search-benchmark chunk (those memories were *semantic*, not procedural) | **Adjust**: narrow suppression to procedural memory overlap only |
| Fix 3: sharper recovery/inefficiency triggers | No false positives in either run (correct behavior) | **Keep** |

**Adjusted Fix 2 (final wording):** Skip ONLY if pattern is substantially identical to a *procedural* memory from the same chunk. Overlap with semantic or episodic memories does not trigger suppression.

---

## Schema changes

### `memories` table
Add new valid values to `memory_type` enum:
- `pattern_strategy`
- `pattern_decision`
- `pattern_recovery`
- `pattern_inefficiency`

### `sessions` table
Add column: `session_type TEXT` — cluster label from structural embedding HDBSCAN.

### New vec table: `session_structural_embeddings`
Mirrors existing `session_embeddings` structure. Keyed by `session_id`. Embedding from raw head+tail text (4k+2k chars).

### Remove
- `occurrences` table and `occurrence_embeddings` vec table (once `light_analytics` is replaced and no consumer remains)
- Legacy pipeline flag `use_legacy_pipeline` and all code paths it gates

---

## Implementation order

### Phase 1 — D-prompt extension (low risk, immediate value)
1. Apply prompt fixes to `chunk_analyzer.py` (pending v2 experiment results)
2. Add `patterns[]` to `DChunkResult` and JSON schema
3. Wire `DChunkResult.patterns` through `ingestion.py` to `memories` table with new type values
4. Add `memory_type` values to schema; migration for existing DB
5. Verify: re-ingest 2–3 known sessions, inspect extracted patterns

### Phase 2 — Session structural embedding (low risk, independent)
1. Add `session_structural_embeddings` vec table to schema
2. Compute head+tail embedding in `ingestion.py` alongside existing summary embedding
3. Store on sessions row
4. Add `session_type` column to sessions table (nullable until Phase 3)
5. Verify: check embeddings are stored; check UMAP separation matches prior EDA

### Phase 3 — Clustering analytics (replaces light_analytics, higher risk)
1. Write `ClusteringAnalyticsPipeline` with HDBSCAN over structural embeddings → session_type tags
2. Write memory+pattern clustering: embed → cluster → recurrence gate → LLM consolidation
3. Tests: unit tests for clustering logic; integration test on known corpus
4. Ship-gate: consolidated memories must be qualitatively reviewed before Phase 4
5. Deprecate `light_analytics.py` once Phase 3 passes review

### Phase 4 — Cleanup
1. Remove legacy pipeline path (`use_legacy_pipeline`, `entity_extractor.py`, `memory_extractor.py`, `summarize_chunk()`)
2. Deprecate `occurrence_extractor.py` and drop `occurrences` table
3. Shrink `entity_resolver.py` to simple store-if-not-exists
4. Soft-deprecate Track 2 (`step_scoring.py`, `critical_steps.py`, `inflection_detector.py`) — leave files but mark as unsupported in README

---

## Open questions

1. **Entity-based search retention** — do we want structured lookup by entity name (e.g. "show all sessions mentioning gemma3:12b")? If yes, keep entity + entity_resolver. If no, entities become informational only and can eventually be dropped. Decision deferred to Phase 4.

2. **Recurrence threshold N** — default N=3 sessions for cluster promotion. Tune on corpus after Phase 3 ships.

3. **`recovery` and `inefficiency` coverage** — these pattern types may remain rare because most session chunks don't contain recoveries. Acceptable if `strategy` and `decision` carry the signal; revisit after 50+ sessions of real extraction.

4. **Track 2 successor** — if Track 2 (per-exchange observer scoring → critical steps) is formally retired, `patterns[]` extraction at chunk level becomes the primary source of failure/recovery signal. Monitor whether `pattern_recovery` captures enough of what critical_steps was meant to find.
