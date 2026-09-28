# Experiment 08: Post-fix re-extraction audit, with temporal-validity correction

**Date:** 2026-07-21
**Motivation:** two corrections to exp07's methodology, raised directly after that report shipped:

1. **Benchmark direction was backwards.** exp07 scored 1,200 memories *extracted by the old, pre-fix
   pipeline* against the log. That's a one-time report, not a reusable benchmark — re-extracting with any
   new prompt/model produces a different 1,200 rows with different IDs, requiring the whole expensive
   judging pass again. This experiment instead re-extracts fresh memories from the *current* (fixed)
   pipeline and scores those, so the number actually reflects what ships today.
2. **Temporal validity was ignored.** A memory can be completely accurate at the moment it was extracted,
   even if the system later changed and the log's current entry says something different — that's normal
   knowledge decay, not a pipeline defect. exp07 conflated "wrong" with "outdated" under a single
   CONTRADICTED verdict. This experiment splits that into HALLUCINATION (wrong even at extraction time)
   and STALE (right then, superseded later), using each memory's session date against the log's own dated
   entries.

**Scope:** re-extracted the single richest ATS session by historical memory yield — `2763d25c` (2026-06-08,
51 chunks, 178 memories under the old pipeline) — using the fixed D-prompt against `gemma4:e4b`,
`gemma4:12b`, and Gemini 2.5 Flash. 884 memories/patterns produced (entities excluded — this audits facts,
not named things). Scored by a fresh Haiku subagent (cheaper/faster per explicit ask) against
`design_decisions.md` (dated) + `exploration.md` (mostly undated, topic-coverage only), reading both in
full once, then judging in 6 batches of ~150 via `SendMessage` continuation.

---

## Headline numbers

| Verdict | Count | % |
|---|---:|---:|
| CORROBORATED | 309 | 35.0% |
| HALLUCINATION | 26 | 2.9% |
| STALE | 58 | 6.6% |
| UNCOVERED | 491 | 55.5% |

**Genuine error rate among checkable claims (CORROBORATED + HALLUCINATION): 7.8%** — down from exp07's
13.8% on the old pipeline. **But this is not a clean apples-to-apples improvement claim**: exp07 tested
1,200 items across 22 sessions; this tests 884 items from 1 session. More importantly, if you apply
exp07's *old* methodology to this same data — counting STALE as an error too, the way exp07 implicitly
did — the rate is **21.4%, worse than exp07's number.** The real story isn't "the fix made things better
by this margin" — it's that **temporal correction matters enormously**: roughly 45% of what would have
looked like "contradictions" (58 of 84 STALE+HALLUCINATION items) are actually normal knowledge decay, not
pipeline defects, and conflating the two would have produced a materially wrong read on the fix's impact
in either direction.

## By model

| Model | CORROB | HALLUC | STALE | UNCOV | Total | Hallucination rate* |
|---|---:|---:|---:|---:|---:|---:|
| gemma4:e4b | 24 | 1 | 0 | 115 | 140 | 4.0% |
| gemma4:12b | 47 | 6 | 15 | 106 | 174 | 11.3% |
| Gemini 2.5 Flash | 238 | 19 | 43 | 270 | 570 | 7.4% |

*among CORROBORATED + HALLUCINATION only (checkable claims)

`gemma4:e4b` has the lowest hallucination *rate*, but also the lowest checkable volume (25 of 140 items
land on a topic the log covers at all — it extracts the least log-relevant content). Gemini has the most
raw hallucinations (19 of 26) simply because it extracts ~4x the volume of either local model — its
*rate* (7.4%) sits between the two local models, not above them. `gemma4:12b` has the highest rate among
the three (11.3%) despite far lower volume than Gemini.

## By memory/pattern type — same finding as exp07, now on independent data

| Type | CORROB | HALLUC | STALE | UNCOV | Hallucination rate |
|---|---:|---:|---:|---:|---:|
| semantic | 82 | 14 | 48 | 91 | **14.6%** |
| recovery | 13 | 2 | 0 | 42 | 13.3% |
| decision | 11 | 1 | 0 | 58 | 8.3% |
| episodic | 174 | 9 | 1 | 158 | 4.9% |
| procedural | 23 | 0 | 9 | 59 | 0.0% |
| strategy | 6 | 0 | 0 | 79 | 0.0% |
| inefficiency | 0 | 0 | 0 | 4 | — |

**Semantic memories are still the worst offender by a wide margin — now confirmed on a second,
independently-collected, temporally-corrected dataset.** They also account for 48 of 58 STALE verdicts
(83%) — nearly the entire staleness phenomenon is concentrated in the type whose own definition ("a
stable fact") is the one most likely to silently stop being stable. This is the same mechanism flagged in
exp07 and matches exactly what the already-shipped episodic/semantic prompt fix targets — but that fix
addresses *episodic-vs-semantic mislabeling*, not the separate problem below.

## Two recurring hallucinations survived the fix entirely

These aren't the citation-confusion or leakage bugs the shipped fix addressed — they're a different
failure mode (misremembered architecture / misremembered numbers), and they show up across all three
models, independently re-derived:

1. **"Track 2 can generate/mint all three memory types"** — 8 independent occurrences across gemma4:12b
   and Gemini. Decision 36/38 (2026-05-29, before this session even started) scopes Track 2 to procedural
   memories only; Track 1 is the one that covers all three. This is a real architectural fact the model
   gets backwards repeatedly, not a citation or leakage issue.
2. **"`min_tool_using_exchanges` was raised from 4 to 10"** — 5 independent occurrences. Verified via
   `git log -S` against the actual codebase, not just the log: the field was committed with value 4
   exactly once and never changed to 10 before being replaced entirely by `human_tool_ratio` on
   2026-06-13. The number 10 doesn't appear anywhere in the project's history.

Both recurred across *different models*, which rules out one model's idiosyncrasy — this is either
something genuinely ambiguous/confusable in the session content itself, or a gap the D-prompt doesn't
currently guard against. Worth a follow-up investigation into the source chunks that produced these,
specifically.

## What temporal correction looks like in practice — the Track 2 deprecation

Track 2 (the entire per-exchange observer-scoring pipeline) was deprecated 2026-07-11 — over a month
after this session. Dozens of memories in this audit accurately describe Track 2 mechanics exactly as
designed at the time (tiered tag priority, the evidence_supports formula, threshold values, CLI behavior)
and got correctly marked STALE, not HALLUCINATION, because the log confirms they were true on 2026-06-08
and only later superseded. Without the temporal check, all of these would have inflated the error count
and produced a false read that the pipeline is worse at describing Track 2 than it actually was at
extraction time.

## Methodology notes

- The judging agent (Haiku, per request) was explicitly told not to repeat exp07's mistake and given the
  temporal rule up front — it caught it live: it initially returned batch 1 with 0 HALLUCINATION / 0 STALE
  out of 150 (suspiciously clean), was pushed to check harder in batch 2, found 6 HALLUCINATION + 12 STALE,
  and went back and corrected one batch-1 item it had wrongly waved through. The final counts include that
  correction.
- Several hallucinations were only catchable by checking actual source code / git history, not the two
  design docs alone (e.g. the `min_tool_using_exchanges` git-log check, confirming `SCORABLE_TYPES` in
  code, confirming `delta_change: float = 0.10` in `config.py`). The judge did this unprompted once asked
  to be rigorous — worth keeping in mind for any future automated version of this audit: log-only
  comparison would have missed several real errors.
- 17 of 51 chunks needed a `gemma4:12b` retry due to Ollama timeouts (contention, not content — the same
  chunks succeeded cleanly when run in isolation with a longer timeout). Full retry trail is in the
  background of this folder's intermediate files; final `merged_score.json` reflects the last successful
  attempt per chunk.

---

## Files in this folder

- `chunks_full_backup.json` — the 51 source chunks from session `2763d25c`
- `final_extraction.json` — 153 chunk-extractions (51 chunks × 3 models), all clean, 0 empty
- `scoreable_items.json` — 884 memories/patterns flattened for scoring
- `score_batch_00.json` … `score_batch_05.json` / `score_verdicts_00.json` … `score_verdicts_05.json` — judging batches and raw output
- `merged_score.json` — **the audit dataset** — 884 items joined with verdicts
- `hallucination_items.json` — the 26 HALLUCINATION rows, full detail
