# Experiment 09: Memory taxonomy redesign — semantic removed, episodic gains status, new preferences field

**Date:** 2026-07-21/22
**Motivation:** direct outcome of the discussion following exp08 — per-chunk extraction cannot verify
the "not a one-session observation" criterion `semantic` requires, and the data across both prior audits
proved it: `semantic` had the worst error rate of any type in both exp07 (14.6%) and exp08 (14.6%,
independently). Rather than patch the definition again, the taxonomy itself changes:

- **`semantic` removed entirely.** Content that would have been semantic now must be phrased as
  `episodic` — a point-in-time observation, not a timeless claim. "Stable fact" status is no longer
  something extraction can claim; it's something only cross-session consolidation can earn (separate,
  not-yet-implemented change to `clustering_analytics.py` — see the two-level within-session /
  cross-session consolidation design discussed but not built in this experiment).
- **`episodic` gains a `status` field**: `resolved | open | reverted`. The old "skip if no resolution"
  filter is gone — it was silently discarding all unresolved deliberation, so the corpus could never
  reflect "we're still deciding between X and Y," only settled outcomes.
- **`procedural` tightened** to durable public interfaces only (CLI commands, documented APIs) — not
  internal implementation detail wearing a how-to's clothing.
- **`patterns` unchanged** — already the strongest-performing category in both prior audits, no reason
  to touch it.
- **New `preferences` field** — user-stated instructions/corrections, separate from memories because
  they're about the user, not the project.
- **Overall bar raised**: explicit "extract sparingly, when in doubt omit" framing.

Full prompt: `v2_chunk_analyzer.py`. Not yet wired into production `chunk_analyzer.py` — this is an
experimental variant for review, run through the same harness pattern as exp06/exp08.

---

## Scope

Same 6 chunks as exp06 (chosen for extraction-quality testing, not for containing unresolved
deliberation or preference statements — see caveats below). Three local models:
`gemma3:12b` (current production default), `gemma4:e4b`, `gemma4:12b`. Gemini 2.5 Flash arm not run in
this pass (pending API key).

## Headline numbers

| Model | Entities | Memories | Patterns | Preferences |
|---|---:|---:|---:|---:|
| gemma3:12b | 27 | 11 | 6 | 0 |
| gemma4:e4b | 11 | 6 | 7 | 0 |
| gemma4:12b | 10 | 4 | 3 | 0 *(4 of 6 chunks only)* |

Memory type/status breakdown across all three models: **17 `episodic/resolved`, 4 `procedural`, 0
`semantic`** (structurally impossible now — the type doesn't exist) **, 0 `open`, 0 `reverted`** in this
final merged run (an earlier partial run before the last prompt-ordering fix did produce one `open`
item: *"previous entity occurrences calculated by an additive layer are considered unreliable... [no
resolution yet]"* — the mechanism works, it just didn't recur in this specific final sample).

**Zero preferences extracted, across all 18 chunk×model runs.** Not a bug — these 6 chunks are
technical-decision-heavy (chess variant implementation, ATS's own pipeline debugging), not
personal-instruction-heavy. This field needs a chunk that actually contains an explicit user preference
to validate against; none of the 6 happened to. Worth testing against a chunk known to contain one
before concluding the field works.

## A regression that turned out not to be one — worth reading if you're reviewing the diff

First pass: `gemma3:12b` and `gemma4:e4b` both re-extracted the fabricated `LangGraph`/`NCBI`/etc.
entities on `opencode_embgeo` c27 — the exact citation-confusion bug that was supposedly already fixed
in production. Rather than assume the new prompt broke something, checked production directly on this
exact chunk before concluding anything:

- **`gemma4:12b`**: confirmed fixed in exp06 (0 items), confirmed fixed here too, and more nuanced —
  correctly extracts the real threshold-tuning fact while still suppressing the quoted content.
- **`gemma4:e4b`**: confirmed **still contaminated** in exp06 (production never actually fixed this for
  e4b). V2: **now fixed** (0 entities).
- **`gemma3:12b`**: **never actually verified fixed** in exp06 — only `gemma4:12b` was checked
  post-fix on this specific chunk, and I'd been assuming (wrongly) that the fix generalized to the other
  models. Direct-checked production against this chunk just now: still 10 contaminated items, confirming
  this was never fixed for this model, not a new regression. V2: improved to 6 fabricated entities,
  memories are now clean (no more fabricated LangGraph/NCBI *memories*, just leftover contaminated
  *entities*).

Net: two of three models newly fixed on this exact failure mode; `gemma3:12b`'s entity extraction on
this specific bug still isn't fully solved. The fix that got it partway there: reordering the prompt so
the citation-vs-assertion rule is the very first substantive instruction (no preamble before it,
matching production's exact structure) and adding an explicit re-check reminder inside the entities
field description itself ("Re-check each candidate against the citation rule above before including it").

## Known gap: `gemma4:12b` failed on 2/6 chunks

`ats_self_hosted` c30 and `gemini_chess` c16 failed consistently — three full retry attempts through the
normal harness (all 300s timeouts) plus an isolated single-chunk run with a 600s timeout and no
concurrent load (`ollama ps` showed nothing else running). All returned `"Expecting value: line 1 column
1"` — an empty response body — not a timeout on the final attempt, suggesting a genuine
generation issue specific to this model on these two chunks under the V2 prompt, not pure Ollama
contention (contention was ruled out for the final attempt). Given repeated non-timeout empty responses
even in isolation, this looks like it could be prompt-length/complexity sensitivity for `gemma4:12b`
specifically (V2's prompt is longer than V1's, adding the preferences field, the status field
description, and the reinforced citation-rule reminder) — worth investigating if `gemma4:12b` stays in
consideration, but not chased further in this pass given time already spent on Ollama contention
elsewhere in this investigation.

## What to look for in manual review

1. **Does `episodic/resolved` read naturally** given the removal of the old "must have an outcome"
   framing, or does the `status` field feel bolted-on?
2. **Is `procedural` actually tighter** — spot check whether any extracted procedural memories are still
   internal-implementation-detail in disguise rather than genuine public-interface how-tos.
3. **Volume**: `gemma3:12b`'s totals (27/11/6) are noticeably higher than the other two models — is that
   the "less is more" bar working differently per-model, or is `gemma3:12b` just not respecting the
   sparseness instruction as strictly? Worth a side-by-side content read, not just the counts.
4. **The two open questions from this writeup**: preferences field needs a chunk that actually contains
   one to validate; `open`/`reverted` status needs the same treatment.

---

## Files in this folder

- `v2_chunk_analyzer.py` — the redesigned prompt (experimental, not wired into production)
- `chunks.json` — same 6 chunks as exp06
- `final_v2_results.json` — merged final results, 18 rows (6 chunks × 3 models)
- `taxonomy_v2_review.html` — the review artifact (published)
