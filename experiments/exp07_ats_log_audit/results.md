# Experiment 07: Full audit of ATS-project memories against the human-vetted design log

**Date:** 2026-07-20
**Motivation:** the design/decision-log idea from a broader conversation about using human-vetted
project logs (design_decisions.md, exploration.md) as ground truth to validate LLM-extracted session
memories. Rather than a spot-check, this audits **every** memory extracted from ATS's own self-hosted
dev sessions against **every** relevant entry in the log — a full benchmark, not a sample.

**Scope:** 1,200 memories, sourced from **22 unique ATS-project sessions** (not 45 — see the corpus-size
correction from exp06 Part 5; most of the 45-session estimate was the generic `claude_code` fallback
workspace bucket, which is a mix of many different projects), judged against `design/design_decisions.md`
(1,657 lines) and `design/exploration.md` (1,338 lines), both hand-vetted by the maintainer.

**Method:** one continued Claude subagent read both log files in full, then judged 12 batches of ~100
memories each (via `SendMessage` continuation, so the judging criteria stayed consistent across the whole
audit rather than drifting across independent calls). Each memory got exactly one verdict:

- **CORROBORATED** — the log confirms this specific fact/event/decision.
- **CONTRADICTED** — the log states something different about the same specific topic. The
  highest-value category: these are memories that would mislead a future session if trusted.
- **UNCOVERED** — the log doesn't address this topic at all. Expected for most memories — the log only
  captures major decisions, not routine implementation detail — and is not a strike against the memory.

---

## Headline numbers

| Verdict | Count | % of all 1,200 |
|---|---:|---:|
| CORROBORATED | 479 | 39.9% |
| CONTRADICTED | 77 | 6.4% |
| UNCOVERED | 644 | 53.7% |

**Among the 556 memories the log actually covers (CORROBORATED + CONTRADICTED), 13.8% are wrong.**
That's the real precision signal — the 53.7% UNCOVERED share is expected and not informative about
accuracy either way, since the log was never meant to cover implementation-level detail.

## Breakdown by memory type

| Type | CORROB | CONTRA | UNCOV | Total | Contradiction rate |
|---|---:|---:|---:|---:|---:|
| semantic | 158 | 33 | 120 | 311 | **10.6%** |
| procedural | 81 | 22 | 137 | 240 | **9.2%** |
| episodic | 62 | 14 | 122 | 198 | 7.1% |
| pattern_strategy | 57 | 4 | 125 | 186 | 2.2% |
| pattern_decision | 96 | 2 | 57 | 155 | 1.3% |
| pattern_recovery | 22 | 2 | 76 | 100 | 2.0% |
| pattern_inefficiency | 3 | 0 | 7 | 10 | 0.0% |

**This directly validates something raised earlier in the same conversation, independent of this audit.**
`semantic` memories ("a stable fact") are the *most* error-prone type by a wide margin — 5-8x higher
contradiction rate than any pattern type. The mechanism is exactly what the episodic-vs-semantic prompt
fix (already shipped, see the prompt-fix conversation) targets: a fact that's true *because of* a specific
recent change gets asserted as a timeless truth, and then the system changes again and the "stable fact"
is stale. Patterns (strategy/decision/recovery/inefficiency) are far more durable — they describe *how the
agent reasoned in that moment*, which stays true regardless of what the codebase does later; they don't
make ongoing claims about current system state, so they can't really go stale the same way.

## The 77 contradictions aren't 77 independent mistakes — 44% are recurring

Grouping the contradicted memories by underlying claim: **34 of the 77 (44%) are re-assertions of just 11
distinct wrong claims**, independently re-minted across different sessions because each D-prompt call has
zero visibility into memories already extracted (let alone already corrected) elsewhere in the corpus.

| Recurring wrong claim | Times re-asserted | What the log actually says |
|---|---:|---|
| Deprecated segmentation/decision-point/task_done-root_cause taxonomy presented as current guidance | 7 | Whole framework deprecated 2026-06-09, replaced by per-turn observer scoring — "no literature precedent" |
| `analytics light` treated as a required pipeline stage | 4 | Deprecated, superseded by D-prompt + ClusteringAnalyticsPipeline; `reset_light_analytics()` etc. now raise `NotImplementedError` |
| Streamlit stale-UI attributed to "caching" | 4 | Log explicitly rules this out — root cause is Streamlit not reloading already-imported modules in a long-running process ("Not a code bug") |
| "90% false positive rate" for the inflection detector | 3 | Log states ~95% |
| "Track 2 expanded to generate all three memory types" | 3 | Track 2 produces procedural memories only; Track 1 is the one that covers all three |
| GLiNER crash fabricated as a threading/deadlock issue | 3 | Real cause: an arithmetic bug in `_split_text`'s overlap calculation (infinite loop); a threading lock was tried and reverted as unhelpful |
| Observer LLM "switched to gemma4:e4b" | 2 | Stayed `gemma3:12b`; gemma4:e4b has the same thinking-budget/truncated-JSON problem, was never the fix |
| `failure_keyword_density` column "never added to schema" | 2 | It was added via `ALTER TABLE` |
| Structural embeddings "first chunk + K sampled middle chunks" | 2 | Actually raw head (4000 chars) + tail (2000 chars) text |
| Pairwise similarity uses "topic embeddings" | 2 | Uses structural embeddings |
| Cluster/session-type labels "should not be persisted" | 2 | A dedicated `session_type_labels` table was added specifically to persist them |

**This is the most actionable finding in the audit.** A single stale fact, once wrong, doesn't cost you
once — it costs you every time a future session touches that topic and the pipeline re-derives (or
re-hallucinates) the same outdated claim from scratch, with no mechanism to check against either the
ground-truth log or the corpus's own prior (corrected) memories.

## What this suggests, concretely

1. **The episodic/semantic prompt fix (already shipped) is well-targeted** — it's specifically pushing
   against the failure mode that's empirically the worst offender (semantic memories asserting
   change-contingent facts as timeless).
2. **A "deprecated topic" gate could catch the biggest recurring cluster directly.** 7 of 77 contradictions
   are the same deprecated-taxonomy confusion. The log's own "Quick Reference" table names every
   deprecated concept explicitly — a cheap check (does this memory's content overlap with a topic the log
   marks deprecated/superseded?) could flag these without needing a full re-judge.
3. **The recurring-cluster pattern is itself the argument for periodic re-auditing**, not a one-time
   pass. As the log grows, this same audit could run again cheaply — most of the judging cost is reading
   the log once; batching is what makes 1,200 judgments tractable.
4. **This dataset is reusable as a benchmark.** `merged_audit.json` (1,200 memories + verdict + log_ref +
   note) can be used to check whether future prompt changes reduce the contradiction rate — re-run
   extraction on the same sessions, re-judge, compare.

---

## Files in this folder

- `ats_memories.json` — the 1,200 source memories (id, type, content, session_id, session_timestamp)
- `batch_00.json` … `batch_11.json` — the 12 judging batches (100 memories each, compact `{id, type, content}`)
- `verdicts_batch_00.json` … `verdicts_batch_11.json` — raw judge output per batch
- `all_verdicts.json` — all 1,200 verdicts concatenated, ID-deduplication verified (0 dupes, 1,200/1,200 unique)
- `merged_audit.json` — **the benchmark dataset** — memories + verdicts joined, one row per memory
- `contradicted_items.json` — just the 77 CONTRADICTED rows, for direct review
