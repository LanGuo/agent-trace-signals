# Exp10 — Retrieval head-to-head: raw session grep vs. design-log grep vs. ats (MCP)

Rerun of the multi-turn retrieval-quality investigation, after two production fixes landed
2026-07-27/28: the RRF guaranteed-slot fix and workspace-scoped filtering. This time uses the
full ats MCP surface (both `recall` and `chunk_search`, workspace filter restricted to this
project) on the same 14 facts the investigation settled on, run through both a precise
(jargon-matching) and a fuzzy (paraphrased, zero shared vocabulary) query each — 28 cases total.
Numbers below were refreshed again after the 2026-07-30 graph-expansion fix landed, and once more
after the 2026-08-04 `chunk_search`-specific graph-expansion fix (see the graph-expansion section
for what changed and what didn't in each case).

Script: `run_h2h.py`. Ground truth: `manifest.json`. Raw output: `h2h_results.json`.

## Method

**The 14 facts** were sampled from `design/design_decisions.md` by stride (every ~9th entry
across its ~95-entry timeline, not cherry-picked) plus 3 entries already under discussion, to
avoid picking cases that were known in advance to favor any one method.

**Ground truth** (which chunk/memory ids actually state each fact) was hand-verified earlier in
this investigation by joining `memory_sources.record_id` back to the exact source chunk and
reading every memory minted from it in full — not by keyword regex on `memories.content`. That
distinction mattered: an earlier regex-only pass claimed a ~54% memory-extraction miss rate, but
several of those "misses" turned out to be memories that paraphrased a fact away from its literal
code-identifier or number (e.g. `GENUINE_TYPES` → described via its actual enum values
`genuine_human`/`agent_continuation`; `MIN_ENTITY_NAME_LENGTH` → "enforces a minimum name length
of 3"). Manual re-verification dropped the confirmed extraction-miss rate to ~4/14 (~29%), not
54% — logged separately in `design_decisions.md`'s 2026-07-28 entries.

**Three arms, each measured for hit/miss, latency, and token cost** (tiktoken `cl100k_base`,
counting the exact bytes an agent would have to read):

1. **Raw session grep** — `grep -rni "<keyword>" ~/.claude/projects/<project>/*.jsonl` over the
   21 ingested session transcripts (118MB). Hit = any output at all.
2. **Design log grep** — same literal keyword against `design/design_decisions.md`.
3. **ats (MCP)** — both `recall` (top_k=5) and `chunk_search` (top_k=10), each called with
   `workspace_id="agent-trace-signals"` (this project only) and through a wrapper that
   reproduces `mcp/server.py`'s exact wire format byte-for-byte (including `chunk_search`'s
   500-char `chunk_text` truncation) — so the token counts are what a calling agent actually
   receives over MCP, not the raw in-process dict. Hit = correct id appears in either tool's
   result set. `graph_walk` isn't a fact-lookup entry point (it BFS's from a known seed memory
   id) and wasn't in scope for cold-start fact queries.

**Grep keywords are a single literal phrase**, chosen the way a user would actually type one —
not a permissive OR-regex. This makes design-log-grep more brittle than earlier looser checks in
this investigation (e.g. `"33 duplicate session pairs"` doesn't match the doc's actual
`"33 duplicate-session pairs"` hyphenation) — a deliberate, honest choice: naive literal grep
really is this fragile to phrasing, and BM25/semantic search isn't.

**Graph expansion** (`include_graph=True`) was measured as a separate side experiment, not
folded into the primary hit-rate comparison — see below for why.

## Results

| Method | Hit rate (28) | Precise (14) | Fuzzy (14) | Mean tokens | Mean latency |
|---|---|---|---|---|---|
| Raw session grep | 100% | 14/14 | 14/14 | 102,414 (median 30,760, max 824,918) | 1.90s |
| Design log grep | 35.7% | 10/14 | 0/14 | 67 (median 0) | 0.013s |
| **ats (MCP, recall+chunk_search)** | **71.4%** | 13/14 | 7/14 | **3,121** (max 3,971) | 0.077s |

**Raw grep "hits" 100% of the time but that number is meaningless on its own** — it means the
keyword appears *somewhere* in 118MB of casual conversation, not that the match is useful. The
real story is token cost: a single grep call can cost 800K+ tokens (an entire context window) to
find one fact, with no ranking to separate the right hit from noise. ats costs a small, bounded,
~constant few thousand tokens per fact regardless of corpus size.

**Design log grep is fast and cheap but brittle and incomplete** — 0% on fuzzy by construction
(only knows what got manually written up), and only 10/14 on precise despite every fact literally
originating in that document. All 4 remaining precise misses trace to the same cause: the keyword
was a plausible paraphrase, not the doc's verbatim wording, and grep needs an exact contiguous
substring — `"Related Sessions page"` vs. the doc's `"Related Sessions tab"`; `` "query_answer_model
AttributeError" `` vs. `` "`query_answer_model` AttributeError" `` (a markdown backtick sits
between the words); `"ollama read timeout"` vs. a heading that never uses that 3-word phrase at
all (`"read timeout"` and `"ollama"` appear, just not adjacent); `"extraction audit bugs"` vs.
`"extraction audit: **four** bugs"`. (A 5th case, `"33 duplicate session pairs"` vs. the doc's
hyphenated `"33 duplicate-session pairs"`, flipped to a hit on rerun — not because the doc got more
complete, but because this very file quoted that unhyphenated phrase verbatim as an example
earlier in this section, and that quote itself now literally appears in `design_decisions.md`. A
self-reference artifact of documenting the investigation in the same repo it's testing, not a
real improvement — kept the original 5-way breakdown above for that reason.) Using the exact
verbatim heading as the keyword would make this arm trivially 14/14 against its own source — a
tautology, not a useful measurement. The realistic case is a plausible-but-imperfect query, and
that's where even best-case grep (searching the document a fact literally came from) already
loses more than a third of the time to something as small as "tab" vs. "page".

**ats's two tools are complementary, not redundant:** of 20 total hits, 12 facts were found by
both tools, 6 were `chunk_search`-only (memory extraction never captured the fact, but the raw
chunk is still searchable), 2 were `recall`-only. A caller that only used `recall` (as the very
first pass of this investigation mistakenly did) would have measured a materially worse hit rate
than ats can actually deliver — the fair comparison requires trying both.

**Fuzzy/paraphrased queries remain hard, RRF fix notwithstanding:** 13/14 precise queries hit vs.
7/14 fuzzy — confirming a finding from earlier in this investigation that survives the RRF fix
unchanged: when a query shares zero vocabulary with the source material, neither the lexical nor
the semantic leg reliably bridges the gap. The RRF fix corrected *how* the two legs get combined;
it doesn't make either leg individually better at true paraphrase.

## Graph expansion: measured, not helpful for fact-lookup — both `recall` (2026-07-30) and `chunk_search` (2026-08-04) fixed

Ran the identical 28 queries again with `include_graph=True` on both tools, same `workspace_id`
filter, same `top_k`. Rerun after the 2026-07-30 clustering/edge-weight fix (design_decisions.md
same date), then again after the 2026-08-04 entity-weighting fix to `chunk_search` specifically:

| | Recall (base) | Recall (+graph) | Chunk (base) | Chunk (+graph) |
|---|---|---|---|---|
| Mean tokens, before either fix | 625 | 392,094 (627×) | 2,496 | 58,460 (23×) |
| Mean tokens, after recall fix (2026-07-30) | 625 | **5,744 (9.2×)** | 2,496 | 58,460 (23×, unchanged) |
| Mean tokens, after chunk_search fix (2026-08-04) | 625 | 5,744 (9.2×, unchanged) | 2,496 | **12,971 (5.2×)** |
| Max extra items | — | — | — | 414 → **66** |
| Rescued a miss into a reasonable read window (top 10/15)? | — | 0 / 28 (all runs) | — | 0/28 → 0/28 → **1/28** |

Graph expansion never once turned a miss into a hit across all 28 cases for `recall`, before or
after its fix. `chunk_search` was the same for its first two measurements, but its 2026-08-04 fix
rescued one case (`min_entity_name_length`, fuzzy). The original token blowups traced to real,
confirmed bugs, not just a missing cap in either case — but two structurally different ones.

**`recall`'s bug**: `recall(include_graph=True)` calls `graph_walk(seed_id, depth=1)` for each of
its top-3 seed memories, and `graph_walk` BFS'd over `session_graph_edges` treating all four edge
types (`workspace`, `structural`, `shared_memory`, `structural_similarity`) identically — flat
`weight > 0.2` threshold, no per-type limit, no cap on neighbour sessions, then pulled **every**
memory belonging to **any** reached session. One seed's depth-1 walk reached 76 of 103 sessions
(74%) and 3,060 of 3,148 memories (97%) — `shared_memory` edges alone (cluster co-membership, not
scoped to a workspace) already reached 74 sessions on their own, because the cross-session
clustering pass (`_cluster_and_mint`) was using HDBSCAN's `eom` selection method, which collapsed
98% of memories into one incoherent 75-session cluster. **Fixed 2026-07-30**: `eom` → `leaf`
(134 coherent clusters instead of one blob), `shared_memory`/`workspace`/`structural` edges
reweighted by inverse specificity instead of a flat 1.0, and `graph_walk` given explicit neighbour
and result caps as a backstop. `recall`'s inflation dropped to 9.2×, in line with the intended
"a bounded exploratory hop," not a corpus dump.

**`chunk_search`'s bug was never the same one — a different pivot, a different distribution
shape, and a different fix.** `chunk_search(include_graph=True)` doesn't call
`graph_walk`/`session_graph_edges` at all — it uses `_chunk_graph_expand`, a separate mechanism
that pivots via `occurrences.entity_id` (chunks sharing a literal named entity: file, commit,
tool, technology). That's structurally narrower than "same cluster," which is why its inflation
was an order of magnitude smaller to begin with — but it had the identical missing-cap shape:
`_chunk_graph_expand` pulled every chunk that mentioned any entity the seed chunk mentioned, no
limit, done for each of the top-3 seed chunks. One query in this sample pulled 414 extra chunks.

Characterizing it before fixing (queried `occurrences` grouped by `entity_id` on the real corpus,
1176 chunks / 1744 entities / 4917 occurrences) showed a **smooth long tail, not a single
pathological blob like `recall`'s `eom` cluster**: the top entities are the project's own name (two
spellings, 179 and 83 chunks — 15.2% and 7.1% of the whole corpus), the LLM models used for
extraction (`gemma4:e4b`/`gemma3:12b`/`gemma4:12b`, 120/109/42 chunks), `Ollama` (92, 7.8%),
`README.md` (68, 5.8%), `traces.db` (51, 4.3%) — all near-universal in this corpus simply because
it's a project about instrumenting itself with these exact tools, not because they're topically
meaningful. Below that handful the drop-off is gradual with no clean knee. That shape argued for
continuous inverse-specificity weighting (same idea as the `structural`/`workspace` edge reweight)
rather than a hard exclusion cutoff.

**Fixed 2026-08-04**: each matched entity is now weighted `1 / chunks_mentioning_it`, and every
expanded chunk scores `seed_score * decay * weight` (max across entities, if reached via more than
one) — plus `_MAX_CHUNK_NEIGHBOURS_PER_ENTITY=10` and `_MAX_CHUNK_EXPANSION_RECORDS=30` as a
backstop, mirroring `graph_walk`'s cap pattern. Verified directly against production `traces.db`
(safe without migration or mutation risk — `chunk_search` never writes to the DB, unlike
`recall`/`graph_walk`, and the fix is pure query-time logic with no stored edge table to
re-migrate): inflation dropped from 23.4× to **5.2×** (58,460 → 12,971 mean tokens), max extra
chunks from 414 to **66**, and a spot-check of the `eom_vs_leaf` case confirmed the top-ranked
expanded chunks now pivot through genuinely on-topic entities (`clustering_analytics.py`,
`HDBSCAN`, the `episodic`/`procedural`/`semantic` taxonomy concepts) rather than generic
project-name noise. See design/design_decisions.md's 2026-08-04 entry for the full writeup.

### Should `include_graph` be on by default now that it's fixed?

Both fixes bring the cost down from catastrophic to merely expensive — worth checking whether
that expense buys anything for fact-lookup specifically. Recomputed hit rate treating
`include_graph=True` as the primary retrieval mode (using the same 28 cases' graph-expansion data
already gathered above, no extra model calls needed):

| | Hit rate (base) | Hit rate (+graph) | Mean tokens (base) | Mean tokens (+graph) |
|---|---|---|---|---|
| All 28 | 20/28 | **20/28** | 3,121 | 18,667 (6.0×) |
| Precise (14) | 13/14 | 13/14 | 3,174 | 18,538 |
| Fuzzy (14) | 7/14 | 7/14 | 3,067 | 18,796 |

**Net hit-rate change: zero**, even post-fix. One row does flag `graph_rescued=True`
(`min_entity_name_length`, fuzzy — `chunk_search`'s base call missed it, the graph-expanded call
found it within a reasonable read window), but that same fact was already a hit via plain `recall`
(rank 2), so the rescue never moves the aggregate. Across all 28 cases, expansion never turns an
overall miss into a hit, before or after either fix. So: leave `include_graph` off by default for
fact-lookup — it costs ~6× the tokens for no measured accuracy gain in this sample. Consistent
with what both fixes' own commit messages already concluded (built for exploratory cross-session
context, not fact lookup) — this just confirms that conclusion survives fixing the cost problem,
not only precedes it.

## Bottom line

For the fact-lookup task this investigation has been testing: **ats (using both MCP tools with
workspace filtering) matches raw grep's practical hit rate at roughly 1/30th the token cost and
1/30th the latency**, loses to nothing except grep's brute-force "the words are in there
somewhere" guarantee (which comes at catastrophic, often context-window-exceeding token cost),
and cleanly beats the design log on completeness and on paraphrase-tolerance (though the design
log remains a valuable, near-zero-cost first check for anything a human already bothered to
write down). The one clear, measured weakness that graph expansion does not fix is fuzzy-query
recall — a query with no shared vocabulary with its target still fails roughly half the time,
regardless of which fix has landed.
