# Experiment 06: D-prompt extraction audit + multi-model comparison

**Date:** 2026-07-15/16
**Sessions used:** 3, hand-picked for harness diversity — `7b9c12a8…` (claude_code, ATS self-hosted), `682b4d8d…` (gemini_cli, chess app), `517fcbdd…` (opencode, ATS's own analytics_light session)
**Chunks:** 6 (2 per session), unmodified from the real corpus
**Prompt:** unmodified D-prompt from `chunk_analyzer.py`
**Rendered report:** `extraction_audit.html` in this folder (also published as a Claude Artifact during the session that produced it)

---

## Part 1 — corpus-wide audit (no fresh model calls, just querying `traces.db`)

Four issues found by direct SQL query against the live 128-session corpus:

1. **Few-shot example leakage.** The D-prompt's `decision`/`strategy`/`recovery`/`inefficiency` pattern
   instructions each include a "GOOD:" example. The model frequently returns that literal example instead
   of grounding in the chunk. Confirmed by exact string match (a floor, not a ceiling — paraphrased leakage
   isn't counted): the "chose a mid-size instruction-following model…" decision example appears verbatim in
   12 unrelated sessions; the shopping-cart inefficiency example appears in 2 of only 26 total inefficiency
   memories (8% contamination). Downstream: the cluster-consolidation pipeline embeds these identical
   strings and promotes them as a fake cross-session "recurring pattern."

2. **Entity resolution fragmentation.** `entity_resolver.py` merges mentions by `(entity_type,
   canonical_name_lower)`, but the D-prompt classifies the same real-world entity inconsistently across
   chunks (`.env` as both `file` and `technology`, etc). 231 of 2,067 entities (11%) are duplicate-name
   collision groups split across two type buckets, silently diluting `degree_count` (the memory-promotion
   gate).

3. **Assertion vs. citation confusion.** One audited chunk (opencode, ATS's own analytics_light inspection
   session) has the agent quoting old pipeline output for illustration — `LangGraph: "When a node is
   killed mid-execution…"`. The D-prompt extracted this as a semantic fact about LangGraph sourced to a
   session that never uses LangGraph. Same chunk, same failure: `NCBI`, `HITL gate`, `SYNTHESIS`.

4. **`technology` catch-all overuse.** 1,108/2,067 entities (54%) are typed `technology`, including
   hyperparameters, UI elements, and internal code symbols that the prompt's own SKIP rule should exclude.

Good news, for balance: random samples of semantic/episodic memories are consistently specific and
grounded (e.g. exact formula corrections, named bug root-causes) — the failure mode above is specific to
the four pattern sub-types falling back to their own examples, not a general quality problem.

---

## Part 2 — fresh 6-chunk × 7-arm comparison

Same 6 chunks, same unmodified prompt, run against: production (gemma3:12b, live DB), a fresh gemma3:12b
re-run, gemma4:e4b, gemma4:12b, olmo-3:7b-think, Gemini 2.5 Flash, and Claude Sonnet 5 (stand-in for
"Sonnet 4.7" — not available in this harness; ran via subagent, no separate Anthropic API key configured).

**Totals across all 6 chunks (entities / memories / patterns):**

| Arm | Entities | Memories | Patterns |
|---|---:|---:|---:|
| Production (gemma3:12b, live DB) | 32 | 13 | 7 |
| gemma3:12b (fresh re-run) | 27 | 10 | 6 |
| gemma4:e4b | 21 | 13 | 11 |
| gemma4:12b | 29 | 15 | 8 |
| olmo-3:7b-think | 12 | 7 | 1 |
| **Gemini 2.5 Flash** | **54** | **41** | 13 |
| Claude Sonnet 5 | 32 | 16 | 7 |

**Read:** Gemini 2.5 Flash roughly doubled every other model's yield with no observed prompt-example leakage in this sample (6 chunks, not a powered eval — don't over-read this). Claude Sonnet 5 matched production on entity count but pulled more memories/patterns, and typed source files as `file` rather than `technology` more consistently (directly avoiding corpus finding #4). Among local models, gemma4:12b performed closest to production once its token-budget bug was fixed; gemma4:e4b had the fewest entities but the most patterns; the fresh gemma3:12b run pulled out fewer entities/memories than its own live-DB output on the identical chunks — worth keeping as a baseline run-to-run variance data point. olmo-3:7b-think was the weakest by a wide margin (see reliability note below).

---

## Two methodology findings from actually running this

**1. Hidden token consumption before visible output.** `DChunkAnalyzer.analyze()` hardcodes
`max_tokens=1024`. Fine for gemma3:12b, but gemma4:e4b, olmo-3:7b-think, and Gemini 2.5 Flash all emit a
`<think>` block before the JSON — and surprisingly so does **gemma4:12b** on complex prompts despite not
being named as a thinking model (a 5-token "Say OK" test needed >5 tokens just to emit "OK"). At 1024
tokens the hidden reasoning/formatting can consume the whole budget, and the call silently returns empty —
no error, no logged truncation. Raising the budget to ~10–12K tokens for these four models fixed it.
`config.py` already documents this exact tradeoff for `observer_llm`/`entity_verifier_max_tokens`; the
D-prompt call in `chunk_analyzer.py` was never updated to match.

**2. olmo-3:7b-think is unreliable on this prompt, independent of token budget.** The same 3 of 6 chunks
(the longer/denser ones) returned empty output across two different budgets (10,240 and 12,288 tokens) —
raising the budget didn't change which chunks failed, ruling out simple truncation. Pushing to 16,000
tokens didn't help either — it crashed the local Ollama server outright ("server disconnected without
sending a response"). Reads as a genuine reliability limitation on complex multi-field extraction at this
input length, not a config problem.

---

## Where to focus prompt work (original recommendations — see Part 3 for what shipped)

1. Make "return empty" the path of least resistance — replace "GOOD:" examples with fill-in-the-blank
   templates, or add an explicit "never reuse example wording; return `[]` if nothing matches" instruction.
2. Resolve entities on name alone (type as tiebreaker), or reconcile type disagreements across chunks
   instead of treating them as different entities.
3. Add a rule distinguishing assertion from citation: content the agent is quoting/reviewing (not newly
   observing) shouldn't be extracted as a fact about the quoted subject.
4. Tighten the `technology` catch-all with a negative rule, or split it — it's absorbing >50% of all
   entities.
5. Fix the hidden-token-consumption budget in `chunk_analyzer.py` for gemma4:e4b, gemma4:12b,
   olmo-3:7b-think, and Gemini 2.5 Flash before trusting their output as representative; don't rely on
   olmo-3:7b-think for this task until its empty-output failures are understood.
6. If extraction volume is the goal, Gemini 2.5 Flash is worth a serious look as `chunk_summarizer` — it
   roughly doubled entity/memory yield over production with no observed leakage in this sample.

---

## Part 3 — What shipped (2026-07-17)

All four corpus-wide findings above got a real fix in production code, not just a recommendation:

| # | Finding | Fix | Where |
|---|---|---|---|
| 1 | Prompt-example leakage | Replaced concrete "GOOD:" example sentences with abstract fill-in-the-blank "shapes"; added explicit "never reuse this wording" instruction | `chunk_analyzer.py` |
| 2 | Entity resolution fragmentation | Added `Store.get_entity_by_name()` (name-only lookup, any type) + an in-memory `structured_name_cache` in `EntityResolver`, checked before the type-specific hash lookup. First-assigned type wins. | `entity_resolver.py`, `db/store.py` |
| 3 | Assertion vs. citation confusion | Added an explicit preamble rule: only extract from first-hand observations; quoted/reviewed content only yields facts about the act of review, not the quoted subject matter | `chunk_analyzer.py` |
| 4 | `technology` catch-all overuse | Negative rule added to both the prompt's SKIP list and the `d_entity_types` description: omit rather than default to `technology` when unsure | `chunk_analyzer.py`, `config.py` |

**Test coverage:** 4 new tests in `tests/test_entity_resolver.py`
(`TestStructuredEntityTypeFragmentation`) covering same-name/different-type collision
within a session, across sessions, and confirming first-type-wins. Full suite: 246
passed, 6 pre-existing failures unrelated to this change (confirmed via `git stash` —
identical failures exist on unmodified `main`, all tied to the already-deprecated
`analytics_light` pipeline and one `opencode` edge case).

---

## Part 4 — Retest: did the fixes (or explicit thoroughness) close the gap to Gemini?

Motivating question from the user: *"I like Gemini's output most, but I'd rather not
always pay for API calls — can local models close the gap, either as-is or via prompt
fixes?"*

### 4a. Fix impact on local-model volume

Same 6 chunks, `gemma4:12b` and `gemma4:e4b` re-run against the now-fixed prompt:

| Model | Entities | Memories | Patterns | vs. pre-fix |
|---|---:|---:|---:|---|
| gemma4:12b (pre-fix) | 29 | 15 | 8 | baseline |
| gemma4:12b (post-fix) | 24 | 10 | 7 | ↓ across the board |
| gemma4:e4b (pre-fix) | 21 | 13 | 11 | baseline |
| gemma4:e4b (post-fix) | 23 | 12 | 9 | roughly flat |
| Gemini 2.5 Flash (pre-fix, reference) | 54 | 41 | 13 | — |

The fixes are precision-oriented (more SKIP rules, a caution-before-extracting
preamble, tighter type rules) — a modest volume dip on gemma4:12b is the expected
trade, not a regression. Neither model leaked a verbatim prompt example pre- or
post-fix in this sample.

**The fixes address the bugs they were built for. They do not, and were never
expected to, close the ~2× volume gap to Gemini 2.5 Flash.**

### 4b. Does an explicit "be thorough" instruction close the gap?

Tested by appending an explicit volume instruction to the (already-fixed) prompt for
`gemma4:12b`, run twice on the same 6 chunks (`--thorough` flag in `run_comparison.py`):

| Chunk | Run 1 | Run 2 |
|---|---|---|
| ats_self_hosted c3 (5500 chars, densest) | **timeout** (300s) | **timeout** (300s) |
| ats_self_hosted c30 | 5e/2m/1p (274s) | 5e/3m/2p (249s) |
| gemini_chess c7 | **timeout** (300s) | 6e/2m/1p (151s) |
| gemini_chess c16 (dense) | **timeout** (300s) | **timeout** (300s) |
| opencode_embgeo c8 | 6e/2m/2p (285s) | **timeout** (300s) |
| opencode_embgeo c27 | **timeout** (300s) | 4e/1m/1p (264s) |

4/6 then 3/6 chunks hit the hardcoded 300s Ollama read timeout outright — zero
output, not "the model chose to extract less." A different subset failed each run,
**except** the two densest chunks (`ats_self_hosted` c3, `gemini_chess` c16), which
failed in **both** runs with no exceptions. Calls that did finish landed close to or
slightly above the post-fix baseline's per-chunk average — so thoroughness framing
does modestly help *when the model can finish in time*.

**Verdict: the instruction doesn't reliably help, because gemma4:12b can't reliably
finish the more demanding generation task inside a practical timeout.** This is a
capacity/latency ceiling, not volume-by-choice — piling on instruction text pushes an
already-borderline model over the edge on its hardest inputs. This confirms rather
than refutes the original hypothesis: the gap to Gemini 2.5 Flash reads as a genuine
capability difference, not a prompt-tuning problem.

### 4c. Gemini 2.5 Flash cost (fetched 2026-07-17, [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing))

- **Input:** $0.30 / 1M tokens. **Output:** $2.50 / 1M tokens (thinking tokens bill as output by default on 2.5-series models).
- **Free tier** exists with limited RPM/RPD — check current limits before assuming it covers your usage.
- One third-party aggregator claimed an October 2026 deprecation date; Google's own pricing page shows
  no deprecation notice as of this fetch — unconfirmed, worth verifying before a long-term dependency,
  not a reason to avoid it today.
- **Rough full-corpus estimate** (128 sessions, 1,334 chunks, ~2,200 input / ~1,500 output tokens per
  chunk including thinking overhead): a complete re-ingestion is on the order of **$5-15**. Incremental
  ingestion of new sessions is very likely to sit entirely inside the free tier. For a single-user
  personal tool (this project's own stated design target), this cost is not a meaningful constraint.

### Recommendation (superseded — see Part 5)

~~1. Keep the four shipped fixes...~~ ~~2. Set `chunk_summarizer` to Gemini 2.5 Flash...~~ — see Part 5,
the "just switch to Gemini" framing needed a real fix-verification pass, and Gemini did not pass it
cleanly on the specific bug that motivated finding #3.

---

## Part 5 — Verification follow-up: did the fixes verifiably fix the four issues, and did Gemini need its own retest?

Prompted by direct questions after the retest above: was "did volume change" actually evidence the four
*specific* bugs were fixed, and should the "switch to Gemini" recommendation have been checked against
the fixed prompt rather than resting on pre-fix Gemini numbers? Both were fair — checked directly.

### 5a. Corpus size correction

The "128-session corpus" framing used in Parts 1-4 overcounts. Using a strict duplicate signal (identical
`chunk_count` + identical first-chunk text, not just similar summaries): **37 of 128 sessions are
redundant copies of another session already in the DB** (same content, different `workspace_id` — likely
the archive-recovery scanner logic double-ingesting under two workspace-detection paths; a separate bug
from anything in this experiment). That's **91 unique sessions**, and the redundant copies skew large —
**495 of 1,334 chunks (37%)**, not 29%. Corrected full-reingest cost estimate: **~$3-9**, not $5-15 (still
trivially cheap; the earlier conclusion on cost stands, the specific number didn't).

### 5b. Direct fix verification (not just volume)

Checked the exact chunks each bug came from, not just aggregate counts, for `gemma4:12b`:

- **Technology catch-all (#4):** `technology`-typed entities dropped 41% → 33% of all entities on the
  same 6 chunks; `file` correctly absorbed the difference (28% → 50%).
- **Assertion vs. citation (#3) — the clean case.** Re-ran the exact chunk that motivated this finding
  (`opencode_embgeo` c27, agent reviewing old memory-pipeline output). Pre-fix: extracted `LangGraph`,
  `NCBI`, `HITL gate` as entities and asserted fabricated memories (*"LangGraph re-executes a node from
  its checkpoint"*). **Post-fix: `entities: []`, `memories: []`** — correctly recognized as citation, not
  first-hand observation.
- **Entity fragmentation (#2):** resolver-level, not observable in one chunk's JSON. Verified via the 4
  new unit tests reproducing the exact `.env`-as-`file`-then-`technology` collision — all pass. Caveat:
  the fix prevents *new* fragmentation; the 231 existing fragmented groups already in the DB are not
  retroactively merged without a backfill.
- **Leakage (#1):** not verifiable from this sample — it was already at zero literal leaked phrases
  pre-fix on these 6 chunks (leakage was a corpus-wide finding, not reproducible on-demand at n=6).

### 5c. Gemini retest — the fix did not carry over

Pulled Gemini 2.5 Flash's pre-fix output on the same `opencode_embgeo` c27 chunk: it fell for the *same*
trap, **worse** than gemma4:12b — 9 contaminated entities including `SYNTHESIS`, plus memory text that
literally says *"An episodic memory example for Python involved a traceback..."* (partially recognizing
the content was illustrative and extracting it anyway).

Re-ran Gemini against the **fixed prompt** on this exact chunk: **13 entities, 13 memories, 2 patterns —
still fully contaminated.** `LangGraph`, `NCBI`, `HITL gate`, `SYNTHESIS` all still extracted as real
facts, and it added a new fabrication on top: a "semantic" memory claiming these entities are *"considered
useful for memory extraction despite having lower occurrence counts"* — a misreading of the source
chunk's actual content about entity thresholds.

**The prompt fix that cleanly resolved this for gemma4:12b did not resolve it for Gemini 2.5 Flash.**
One chunk is not a powered test, but it's decisive on that chunk, and it directly undercuts the earlier
recommendation: Gemini's ~2× volume lead was measured pre-fix, and at least part of that lead is now
confirmed to include the same category of fabrication the fix was built to remove — not purely "more
genuine signal." 45 of 128 sessions (35%) are ATS working on itself, which is exactly the kind of
self-referential context where "reviewing prior output" moments like this one occur — not a rare edge
case.

### Updated recommendation

1. Keep the four shipped fixes — verified, correctness improvements independent of model choice.
2. **Don't switch `chunk_summarizer` to Gemini 2.5 Flash yet.** Its volume lead is real but at least
   partly inflated by unfixed citation-confusion; recommending it wholesale would risk reintroducing
   finding #3's contamination at production scale, likely at a *higher* rate than local models given
   Gemini's more aggressive extraction style.
3. Before reconsidering Gemini: either iterate on the assertion-vs-citation instruction specifically for
   Gemini's instruction-following style (it may need a stricter/checklist framing rather than prose), or
   test citation-confusion behavior across more self-referential chunks to size the actual blast radius
   rather than generalizing from one.
4. `gemma4:12b` post-fix is a more trustworthy default right now on this specific dimension — it's the
   one arm with a *verified* fix for the fabrication bug, even though its raw volume is lower.
5. Corpus-cost math should use ~91 unique sessions / ~839 unique chunks, not 128/1,334.

---

## Files in this folder

- `run_comparison.py` — harness: runs `DChunkAnalyzer` against Ollama models + a minimal Gemini REST
  provider on the chunks in `chunks.json`. Needs `GEMINI_API_KEY` in the environment to use `--with-gemini`
  or `--gemini-only` (no key is stored in this repo). `--out <file>` to pick an output path (default
  `results.json`), `--append` to add to an existing one, `--thorough` to append the explicit
  volume-instruction suffix used in Part 4b. Run: `python3 run_comparison.py <model> [<model> ...]
  [--with-gemini] [--append] [--out FILE] [--thorough]`.
- `chunks.json` — the 6 selected chunks (label, session_id, chunk_index, chunk_text, existing production
  summary).
- `production_results.json` — what's actually stored in `traces.db` for these 6 chunks today.
- `claude_results.json` — the Claude Sonnet 5 arm (run via subagent, not this script).
- `results.json` — pre-fix baseline: raw output of the original `run_comparison.py` run (local models +
  Gemini), used throughout Part 2.
- `results_postfix.json` — post-fix retest: `gemma4:12b`/`gemma4:e4b` re-run against the fixed prompt, plus
  the `gemma4:12b+thorough` variant (2 runs) — the data behind Part 4.
- `final_comparison.json` — merged canonical dataset behind the Part 2 comparison table (production +
  gemma3:12b fresh + gemma4:e4b + gemma4:12b + olmo-3:7b-think + Gemini 2.5 Flash + Claude Sonnet 5,
  42 rows, all pre-fix).
- `build_artifact.py` + `shell.html` — generate `extraction_audit.html` from `final_comparison.json`.
- `extraction_audit.html` — the full rendered report from Part 1/2 (gitignored — regenerate with
  `build_artifact.py`; does not yet reflect Part 3/4, see this file for that).
