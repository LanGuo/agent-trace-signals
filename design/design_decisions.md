# ATS — Evolution of Design Decisions

> Chronological record of how the design changed and why. The current design is in
> `design/exploration.md`. This doc captures the *thinking that got us there* —
> including dead ends, reversals, and the reasoning behind pivots.

---

## Quick Reference (reverse chronological)

| Date | Decision | Summary |
|------|----------|---------|
| 2026-09-27 | [Open-source prep: raw trace data untracked, repo published with fresh history](#2026-09-27--open-source-prep-raw-trace-data-untracked-repo-published-with-fresh-history) | Audit before going public found verbatim trace text (incl. other private projects' paths/content) in tracked experiment outputs and root `labeled_*` HTML, plus `node_modules`/annotations in history. Untracked all raw outputs (experiments now ship scripts + write-ups only), tightened `.gitignore`, added MIT license, moved `notes/TODO.md` into README "Known limitations". Public repo starts from a single fresh commit; the private repo keeps full history |
| 2026-08-18 | [Explore Sessions' memory/entity-type breakdown charts were biased by session length](#2026-08-18--explore-sessions-memoryentity-type-breakdown-charts-were-biased-by-session-length) | Both charts averaged raw counts per session, but structural session types vary enormously in length (`tool-driven` averages 40.8 chunks/session, `focused extraction` averages 1.2) — a cohort's per-session count mostly reflected length, not a real memory-type signature. Fixed by normalizing per chunk instead. Recomputed on the live corpus: the apparent 55.5–100.4 memories/session spread across clusters mostly evaporates once corrected — the three well-populated clusters converge to ~2.5–2.8 memories/chunk and a nearly identical ~48% episodic / ~33% strategy composition. A demo-video scene built on the "session type predicts memory type" premise was cut once the corrected numbers showed no real effect |
| 2026-08-04 | [Query page "Generate Answer" felt slow: measured, then streamed + kept the model warm](#2026-08-04--query-page-generate-answer-felt-slow-measured-then-streamed--kept-the-model-warm) | Measured the real Ollama call: warm+GPU generation is ~43 tok/s, but a large `chunk_search`-style context pushes prompt-eval to ~5s and a cold reload adds ~3s — none catastrophic alone, but `"stream": false` meant the whole generation sat behind one blank spinner, likely the dominant "feels slow" factor. Added `OllamaProvider.complete_text_stream()` (`st.write_stream()` on the Query page) and an opt-in `keep_alive="30m"` on that call site only (not applied globally, to avoid reintroducing the multi-model-residency OOM pattern already fixed once). Verified live: real incremental chunks, `keep_alive` confirmed via `ollama ps`'s unload countdown, answer now renders progressively in the browser |
| 2026-08-04 | [`chunk_search(include_graph=True)`'s 23x fan-out: fixed with inverse-specificity entity weighting + caps](#2026-08-04--chunk_searchinclude_graphtrues-23x-fan-out-fixed-with-inverse-specificity-entity-weighting--caps) | `_chunk_graph_expand` pivots via `occurrences.entity_id` (shared named entities), a structurally different path from `recall`'s `graph_walk`/`session_graph_edges` that the 2026-07-30 fix never touched. Root cause was the same missing-cap shape but a smooth long-tail distribution, not one pathological blob: the project's own name/aliases, LLM model names, and infra files (README.md, traces.db) are each mentioned in 4-15% of all chunks. Fixed by weighting each matched entity `1/chunks_mentioning_it` (same idea as `structural`/`workspace` edge reweighting) plus per-entity and per-call caps (`_MAX_CHUNK_NEIGHBOURS_PER_ENTITY=10`, `_MAX_CHUNK_EXPANSION_RECORDS=30`) as a backstop, mirroring `graph_walk`'s pattern. Verified against real `traces.db` (no mutation needed — pure query-time fix, no stored edge table): inflation dropped from 23.4x (58,460 mean tokens, max 414 extra chunks) to 5.2x (12,971 tokens, max 66 extra chunks), and a spot-check confirmed the surfaced chunks are now dominated by genuinely on-topic entities instead of generic project-name noise — one case even flipped from miss to rescued |
| 2026-07-30 | [`recall(include_graph=True)`'s 627x fan-out: fixed at the clustering, edge-weight, and BFS layers](#2026-07-30--recallinclude_graphtrues-627x-fan-out-fixed-at-the-clustering-edge-weight-and-bfs-layers) | Root cause was `_cluster_and_mint()` using HDBSCAN's `eom` (collapsed 98% of memories into one incoherent 75-session cluster — the exact failure mode `tag_session_types()` already fixed on 2026-07-13, but never applied here) → switched to `leaf`; also weighted `shared_memory`/`workspace`/`structural` session-graph edges by inverse specificity (flat 1.0 let a near-universal file or workspace-wide clique outrank a genuinely tight cluster) and capped `graph_walk`'s per-hop fan-out and final memory pull. Verified on the real corpus, then migrated production `traces.db` itself the same day (backed up first): wiped the stale eom-era cluster state, recomputed edges, re-ran real LLM consolidation (102 new clusters, max 20 members/7 sessions, 8129s). Production `graph_walk` depth-1 walks now average 28.9 memories (max 30, the cap), down from a ~3060-memory worst case |
| 2026-07-29 | [Exp10 retrieval head-to-head: raw grep vs. design log vs. ats (MCP), rerun with both fixes](#2026-07-29--exp10-retrieval-head-to-head-raw-grep-vs-design-log-vs-ats-mcp-rerun-with-both-fixes) | On 14 hand-verified facts × precise/fuzzy (28 cases): ats via MCP (`recall`+`chunk_search`, workspace-filtered) hits 71.4% at ~3.1K tokens/0.06s vs. raw grep's 100%-but-meaningless hit rate at ~98K mean tokens (max 817K) and design log grep's 32.1% at near-zero cost; `recall` and `chunk_search` are complementary (6/20 hits are chunk-only, 2/20 recall-only); `include_graph` never rescued a miss (0/28) while inflating tokens 23–627× — confirmed exploratory-context tool, not a fact-lookup one, and its uncapped size is a separate risk worth flagging |
| 2026-07-28 | [RRF buries a leg's #1 pick behind two mediocre-on-both-legs candidates: guaranteed-slot fix](#2026-07-28--rrf-buries-a-legs-1-pick-behind-two-mediocre-on-both-legs-candidates-guaranteed-slot-fix) | Reproduced + quantified (2/23 sampled queries demoted out of top-5) a structural RRF weakness where a candidate ranked #1 on one leg but absent from the other loses to candidates ranking moderately on both; rejected tuning `k` (fixes one case, never fixes a rank-4 one even at `k=1`) and weighted RRF (leg reliability is query-dependent); fixed with a guaranteed slot for each leg's #1 result only — extending to top-2/3 was tested and doesn't help further while eroding fusion's purpose |
| 2026-07-27 | [Workspace-scoped filtering added to the retrieval engine](#2026-07-27--workspace-scoped-filtering-added-to-the-retrieval-engine) | `recall`/`chunk-search`/`session-search` can now be restricted to one project via a new `workspace_id` param (`--workspace`/`-w` CLI flag, `workspace` MCP param) pushed into the store-level SQL before RRF truncation, same pattern as the `memory_type` fix below. Uses `LIKE '%...%'` rather than exact equality since the same real project can be ingested under different `workspace_id` strings across plugins/code paths |
| 2026-07-27 | [`recall()`'s `memory_type` filter was applied after RRF truncation, not before](#2026-07-27--recalls-memory_type-filter-was-applied-after-rrf-truncation-not-before) | `search_memories_bm25` had no `memory_type` filter at all — only the semantic leg did — so a wrong-type memory ranking well on BM25 alone could occupy a top-k RRF slot and then get silently dropped by `recall()`'s post-hoc SQL `WHERE memory_type=...`, returning fewer than `top_k` results even when enough correctly-typed memories existed just below the cutoff. Fixed by pushing the filter into `search_memories_bm25` itself (optional `memory_type` param, JOIN to `memories`), mirroring the existing `session_id` pattern on `search_records_bm25` |
| 2026-07-25 | [Cluster memory retrieval-health view; `memory_retrievals` goes from orphan producer to live consumer](#2026-07-25--cluster-memory-retrieval-health-view-memory_retrievals-goes-from-orphan-producer-to-live-consumer) | New Explore Memories section aggregates the already-logged `memory_retrievals` table per cluster memory (retrieval count + recency, least-retrieved-first) — closes a gap where Stage 6's retrieval log (2026-06-11) had accumulated data with no consumer since its intended target, the Bayesian posterior spec, was deprecated 2026-07-22; on the current corpus 3/3 active cluster memories have zero logged retrievals. Also confirmed two cluster-lifecycle invariants (no code change): `memory_cluster_members` has no timestamp because membership is written once at mint time and never appended to afterward, and `mark_superseded_clusters()`'s overlap check (not a diff check) is correct given HDBSCAN's from-scratch re-clustering every run |
| 2026-07-25 | [UI audit: 2 real bugs, 4 built-but-unshowcased features, all fixed](#2026-07-25--ui-audit-2-real-bugs-4-built-but-unshowcased-features-all-fixed) | Home page's Related Sessions tab would render `"None (None)"` for `structural_similarity` edges (824 of them, the densest edge type); Explore Sessions' graph couldn't show that edge type at all; `include_graph`, `conflicts`, cluster supersession, and episodic `status` all had zero UI presence despite being fully built — all 6 fixed across Home.py, both Explore pages, Query page, and retrieval.py |
| 2026-07-24 | [`providers/ollama.py`'s HTTP timeout was wrong on both dimensions; fixed the real one](#2026-07-24--providersollamapys-http-timeout-was-wrong-on-both-dimensions-fixed-the-real-one) | Misdiagnosed a persistent "timed out" failure twice (bumped connect timeout 30s→60s→180s, no effect) before reproducing directly and finding the real cause: genuine 310s generation time hitting the old 300s *read* timeout — never a connection issue. Fixed (read 300s→900s), completing the taxonomy v2 rollout: 103 sessions, 3,988 memories, first real-data test of `conflicts` (7 rows) and cluster supersession (159 rows) |
| 2026-07-24 | [Cleaned up the 33 duplicate-session pairs, with explicit confirmation](#2026-07-24--cleaned-up-the-33-duplicate-session-pairs-with-explicit-confirmation) | Backed up traces.db, verified the flat-vs-nested keep/drop rule (broken vs. real `workspace_id`) held across all 33 pairs, dropped the 33 flat duplicates via `ats drop-session`, explicitly marked them `skipped` (drop-session alone resets to `pending`, which would have re-ingested the same duplicate again) — 99 sessions remain, 0 active duplicates, pytest clean |
| 2026-07-24 | [The 2026-07-14 duplicate-session fix was incomplete; closed the gap](#2026-07-24--the-2026-07-14-duplicate-session-fix-was-incomplete-closed-the-gap) | The original fix only guarded `_scan_archive_files()`; `_process_file()` (the live-scanner entry point) lacked the same content-hash-across-paths check, so a live-discovered duplicate of an already-archived file still re-ingested — surfaced as 33 new duplicate pairs during a 113-session ingestion. Fixed + regression-tested; the 66 duplicate rows already in `traces.db` are flagged, not yet cleaned up |
| 2026-07-23 | [Cluster-memory supersession, decayed graph-expansion scoring, session_search graph expansion](#2026-07-23--cluster-memory-supersession-decayed-graph-expansion-scoring-session_search-graph-expansion) | `mark_superseded_clusters()` marks a prior cluster memory `status='superseded'` when a majority of its members land in a newly-minted one (reuses existing `status`/`metadata` columns, no schema change); `recall()`'s graph expansion now decays from the seed's score instead of flat `0.0`; `session_search()` gained `include_graph`, the last of the three retrieval units to get it |
| 2026-07-23 | [Removed the now-fully-dead LightAnalyticsPipeline module + its tests](#2026-07-23--removed-the-now-fully-dead-lightanalyticspipeline-module--its-tests) | Deleted `pipeline/light_analytics.py` + `test_light_analytics.py` outright — zero callers anywhere outside its own tests since the 2026-07-11 CLI deprecation; kept the 5 deprecated CLI stub commands (useful signposting) and `config.py`'s now-inert `LightAnalyticsConfig` (separate, unrequested scope) |
| 2026-07-23 | [Persisted structural_similarity session edges + chunk_search graph expansion](#2026-07-23--persisted-structural_similarity-session-edges--chunk_search-graph-expansion) | New 5th `session_graph_edges` type, `structural_similarity` (top-8 cosine-similarity neighbours per session over `session_structural_embeddings`, full replace on every `ats embed` run — no per-run versioning, so nothing goes stale); `chunk_search()` gained `include_graph`, expanding top hits to same-entity chunks in other sessions via a one-hop `occurrences` self-join, with hop-decayed (not flat-zero) scoring |
| 2026-07-23 | [Removed dead `same_entity` session-graph edge code, completing the 2026-07-11 analytics-light deprecation](#2026-07-23--removed-dead-same_entity-session-graph-edge-code-completing-the-2026-07-11-analytics-light-deprecation) | `build_same_entity_edges()` in `light_analytics.py` survived the 2026-07-11 `analytics light` deprecation as unreachable code — 0 rows in production, since its only caller (`LightAnalyticsPipeline.run()`) has had no live CLI entry point since that cleanup. Deleted the method, its call site, and its 3 dedicated tests; `structural` (ingestion-time, file/commit/pr entities) remains the sole entity-based session-edge mechanism, unchanged and un-widened |
| 2026-07-23 | [Follow-ups: conflicts table hookup, reverted-status example, cluster-level UI, Memory Bank diagram](#2026-07-23--follow-ups-conflicts-table-hookup-reverted-status-example-cluster-level-ui-memory-bank-diagram) | First writer for the previously-dead `conflicts` table (piggybacks on the consolidation LLM call, zero extra cost); added a worked example for episodic `status="reverted"`; Explore Memories UI now distinguishes within/cross-session consolidation; Home page diagram updated to the new taxonomy; new `notes/TODO.md` for durable gap tracking |
| 2026-07-22 | [Taxonomy v2 shipped to production; two-level consolidation implemented; first live test ingestion](#2026-07-22--taxonomy-v2-shipped-to-production-two-level-consolidation-implemented-first-live-test-ingestion) | Ported v2 prompt + schema + `gemma4:e4b` default into production; fixed a real null-handling bug caught on first live run (JSON `null` status crashed 3/51 chunks); implemented `consolidate_within_session()`/`consolidate_memories()` two-level split; first 5-session ingestion clean, old DB backed up as reference |
| 2026-07-22 | [Decision: adopt taxonomy v2 prompt + switch default model to gemma4:e4b](#2026-07-22--decision-adopt-taxonomy-v2-prompt--switch-default-model-to-gemma4e4b) | Confirmed adoption of the exp09 v2 prompt and `gemma4:e4b` as default `chunk_summarizer`, pending implementation; corrected an earlier max_tokens recommendation from an untuned 12288 harness ceiling down to a measured ~4096 |
| 2026-07-21 to 2026-07-22 | [Taxonomy v2 redesign (experimental, NOT merged to production)](#2026-07-21-to-2026-07-22--taxonomy-v2-redesign-experimental-not-merged-to-production) | `semantic` type removed, `episodic` gains `status` (resolved/open/reverted), `procedural` tightened to public interfaces, new `preferences` field, `decision`+`strategy` merged, `recovery` now evidence-gated; added seed param, root-caused an entity-completeness gap as prompt-wording not token-budget, model-split beats same-model task-split; lives only in `experiments/exp09_taxonomy_v2/v2_chunk_analyzer.py` |
| 2026-07-21 | [Post-fix re-extraction audit with temporal correction; two hallucinations survive the fix](#2026-07-21--post-fix-re-extraction-audit-with-temporal-correction-two-hallucinations-survive-the-fix) | Re-extracted fresh memories with the fixed pipeline and judged with temporal-aware scoring: 7.8% genuine error rate (vs. 13.8%/21.4% with exp07's conflated methodology); `semantic` still worst type; two hallucination patterns (Track 2 scope, a misremembered config value) survived the shipped fix untouched |
| 2026-07-20 | [Full audit: 1,200 ATS-project memories vs. the hand-vetted design log — and two methodology bugs in the audit itself](#2026-07-20--full-audit-1200-ats-project-memories-vs-the-hand-vetted-design-log--and-two-methodology-bugs-in-the-audit-itself) | 39.9% corroborated / 6.4% contradicted / 53.7% uncovered against the design log; found the audit itself used the wrong benchmark direction and had no temporal-validity check — a "hallucination" turned out to be an accurate same-session report once raw JSONL was checked |
| 2026-07-15 to 2026-07-16 | [D-prompt extraction audit: four bugs found and fixed in production](#2026-07-15-to-2026-07-16--d-prompt-extraction-audit-four-bugs-found-and-fixed-in-production) | Found and fixed 4 real extraction bugs (prompt-example leakage, entity fragmentation from type-inconsistent resolution, assertion-vs-citation confusion, `technology` catch-all overuse) with query-backed evidence; added priority-ordered pattern tie-break + citation re-check + name-only entity cache; all 246 tests pass |
| 2026-07-14 | [Home page diagram polish + standalone SVG export to README + doc accuracy pass](#2026-07-14--home-page-diagram-polish--standalone-svg-export-to-readme--doc-accuracy-pass) | Bigger font sizes across both Home page diagrams (title/sub classes scaled ~20%, a few labels shortened to avoid overflow); both diagrams exported as standalone `docs/assets/*.svg` files and embedded in a new README "Architecture" section — found the memory-bank diagram was missing its own copy of `.ats-box-title`/`.ats-box-sub` CSS (previously relied on the other diagram's `<style>` block being present on the same page), fixed for standalone use; also fixed two stale defaults in README (`cluster-memories` default model, `--min-recurrence` example) |
| 2026-07-14 | [Home page: memory-bank schematic (extraction richness → RAG)](#2026-07-14--home-page-memory-bank-schematic-extraction-richness--rag) | Added a second SVG diagram below the pipeline overview: Traces → Memory Bank (8 separate extraction-output boxes — entities + 3 memory types + 4 pattern types — plus 2 consolidation boxes) → RAG. Found and fixed a real bug while building it: a blank line inside the SVG string caused Streamlit's markdown parser to split it into two HTML fragments, orphaning everything past that point outside the `<svg>` tag. Later increased font sizes across both diagrams for legibility; extracted both as standalone `docs/assets/*.svg` files (with their own complete `<style>` blocks, since the inline versions relied on cross-diagram global CSS class leakage that doesn't exist in a standalone file) and embedded them in the README |
| 2026-07-14 | [Query page: example-question picker + top_k default raised to 15](#2026-07-14--query-page-example-question-picker--top_k-default-raised-to-15) | Added a "Try an example question" selectbox wired via `on_change` + `st.session_state` to prepopulate the Query text input; raised default `top_k` 10→15 |
| 2026-07-14 | [UI crash: numba "workqueue... accessed concurrently by multiple threads"](#2026-07-14--ui-crash-numba-workqueue-accessed-concurrently-by-multiple-threads) | `umap-learn`'s numba JIT dispatcher isn't safe across concurrent Streamlit session threads (two tabs both loading a UMAP chart at once). Fixed with `os.environ.setdefault("NUMBA_NUM_THREADS", "1")` at the top of `ui/common.py`, before numba's first parallel dispatch anywhere in the process |
| 2026-07-14 | [Recovery leaderboard ranking bias + major duplicate-session ingestion bug found and partially fixed](#2026-07-14--recovery-leaderboard-ranking-bias--major-duplicate-session-ingestion-bug-found-and-partially-fixed) | Leaderboard sorted by raw `recovery_rate` % with no size floor, so 1-chunk/1-recovery-memory sessions (50%) outranked a 45-chunk/17-recovery-memory session (9.5%) — fixed with a min-memories filter + sort toggle + workspace column. Separately found **33 files ingested twice each at two different archive paths** (byte-identical content, different session IDs since `_compute_session_id()` hashes the absolute path) — 66 of 128 session rows (51.6%) are duplicates, inflating every corpus-wide total in the UI. Root cause fixed prospectively (`get_ingested_state_by_content_hash()` now checked regardless of path); **existing 66 duplicate rows are NOT yet cleaned up** — flagged to the user rather than run a destructive multi-table delete without confirmation |
| 2026-07-14 | [Query page "Generate Answer" AttributeError; query_answer_model switched to gemma4:e4b](#2026-07-14--query-page-generate-answer-attributeerror-diagnosed-as-stale-process-query_answer_model-switched-to-gemma4e4b) | `query_answer_model` AttributeError traced to a stale running Streamlit process predating the config field's addition, not a code bug. Separately, `query_answer_model` switched `gemma4:31b` → `gemma4:e4b`: measured ~7x slower (~79s vs ~11s) on equivalent prompts with no quality improvement |
| 2026-07-14 | [Home page: "What we learned" removed, folded into "Key technical approaches" (5 cards)](#2026-07-14--home-page-what-we-learned-removed-folded-into-key-technical-approaches-5-cards) | Redundant standalone section removed; its two non-redundant points became a 5th "Key technical approaches" card (granularity/signal-to-noise), reordered to sit next to "Dual embeddings" |
| 2026-07-13 | [Five UI pages consolidated to four: Explore split into Explore Sessions / Explore Memories, Session Compare folded into Explore Sessions](#2026-07-13--five-ui-pages-consolidated-to-four-explore-split-into-explore-sessions--explore-memories-session-compare-folded-into-explore-sessions) | `5_Explore.py` split into `2_Explore_Sessions.py` (session-structure EDA + session connection graph) and content moved to `3_Explore_Memories.py` (was `2_Memories.py`, now also has the memory-yield-by-session-type chart); `4_Compare.py` ("Session Compare") deleted — its hand-picked-session comparison UI now lives as a "Compare specific sessions" section at the bottom of Explore Sessions; new default-view charts added to Explore Sessions: memory-type and entity-type composition by structural session type (avg per session, not raw sum, so cohorts of different sizes are comparable) |
| 2026-07-13 | ["Agent Compare" renamed to "Explore"; scatter/UMAP dot size removed entirely; Memories UMAP explains "Unclustered"](#2026-07-13--agent-compare-renamed-to-explore-scatterumap-dot-size-removed-entirely-memories-umap-explains-unclustered) | Page renamed from "Agent Compare" (`5_Agent_Compare.py`) to "Explore" (`5_Explore.py`) — the page is open-ended EDA over the whole corpus, not a fixed comparison; dot-size-by-chunk-count removed from the session scatter and structural UMAP (uniform size now, matching the community graph fix) after captions alone failed to resolve confusion twice; Memories page's memory-level UMAP caption now states the real cluster/member/unclustered counts (317/1874/2456, 56.7% unclustered) and explains why that's expected HDBSCAN behavior, not a bug |
| 2026-07-13 | [Session connection graph: node size was degree — made uniform](#2026-07-13--session-connection-graph-node-size-was-degree--made-uniform) | Same dot-size confusion as the scatters, different chart: node size was `G.degree(n)` (how many other sessions that node connects to for the selected edge types), which made highly-connected sessions look like they represented multiple sessions or sub-clusters. Set all nodes to a uniform size; degree is still available in the hover text. |
| 2026-07-13 | [Agent Compare scatters: dot-size explanation still unclear, added opacity + workspace in hover](#2026-07-13--agent-compare-scatters-dot-size-explanation-still-unclear-added-opacity--workspace-in-hover) | The previous round's captions explaining dot size = chunk count didn't resolve confusion — root cause was that same-colored dots frequently sit at identical (x, y) and fully occlude each other at `opacity=1`, which reads as one big session rather than several overlapping ones; added `opacity=0.6` to both the session scatter and structural UMAP so real overlap shows as a darker patch, reworded captions to name the mechanism explicitly, and added `workspace` to both charts' hover tooltips |
| 2026-07-13 | [Cluster memories: auto-embed on mint, missing FTS index; community graph: discrete palette + tunable granularity](#2026-07-13--cluster-memories-auto-embed-on-mint-missing-fts-index-community-graph-discrete-palette--tunable-granularity) | `ats cluster-memories` now embeds newly minted cluster memories automatically (`--no-embed` to skip) instead of requiring a manual `ats embed` re-run; fixed cluster memories never being written to `memories_fts`, so they were lexically unsearchable until the next process restart's lazy backfill kicked in; session connection graph switched from a continuous colorscale (made community boundaries look like a gradient) to a discrete qualitative palette with a real per-community legend, plus a `resolution` slider so large communities can be split more granularly on demand |
| 2026-07-13 | [Memory-level UMAP, memory-yield-by-session-type chart, dot-size fixes, selectable scatter axes](#2026-07-13--memory-level-umap-memory-yield-by-session-type-chart-dot-size-fixes-selectable-scatter-axes) | Memories page: new corpus-wide memory embedding UMAP, colorable by type/cluster-status/harness/workspace. Agent Compare: "Chunks vs memories" scatter (uninformative) replaced with a memory-yield-per-chunk box plot faceted by structural session type; dot sizes on both session-level scatters capped lower with an explicit "each dot is one session" caption to stop them reading as session-cluster sizes; health-quadrant axes made freely selectable via two dropdowns instead of hardcoded tool-calls-vs-recovery |
| 2026-07-13 | [Session-type labeling, Compare page split, UMAP fix, Memories chart fixes, session community graph](#2026-07-13--session-type-labeling-compare-page-split-umap-fix-memories-chart-fixes-session-community-graph) | `session_type` clusters now get an LLM-generated label describing shared interaction shape (not topic); split Compare into Session Compare (hand-picked sessions) and Agent Compare (corpus-wide cohorts, +session-type as a 5th grouping dim); UMAP recolored by session type instead of a mostly-zero continuous metric; Memories page cluster-composition chart got distinct colors, clearer caption, word-boundary-truncated labels; new session connection graph with community detection across all 3 edge types |
| 2026-07-13 | [Memory clustering follow-ups: Memories page charts, resume support, shared_memory session edges](#2026-07-13--memory-clustering-follow-ups-memories-page-charts-resume-support-shared_memory-session-edges) | Split `cluster` out of the type-breakdown chart + honest random-sample preview + new "Cluster themes" chart; `min_recurrence` dropped to 1 (session-local 3+ clusters are real signal); consolidation parallelized 4x and switched to `gemma4:e4b` (~8x faster than sequential `gemma4:31b`, no quality loss); resume-safe kill/restart (3 model swaps, zero duplicates); new `shared_memory` session-graph edge type — sessions linked by contributing to the same cluster memory, surfacing cross-project connections neither `workspace` nor `structural` edges could find |
| 2026-07-13 | [UI: Home/Memories/Compare pages reworked; Query answer model raised](#2026-07-13--ui-homememoriescompare-pages-reworked-query-answer-model-raised) | Home: click-to-select sessions, clickable memory-type chart, new Structural tab, reworked Related Sessions, removed Chunks tab. Memories page: full redesign with overview charts + cluster-member drill-down. Compare: added permission-mode/entrypoint grouping, fixed a NaN crash. Query page: answer model raised to `gemma4:31b` with a thinking-model-appropriate token budget. |
| 2026-07-13 | [Memory clustering: evidence_count bug, conflated thresholds, membership tracking](#2026-07-13--memory-clustering-evidence_count-bug-conflated-thresholds-and-membership-tracking) | `evidence_count` was always 1 (never written); `min_cluster_size`/`min_recurrence` were the same value, discarding 58% of found clusters (mostly genuine 2-session recurrence); cluster membership was previously unrecoverable — added `memory_cluster_members` table |
| 2026-07-13 | [Session-type clustering collapsed to one mega-cluster: eom → leaf](#2026-07-13--session-type-clustering-collapsed-to-one-mega-cluster-eom--leaf) | HDBSCAN's `eom` cluster-selection method collapsed 87/128 sessions into one cluster; `leaf` gives 11 interpretable clusters with no other changes needed |
| 2026-07-13 | [Session graph edges: two independent bugs fixed + deterministic workspace edges](#2026-07-13--session-graph-edges-two-independent-bugs-fixed--deterministic-workspace-edges-added) | Edges were one-directional despite being modeled as undirected; the only edge mechanism required an LLM-classified file/commit/PR entity when "same project" is already deterministic from `workspace_id` — added `edge_type="workspace"`. Related Sessions coverage went from 27% to 94% of sessions |
| 2026-07-13 | [embed_cmd backfill loops silently lost their writes](#2026-07-13--embed_cmd-backfill-loops-silently-lost-their-writes) | Structural/topic embedding backfill loops in `ats embed` never wrapped writes in a transaction — reported success while committing nothing |
| 2026-07-13 | [Scanner: pick up archive-only orphan sessions](#2026-07-13--scanner-pick-up-archive-only-orphan-sessions) | `data/archive/` was write-only; new `_scan_archive_files()` reads it directly too, recovering 116 previously-invisible archived sessions |
|------|----------|---------|
| 2026-07-11 | [Cleanup: deprecate Track 2 CLI, analytics light, graph walk](#2026-07-11--cleanup-deprecate-track-2-cli-analytics-light-graph-walk) | Deprecated `analytics signals/step-scoring/critical-steps/light/reset-light` CLI commands; gutted `LightAnalyticsPipeline` store methods; replaced Graph Walk page with Related Sessions (session-first, structural edges only); updated all docs |
| 2026-07-01 | [Memory analytics v2 architecture](#2026-07-01--memory-analytics-v2-architecture) | Replaced entity-frequency analytics with embedding-similarity clustering; D-prompt extended with patterns[] field; session structural embeddings added; Track 2 soft-deprecated |
| 2026-06-13 | [FP suppression: human_tool_ratio gate + stub chunk filter](#2026-06-13--fp-suppression-human_tool_ratio-gate--stub-chunk-filter) | Replaced `min_tool_using_exchanges` count gate with `human_tool_ratio`-based session filter (default ≤ 8.0); added stub chunk filter in step-scoring (skip exchanges with <50 chars of evidence+user_text). Both motivated by embedding EDA findings. |
| 2026-06-13 | [Session-level embedding: two representations](#2026-06-13--session-level-embedding-two-representations) | `ats embed` already produces session embeddings from `session_summary` text. EDA used raw head+tail as a complementary signal. Both representations capture different aspects: summary → semantic topic; raw → harness structure. |
| 2026-06-11 | [Stage 4 validation tooling: critical-step labeler + viewer + precision report](#2026-06-11--stage-4-validation-tooling) | New `ats label-critical-steps` + `ats critical-steps-precision` commands. Self-contained HTML viewer mirroring `failure_viewer.py`. Hard ship-gate: precision ≥ 0.80 AND ≥ 20 labeled before Stage 5 induction is trusted. |
| 2026-06-11 | [Track 2 (trajectory_signals) does not depend on Track 1 (D-prompt) outputs — confirmed invariant](#2026-06-11--track-2-does-not-depend-on-track-1-outputs) | step_scoring reads `records.evidence_text` (Stage 1 plugin) + `chunk_text` fallback (plugin serializer, predates D-prompt) + `sessions.turn_descriptors` (plugin Stage 0). Does NOT read `chunk_summary`, `entities`, `occurrences`, `memories` — all D-prompt outputs. Confirmed in `scripts/backfill_trajectory_signals.py`, which refreshes only the trajectory_signals fields without touching D-prompt outputs. |
| 2026-06-11 | [Backfill script for legacy sessions: trajectory_signals fields only](#2026-06-11--backfill-script-for-legacy-sessions) | `scripts/backfill_trajectory_signals.py` re-parses sessions ingested before Stages 0–2 and UPDATEs `records.evidence_text` + `sessions.turn_descriptors`. Skips D-prompt entirely (free with Ollama, but slow). Known limit: when chunking strategy changed (e.g., old per-exchange chunks vs. new 800-token accumulator), DB records that no longer have a matching chunk_index from re-parse keep empty evidence_text; step_scoring falls back to chunk_text for those. Diluted but functional. Selective full re-ingest of representative sessions is the clean alternative. |
| 2026-06-11 | [Stage 3 default thresholds retuned for the default Ollama observer](#2026-06-11--stage-3-default-thresholds-retuned) | Original spec defaults (delta_low=0.35, delta_high=0.80, delta_change=0.25) were calibrated for Haiku-class capability. observer_llm defaults to gemma3:12b which scores conservatively (typical max ~0.25), so the spec defaults saturated 10/10 steps as failure_critical on real data. Retuned to delta_low=0.10 / delta_high=0.20 / delta_change=0.10 for consistency with the default observer. Tests pinned to original spec values via `_TEST_CONFIG`. Haiku users should raise thresholds via CLI flags. |
| 2026-06-11 | [Stage 2 user_text patch: TurnDescriptor.user_text + plugin propagation](#2026-06-11--stage-2-user_text-patch) | Initial Stage 2 implementation passed a synthetic '[~N-word user directive]' stub to the observer because TurnDescriptor didn't carry user text. Observer couldn't judge delta_scope. Added `user_text: str = ""` to TurnDescriptor; all three plugins now populate it (capped 2000 chars). Empirical impact on SLR-gemma4 first-10 test: mean evidence_supports 0.035 → 0.155 (4.4×), JSON parse failures 2/10 → 0/10, delta_scope began firing positive on aligned actions. Stage 4 labeler also reads from TurnDescriptor.user_text for review context. |
| 2026-06-11 | [Memory retrieval log (Stage 6) — instrumentation, no posterior](#2026-06-11--memory-retrieval-log-stage-6) | `memory_retrievals` table + Pydantic model + RetrievalEngine instrumentation. Each surfaced memory logs one row with `(session_id, exchange_idx, memory_id, query_text, query_features, relevance_rank, relevance_score, surfaced)`. Pure side effect — recall return signatures unchanged. Foundation for the future Bayesian posterior in memory_evolution.md without committing to the scoring math yet. |
| 2026-06-11 | [Implementation rollout: Stages 0, 1, 2, 3, 4, 6 shipped — Track 2 within-session pipeline](#2026-06-11--implementation-rollout-stages-0-6-shipped) | Six stages of trajectory_signals shipped (exchange classifier → evidence_text plugin contract → per-step observer scoring → critical-step detection → validation tooling → memory retrieval log). All checkpoint-verified independently on merged main. Stages 5 (within-session candidate memory mint) and 7 (cross-session consolidation pipeline) gated on Stage 4 reaching ≥ 80% precision on ≥ 20 labeled candidates. |
| 2026-06-09 | [Exchange classifier reframed as consumer-agnostic; per-consumer filter sets](#2026-06-09--exchange-classifier-reframed-as-consumer-agnostic) | Old doc anchored the classifier to the inflection detector. Reframed as a shared taxonomy with each consumer choosing its own filter — inflection detector uses `GENUINE_TYPES`; per-step observer uses wider `SCORABLE_TYPES` that includes `agent_continuation` (82% of Gemini exchanges) and `summary_diff`. `notes/exchange_classifier_design.md` deprecated; replaced by `design/exchange_classifier.md`. |
| 2026-06-09 | [Per-session within-session mint + cross-session consolidation pipeline](#2026-06-09--per-session-mint--cross-session-consolidation) | Removed "≥2 sessions sharing feature buckets" trigger for Track 2 induction. Every `critical_step` mints a candidate memory immediately in `lifecycle_state='exploring'`. Cross-session embedding clustering + LLM-driven compress/split/patch runs as a separate analytics consolidation pipeline (analog of `analytics_light`). Matches Voyager / SkillWeaver / SWE-TRACE per-trajectory mint pattern. |
| 2026-06-09 | [Symmetric success / recovery / failure memory induction](#2026-06-09--symmetric-success-recovery-failure-memory-induction) | Track 2 now mints three kinds of procedural memory from critical-step tags: `success_pattern` (well-supported actions), `recovery_pattern` (stuck-then-recovered moments), `failure_response` (poorly-supported actions). All three share lifecycle, posterior, and provenance machinery. Aligns with literature trend that success-driven induction dominates (Voyager, SkillWeaver, SWE-TRACE); failure-driven (Reflexion) is the minority. |
| 2026-06-09 | [Pivot: segmentation + decision-typed calibration → per-turn scoring + critical-step detection](#2026-06-09--per-turn-scoring--critical-step-detection-supersedes-segments--decision-points) | After reviewing 11 papers (SWE-TRACE, TELBench/DRIFT, Harness-1, capability collapse, Reflexion, Voyager, SkillWeaver, Agent KB, SEAgent, MemoryAgentBench, ContinualLearning→Memory, SKILLFOUNDRY), pivoted to per-turn observer scoring with progress/cost vector decomposition and SWE-TRACE-style critical-step gradient detection. Deprecated `design/segments_and_calibration.md`; new spec at `design/trajectory_signals.md` paired with `design/memory_evolution.md`. |
| 2026-06-07 | [Failure keyword density — independent analytics step](#2026-06-07--failure-keyword-density-independent-analytics-step) | `ats analytics signals` computes per-harness keyword density on chunk_text; independent of analytics light and embed; leading indicator for failure escalation |
| 2026-06-07 | [Inflection detector evaluation: structural features insufficient](#2026-06-07--inflection-detector-evaluation-structural-features-insufficient) | Manual labeling found ~95% false positive rate; root cause: harness-injected exchanges contaminate z-score baseline; exchange classifier pre-filter + semantic signals required |
| 2026-06-06 | [opencode plugin: three ingestion bugs fixed](#2026-06-06--opencode-plugin-three-ingestion-bugs-fixed) | `_split_text` infinite loop; `sqlite3 mode=ro` WAL hang; silent error swallowing → sessions stayed pending forever |
| 2026-06-06 | [Confidence field retired](#2026-06-06--confidence-field-retired) | `confidence` column on entities is a legacy artifact of the old regex/GLiNER/verifier pipeline; D-prompt redesign made it meaningless |
| 2026-06-06 | [records_fts: add chunk_summary to BM25 index](#2026-06-06-records_fts--chunk_summary-added-to-bm25-index) | `chunk_summary` was stored in `records` but absent from `records_fts`; now both `chunk_text` and `chunk_summary` are indexed for lexical chunk search |
| 2026-06-05 | [D-prompt chunk analysis: parallel + progress logging](#2026-06-05--d-prompt-chunk-analysis-parallel-execution--progress-logging) | `analyze_batch` was sequential/silent; now runs chunks in parallel threads with INFO-level progress |
| 2026-06-05 | [Entity type descriptions in D-prompt](#2026-06-05--entity-type-descriptions-included-in-d-prompt) | Added per-type descriptions to reduce inconsistent LLM classifications and spurious duplicate entities |
| 2026-06-05 | [D-prompt entity types configurable + normalized](#2026-06-05--d-prompt-entity-types-configurable--normalized-to-shared-vocabulary) | `d_entity_types` moved to config; `D_TYPE_NORMALIZATION` map unifies D and legacy vocabulary |
| 2026-06-05 | [Ingestion redesign: combined D-prompt](#2026-06-05--ingestion-pipeline-redesign-combined-d-prompt-replaces-regexglinerverfierexplicit-memory) | Replaced 4 pipeline steps (summarizer + regex + GLiNER + verifier + memory) with one D-prompt call per chunk |
| 2026-06-04 | [All search methods route through RRF](#2026-06-04-always-route-all-search-methods-through-rrf-for-consistent-scoring) | Unified score scale across lexical/semantic/hybrid by always passing through `_rrf()` |
| 2026-06-04 | [sessions_fts — BM25 for session search](#2026-06-04-sessions_fts--proper-bm25-for-session-search) | Replaced hand-rolled term counter with FTS5 index on `session_summary + workspace_id` |
| 2026-06-03 | [Include thinking blocks in CC serialization](#2026-06-03-include-thinking-blocks-in-cc-exchange-serialization) | `type=thinking` blocks now included at 300-char cap — high-signal content for memory and retrieval |
| 2026-06-03 | [Idempotent analytics light re-runs](#2026-06-03-idempotent-analytics-light-re-runs) | Re-runs now delete stale output for updated entities before re-generating, preventing accumulation |
| 2026-06-03 | [Infinite loop in `_split_text`](#2026-06-03-infinite-loop-in-_split_text--_split_long_exchange) | Fixed off-by-one in split-with-overlap loop that hung when text was shorter than the split window |
| 2026-06-03 | [Session metadata — separate table](#2026-06-03-session-metadata--separate-table-over-column-extension) | Added `session_metadata` table (FK → sessions) for model, tokens, duration, turn counts, etc. |
| 2026-06-03 | [Per-session occurrence threshold](#2026-06-03-per-session-occurrence-threshold-for-analytics-light) | `--session-occurrence-threshold` qualifies by MAX per-session density, not cross-session total |
| 2026-06-03 | [GLiNER sub-window batching](#2026-06-03-gliner-sub-window-batching) | Replaced hardcoded 500-char truncation with 1400-char windows batched in one forward pass |
| 2026-06-03 | [Accumulator chunking + 800-token chunks](#2026-06-03-accumulator-chunking--chunk-size-increase) | Replaced per-exchange chunking with greedy accumulator; 350→800 token ceiling |
| 2026-06-03 | [Opencode: virtual paths for DB-backed sessions](#2026-06-03-opencode-ingestion--virtual-paths-for-db-backed-sessions) | `{db_path}::{session_id}` virtual paths for per-session change detection against a single SQLite DB |
| 2026-06-01 | [Model selection: gemma4:e4b vs gemma3:12b](#2026-06-01-model-selection--gemma4e4b-vs-gemma312b) | Split model assignment by prompt length — gemma4:e4b for short extraction, gemma3:12b for long prompts |
| 2026-06-01 | [GeminiSource JSONL format + routing fixes](#2026-06-01-geminisource-jsonl-format--routing-fixes) | Added `.jsonl` support; fixed CC plugin hijacking Gemini files; fixed `workspace_id` extraction |
| 2026-05-30 | [GeminiSource plugin + `agent_thrash` inflection](#2026-05-30-geminisource-plugin--agent_thrash-inflection-type) | New source plugin for Gemini CLI; new inflection type for read-only thrash without action-target recurrence |
| 2026-05-30 | [Inflection detection: structural features + episode schema](#2026-05-30--inflection-detection-structural-features--episode-schema) | Replaced keyword heuristics with 4 structural features + z-score normalization; added 3-index episode schema |
| 2026-05-29 | [Multi-judge annotation: scope reduced to two surfaces](#2026-05-29--multi-judge-annotation-scope-reduced-from-three-surfaces-to-two) | Dropped entity judging (redundant with verifier); replaced session-outcome judging with inflection validation |
| 2026-05-29 | [Memory taxonomy: `action_orientation` second dimension](#2026-05-29--memory-taxonomy-action_orientation-as-second-dimension) | Added nullable `action_orientation` to memories rather than replacing episodic/procedural/semantic |
| 2026-05-29 | [Two-track memory extraction](#2026-05-29--two-track-memory-extraction) | Track 1 (entity-centric) + Track 2 (action-pattern from inflection contexts) for complementary coverage |
| 2026-05-29 | [Track 1 entity limitations noted](#2026-05-29--what-track-1-entities-do-and-dont-capture) | Entities capture nouns, not actions; entity profiles not yet embedded — known gaps |
| 2026-05-29 | [Two-tier IDF exclusion for analytics](#2026-05-29--two-tier-idf-exclusion-for-analytics-promotion) | Cross-session IDF (`>70% of sessions`) + chunk IDF (`>70% of chunks`) to suppress omnipresent entities |
| 2026-05-29 | [Analytics: 3→1 LLM call, parallel workers, intermediate flushing](#2026-05-29--analytics-pipeline-31-llm-call-parallel-workers-intermediate-flushing) | Combined `compress_to_facts+extract_memories+profile` into one call; added parallelism and crash-safe flushing |
| 2026-05-29 | [Entity extraction noise fixes](#2026-05-29--entity-extraction-noise-and-type-duplication-fixes) | Min name length=3, extended blocklist, restored `rejected[]` in verifier prompt |
| 2026-05-29 | [Pivot: session labels → inflection detection](#2026-05-29--pivot-session-level-labels--inflection-point-detection) | Coarse session outcome labels replaced by turn-level inflection detection (zero extra pipeline step) |
| 2026-05-29 | [Loop closure research synthesis](#2026-05-29--loop-closure-research-synthesis) | Synthesized A2P, TRAIL, EvolveR, T²PO; established session-outcome context as key signal direction |
| 2026-05-26/27 | [Level 1a eval + annotation UI](#2026-05-26-to-2026-05-27--level-1a-eval-and-human-annotation-ui) | Replaced annotation-sparsity FPR proxy with true precision via `labeled_memories` human annotation |
| 2026-05-21 | [Evaluation framework locked in](#2026-05-21--evaluation-framework-locked-in) | Four eval levels (L0–L3); L0 passing is hard prerequisite before L1 is meaningful |
| 2026-05-21 | [Entity-occurrence model + two-step fact compression](#2026-05-21--entity-occurrence-model-and-two-step-fact-compression) | Entities as first-class nodes; occurrences carry contextual embeddings; facts as de-noising layer |
| 2026-05-16/19 | [Separate ingestion, analytics, retrieval pipelines](#2026-05-16-to-2026-05-19--separate-ingestion-analytics-retrieval) | Split single pipeline into P1/P2a/P2b/P3 for independent evaluation and incremental delivery |
| 2026-05-14 | [Starting point: single analytics pipeline](#2026-05-14--starting-point-single-analytics-pipeline) | Original framing: one pipeline clustering sessions directly into memories |

---

## 2026-06-11 — Implementation rollout: Stages 0–6 shipped

Six stages of the trajectory_signals + memory_evolution rollout
implemented and merged on `main`. Each was independently
checkpoint-verified on the merged codebase after agent dispatch (not
just trusted from the agent's self-report). Stage IDs match the
sequencing in `design/trajectory_signals.md`.

| Stage | What | Verification |
|---|---|---|
| 0 | Exchange classifier — `TurnDescriptor.exchange_type`, `GENUINE_TYPES` / `SCORABLE_TYPES`, per-plugin `_classify_exchange`, inflection detector filtered on `GENUINE_TYPES`. | On SLR-gemma4 (251 exchanges): BEFORE 20 inflections, AFTER 13 (−35%); `agent_thrash` 7→1 (−86%); 40% of BEFORE inflections fired on demonstrably non-genuine exchanges. |
| 1 | `evidence_text` plugin contract — `RawChunk.evidence_text` + `Record.evidence_text` + `records.evidence_text` migration; each plugin emits verbatim tool outputs (4000-char per-tool cap, no exchange cap). Gemini regression fix: tool result outputs were previously dropped. | SLR-gemma4 CC session: 100% chunks non-empty evidence_text, median 17.8KB, max 165KB. Gemini: confirmed dropped tool outputs now captured. Migration on existing traces.db: column added, 2386 legacy records default to ''. |
| 2 | Per-step observer scoring — `step_scores` table + `StepScore` Pydantic + `pipeline/step_scoring.py` + `providers/provider_for_model` router. Deterministic cost vector (tokens / redundancy); LLM-judged progress vector via observer (default `gemma3:12b` on Ollama; Haiku enabled by setting `ModelConfig.observer_llm` to a Claude model name). | After the user_text follow-up patch (see entry below): SLR-gemma4 first-10 SCORABLE, gemma3:12b: mean evidence_supports 0.155, max 0.249, 0/10 JSON parse failures, delta_scope fires +1 on aligned actions. |
| 3 | Critical-step detection — `critical_steps` table + deterministic SWE-TRACE-adapted criterion (absolute_low / negative_jump → failure_critical; absolute_high + non-trivial cost → success_critical; positive_jump + 3 prior low steps → recovery_critical). Tag precedence: recovery > failure > success. Failure-mode derivation from dominant negative progress dim + `trajectory_inflation` from cost. | After defaults retune (see entry below): 4 critical_steps from the Stage 2 verify DB (3 failure_critical + 1 success_critical), failure_mode `trajectory_inflation` correctly identified. |
| 4 | Validation tooling — `annotation/critical_step_labeler.py` + `annotation/critical_step_viewer.py` + `ats label-critical-steps` + `ats critical-steps-precision`. Mirrors `failure_labeler.py` pattern; self-contained HTML viewer with tri-state TP/FP buttons + tag/failure_mode correction dropdowns + live precision sidebar; download-JSON workflow. | Labels JSON populated with real `user_text` directives (post-fix); precision math correct at 0/50/80/100% boundary cases; ship_gate enforces ≥ 0.80 precision AND ≥ 20 labeled. |
| 6 | Memory retrieval log — `memory_retrievals` table + `MemoryRetrieval` Pydantic + RetrievalEngine instrumentation. Pure side effect of `recall()` / `graph_walk()`. | One real `engine.recall("database migration", lexical, top_k=5)` → exactly 5 logged rows with monotonic RRF scores, `surfaced=1`, `query_text` populated. |

Stages 5 (within-session candidate memory mint) and 7 (cross-session
consolidation pipeline) are gated on Stage 4 reaching ≥80% precision
on ≥20 labeled candidates. Until that gate passes, downstream memory
induction is not built.

Per-stage commits on `main`: classifier `195c421`, retrieval log
`9aa92f3`, evidence_text `696443c`, per-step scoring `0949a36`,
critical-step detection `f80988d`, validation tooling `6318f1b`, plus
follow-up patches (`11a2c04` user_text, `a0defd1` default thresholds,
`3aa6514` labeler user_text source).

---

## 2026-06-11 — Stage 2 user_text patch

**What changed:** added `user_text: str = ""` field to `TurnDescriptor`.
Each plugin's `_extract_turn_descriptor` now populates it from the raw
user message (capped 2000 chars). `pipeline/step_scoring.py` uses
`td.user_text` as the observer's `user_message` input instead of the
synthetic `[~N-word user directive at exchange X]` stub.

**Why:** the initial Stage 2 implementation passed the stub because
TurnDescriptor didn't carry user text. The observer could not judge
`delta_scope` (target alignment with user intent) without the user's
actual words, so the progress vector collapsed to zero across all
dimensions on every step.

**Empirical impact** (SLR-gemma4 first-10 SCORABLE test with
gemma3:12b):
- mean evidence_supports 0.035 → **0.155** (4.4×)
- max 0.153 → **0.249**
- quintile [10,0,0,0,0] → **[6,4,0,0,0]** (real spread emerged)
- delta_scope began firing positive on aligned actions (ex=2: web
  search "compare LangGraph, CrewAI" aligned with the user's "explore
  this" directive)
- 2/10 JSON parse failures → **0/10** (clearer prompt input)

The Stage 4 labeler also reads from `TurnDescriptor.user_text` (via
`_fetch_user_directive`) when populating per-candidate review context.

---

## 2026-06-11 — Stage 3 default thresholds retuned

**What changed:** `CriticalStepConfig` and CLI defaults retuned from
spec values to Ollama-friendly values:

| Field | Was (spec) | Now (default observer) |
|---|---:|---:|
| `delta_low` | 0.35 | **0.10** |
| `delta_high` | 0.80 | **0.20** |
| `delta_change` | 0.25 | **0.10** |
| `k` | 3 | 3 (unchanged) |

**Why:** the spec defaults were calibrated for a Haiku-class observer,
but `observer_llm` defaults to `gemma3:12b` which scores
conservatively (typical max ~0.25 on healthy work). The mismatch made
the detector saturate — 10/10 steps were below `delta_low=0.35`,
tagged failure_critical, no differentiation. With the retuned
defaults, the same Stage 2 verification DB produces 4/10 critical
steps (3 failure_critical + 1 success_critical), which lines up with
the ship-checkpoint criterion of 3–15 critical steps per session.

**Test compatibility:** the 16 critical_steps tests are pinned to the
**original** spec values via a `_TEST_CONFIG = CriticalStepConfig(0.35,
0.80, 0.25, 3)` fixture so they verify rule logic independently of
default changes. Haiku users override on the command line:
`--delta-low 0.35 --delta-high 0.80 --delta-change 0.25`.

---

## 2026-06-11 — Track 2 does not depend on Track 1 outputs

**Confirmed invariant:** Track 2 (trajectory_signals) reads only
plugin-layer artifacts. It does NOT read any D-prompt outputs.

| Field | Producer | Layer | Read by Track 2? |
|---|---|---|---|
| `records.evidence_text` | Plugin Stage 1 — verbatim tool outputs | Plugin | **Yes** (primary observer input) |
| `records.chunk_text` | Plugin chunk serializer | Plugin | **Yes** (fallback when evidence_text empty) |
| `sessions.turn_descriptors` | Plugin `_extract_turn_descriptor` | Plugin | **Yes** (exchange iteration + classifier filter + user_text) |
| `records.chunk_summary` | D-prompt analyzer | Track 1 | **No** (would be LLM-on-LLM contamination per `design/trajectory_signals.md`) |
| `entities`, `occurrences` | D-prompt extraction | Track 1 | **No** |
| `memories` from `frequency`/`explicit` | D-prompt + analytics_light | Track 1 | **No** |

The `_build_evidence_map` fallback to `chunk_text` is fine because
chunk_text is plugin-produced narrative serialization
(`USER: ... \nA: ... \n[tool: ...]`), predating the D-prompt rewrite
of 2026-06-05.

This means:

- **Re-running ingestion is not required** for Track 2 to refresh
  signals on a legacy session — only the plugin-layer fields need to
  be refreshed.
- `scripts/backfill_trajectory_signals.py` exploits this: re-parse the
  source file, UPDATE `records.evidence_text` + `sessions.turn_descriptors`
  only. Skip D-prompt entirely.
- Future Track 2 stages (per-step observer prompt updates, critical-
  step threshold tuning, induction prompt revisions) won't require
  Track 1 reprocessing either.

---

## 2026-06-11 — Backfill script for legacy sessions

**Script:** `scripts/backfill_trajectory_signals.py`

**What it does:** for each session in the DB with an archived source
path, re-parses via the current plugin and UPDATEs only the
trajectory_signals fields (`records.evidence_text`,
`sessions.turn_descriptors`). No LLM calls; no touch on entities,
memories, occurrences, embeddings, chunk_summary, FTS indexes, or
inflection_detector outputs.

**Known limit:** when a session was originally ingested with a
different chunking strategy (e.g., the per-exchange or 350-token
accumulator that preceded the 2026-06-03 800-token greedy accumulator),
the re-parse produces fewer chunks than exist in the DB. The script
updates evidence_text for the chunks whose `chunk_index` aligns (e.g.,
new chunks 0..64), but the older orphan chunks (chunk_index 65..N)
keep empty evidence_text. step_scoring falls back to `chunk_text` for
those exchanges via `_build_evidence_map`, so the result is a diluted
but functional signal.

**Clean alternative:** selective full re-ingest of representative
sessions for validation. Free in $ via Ollama, but slow (~5–15s per
chunk on gemma3:12b).

**Recommendation:** start with backfill on the whole DB (fast); if
dilution makes Stage 2 signal too noisy, re-ingest 5–10 sessions.
Going forward, fresh ingests Just Work because plugins now produce
all trajectory_signals fields natively.

---

## 2026-06-11 — Stage 4 validation tooling

**What changed:** Built `annotation/critical_step_labeler.py` +
`annotation/critical_step_viewer.py` + `ats label-critical-steps` +
`ats critical-steps-precision`. Mirrors the existing `label-failures`
pattern but targets `critical_steps` instead of `session_inflections`.

**Why a hard gate:** the design principle from
`notes/inflection_detector_evaluation.md` — that pipeline output is
not trusted until human-labeled — applies equally to critical-step
detection. Stage 5 (within-session candidate memory mint) would
amplify any FPs into spurious memories. Gate at `precision ≥ 0.80
AND labeled ≥ 20` matches the ship-checkpoint criterion in
`design/trajectory_signals.md`.

**Per-candidate context** the viewer surfaces for the reviewer:
- color-coded tag + failure_mode badge
- evidence_supports, gradient, preceding/following neighbor scores
- progress_vector + cost_vector (so reviewer sees which dimension
  drove the score)
- agent_action_summary (observer-produced)
- `user_text` = the most recent `genuine_human` directive at or before
  this exchange (from TurnDescriptor)
- `evidence_text_preview` = first 2000 chars of the chunk's
  evidence_text (verbatim tool outputs)

**Naming collision risk:** `label-failures` (legacy inflection
labeler) and `label-critical-steps` (new) coexist. Future cleanup:
either rename `label-failures` → `label-inflections-deprecated` or
add an `ats validate` umbrella.

---

## 2026-06-11 — Memory retrieval log (Stage 6)

**What changed:** added `memory_retrievals` table +
`MemoryRetrieval` Pydantic model + instrumentation in
`pipeline/retrieval.py` (`RetrievalEngine.recall` and `graph_walk`).
Each surfaced memory writes one row capturing `(session_id,
exchange_idx, memory_id, query_text, query_features, relevance_rank,
relevance_score, surfaced)`. Idempotent on a deterministic
`id = sha256(memory_id + retrieved_at + session_id)`.

**Why now (Stage 6 was originally last in the rollout):** built early
so retrievals from existing pipelines (D-prompt explicit memories +
analytics_light frequency memories) start accumulating data
immediately. This populates the input the `memory_evolution.md`
Bayesian posterior will need — without committing to the posterior
math or any of the consolidation logic yet.

**Scope decisions:**
- Logs only memory retrievals, not chunk_search or session_search
  (different downstream consumers, different table if needed later).
- `surfaced=False` rows for considered-but-cut candidates skipped in
  v1; engine returns top-K final list only.
- MCP server tool handlers (search/recall) automatically wired
  because they go through `engine.recall()` — no separate
  instrumentation needed.
- `query_features` is empty `"{}"` in v1; future work populates the
  bucketed features from `step_scoring`.

---

## 2026-06-09 — Per-turn scoring + critical-step detection supersedes segments + decision-points

**What changed:** the within-session signals design pivoted from
*task/topic segmentation + typed decision-point calibration* to
*per-turn observer scoring + SWE-TRACE-style critical-step gradient
detection*. The previous spec
(`design/segments_and_calibration.md`, now
`design/deprecated_segments_and_calibration.md`) is replaced by
`design/trajectory_signals.md`.

**Why:** a deep review of 11 papers (SWE-TRACE, TELBench/DRIFT,
Harness-1, capability collapse, Reflexion, Voyager, SkillWeaver,
Agent KB, SEAgent, MemoryAgentBench, When Continual Learning Moves to
Memory, SKILLFOUNDRY — captured in `notes/swe_trace_findings.md`,
`notes/continual_learning_and_self_improvement_papers.md`,
`notes/agent_self_improvement_skill_promotion_papers.md`) surfaced
three findings that changed the architecture:

1. **No paper segments trajectories** into task/topic boundaries
   before analysis. They operate per-step, per-claim-span, or
   whole-trajectory. Segmentation as a pipeline stage was a design
   choice we invented with no literature precedent.
2. **SWE-TRACE's critical-step detection** —
   `K_t = { j : s_j ≥ δ_abs OR |s_j − s_{j-1}| ≥ δ_chg }` — is a more
   general and less brittle signal extractor than "agent assertion →
   committing action" pattern matching. Needs only a scalar progress
   score per step; no typed taxonomy.
3. **DRIFT's claim-tracking** at span level confirms the per-step
   direction.

The previous decision-type taxonomy (`task_done`, `root_cause`, etc.)
imposed a structure the literature doesn't ratify. It made the
pipeline brittle without buying signal quality over per-step scoring.

**What's new in `trajectory_signals.md`:**

- Per-substantive-exchange observer LLM returns `progress_vector
  (Δ_test, Δ_scope, Δ_patch, Δ_info)` + `cost_vector (C_tok, C_red)`
  + scalar `evidence_supports`.
- Critical-step detection extends SWE-TRACE's formula: adds `δ_low`
  alongside `δ_high` for symmetric detection of poorly-supported
  states; signs the gradient to distinguish recovery (positive after
  sustained low) from sudden drop.
- Three critical-step tags: `failure_critical`, `success_critical`,
  `recovery_critical`.
- Failure-mode tagging is derived from the dominant negative
  `progress_vector` component (no separate extractor).

**What's deprecated:** `session_segments` table + boundary detection;
`decision_points` table + typed extractor; linguistic hedging as a
hard requirement; "agent assertion → committing action" as a
fundamental unit.

**What's kept:** exchange classifier; `evidence_text` per-plugin
contract; observer LLM concept (now per-step); bucketed evidence
features (now per-step over preceding window); failure-mode labeling
(now derived from score vector).

---

## 2026-06-09 — Symmetric success / recovery / failure memory induction

**What changed:** Track 2 now mints three kinds of procedural memory
from critical-step tags (not just failure-driven memories as the
earlier draft of `memory_evolution.md` implied):

| Critical-step tag | Mints | Pattern captured |
|---|---|---|
| `success_critical` | `success_pattern` memory | "When X, do Y — works under Z conditions" |
| `recovery_critical` | `recovery_pattern` memory | "When stuck in X, try Y" |
| `failure_critical` | `failure_response` memory | "When X risks Z, avoid Y; instead W" |

All three share the same `memories` schema, the same `memory_evidence`
Bayesian posterior, the same `procedural_associations`, and the same
lifecycle states. They differ only in `extraction_method` and prompt
template.

**Why:** the literature trend in 2025-2026 is dominantly
**success-driven** skill induction (Voyager keeps verified successful
programs; SkillWeaver distills successful trajectories into Python
APIs; SWE-TRACE curates shortest-path successful trajectories for
SFT). Failure-driven induction (Reflexion 2023) is the minority. The
earlier `memory_evolution.md` overweighted failure; symmetric
treatment is closer to what the literature validates and produces a
better-balanced memory library — agents need a positive playbook,
not just "avoid Z" warnings.

`recovery_pattern` sits between the two — sparser than the other
tags but often the most load-bearing knowledge ("when stuck, do Y").

---

## 2026-06-09 — Per-session mint + cross-session consolidation

**What changed:** Track 2 induction is now **per-session at mint time**;
cross-session work moved to a separate analytics consolidation
pipeline. Previously the draft `memory_evolution.md` required "≥2
sessions sharing failure_mode + feature buckets" before minting a
memory. That cross-session threshold has been removed.

**Why:**

- **Literature alignment:** Voyager / SkillWeaver / SWE-TRACE all mint
  memories per-trajectory. None imposes a cross-trajectory recurrence
  threshold before storage. The "≥2 sessions" threshold was
  Bayesian-Agent's specific design choice, mistakenly applied beyond
  its scope.
- **Cold-start:** a fresh corpus has no cross-session recurrence by
  definition. Per-session mint means useful candidates from day 1.
- **Architectural clarity:** within-session signal extraction →
  candidate mint, then cross-session clustering → consolidation. Each
  layer has one job.

**New pieces:**

1. **Per-session candidate mint** (within `trajectory_signals.md`
   pipeline): every detected `critical_step` immediately mints a
   candidate memory in `lifecycle_state = 'exploring'`, carrying full
   provenance and a weak prior. Eligible for retrieval immediately
   but downweighted by the exploring state.
2. **Analytics consolidation pipeline** (in `memory_evolution.md`,
   analogous to `analytics_light`): periodically runs HDBSCAN-style
   embedding clustering over procedural memories, then LLM-driven
   refinement to compress/split/patch clusters. Posterior promotion
   transitions `exploring → active` (when accumulated retrieval
   evidence supports it) or `exploring → retired` (when evidence is
   negative).
3. **Extension-vs-mint** check at mint time uses a high similarity
   bar (0.92) so only near-identical duplicates are merged at mint;
   looser merges are LLM-judged in the consolidation pipeline.

**Noise control:** deliberately no within-session pre-filter at mint.
"Mint freely, let the posterior sort it out" matches the
Voyager/SkillWeaver/SWE-TRACE pattern. Filters can be added after
measuring on real corpus if needed.

---

## 2026-06-09 — Exchange classifier reframed as consumer-agnostic

**What changed:** the exchange classifier was originally designed
specifically as an input filter for the inflection detector
(`notes/exchange_classifier_design.md`, now
`notes/deprecated_exchange_classifier_design.md`). It now has multiple
downstream consumers — inflection detector, per-step observer scoring,
failure keyword density, Track 2 memory induction. Reframed as a
shared taxonomy with each consumer picking its own filter set.

**Why per-consumer filters matter:** the inflection detector and the
per-step observer have different needs.

- The inflection detector computes sliding-window z-scores;
  consecutive `agent_continuation` exchanges (Gemini's autonomous-
  agent multi-turn) create a degenerate zero-entropy window that
  fires false `loop_entry`. So the inflection detector uses
  `GENUINE_TYPES = {genuine_human, genuine_text}` and excludes
  `agent_continuation`.
- The per-step observer scores each step independently with no
  window averaging — no degeneracy. But `agent_continuation` is 82%
  of exchanges in a typical Gemini session; excluding it would make
  the observer essentially silent on autonomous SDE work. So the
  observer uses `SCORABLE_TYPES = {genuine_human, genuine_text,
  agent_continuation, summary_diff}`.

**New doc:** `design/exchange_classifier.md` — taxonomy is unchanged;
empirical grounding (CC c30450db, Gemini symphony + chess, Opencode
517fcbdd) preserved; per-consumer filter sets and rationale are now
the structural framing.

**Principle:** the classifier is one source of truth; how each
consumer uses the tag is a per-consumer choice.

---

## 2026-05-14 — Starting point: single analytics pipeline

**Original framing**: one pipeline that ingests agent traces, clusters sessions, and
labels clusters as memory. "Chat with all your agent traces" as the primary use case.

Analytics and memory were a single step. The assumption was that clustering sessions
would directly surface the patterns worth remembering.

---

## 2026-05-16 to 2026-05-19 — Separate ingestion, analytics, retrieval

**What changed**: split into four independent pipelines (Pipeline 1: ingestion,
Pipeline 2a: light analytics, Pipeline 2b: full analytics, Pipeline 3: retrieval).

**Why**: a single pipeline can't be evaluated incrementally. If memory quality is bad,
you can't tell if the problem is ingestion quality, analytics quality, or both. Each
pipeline is independently deployable and independently evaluatable — you can ship
Pipeline 1 + 2a as useful without Pipeline 2b or 3.

This also separated fast, incremental work (embedding new records) from slow periodic
work (clustering the full corpus). Decision 12 captured the three-speed analytics model.

---

## 2026-05-21 — Entity-occurrence model and two-step fact compression

**What changed**: entities became first-class nodes in the data model. Occurrences
(entity mentions with context) replaced flat `record_entity_edges`. Two-step fact
compression (occurrence contexts → atomic facts → memories) replaced direct
context-to-memory extraction.

**Why**: direct context-to-memory extraction on raw occurrence texts produced redundant,
noisy, contradictory memories. The fact compression step acts as a de-noising layer.
Occurrence nodes carry contextual embeddings per mention — the centroid of all
occurrence embeddings for an entity is a semantic profile of how that entity is *used*
in the corpus, not just what it is.

Also introduced: entity profiles (LLM-generated prose per entity), `degree_count` as
the promotion trigger, and the AND criterion `degree_count >= threshold AND occurrence_count >= occurrence_threshold`
for promotion. `threshold=0` disables the session-count gate, enabling single-session-project
entities to qualify via occurrence density alone (Decision 2a iteration).

---

## 2026-05-21 — Evaluation framework locked in

**What changed**: defined four evaluation levels (L0 ingestion, L1a light analytics,
L1b full analytics, L2/L3 retrieval) with explicit thresholds. Made L0 passing a
hard prerequisite before L1 is meaningful.

**Why**: "build and hope it works" doesn't produce a system you can improve. Each
pipeline needs a quality gate before the next pipeline is trusted. The delta between
retrieval-with-analytics and retrieval-baseline is the measured value of the analytics
investment — if the delta is small, the analytics pipeline doesn't justify its
complexity (Decision P6 applied).

---

## 2026-05-26 to 2026-05-27 — Level 1a eval and human annotation UI

**What changed**: implemented Level 1a eval framework and an HTML memory annotation
viewer. Added `labeled_memories` support for true FPR (`incorrect / (correct + incorrect)`)
replacing the earlier annotation-sparsity proxy FPR (`(total - matched) / total`).

**Why**: the annotation-sparsity proxy FPR was measuring how few ground-truth memories
we had annotated, not how many extracted memories were wrong. 10 annotations vs 84
memories produced FPR = 0.917 that reflected sparsity, not precision. The labeled_memories
approach lets a human label a subset of extracted memories directly, giving true precision.

Also fixed: `occurrence_promotion_threshold` as an OR criterion for single-session
projects (entities with many occurrences in one session weren't qualifying under
`degree_count` alone).

---

## 2026-05-29 — Loop closure research synthesis

**What changed**: synthesized findings from A2P (2509.10401), TRAIL (2505.08638),
EvolveR (2510.16079), Trajectory-Informed Memory (2603.10600), and others. Identified
that ATS is "on the right side of the observability gap" (captures intermediate traces,
not just summaries) but is leaving value on the table by treating all sessions as
equally valuable signal.

**First proposed mechanism**: session-level outcome labels (`success / failure /
recovery / abandoned / unknown`) — a heuristic classifier at ingest time, analogous to
A2P's approach and the basis for EvolveR's distillation.

**Why this was the right direction**: the research consistently shows that session
outcome context dramatically improves memory extraction quality. Trajectory-Informed
Memory distinguishes strategy/recovery/optimization tips by outcome class. EvolveR uses
outcome-conditioned distillation.

---

## 2026-05-29 — Pivot: session-level labels → inflection point detection

**What changed**: replaced session-level outcome classification with turn-level
inflection point detection.

**Why the pivot**: session-level `success/failure` is the wrong granularity. Most
sessions are "successful" in some coarse sense — they produce some outcome. A session
labeled `success` overall can contain failed attempts, corrections, and pivots; a
session labeled `failure` might have gotten within two turns of succeeding. The coarse
label discards the internal structure that contains the real signal.

The right question isn't "did this session succeed?" but "where did things change
direction, and why?" That's a turn-level question, not a session-level one.

Inflection points (user corrections, agent retractions, loop entry/exit, escalations)
are detectable heuristically from tool call sequences and turn text without LLM
involvement. They run in the same pass as chunking — zero additional pipeline step.

Segment boundaries are a derived concept (span between inflections), not something
that needs to be detected independently.

---

## 2026-05-29 — Entity extraction noise and type duplication fixes

**Problem observed on real corpus**: entity extraction produced significant noise —
single-char tokens ("A"=270 occurrences, "I"=77), path fragments ("Users"=120,
"languo"=136, "claude"=78), and generic words ("en"=88, "commit"=23). These dominated
the analytics promotion candidates.

**Root cause**: the original LLM verifier had explicit rejection of false positives.
When the verifier was slimmed to corrections+added only (to reduce token output), the
rejection step was lost. Pre-filtering by the blocklist was insufficient.

**Fixes applied**:
1. `_MIN_ENTITY_NAME_LENGTH=3` — drops single chars and 2-char tokens before LLM
2. Extended `_ENTITY_BLOCKLIST` with path segments, git words, and boolean/null tokens
3. `rejected[]` array added back to verifier prompt — compact name list only (not full
   enumeration), so output stays small while restoring the rejection signal

**Entity type duplication**: same entity appearing as both `concept` and `technology`
(e.g. "SLR agent", "venv") is caused by GLiNER classifying inconsistently across chunks.
The verifier `corrections[]` field addresses this — the LLM can correct the type in each
chunk. The entity resolver deduplicates by canonical ID which includes type, so fixing
at the verifier level (before resolution) is the right place.

---

## 2026-05-29 — Analytics pipeline: 3→1 LLM call, parallel workers, intermediate flushing

**Three sequential LLM calls → one combined call (`process_entity`)**

The original design issued three calls per entity: `compress_to_facts` → `extract_memories` → `generate_entity_profile`. These were combined into a single `complete_json` call returning `{"facts": [...], "memories": [...], "profile": "..."}`. No quality change — the model has identical context for all three outputs in one pass. 3× fewer round trips. `max_tokens=768` (vs 3 × 512 = 1536 previously). Old method names kept as backward-compat wrappers.

**Parallel workers via `ThreadPoolExecutor`**

`pipeline.run(workers=N)` dispatches `process_entity()` concurrently. Ollama supports multiple inference slots via `OLLAMA_NUM_PARALLEL` — set the env var to `N` before `ollama serve` so it pre-allocates KV cache. Each extra slot costs ~3% of model size in KV cache. Default `workers=1` (sequential). CLI: `--workers N`.

**Intermediate DB flushing (every 10 entities)**

Previously, `write_analytics_batch` was called once after all entities finished — a crash at entity 50/200 lost all output. Now: `write_analytics_batch(run=None)` flushes accumulated memories/edges/entity_updates every 10 entities; the `analytics_runs` record (`run=run`) is written only on the final flush with `completed_at` set. At most one partial batch (≤10 entities) is lost on crash.

---

## 2026-05-29 — Two-tier IDF exclusion for analytics promotion

**Problem**: Pipeline 2a processed omnipresent entities (Edit, Bash, Read, languo) that
appear in nearly every session AND in most chunks within a session. These produced
low-value memories because their occurrence contexts are dominated by file path strings
and generic invocations.

**Cross-session IDF** (`max_entity_session_coverage = 0.7`): exclude entities present in
more than 70% of sessions. With 15 sessions, excludes entities in 11+ sessions. Edit
(14/15), Bash (~13/15), Read (~12/15) are excluded. Only meaningful with 2+ sessions —
skipped when corpus has fewer.

**Within-session chunk IDF** (`max_entity_chunk_coverage = 0.7`): exclude entities
present in more than 70% of chunks across the corpus. Works immediately with a single
session — 92-chunk session with Bash in 80 chunks → excluded. Kills generic tokens
(Bash, Read, Edit, languo, SLR-agent when working on a single project) without needing
cross-session data.

**Why chunk IDF and not session IDF for single-session corpora**: the occurrence_threshold
criterion was intended to handle dense single-session entities, but it favors
high-frequency generic entities over informative mid-frequency ones. Chunk coverage
directly measures ubiquity within the corpus regardless of session count.

**Info loss concern**: project-central entities (e.g., "ATS") may appear in >70% of
chunks in a session about ATS. This is the correct behavior — "ATS" appearing everywhere
adds no discriminative signal for memory promotion. What a session is about is captured
by the session summary (stored in `sessions.session_summary`, embedded separately).
Specific knowledge about ATS comes from lower-frequency entities like "SQLite",
"entity_extractor", "GLiNER" that appear in focused subsets of chunks.

**Config knobs**: `LightAnalyticsConfig.max_entity_session_coverage = 0.7` (CLI: `--max-coverage`),
`LightAnalyticsConfig.max_entity_chunk_coverage = 0.7` (CLI: `--max-chunk-coverage`).
Set to `1.0` to disable either filter. Both require minimum 2 records/sessions to activate.

---

## 2026-05-29 — What Track 1 entities do and don't capture

**Entities are nouns, not actions.** The current entity model extracts things: files,
commits, technologies, concepts, persons, orgs, tools, models, datasets. Tool calls are
partially captured — the tool name becomes a `tool` entity (e.g. `Bash`, `Read`), and
file arguments become `file` entities with roles like `file_modified`. But the action
tuple — what was called, with what arguments, what the outcome was — is not captured.
Model reasoning (the agent's text before acting) and user messages are not captured at all.

This means Pipeline 2a's frequency memories answer "what did we work on / what tools
did we use?" but cannot answer "what went wrong and why?" — the action-pattern signal
that identifies failure modes lives in the interaction structure (turn sequences, tool
outcomes, user corrections), not in which nouns appeared.

**Also noted**: entity profiles (LLM prose summaries generated by Pipeline 2a, stored in
`entities.entity_profile`) are not currently embedded. Entities are embedded as bare
`"{type}: {name}"` strings. For semantic entity search in Pipeline 3, embedding the
profile would be substantially more useful. This is a known gap to fix before Pipeline 3.

---

## 2026-05-29 — Two-track memory extraction

**What changed**: split memory extraction into two tracks with different units and
different purposes.

**Why entities alone are insufficient**: project-level entities (SLR Agent, ATS, the
current project) appear throughout every session working on that project — their
distribution relative to inflection points is flat. "SLR Agent appeared near a
loop_exit inflection 3 times" tells you nothing useful, because SLR Agent appears
everywhere in those sessions regardless.

The root cause of an inflection is not an entity — it's an **action pattern**: the
specific `(tool, target, outcome)` triple that repeated, or the specific edit that got
corrected, or the specific misunderstanding in the agent's reasoning text.

**Track 1** (entity-centric, existing): answers "what does the corpus know about X?"
**Track 2** (action-pattern-centric, new): answers "what action pattern caused this
stuck state / correction / retraction?"

The inflection context tuple captures all three causal dimensions:
- `precipitating_action`: the mechanical action (tool call and outcome)
- `user_signal`: the user's content (not just sentiment polarity — "that's not right,
  the function should handle None" tells you what was misunderstood)
- `agent_reasoning`: the agent's text before the action (captures the wrong mental model)

The `interaction_type` dimension distinguishes failure modes:
- `tool_call` → knowledge or environment gap
- `clarifying_exchange` → ambiguous or misread request
- `self_correction` → successful metacognition (a positive signal worth capturing too)

---

## 2026-05-29 — Memory taxonomy: action_orientation as second dimension

**What changed**: rather than replacing the episodic/procedural/semantic taxonomy with
strategy/recovery/optimization, added `action_orientation` as a nullable second
dimension on `memories`.

**Why**: the existing taxonomy describes *what kind of knowledge* a memory captures and
still has genuine value:
- Episodic: time-anchored, specific past events (Track 1)
- Semantic: stable facts about what things are (Track 1)
- Procedural: how to do something (both tracks)

Replacing it would lose the signal in Track 1 memories. The `action_orientation`
dimension describes *what behavioral context a memory came from* — it applies only to
Track 2 corrective procedural memories. Existing memories get `NULL`, backward
compatible.

The 2D characterization `(memory_type, action_orientation)` is more expressive than
either dimension alone.

---

## 2026-05-29 — Multi-judge annotation: scope reduced from three surfaces to two

**What changed**: the multi-judge design initially covered three surfaces (entity,
memory, session/segment). Entity judging was dropped; session classification was
replaced by inflection validation.

**Why entity judging was dropped**: the existing LLM verifier at ingest already does
this — it checks whether extracted spans are genuine entities of the classified type.
A separate judge pass would be circular (and redundant).

**Why session classification judging was replaced**: the original design had judges
classify session outcomes (success/failure/recovery). Once session-level labels were
replaced by inflection detection (above), this surface became inflection validation
instead — judges verify whether heuristically detected inflections are genuine, giving
a precision estimate for the detector before its output is used in Track 2 extraction.

**What remains**: two surfaces — memory quality (automated Level 1a eval ground truth)
and inflection validation (inflection detector precision estimation). Both use 3-provider
ensembles (Ollama OSS + Anthropic + Gemini) with human review on splits only.

---

## 2026-05-30 — Inflection detection: structural features + episode schema

**What changed**: replaced keyword-based heuristic inflection detection with structural
feature computation + session-relative z-score normalization, and replaced single
`turn_index` with a three-index episode schema.

**Why keyword heuristics are brittle**: hardcoded negation keywords and weighted sums of
text signals break on paraphrase, user communication style variation, and task types not
seen during design. Weights have no principled basis and need manual recalibration.

**Structural features instead**: four features computable purely from parsed JSONL
structure — no text parsing required:
- `action_entropy`: Shannon entropy of tool names in last 5 turns
- `action_target_recurrence`: fraction of `(tool, target)` pairs that are exact repeats of earlier session history
- `error_rate`: rolling mean of `is_error` on tool results, last 5 turns
- `user_turn_length_ratio`: current user turn word count / session median

**Session-relative z-score**: features normalized against that session's running
statistics. No global thresholds. Self-calibrates to session style — an agent that always
uses many tools has a higher `action_entropy` baseline than a focused session.
`signal_strength` is the z-score magnitude of the dominant feature at detection time.

**Episode schema**: a "loop" has temporal structure — precipitating cause (turns before
the detectable entry), entry (structural threshold crossing), and exit (resolution).
Storing `(precipitating_turn, entry_turn, exit_turn)` instead of a single `turn_index`
lets Track 2 extraction span the full episode arc, producing more actionable memories.

**Inflection type from dominant feature**: which feature triggered the episode determines
the type — `action_target_recurrence` ↑ + `action_entropy` ↓ = `loop_entry`;
`user_turn_length_ratio` ↓ sharply = `user_correction`; etc. No hardcoded rules on
text content.

**Text signals deferred**: agent hedging language and user sentiment are a secondary
layer — one LLM call per flagged episode (not per turn) for `interaction_type`
classification. Not needed for initial implementation.

**Inspired by**: T²PO (arxiv 2605.02178). Key mapping: T²PO's uncertainty plateau
(negligible exploration progress) ≈ ATS `loop_entry` (transition into repetitive failure);
T²PO's uncertainty spike ≈ ATS `user_correction`/`agent_retraction`. The two-tier
signal structure (token-level + turn-level in T²PO; per-turn features + episode in ATS)
validated the multi-granularity approach.

---

## 2026-05-30: GeminiSource plugin + agent_thrash inflection type

### GeminiSource plugin

Gemini CLI stores sessions as a single JSON file (not JSONL) at `~/.gemini/tmp/<project>/chats/session-*.json`. Key structural differences from Claude Code:
- Tool calls are embedded in gemini messages as `toolCalls: [{id, name, args, result}]` — not separate tool_result messages
- Multiple gemini messages per user exchange (fragmented execution) — `model_turn_count` in `TurnDescriptor` captures this
- Tool taxonomy: `read_file`, `write_file`, `replace`, `run_shell_command`, `list_directory`, `google_web_search`, `web_fetch`
- Error detection from tool output text (keyword matching) rather than structured `is_error` field

**Scanner integration:** `ScannerConfig.gemini_paths` defaults to `~/.gemini/tmp`. Scanner uses `*/chats/session-*.json` glob (not rglob) to stay within the expected structure. `GeminiSource.can_handle()` accepts `.json` as the final filter.

### agent_thrash inflection type

**Observed failure mode:** A Gemini CLI session working on a hybrid search prototype spent ~30 exchanges cycling through read-only information gathering — reading files one at a time across many sequential model turns — without diagnosing root causes or making clean decisions. Classic "planner deficit" pattern.

**Why not loop_entry?** `loop_entry` fires on action-target *recurrence* (reading the same file again). Thrash reads *different* files each time — low recurrence, but still no forward progress.

**Detection:** Two new features in `InflectionDetector`:
- `read_only_ratio` = read-only tool calls / total tool calls in sliding window
- `model_turn_count` = average model turns per exchange in sliding window

Fires as `agent_thrash` when dominant feature is `model_turn_count` (z > 0) AND `read_only_ratio > 0.6`, or when dominant is `read_only_ratio` (z > 0) AND `model_turn_count > 2.0`. Checked before `loop_entry` in `_classify()` so thrash-heavy sessions aren't misclassified.

---

## 2026-06-01: GeminiSource JSONL format + routing fixes

### JSONL streaming format

Discovered that newer Gemini CLI versions write sessions as `.jsonl` (streaming format) instead of a single `.json` file. The JSONL format includes:
- Line 0: session header (`sessionId`, `startTime`, etc.)
- Subsequent lines: individual message records OR `$set` delta records (incremental updates to be skipped)
- `toolCalls[].status` field for direct error detection (no keyword scanning needed)
- `web_fetch` uses `prompt` arg key instead of `query`/`url`

Scanner updated to glob both `*/chats/session-*.json` and `*/chats/session-*.jsonl`.

### Routing bug fix

`ClaudeCodeSource.can_handle()` accepted any `.jsonl` file. When plugins are tried in order `[ClaudeCodeSource, GeminiSource]`, CC grabbed Gemini JSONL files first, parsing them with the wrong format logic (producing near-zero useful chunks). Fix: added `.gemini` path exclusion to `ClaudeCodeSource.can_handle()`.

### workspace_id fix

`GeminiSource._workspace_id()` matched the first `tmp` component in the path. In test environments, paths like `/private/tmp/.../myproject/chats/...` caused it to return `<pytest-tmpdir>` instead of `myproject`. Fix: now searches for `.gemini` immediately followed by `tmp`, then returns the next component.

---

## 2026-06-01: Model selection — gemma4:e4b vs gemma3:12b

E2e testing revealed a split: `gemma4:e4b` is fast and sufficient for per-chunk entity extraction (short prompts, ~500 tokens), but returns empty responses on longer prompts when it exhausts its generation budget mid-think.

**Root cause (confirmed 2026-06-03):** `gemma4:e4b` is a thinking model — chain-of-thought reasoning tokens count against `num_predict`. With `num_predict=512` and a multi-chunk verification prompt (~4500 chars / ~1200 tokens of input), the model hits the cap mid-reasoning and emits zero output tokens. `eval_count` exactly equals `num_predict` in these cases. This is not a bug in the model, just a budget issue. Fix: raise `entity_verifier_max_tokens` to 2048 for the batch verifier call. This does **not** affect KV cache (`ollama_num_ctx` controls that independently).

**Final config:**
- `entity_extractor_llm = gemma4:e4b` — fast for per-chunk extraction and batch verification (with `entity_verifier_max_tokens = 2048`)
- `light_analytics_llm = gemma3:12b` — reliable JSON on complex/long prompts; `gemma4:e4b` still fails on analytics-length prompts even at 2048 tokens
- `chunk_summarizer / explicit_memory / session_summarizer = gemma3:12b` — kept on 12b; these prompts can be long and reliability matters more than speed
- `ollama_num_ctx = 8192` — cuts KV cache from ~32 GB → ~2 GB without truncating any prompt we generate; independent of `num_predict`/`max_tokens`

---

## 2026-06-03: Accumulator chunking + chunk size increase

**Problem:** Both plugins chunked 1 exchange → 1 chunk. Short exchanges (single-line turns, "ok", tool calls) each emitted their own chunk, inflating chunk counts dramatically. A 144-turn Gemini session produced 144+ chunks instead of ~15, making GLiNER and LLM verification calls scale with turn count rather than session content density. This caused ingest to time out on large sessions.

**Fix:** Replaced per-exchange chunking with a greedy accumulator in both `GeminiSource` and `ClaudeCodeSource`. Exchanges are packed into the current chunk until the token budget is reached; only oversized single exchanges are split. Chunk count dropped from 144 → 15 for the problematic session.

**Chunk size: 350 → 800 tokens.** The old 350-token ceiling was sized for single exchanges, not packed ones. 800 tokens matches common embedding model sweet spots for retrieval precision and gives GLiNER enough context per chunk. `nomic-embed-text` handles 8192 tokens so there's no embedding constraint.

---

## 2026-06-03: GLiNER sub-window batching

**Problem:** `_extract_gliner_layer` fed `chunk_text[:500]` to GLiNER — a hardcoded truncation from when chunks were 350 tokens. With 800-token chunks (~3000+ chars), the majority of each chunk was silently dropped. GLiNER's BERT encoder also has a hard 384 subword token (~1400 char) limit that issues truncation warnings but processes anyway.

**Fix:** Remove the Python-level truncation. Split chunk text into 1400-char windows and call `model.inference(windows, labels)` in a single batched call. Deduplicate entities across windows by canonical name. This gives GLiNER full chunk coverage with no silent data loss and amortizes model overhead across windows in one forward pass.

---

## 2026-06-03: Per-session occurrence threshold for analytics light

**Problem:** `--occurrence-threshold` counts total occurrence mentions across all sessions. A lightly-mentioned entity in many sessions accumulates the same count as a deeply-discussed entity in one session — the semantics are different and both can pass or fail the filter for the wrong reasons. For small corpora (1–5 sessions), most entities are single-session, so the cross-session total is effectively a per-session count anyway. But for larger corpora, a generic term mentioned twice per session across 10 sessions hits a threshold of 20 while a specific technical entity mentioned 15 times in one session does not.

**Fix:** Added `--session-occurrence-threshold N` (CLI) / `session_occurrence_threshold` (store + pipeline). Qualifies entities with MAX per-session occurrence count ≥ N — i.e., the entity was mentioned ≥N times in at least one session. Implemented as a subquery: `MAX(COUNT(*) GROUP BY session_id) >= N`. The existing `--occurrence-threshold` remains as a cross-session total floor and the two are AND-combined when both are set.

**Recommended usage:** `--threshold 0 --occurrence-threshold 1 --session-occurrence-threshold 7 --max-chunk-coverage 0.5` — disable the session-count and cross-session total gates, qualify by per-session density, exclude noise via chunk IDF.

---

## 2026-06-03: Session metadata — separate table over column extension

**Context:** Raw JSONL files contain rich per-session metadata not currently extracted: model name, permission mode, token usage (input/output/cache/thoughts), entrypoint (sdk-cli/ide/cli), duration, turn counts, git branch, is_sidechain. This data is valuable for quality analysis, cost tracking, and UI display.

**Decision:** Add a separate `session_metadata` table (FK → sessions, ON DELETE CASCADE) rather than extending the sessions table with more columns or stuffing into `raw_facets` JSON.

**Why not extend sessions columns:** The sessions table already has many columns; metadata is optional and source-specific (CC has permissionMode, Gemini has thought_tokens). A separate table keeps the sessions table stable and lets metadata schema evolve independently.

**Why not raw_facets JSON:** `json_extract()` everywhere is tedious and not indexed. A proper table lets you do `WHERE sm.permission_mode = 'bypassPermissions'` cleanly.

**Backfill strategy for existing sessions:** Re-parsing source files for metadata only (no entity extraction, no LLM) via `ats backfill-metadata` — fast, idempotent, no disruption to existing records/memories. Full re-ingest is also valid for small corpora.

See `design/session_metadata.md` for full field inventory, schema DDL, and implementation plan.

---

## 2026-06-03: Opencode ingestion — virtual paths for DB-backed sessions

**Context:** Opencode stores all sessions in a single SQLite database (`~/.local/share/opencode/opencode.db`) rather than individual files. The existing scanner/plugin model assumes one file per session (path → content hash → IngestionState → plugin.parse).

**Decision:** Use virtual paths of the form `{db_path}::{session_id}` (e.g. `/home/user/.local/share/opencode/opencode.db::ses_1abc...`) as the canonical path for each session. Content hash is computed as `sha256(session_id:time_updated)` so sessions are re-ingested if they change.

**Why not export to JSONL:** The opencode DB schema is stable and queryable directly. Exporting adds a sync step and a temporary file to manage. Reading from the DB directly via a read-only SQLite connection is simpler and has no data duplication.

**Why not treat the DB as a single "file":** The whole-DB content hash would mark every session as dirty whenever any session changes. Per-session virtual paths give correct granular change detection.

**Virtual path handling in CLI:** The `backfill-metadata` command previously did `Path(abs_path).exists()` to check if a source is still present. Updated to split on `::` and check only the DB file portion.

**Data model:** Opencode messages carry role (user/assistant), tokens, and model fields. Tool calls are in `part` rows of `type=tool` with `state.input` holding arguments. Files appear in `filePath` / `path` args; shell commands in `command` / `cmd`. All mapped to the same `RawEntityMention` types as Claude Code and Gemini.

---

## 2026-06-03: Infinite loop in _split_text / _split_long_exchange

**Bug:** Both `GeminiSource._split_text()` and `ClaudeCodeSource._split_long_exchange()` contained an infinite loop triggered when an oversized exchange's serialized text is shorter than `max_chars` (the split window size).

The loop:
```python
while start < len(text):
    end = min(start + max_chars, len(text))
    chunks.append(text[start:end])
    start = end - overlap  # BUG: when end == len(text), start resets to len(text) - overlap < len(text)
```

When `len(text) < max_chars`, `end` always equals `len(text)` and `start` always resets to `len(text) - overlap` — which is less than `len(text)` — so the loop condition `start < len(text)` is always true.

**Why it was masked in CC sessions:** CC `_serialize_exchange` produces very compact text (thinking blocks produce 0 chars; each tool_use/tool_result is ~30-100 chars). Most CC exchanges produce tiny serialized text that fits in the accumulator without triggering `_split_text`. Even large CC sessions with 100+ exchanges rarely hit the split path.

**What triggered it in the Gemini session:** A Gemini exchange with 28 gemini turns serialized to 5,470 chars — larger than `max_chunk_tokens=800` (triggering the split path) but smaller than `max_chars=6,150` (the split window), which is exactly the condition that causes the infinite loop.

**Fix:** Break immediately when `end == len(text)` — the full text was appended and there's nothing left to split.

```python
if end == len(text):
    break
start = end - overlap
```

**Investigation path:** The session printed "Ingesting session-X..." then hung silently with no further output — no GLiNER warnings, no error, no traceback. Narrowed to `_split_text` by instrumenting `_chunk_exchanges` step-by-step and observing the last printed exchange before silence. Confirmed infinite loop by inspecting the while-loop arithmetic for the specific text length.

---

## 2026-06-03: Idempotent analytics light re-runs

**Problem:** `ats analytics light` was not safe to re-run after new ingestion. `get_entities_for_promotion` correctly skips entities where `memory_promoted >= last_seen` (no new data), but entities with `last_seen > memory_promoted` (updated since last run) were re-processed with fresh `uuid4()` memory IDs. Because `write_analytics_batch` uses `INSERT OR REPLACE` keyed on ID, old memories were never replaced — they accumulated alongside the new ones.

**Fix:** Before re-processing any entity that was already promoted (`memory_promoted IS NOT NULL`), call `store.delete_entity_light_analytics(entity_id)` to atomically remove its existing frequency memories (+ vec0, FTS5, memory_sources) and same_entity edges. The subsequent write then produces a clean replacement. Entities with no new data continue to be skipped entirely.

**Behaviour summary:**
- Run with no new data → all entities skipped (unchanged)
- First-ever run → entities processed, `memory_promoted` set
- Re-run after new ingestion → stale output for affected entities deleted, then re-generated; unaffected entities skipped

`reset-light` still exists for wiping all analytics output to start completely fresh.

---

## 2026-06-03: Include thinking blocks in CC exchange serialization

**Context:** Claude Code JSONL stores extended thinking as separate `type=thinking` message blocks, each containing the model's chain-of-thought reasoning (`block.thinking` field). The original `_serialize_exchange` only handled `type=text` and `type=tool_use` blocks — thinking blocks fell through silently, producing zero chars in the serialized output.

**Decision:** Include thinking content in serialization: `[thinking]: block.thinking[:300]`

**Why:** Thinking blocks contain the model's reasoning about what it's doing, why it's choosing a particular approach, tradeoffs considered, and what it expects. This is high-signal content for:
- **Memory extraction** — the model often articulates intent and decisions in thinking that never appear in the final text response
- **Entity extraction** — concepts, files, and technologies referenced in thinking are as relevant as those in responses
- **Retrieval** — "why did the agent do X" queries are more likely to match thinking content than tool calls or responses

**Why 300-char cap per thinking block:** Thinking can be very long (1000+ chars). The full content would inflate chunk sizes significantly. 300 chars captures the opening reasoning (usually the most salient part) without dominating the exchange text. The turn cap (5 head + 3 tail) further bounds total thinking content per exchange.

**What changed:** Sessions re-ingested after this change will have richer chunk text and potentially more diverse entity extraction from thinking content. Previously ingested sessions retain their current representation.

---

## 2026-06-04: sessions_fts — proper BM25 for session search

**Problem:** `session_search()` used a hand-rolled term counter (count query words in summary, divide by summary length). This had two issues: (1) common words like "ai", "the", "in" matched broadly across all sessions, drowning out specific signals; (2) workspace_id was not searched at all, so "coral ai" couldn't find the coral-ai workspace session.

**Fix:** Add `sessions_fts` FTS5 virtual table (porter stemmer, same pattern as `records_fts` and `memories_fts`) indexing `session_summary` and `workspace_id`. Add `store.search_sessions_bm25()`. Replace the term counter in `session_search()` with the BM25 call.

**Why workspace_id in the FTS table:** Workspace names are the most specific signal for project-scoped queries ("what did we work on in coral ai?"). Including it in the FTS index means "coral ai" surfaces `-Users-you-src-coral-ai` via porter stemming even though the path contains hyphens — FTS5 tokenizes on word boundaries after the hyphens-as-spaces tokenization.

**Backfill:** `migrate()` creates `sessions_fts` and bulk-inserts all existing sessions when the table is missing. New sessions are inserted at ingest time in `write_session_batch()`.

**Remaining limitation:** Lexical search still can't do intent-based queries ("what were the main things we worked on"). Semantic search (avg-chunk embeddings) handles those — requires `ats embed` to have run.

---

## 2026-06-04: Always route all search methods through RRF for consistent scoring

**Problem:** The three `--method` options (hybrid/semantic/lexical) for `recall`, `chunk_search`, and `session_search` produced scores on incompatible scales:
- `lexical`: raw negative FTS5 rank (e.g. -2.34) — more negative = better
- `semantic`: cosine similarity (0.0–1.0) — higher = better
- `hybrid`: RRF score (0.016–0.033) — higher = better

This made cross-method comparison meaningless and the displayed scores confusing.

**Fix:** All three methods always pass through `_rrf()`. For `lexical`, the semantic list is empty; for `semantic`, the BM25 list is empty. RRF of a single ranked list produces consistent `1/(k+rank)` scores (e.g. rank 1 = 0.0164, rank 2 = 0.0161). The score scale is now identical regardless of which legs are active.

**Why this works:** `_rrf()` only uses **rank position** (enumerate index), never the raw score values. Feeding it a single list is mathematically equivalent to a degenerate fusion where one leg contributes nothing — the output scores are still valid and comparable.

---

## 2026-06-06 — opencode plugin: three ingestion bugs fixed

Three bugs in `plugins/opencode.py` caused sessions to stay `pending` forever with no error output.

**Bug 1: `_split_text` infinite loop.** When an oversized exchange serializes to text shorter than `max_chars` (6150 chars), `end` always equals `len(text)` and `start = end - overlap` resets to the same value every iteration. Fixed with `if end >= len(text): break`. The Gemini plugin had this right already; opencode did not. Triggered here by an exchange with 71 user/assistant turns serializing to ~15 KB of text — over the `max_chunk_tokens` split threshold but under `max_chars`, the exact condition that causes the loop.

**Bug 2: `sqlite3.connect(uri=True, mode=ro)` hangs under WAL with active writer.** The opencode plugin opened the DB with `file:{db_path}?mode=ro` URI mode. When the target session is open in a running opencode process, this form can't acquire the WAL shared-memory lock and blocks indefinitely. The scanner opens the same DB without URI mode and works fine. Fixed by using `sqlite3.connect(db_path, timeout=10.0)`.

**Bug 3: silent error swallowing → `mark_ingested` on empty sessions.** `parse()` caught all exceptions and returned an empty `ParsedSession`. `pipeline.run()` returned a placeholder `Session` silently when no chunks were produced. The CLI then called `scanner.mark_ingested()` on the empty result, so the session was recorded as ingested with nothing written — and never retried because `status='ingested'` suppresses re-queuing. Fixed by raising in both `parse()` and `pipeline.run()` (no-chunk path), so the CLI catches and calls `mark_failed()` with a real message, and the scanner re-queues on the next run.

**Key finding:** re-ingestion of active (open) opencode sessions works — WAL mode allows concurrent reads. The actual hang was Bug 1, not a locking issue.

---

## 2026-06-06 — Confidence field retired

The `confidence` column on the `entities` table (`raw` / `llm_verified` / `session` / `corpus`) was designed for the original four-layer extraction pipeline (regex → GLiNER → LLM verifier → session promotion). Each tier tracked how much validation evidence an entity had survived.

The D-prompt redesign (2026-06-05) removed all three of those layers. D entities are inline-validated by the same LLM call that extracts them, with a `role` field that is more informative than a confidence tier. Cross-session quality is tracked by light analytics promotion (degree/occurrence thresholds), not a column on the entity row.

**Status:** column remains in schema for backward compatibility but carries no meaningful signal for D-prompt-ingested sessions. Can be dropped in a future schema cleanup. `_compute_entity_confidence()` and its callers can be removed at the same time.

---

## 2026-06-06 — records_fts: chunk_summary added to BM25 index

### Background

`records_fts` indexed only `chunk_text` (the raw conversation text). The `chunk_summary` column — an LLM-generated summary per chunk — was stored in the `records` table but not included in the FTS5 virtual table, so it was invisible to lexical (`--method lexical`) and hybrid search.

### Decision

Add `chunk_summary` as a second indexed column in `records_fts`. FTS5 searches all indexed columns by default when no column filter is specified, so no query-side changes are needed — the MATCH expression already covers both.

FTS5 virtual tables do not support `ALTER TABLE ADD COLUMN`, so the migration drops and rebuilds `records_fts` and backfills from the `records` table. The migration runs automatically at DB open time via `migrate()`, so no manual step is required — just finish any in-progress ingestion session before restarting the app.

Also fixed a latent guard bug: the FTS insert was gated on `if r.chunk_summary`, which silently skipped records that had `chunk_text` but no summary. Guard is now `if r.chunk_text or r.chunk_summary`.

---

## 2026-06-05 — Ingestion pipeline redesign: combined D-prompt replaces regex+GLiNER+verifier+explicit memory

### Background

The original Pipeline 1 (ingestion) made approximately **2.2N + 1 LLM calls** per session of N chunks:
- N chunk summarizer calls
- ceil(N/5) LLM verifier calls (batched over regex+GLiNER candidates)
- N explicit memory extraction calls
- 1 session summary call

It also loaded GLiNER (400MB model, ~2.2GB cached) on every ingest run.

### Problem discovered through experiments (EXP01–EXP05)

Five experiments were run over chunks from two sessions (SLR-gemma4 and agent-trace-signals) comparing pipeline output quality against real recall use cases.

**EXP01 — baseline quality audit:**
- Entity lists dominated by noise: tool names (Bash, Read, Edit), full file paths duplicated at multiple path granularities, path fragments (`nals`, `nguo`, `r_agent`, `kpointBroker`), commit hashes classified as technology.
- Explicit memories were verbose and trivial before prompt fix: "n_retrieved is 170", "The user responded with '1-3 yes, no 4'", "A Bash command was used to grep for lines containing...".
- Session summarizer degraded badly above ~30 chunks — produced meta-commentary ("here is a breakdown of key themes") instead of a coherent summary for 104+ chunk sessions.
- Chunk summaries were stable across runs but optimized for narrative prose, not structured knowledge extraction.

**EXP02 — chunk summarizer prompt variants:**
- Tested four variants (current, A entity-anchored, B decision-focused, C structured, D combined JSON).
- **Variant B** ("what problem, what decision, why, what changed") consistently named systems and explained the why without noise.
- **Variant D** (combined prompt producing `{summary, entities, memories}` in one JSON call) produced the best per-chunk structured output. D correctly returned no memories for low-signal chunks; B produced three weak memories for the same chunk including "`orchestrator.py` is a file."
- D entity extraction had some file path leakage but its concept entities (search_sources, date_range, cfg, Stage 1 gate) captured real domain knowledge that NER entirely missed.

**EXP03 — NER on B summaries vs D combined vs E cross-chunk unified:**
- NER on B summaries dramatically reduced entity noise: zero garbage entities, 4–5 high-quality entities per chunk (when verifier worked).
- LLM verifier (`gemma4:e4b`) failed reliably on batched multi-summary prompts — returned empty response. Works per-chunk only.
- **E cross-chunk unified** (one call over all 5 B summaries) produced the best memory set: 5 memories for the whole window, zero repetition, each capturing the right abstraction level. Directly answered "what did I work on?" better than any per-chunk approach.
- E missed one gap (Stage 3 screening criteria) that D per-chunk caught. One miss across 5 chunks.

**EXP04 — D per-chunk → E synthesis over D outputs:**
- E synthesis over structured D JSON outputs failed to synthesize — it passed through per-chunk D memories unchanged. Every memory tagged `[chunks [0]]`, `[chunks [1]]` etc.
- When E gets prose summaries (B) it must actively construct memories (synthesis happens). When it gets pre-extracted memories it takes the path of least resistance (pass-through).
- **Finding:** D→E does not improve over B→E for memory synthesis. D per-chunk outputs are valuable in isolation but not as E synthesis input.

**EXP05 — D vs stored pipeline on real session (1bae6787, 29 chunks, 149 entities, 366 memories):**
- D produced 2–7 entities per chunk with types and roles, zero noise. Stored had 6–41 per chunk dominated by tool names, full/partial file paths, garbage tokens.
- D memories were actionable and self-contained: exact CLI commands, named architectural decisions, procedural patterns. Stored memories ranged from useful fragments to pure noise ("Task #8 status was updated", "The Uvicorn server started on 0.0.0.0:8501").
- Summaries: D consistently named the *why*; stored summarizer described *what happened* without capturing decisions or rationale.
- One genuine D weakness: multi-fact findings (e.g. the num_predict/eval_count breakdown had 7 individual facts) get compressed into one procedural memory, losing granularity. Prompt tuning opportunity, not structural.

### Decision

**Replace the four separate pipeline steps (chunk summarizer + regex + GLiNER + verifier + explicit memory extractor) with a single D-prompt call per chunk.**

The D prompt produces `{summary, entities: [{name, type, role}], memories: [{type, content}]}` in one LLM call. The entity `role` field replaces chunk-window `context_text` for occurrence rows, which is actually *more* informative than `chunk_text[:500]` for light analytics.

**Session summary uses hierarchical reduction:** chunk D summaries → group summaries (every 10 chunks via B-style prompt) → session summary. Fixes the >30 chunk degradation.

**LLM call count:**
- Old: ~2.2N + 1
- New: N + ceil(N/10) + 1 ≈ 1.1N + 1
- 123-chunk session: 272 → 137 calls (~50% reduction)

**GLiNER is no longer loaded** by default. Available via `--legacy-pipeline` flag.

### What was kept

- Entity resolution (Levenshtein fuzzy match for person/org, hash-based for structured types) — unchanged, now applied to D entity strings.
- Occurrence extraction — unchanged, now uses D entity `role` as `context_text`.
- Light analytics pipeline — unchanged, now receives higher-quality entity context.
- `--legacy-pipeline` flag preserves the full old path for comparison or rollback.

### What was removed from default path

- GLiNER (400MB model dependency)
- Regex entity extraction layer
- LLM verifier step
- Separate chunk summarizer step
- Separate explicit memory extractor step

### Archive behavior added

`CorpusScanner` now copies source files to `data/archive/<plugin_type>/` on first discovery. `ingestion_state.abs_path` points to the archive copy. Prevents session loss when Claude Code auto-purges `~/.claude/projects/`.

---

## 2026-06-13 — FP suppression: human_tool_ratio gate + stub chunk filter

**Motivation:** Cohort-1 labeling (precision=0.33) identified two FP sources: (1) discussion/design sessions with near-zero tool activity where the observer's progress dimensions don't apply, causing every exchange to fire `failure_critical`; (2) near-empty exchanges (stubs) with trivial evidence text that score 0.0 progress and 0.0 cost, also trivially tripping the `absolute_low` rule. Embedding EDA (2026-06-12) provided quantitative grounding for the session-level filter.

**Change 1: `human_tool_ratio` session gate (replaces `min_tool_using_exchanges`)**

`CriticalStepConfig.max_human_tool_ratio = 8.0` (default). Computed from `sessions.turn_descriptors` as `genuine_human_turns / n_tool_calls`. If `n_tool_calls == 0` or ratio > threshold, skip detection for the whole session.

Why ratio beats raw count:
- `min_tool_using_exchanges=5` failed on cc:18 (lexical search, 7 tool calls, real implementation) while a design session with 8 scattered file-reads would pass.
- Embedding EDA showed `human_tool_ratio` has RF importance 0.20 (top feature) and r=0.75 with the UMAP autonomy axis — it's the strongest single separating signal between implementation and conversation sessions.
- Calibration from observed cluster extremes: design conversations ~15-20, implementation sessions ~0.5-3.0, long autonomous coding ~0.15-0.5. Threshold 8.0 sits well between.

**Change 2: stub chunk filter in step-scoring**

`ModelConfig.min_evidence_chars = 50` (default). In `StepScoringPipeline.score_session`, exchanges where `len(evidence_text) + len(user_text) < 50` are silently skipped before calling the observer. Chunk-level embedding EDA found cluster 0 (mean=21 chars) reliably identifies near-empty chunks that cannot carry meaningful progress signal.

Both changes are disabled in unit tests (`max_human_tool_ratio=None`, synthetic fixtures exercise edge cases independently of production gates).

---

## 2026-06-13 — Session-level embedding: two representations

`ats embed` already produces session-level embeddings stored in `session_embeddings` (vec table). The text embedded is `sessions.embedding_text = session_summary` — a LLM-generated prose summary capturing semantic topic.

The EDA experiment used a different representation: raw head (4k chars) + tail (2k chars) of the session file. This captures harness structure (tool call patterns, scaffolding syntax) that the summary strips out. The UMAP x-axis (tool_call_rate, r=0.61) and y-axis (human_tool_ratio, r=0.75) are both recoverable from the raw-text embeddings but not from summary embeddings.

**Decision: keep both representations orthogonal.** Summary embeddings (current `ats embed`) serve semantic retrieval and memory recall ranking. Raw-trace embeddings (from EDA scripts, not yet in the pipeline) would serve structural session classification and eval stratification. Adding raw-trace session embeddings to the pipeline is future work — the EDA scripts in `experiments/` are the proof-of-concept.

---

## 2026-06-12 — Embedding signals exploration on raw traces

**Experiment:** `experiments/embed_cluster_eda.py`, `embed_raw_traces.py`, `harness_features_eda.py`  
**Full findings:** `experiments/EXP_EMBEDDING_SIGNALS.md`  
**Model:** `qwen3-embedding:0.6b` via Ollama  

Embedded 677 pre-processed chunks and 66 raw archive sessions, clustered with HDBSCAN on UMAP coords, then analyzed whether clusters are driven by topic content or agent harness structure (tool calls, human turns, etc.).

**Key findings:**

1. **Topic drives cluster identity, but harness structure co-varies significantly.** Every structural feature (tool calls, human turns, tool diversity) predicts cluster membership with p < 0.0001 (Kruskal-Wallis).

2. **The UMAP axes encode orthogonal signals:**
   - x-axis: `tool_call_rate` (r = +0.61) — how tool-heavy the session is
   - y-axis: `human_tool_ratio` (r = +0.75) — how human-directed vs autonomous

3. **`human_tool_ratio` is the strongest single predictor of cluster** (RF importance 0.199), ahead of raw tool count. The balance between human direction and autonomous action matters more than volume alone.

4. **Chunk-level clustering reliably surfaces near-empty chunks** (cluster 0, mean 21 chars) — feeds back into ingestion as a filter signal.

5. **ATS sessions split into 3 sub-clusters by phase** (pipeline coding / eval work / design conversations), each separable by both topic and harness structure. Conversation-only sessions (zero tool calls) form a clean cluster distinct from coding sessions.

**Implication for eval stratification:** when sampling sessions for labeling, stratify by `human_tool_ratio` and `tool_call_rate` buckets, not just project/topic, to get harness-diverse coverage.

---

## 2026-06-05 — D-prompt entity types: configurable + normalized to shared vocabulary

**Problem:** D-prompt entity types were hardcoded as a string in the prompt (`technology | framework | algorithm | concept | model | dataset | person | project`). Three issues:
1. Not configurable without editing source
2. No validation — model could return any string, stored as-is
3. Type mismatch with legacy pipeline (`framework`, `algorithm`, `project` are D-only; `tool`, `org`, `file`, `commit`, `pr` are legacy-only) — different vocabulary in the DB for the same pipeline

**Fix:**
- `PipelineConfig.d_entity_types` — tuple of type labels, same pattern as `ner_entity_labels`. Change without touching code.
- Prompt reads from config dynamically.
- `D_TYPE_NORMALIZATION` map folds D-specific types into the shared entity vocabulary before storage: `framework→technology`, `algorithm→concept`, `project→technology`.
- Any unrecognized type falls back to `concept`.
- `_SHARED_ENTITY_VOCAB` defines the canonical set both paths write to.

**Why project→technology:** Repos and systems (SLR-gemma4, agent-trace-signals) are effectively technology entities in the context of retrieval. The alternative was adding `project` to the shared vocab, but that would require schema and resolver changes for no retrieval benefit.

---

## 2026-06-05 — Entity type descriptions included in D-prompt

**Problem:** `d_entity_types` stored type labels as a tuple of strings. The prompt rendered them as `technology | framework | algorithm | ...` with no explanation of what each type covers. The LLM had to guess intent, producing inconsistent classifications (e.g. "LangGraph" classified as `technology` in one chunk, `framework` in another; `SqliteSaver` as `concept` rather than `technology`).

**Fix:** Changed `d_entity_types` from `tuple[str, ...]` to `dict[str, str]` (type → description). Both label and description are injected into the prompt:

```
Types (use the most specific match):
technology: libraries, tools, CLIs, APIs, languages, platforms, services
framework:  orchestration and workflow libraries (→ stored as technology)
...
```

The `→ stored as X` annotations in descriptions also communicate normalization intent to the model, reducing attempts to invent new type labels.

**Why descriptions matter:** Classification consistency directly affects entity resolution. If the same entity is typed `technology` in session A and `framework` in session B, the hash-based resolver creates two separate entity rows instead of one. More consistent typing = fewer spurious duplicate entities = better light analytics cross-session synthesis.

---

## 2026-06-05 — D-prompt chunk analysis: parallel execution + progress logging

**Problem:** `DChunkAnalyzer.analyze_batch` was a sequential loop — one blocking Ollama HTTP call per chunk (read timeout: 300s). A session with 10+ chunks could take tens of minutes with no console output, indistinguishable from a hang. The legacy pipeline already used `ThreadPoolExecutor` for chunk processing; the D-prompt path did not.

**Fix:**
- `analyze_batch` now accepts `max_workers` (default 4) and dispatches chunks via `ThreadPoolExecutor`, collecting results back in original order via index.
- `IngestionPipeline._run_d_prompt_path` passes `self.max_workers` through so the pipeline-level cap is respected.
- Added `logger.info` progress lines: `"D-prompt analysis: N chunk(s) to process"` at start and `"chunk X/N complete"` as each finishes — makes slow Ollama responses visible and distinguishes normal slowness from a real hang.

**Why not async:** Ollama calls are synchronous `httpx` requests; switching to async would require changing the provider interface across all callers. Thread parallelism is sufficient and keeps the provider API stable.

**Why max_workers=4:** Matches the existing `self.max_workers = 4` cap in `IngestionPipeline`. Ollama runs one inference at a time by default, so additional threads mostly help overlap network/serialization latency rather than true parallelism. Raising this without a concurrent-capable backend would queue requests and add overhead.

**Tests added:** `tests/test_opencode_plugin.py` — 14 unit tests covering `split_virtual_path`, `can_handle`, full parse round-trips against a real in-memory SQLite DB, and `analyze_batch` order preservation with a mocked provider (no Ollama required).

---

## 2026-06-05 — Phase 0 findings: four inflection context gaps fixed

### Background

Phase 0 investigation (`notes/phase0_findings.md`) re-parsed source files for the top-5 inflections to verify that exchange-level context could be recovered and used as ground truth. The approach works cleanly. The investigation also exposed four schema/persistence gaps that needed fixing before the export pipeline could be built.

### GAP-1: span_start/span_end always NULL on records

**Root cause:** `_chunk_exchanges` in both `claude_code.py` and `opencode.py` packed exchanges into chunks without recording which exchange indices went into each chunk.

**Fix:** Track `acc_start` (first exchange index in the accumulator) and pass `end_exchange_idx` to `_flush_acc`. Each `RawChunk` now gets `span_start=str(first_exchange_idx)` and `span_end=str(last_exchange_idx)`. Oversized single-exchange chunks get `span_start == span_end == str(exchange_idx)`.

**Impact on UI:** `nearest_chunk()` in the inflections tab now does an exact span lookup first (`span_start <= turn_idx <= span_end`) before falling back to nearest-by-index walk. This eliminates false same-chunk collisions that made precipitating/entry context look identical.

### GAP-2: turn_descriptors discarded after inflection detection

**Root cause:** Turn descriptors were computed by the plugin and passed through `parsed_session.metadata["turn_descriptors"]` to inflection detection, then dropped. Not persisted.

**Fix:** After inflection detection in ingestion step 12, serialize `raw_tds` as JSON and `UPDATE sessions SET turn_descriptors = ?`. Schema migration adds the `turn_descriptors TEXT` column if missing.

**Value:** Export pipeline can reconstruct what tool calls occurred at any exchange index without re-parsing source files.

### GAP-3: sessions.raw_facets always {}

**Root cause:** `parsed_session.raw_facets` (set by plugin with title, directory, model_id, etc.) and plugin metadata (`cwd`, `git_branch`, `cc_version`, `away_summary_latest`) were never forwarded to the `Session` object at creation time.

**Fix:** After Session creation in ingestion, populate `session.raw_facets` from `parsed_session.raw_facets` + extracted metadata fields (`cwd`, `git_branch`, `cc_version`, `away_summary`), filter None values, serialize as JSON.

### GAP-4: Context-continuation messages look like corrections (export filter)

**Pattern:** When Claude Code runs out of context and is resumed, the injected summary appears as a `user` message at `entry_turn + 1`. Beginning: "This session is being continued from a previous conversation..."

**Not an ingestion fix.** Documented as a comment near inflection detection. Detection at export time: check if `exchanges[entry_turn + 1]` user content starts with "This session is being continued".

### Pipeline compatibility

All four gaps operate at the plugin/parse layer or at post-extraction steps 11–12 — **both before and after the D-prompt/legacy pipeline split**. GAP-1 fires in `_chunk_exchanges` (plugin, pre-split). GAP-2/3 fire after both paths converge (step 11–12). GAP-4 is export-only. No D-prompt path changes were needed.

### Backfill for already-ingested sessions

Sessions ingested before this fix have NULL `turn_descriptors`, empty `raw_facets`, and NULL `span_start/span_end`. Run `uv run python scripts/backfill_gaps.py` to re-parse source files and patch the missing columns in-place without touching entities/memories/occurrences.

---

## 2026-06-07 — Inflection detector evaluation: structural features insufficient

**What changed:** manual labeling of 19 inflection candidates found ~95% false positive
rate. The one true positive was the chess bughouse escalation (Gemini CLI, error_rate > 0.5).

**Root cause:** harness-injected exchanges — skill invocations, `<task-notification>`
messages, ScheduleWakeup loops, and context-continuation summaries — produce systematic
z-score deviations that are indistinguishable from genuine failures using structural
features alone. These injections contaminate the session's running mean, making the
baseline unreliable.

**Additional finding from cross-harness analysis:**
- `agent_thrash` and `loop_entry` via entropy=0 fire reliably on harness injections
  across all three harnesses (CC, Gemini, Opencode)
- `strategy_pivot` and `user_correction` are too noisy without semantic grounding;
  disabled pending exchange classifier
- `model_turn_count > 2.0` absolute threshold is meaningless for Gemini SDE sessions
  (10–27 turns per exchange is normal); must use z-score only
- Gemini text-only research sessions are completely opaque to structural features;
  thought subjects and keyword density in user messages are the available signals

**Next step:** exchange classifier pre-filter (`exchange_type` field on TurnDescriptor)
to exclude harness-injected exchanges before z-score computation. Design in
`notes/exchange_classifier_design.md`.

---

## 2026-06-07 — Failure keyword density — independent analytics step

**What:** `ats analytics signals` computes `failure_keyword_density` (raw keyword count)
per chunk from `records.chunk_text`. No LLM, no embeddings. Stores result in a new
`failure_keyword_density INTEGER` column on `records` (added via `ALTER TABLE` if absent).

**Why independent:** depends only on `records.chunk_text` (set at ingest). No dependency
on `analytics light` (which reads entities/occurrences) or `embed` (which reads
profiles/memories). Can run in any order after ingest — including before `analytics light`.

**Empirical validation on chess session (Gemini CLI, known genuine failure):**
- Baseline chunks 0–2: density 0; peak chunk 16: density 38 (z=+2.8)
- Keyword signal leads structural inflection detector by ~4 chunks — **leading indicator**
- Cross-session: chess peak (38) is 3–4× the next highest session (23), detectable
  as an outlier even without z-scoring

**Per-harness keyword sets:** universal base (user frustration + agent self-correction
phrases) extended with CC-specific, Gemini-specific, and Opencode-specific error tokens.
Plugin identified via `sessions.source_plugin`.

**Gemini thought signal (planned extension):** Gemini turns expose `thoughts` blocks
with structured `{subject, description}` dicts. Subject keyword matching provides a
semantic failure signal for text-only Gemini sessions where no tool calls exist.
Currently in `failure_signals.py` as `thought_failure_density_gemini()`; not yet
wired into the ingestion pipeline.

**UI:** Chunks tab (Home.py) shows per-chunk density bar chart with z-score colour
coding on expander labels. Compare tab (4_Compare.py) adds session-level KD metrics,
per-session density timeline charts, and an aggregate section with distribution
histograms, box plots, and scatter plots grouped by harness or model.

---

## 2026-07-11 — Cleanup: deprecate Track 2 CLI, analytics light, graph walk

**What changed:**

- **CLI deprecation stubs**: `ats analytics signals`, `step-scoring`, `critical-steps`, `light`, `reset-light` now print deprecation messages and exit. Track 2 (per-exchange observer scoring, `step_scores`/`critical_steps` tables) never produced real signal — cohort 1 precision was 0.33 and the tables have 0 rows. `analytics signals` (keyword density) was discouraged because hard-coded keyword lists are fragile and incomplete. `analytics light` is superseded by D-prompt + ClusteringAnalyticsPipeline.

- **`store.py` gutted**: `reset_light_analytics()` and `delete_entity_light_analytics()` replaced with `NotImplementedError` stubs pointing to `ats cluster-sessions` / `ats cluster-memories`.

- **Graph Walk → Related Sessions**: The old Graph Walk UI page used a memory-seed BFS walk, which was confusing and not useful for the actual corpus. Replaced with `3_Related_Sessions.py`: session-first entry point showing all sessions that share structural entities (file/commit/PR) with the selected session. Uses `session_graph_edges` directly — no traversal, just a one-hop join per selected session.

- **`ats graph-walk` CLI rewritten**: Now takes a session prefix and shows connected sessions via structural edges, not memory-seed BFS.

- **Docs updated**: README pipeline order, usage examples, MCP tool table, project structure. `design_decisions.md` (this entry).

**Why:**

Track 2 consumed significant design and implementation effort (6 stages) but the LLM-based observer scoring was not reliable at the per-exchange granularity. Keyword density (analytics signals) is similarly brittle. The D-prompt already extracts `pattern_recovery` and `pattern_inefficiency` memories semantically — these are the right failure signal, surfaced through the Home page session detail and the sessions table `Recovery` column. The analytics light pipeline was the original entity-frequency memory promotion approach, fully superseded by D-prompt extraction.

The Graph Walk page had a usability problem: you had to know a seed memory ID to start, and the BFS traversal of the memory graph was not meaningful for the corpus (most edges are session↔session structural edges, not memory↔memory edges). Session-first Related Sessions is directly actionable.

---

## 2026-07-01 — Memory analytics v2 architecture

**What changed:** Major architecture pivot in four phases:
1. D-prompt extended with `patterns[]` field (strategy/decision/recovery/inefficiency types)
2. Session structural embedding (raw head+tail) stored at ingest time; HDBSCAN session_type tagging
3. `light_analytics.py` replaced by `ClusteringAnalyticsPipeline` — embedding-similarity clustering of memory+pattern rows, LLM consolidation of recurring clusters
4. Legacy pipeline path, entity_extractor, memory_extractor deleted; Track 2 soft-deprecated

**Why:** Entity-frequency promotion required entity name normalization (fragile); patterns (which have no entity anchor) couldn't participate. Embedding-similarity clustering handles both memories and patterns uniformly and is more robust to name variation. Track 2 per-exchange observer scoring had precision=0.33 (cohort 1) and never reached the ≥0.80 ship-gate.

**Full spec:** `design/2026-07-01-memory-analytics-v2-design.md`

---

## 2026-07-11 — Related Sessions moved into Home session detail

**What changed:** The standalone `3_Related_Sessions.py` page was deleted. Connected sessions (those sharing structural entities via `session_graph_edges`) are now shown as a "Related Sessions" tab within the Home page session detail section.

**Why:** Related Sessions is a facet of session detail, not a top-level destination. A user reviewing a session should find connected sessions in the same place as chunks, entities, and metrics — not on a separate page they'd need to navigate to. Removing the page also simplifies the nav from five pages to four.

---

## 2026-07-13 — Scanner: pick up archive-only orphan sessions

**What changed:** New `CorpusScanner._scan_archive_files()` walks `data/archive/claude_code/` and `data/archive/gemini_cli/` directly, in addition to the existing live-path scans (`~/.claude/projects`, `~/.gemini/tmp`).

**Why:** `data/archive/` was write-only — the live scanners copy each discovered file there on first sight (so sessions survive Claude Code/Gemini pruning their own history) and rewrite the tracked `abs_path` to the archive copy, but the scanner never read `archive_dir` as an input. Any file that existed *only* in the archive (its live source since deleted, or arrived from another machine and never live here) was invisible to `ats ingest` — found 116 such files on this corpus, including entire dead projects.

**Safety:** verified `session_id` is derived from `(plugin, abs_path, content_hash)` and `abs_path` gets rewritten to the archive path at first discovery — so a file with both a live source and an archive copy converges on the same `session_id` either way, and `ingestion.py`'s `session_exists()` check skips it cleanly (no duplicate LLM work). Hit one real bug during rollout: `ingestion_state.abs_path` is `UNIQUE`, but `file_id` (the lookup key) differs between a live-path discovery and a direct archive-path discovery of the same file — inserting the second one raised `IntegrityError`. Fixed by adding `get_ingestion_state_by_path()` and checking it before processing each archive-scanned file, skipping anything already tracked under any `file_id`.

---

## 2026-07-13 — `embed_cmd` backfill loops silently lost their writes

**What changed:** The structural- and topic-embedding backfill loops in `embed_cmd` (`cli.py`) now wrap their writes in `with store.conn:`.

**Root cause:** `_vec_upsert()` (`store.py`) never calls `commit()` — it relies on the caller to wrap it in a transaction. `write_embeddings()` does this correctly (`with self.conn:`), so records/entities/memories/sessions embeddings always persisted. But the two backfill loops immediately below it (structural embeddings, topic embeddings) called `store.upsert_structural_embedding()` / `store.upsert_topic_embedding()` directly, with no transaction wrapper. The process printed "Done." and exited cleanly every time, but every write in those two loops was silently rolled back on connection close.

**How this was found:** live-reproduced the exact backfill logic outside `embed_cmd`, which also "succeeded," then a fresh count still showed 0 topic embeddings. Confirmed root cause is the missing `with store.conn:`; wrapping it and re-running persisted all 52 rows correctly.

**Impact:** every `ats embed` run before this fix silently produced zero `session_topic_embeddings` (and would have produced zero structural-embedding backfills too, on any DB where ingest-time structural embeddings were missing) despite reporting success. Existing DBs need one `ats embed` re-run after the fix to backfill what was lost.

---

## 2026-07-13 — Session graph edges: two independent bugs fixed + deterministic workspace edges added

**Problem reported:** Related Sessions was empty for the majority of sessions checked, despite sessions clearly sharing a project/codebase.

**Bug 1 — edges were one-directional despite `direction='undirected'`.** Edge creation only ever inserted `(session-being-ingested → other-session)`. Every UI/CLI query filters `WHERE source_session_id = ?`, so a session only saw its connections if it happened to be ingested *after* the sessions it connects to. All 390 pre-existing edges were one-directional. Fixed: `ingestion.py`'s edge-creation now inserts both `(A→B)` and `(B→A)` for every edge found. Backfilled 390 missing reverse rows on the existing corpus.

**Bug 2 — the only edge mechanism required an LLM-classified entity, when the underlying fact (same project) is already deterministic.** `session_graph_edges` only formed from entities typed `file`/`commit`/`pr` (`structural_entity_types` in `config.py`). Two compounding gaps: (a) the D-prompt's own type vocabulary (`d_entity_types`) never offered `"file"` as a classification option — only technology/framework/algorithm/concept/model/dataset/person/project — so files mentioned in prose (not as a structured tool-call argument) got misclassified as `technology` and were invisible to edge creation; (b) `project`-type entities normalize to `technology` too (`D_TYPE_NORMALIZATION`), so even a correctly-classified "project" entity never triggered an edge either, since `technology` isn't in `structural_entity_types`.

Rather than widen `structural_entity_types` to include `technology` (which would reintroduce LLM-dependence and add noise — unrelated projects sharing a library like "Ollama" would spuriously link), added a new **deterministic edge type**: `edge_type="workspace"`, created directly from `sessions.workspace_id` equality (normalized via `format_workspace()`, moved from `ui/common.py` to `pipeline/utils.py` as the shared source of truth, since different harnesses encode the same project differently — e.g. Claude Code's `-Users-x-src-foo` vs. opencode's bare `foo`). `via_entity_id=''` for these edges (schema already supported this — composite PK includes `via_entity_id`, and the Pydantic model already had `direction` as a field, suggesting non-entity edges were an intended-but-unbuilt case). Also added `"file"` as an explicit D-prompt category for future ingests, since prose-mentioned files should still resolve to structural edges too.

**Backfill:** computed workspace edges for the whole existing corpus. Coverage went from 35/128 sessions (27%) with any connection to 120/128 (94%).

**UI/CLI fixes required alongside this:** both Home.py's Related Sessions tab and the `ats graph-walk` CLI command did `JOIN entities e ON e.id = sge.via_entity_id` (INNER JOIN) — workspace edges have `via_entity_id=''`, matching no entity row, so an inner join silently dropped every workspace edge from the results. Changed both to `LEFT JOIN`, and both now label each connection as "same project" (workspace edge) vs. `entity_name (entity_type)` (structural edge).

---

## 2026-07-13 — Session-type clustering collapsed to one mega-cluster: `eom` → `leaf`

**What changed:** `tag_session_types()` (`clustering_analytics.py`) now uses `cluster_selection_method="leaf"` instead of HDBSCAN's default `"eom"` (Excess of Mass).

**Root cause:** `eom` strongly prefers fewer, larger, more "stable" clusters. On this corpus it collapsed 87/128 sessions (all substantial multi-chunk work, regardless of actual project or content) into a single cluster, leaving only small clusters of near-identical trivial 1-chunk sessions distinguishable from each other.

**Empirically tested before changing:** normalization (embeddings were already unit-normalized — not the issue), UMAP dimensionality reduction + `eom` (still one 80-session dominant cluster), UMAP + `leaf` (16 clusters / 27 noise — finer but adds a UMAP dependency to a step that didn't need one). Plain `leaf` on the existing raw embeddings, no other changes, gave 11 clusters (sizes 3–16) + 49 noise — the best signal-to-simplicity tradeoff. Re-running post-fix showed clusters that are now genuinely interpretable, e.g. one 10-session cluster of large tool-heavy sessions (avg 16.8 chunks / 426 tool calls) distinct from a 4-session cluster of even larger ones (avg 25.5 chunks / 660 tool calls) — previously both were indistinguishable inside the single mega-cluster.

---

## 2026-07-13 — Memory clustering: evidence_count bug, conflated thresholds, and membership tracking

Three related fixes to `consolidate_memories()` (`clustering_analytics.py`), found while investigating why "top memories by evidence" on the Memories page looked arbitrary.

**Bug 1 — `evidence_count` was always 1.** The consolidation loop computed `distinct_sessions` per cluster (for the recurrence gate) but never wrote it to the memory row — the INSERT hardcoded no `evidence_count` value, so every one of the 132 consolidated memories defaulted to 1. Sorting "top by evidence" was therefore sorting on a constant. Fixed the INSERT to set `evidence_count = distinct_sessions`, and backfilled existing rows from their actual `memory_sources` counts (real range: 1–16 sessions).

**Bug 2 — `min_cluster_size` (HDBSCAN's density parameter, i.e. minimum *memories*) and `min_recurrence` (minimum *distinct sessions* before minting) were the same config value, defaulting to 3.** Quantified the cost: of 317 raw HDBSCAN clusters found, 184 (58%) were dropped by the `distinct_sessions >= 3` gate — and of those, 183 had *exactly 2* distinct sessions (only 1 had exactly 1). Almost none of the loss was from LLM failures (only 1 of the 133 eligible clusters failed the consolidation call). So the threshold, not extraction quality, was discarding the majority of found signal. Decoupled the two into independent parameters (`ats cluster-memories --min-cluster-size --min-recurrence`).

**Revisited same day — `min_recurrence` default lowered again, 2 → 1.** First pass reasoned that 2-session recurrence was real signal being lost to an over-strict 3-session floor, and set the default to 2. Pushed further: a cluster of 3+ memories that all came from a *single* session is also real signal — `min_cluster_size` (density: at least 3 memories independently landing in the same embedding region) is the actual quality gate; `min_recurrence` on top of it should only fire if you specifically want to require the pattern proved out across separate conversations, not as a default filter. Some genuine insights are sparse and session-local — discarding them because they never recurred elsewhere throws away signal, not noise. `min_recurrence` now defaults to 1 (no additional gate beyond `min_cluster_size`); raise it back to 2 or 3 if cross-session confirmation is specifically wanted.

**Same revisit — consolidation LLM call upgraded.** Was using `chunk_summarizer`'s default (`gemma3:12b`, 256 max_tokens, only the 10 members closest to the cluster centroid shown to the LLM). Changed to `gemma4:31b` (bigger/more capable — this text becomes the corpus's permanent "recurring insight" summaries, worth the quality), `max_tokens` 256 → 2048 (gemma4:31b is a thinking model, same reasoning-budget consideration as `entity_verifier_max_tokens`/`query_answer_max_tokens` elsewhere), and **all** cluster members included in the prompt, not a truncated sample (largest cluster on this corpus is 32 members at ~170 chars avg — well within `ollama_num_ctx=8192`). Both are now CLI flags (`--model`, `--max-tokens`).

**Bug 3 (design gap, not really a bug) — cluster membership was unrecoverable after the run.** `memory_sources` only linked the consolidated memory to session ids, never to which original `memories` rows were clustered together — HDBSCAN's cluster label is ephemeral, never persisted. This meant there was no way to inspect what a `cluster` memory actually consolidated, or to show its original per-member types on the Memories page. Added `memory_cluster_members(cluster_memory_id, member_memory_id, member_memory_type)`, populated at mint time. The Memories page detail panel now shows, for any `cluster`-type memory, its full type breakdown and the original member contents.

**On "dominant type" and content-based clustering:** clustering pools all seven memory types (episodic/procedural/semantic/pattern_*) into one embedding space — type is never a clustering feature, only computed post-hoc via majority vote to bias the LLM consolidation prompt's phrasing register. Investigated whether this causes incoherent clusters: sampled real content from the *lowest*-purity cluster in the corpus (0.25 — 4 types, 2 members each) and found all 8 members described the exact same underlying fact ("editable installs require source changes in the right location for the CLI to pick them up"), just framed through different memory-type lenses. Mean purity across all clusters was 0.66, but low purity did not correlate with incoherent content in every case sampled — `memory_type` in this pipeline is a soft per-extraction framing choice, not a hard taxonomic boundary, so clusters routinely and correctly span multiple types. Simplified the consolidation LLM call to stop requesting an unused `"memory_type"` field in its JSON output (it was computed but never read); all consolidated memories are stored as `memory_type='cluster'`, with the real type mix now recoverable via `memory_cluster_members`. Also changed the LLM's 10-member content sample from arbitrary DB-fetch order to distance-to-centroid ranking, so large clusters are summarized from their most representative members rather than whatever order SQLite happened to return.

---

## 2026-07-13 — UI: Home/Memories/Compare pages reworked; Query answer model raised

**Home.py session detail:**
- Clicking a row in the top sessions table now drives the session-detail selection below (`st.dataframe(..., on_select="rerun", selection_mode="single-row")`), instead of requiring the separate dropdown.
- Clicking a bar in "Memory type breakdown" shows an inline preview of that type's memories, instead of requiring a separate search.
- New **Structural** tab: shows the session's `session_type` cluster (with member count / avg chunks for context, not just a bare cluster id) and its top-8 structurally-similar sessions by cosine similarity of `session_structural_embeddings` — a distinct signal from Related Sessions (interaction *shape*, not shared topic/entities).
- **Related Sessions** tab reworked to show *why* each session is connected ("same project" vs. shared file/commit/PR entity name+type) — see the session-graph-edges entry above.
- Removed the **Chunks** tab (raw chunk dump judged not informative enough to keep).

**2_Memories.py — full redesign**, from a bare filtered table to: corpus overview metrics (total memories, sessions with memories, workspaces, recovery rate), a clickable memory-type breakdown chart (same click-to-preview pattern as Home), a memories-over-time weekly chart (bucketed by `first_observed` — i.e. when the work happened, not when ingest ran), and a memories-by-workspace bar chart. The existing filtered browse table and detail panel are kept, enriched with Workspace/First-observed columns and (for `cluster`-type memories) the new member/type-breakdown view described above.

**4_Compare.py:** added `permission mode` and `entrypoint` as groupable dimensions alongside the existing harness/model toggle (both fields were already populated with real variation in `session_metadata` but unused in the UI). Found and fixed a live bug while testing: SQL `NULL` surfaces as float `NaN` (not `None`) in this pandas path, and `not NaN` is `False` (NaN is truthy) — the permission-mode normalizer crashed on `.startswith()` for any session with no permission mode recorded. Fixed with an explicit `isinstance(v, str)` check. JSON-array permission configs (newer CC versions emit granular per-permission rules instead of one mode string) collapse into a single `custom_restricted` bucket rather than fragmenting the group-by on near-duplicate JSON strings.

**Query page:** added `ModelConfig.query_answer_model` (default `gemma4:31b`) and `query_answer_max_tokens` (default 2048, up from a hardcoded 512), used only for the "Generate Answer" RAG synthesis step — deliberately kept separate from `chunk_summarizer` so raising answer quality doesn't also change ingestion behavior. `gemma4:31b` is a thinking model like `gemma4:e4b`: reasoning tokens count against `num_predict`, so it needs the same higher budget already established for `entity_verifier_max_tokens`/`observer_max_tokens` elsewhere in the codebase — 512 would very likely have produced empty/truncated answers exactly like `gemma4:e4b` did before that fix.

---

## 2026-07-13 — Memory clustering follow-ups: Memories page charts, resume support, `shared_memory` session edges

Several fast-follow refinements after the initial memory-clustering fixes above, made while actually running `ats cluster-memories` end to end.

**Memories page: `cluster` split out of the type-breakdown chart, plus a dedicated "Cluster themes" chart.** The base memory-type breakdown chart now excludes `cluster` (it's a derived/meta category spanning all 7 base types, not a peer of them). The click-to-preview interaction was quietly wrong too: it sorted by `evidence_count DESC` and labeled results "top N by evidence" — but every non-cluster memory has `evidence_count=1` (nothing increments it pre-clustering in the current D-prompt pipeline), so that was sorting on a constant and mislabeling a random order as a ranking. Changed to `ORDER BY RANDOM()` with an honest caption. Added a new **Cluster themes** chart: top 15 `cluster` memories by `evidence_count` (a real ranking axis for clusters, unlike base types) as a horizontal stacked bar showing each cluster's original member-type composition, labeled by its consolidated content.

**`min_recurrence` dropped further, 2 → 1.** Pushed back on: why gate on cross-session recurrence at all? `min_cluster_size` (≥3 memories independently landing in the same embedding region) is the real quality signal; requiring the pattern to *also* recur in a second session discards genuinely real but session-local/sparse insights. Default is now 1 — no additional gate beyond the density floor. Raise it back via `--min-recurrence` if cross-session confirmation is specifically wanted.

**Consolidation LLM call upgraded, then re-tuned for speed.** First pass: `gemma4:31b` (bigger/more capable) + `max_tokens` 256→2048 (thinking-model budget) + all cluster members shown to the LLM (was top-10-by-centroid-distance only). This worked but was ~8x slower than the original `gemma3:12b` run — diagnosed via Ollama server logs: (a) `consolidate_memories()` called the LLM in a plain sequential loop, no concurrency at all, unlike ingestion's 4-worker `ThreadPoolExecutor`; (b) `gemma4:31b`'s decode is memory-bandwidth-bound enough that even after adding parallelism, per-slot speed didn't scale cleanly; (c) real observed variance between consecutive single-stream calls to the same model (20.3 t/s vs 7.9 t/s) suggestive of memory-pressure effects, not just model size. Fixed (a) by parallelizing the LLM calls exactly like ingestion does — pure-compute `_consolidate_one()` function dispatched via `ThreadPoolExecutor(max_workers=4)`, DB writes kept sequential afterward (same safe split ingestion already uses). Then swapped model to `gemma4:e4b` (same thinking-model family, ~1/3 the size) — confirmed via server log that this let all 4 parallel slots run at ~22-23 t/s *each* simultaneously (vs. `31b`'s single-stream 8-20 t/s), roughly an 8x aggregate throughput improvement with no visible quality loss on spot-checked output. `gemma4:e4b` is now the default; `gemma4:31b` remains available via `--model` if maximum quality is ever wanted over speed.

**Resume support, so a killed/interrupted run doesn't duplicate or lose work.** All three model swaps above happened by killing an in-progress run and restarting — safe for already-minted memories (each cluster commits immediately after minting) but re-running from scratch would re-process every cluster including ones already done, minting near-duplicates. Added a check at the top of `consolidate_memories()`: fetch `memory_cluster_members.member_memory_id` already on record, and skip any newly-found cluster whose members overlap ≥80% with an existing mint (HDBSCAN is deterministic for unchanged input+params, so a resume reproduces the same cluster boundaries). Verified across three consecutive kill/restart/model-swap cycles: 75 → (parallelism fix) → 75 unchanged at restart → (model swap to `gemma4:e4b`) → 75 unchanged at second restart → 317 final, with `memory_cluster_members` showing exactly 317 distinct `cluster_memory_id`s and zero duplicates.

**New edge type: `session_graph_edges.edge_type = "shared_memory"`.** Neither `cluster-sessions` nor `cluster-memories` created any session-to-session edges before this — asked directly, confirmed via `grep` there was zero `session_graph_edges` involvement in `clustering_analytics.py`. Since a consolidated cluster memory already records (via `memory_sources`) every distinct session that contributed a member, added a pairwise edge between every such session pair, `via_entity_id` set to the cluster memory's own id (so multiple shared clusters between the same session pair correctly produce multiple distinct edge rows, mirroring how multiple shared-file `structural` edges already work). Backfilled for the existing 317 clusters: 316 of them span ≥2 sessions, producing 1,597 pairwise links (3,194 rows both directions) — comparable in scale to the existing `workspace` edge type (2,046 rows). Session connectivity via any edge type rose from 120/128 to 123/128. More importantly this is a genuinely different signal than the other two edge types: spot-checked one session and found it now links to sessions in *entirely different projects* (`coral-ai`, `src`, even a different harness — `opencode`) purely because they independently hit the same recurring pattern (e.g. "when multiple local models are available, prioritize mid-size ones") — a connection neither `workspace` (same project) nor `structural` (same file) could ever surface. Home.py's Related Sessions tab and `ats graph-walk` both updated to label these as `"shared pattern: {content}"` and `LEFT JOIN memories` alongside the existing entity join (same silent-inner-join-drop risk as the earlier `workspace` edge fix — `via_entity_id` for `shared_memory` edges points at `memories.id`, not `entities.id`).

---

## 2026-07-13 — Session-type labeling, Compare page split, UMAP fix, Memories chart fixes, session community graph

A second round of follow-ups, prompted by direct questions about what `cluster-sessions` actually surfaces and several UI clarity issues found by using the just-built features.

**`session_type` had no semantic meaning — only ever displayed as a raw HDBSCAN id (`cluster_8`), nowhere outside Home.py's Structural tab.** Unlike memory clusters (which get an LLM-written summary), `tag_session_types()` never generated any description — `cluster_8` told you nothing. Added `label_session_types()` (`clustering_analytics.py`) + a new `session_type_labels` table: samples each cluster's member `session_summary`s and asks an LLM for a short label + one-sentence description. Critically, the prompt explicitly tells the LLM to characterize the shared INTERACTION SHAPE (turn rhythm, tool density, session length), not topic — verified two sessions in the same cluster covered completely unrelated subjects (a Kaggle benchmark pivot vs. ingestion pipeline debugging) yet share the same structural embedding neighborhood, since that embedding is built from raw head+tail text, not semantic content. Real output on this corpus: `"Iterative, problem-solving development"`, `"Abrupt, unfinished user exits"`, `"Stalled, iterative troubleshooting"`, `"Focused literature review sessions"` — genuinely descriptive, not generic. Two clusters (all single-chunk sessions, no `session_summary` to draw from) got a stats-only fallback label (`"Minimal / trivial sessions"`) instead of being silently skipped. Wired into `ats cluster-sessions` as an automatic follow-up step (`--no-label` to skip, `--label-model` to override, default `gemma3:12b` — fast, non-thinking, sufficient for a short synthesis task).

**Split `4_Compare.py` into two pages.** The old page mixed two different mental models: a top section for hand-picking specific sessions to compare side-by-side, and a bottom "Aggregate — all sessions" section for comparing cohorts/configurations (harness, model, permission mode, entrypoint) across the whole corpus. Kept the former as **Session Compare**, moved the latter to a new **Agent Compare** page. `structural session type` (using the new labels) added as a 5th groupable dimension on Agent Compare, alongside the existing four.

**UMAP "Session map" wasn't informative — root cause was the color encoding, not the projection.** It colored points by `recovery_rate`, a continuous metric that's 0 for most sessions (most sessions don't struggle) — so nearly every point rendered as the same color regardless of position, and the plot conveyed nothing beyond raw scatter. Switched to coloring by the new `session_type_label` (categorical) — since the UMAP projection and the HDBSCAN clustering both operate on the same structural embeddings, colors now correspond to the visual groupings, which is what the chart's own caption always claimed but didn't deliver before this fix.

**Memories page "Cluster themes" chart — three separate clarity issues fixed.** (1) Caption was genuinely confusing ("distinct source sessions — the one memory-type where this is a meaningful ranking") — rewritten in plain language explaining evidence_count=1-for-everything-else directly. (2) All 7 possible member types in the stacked composition bars used only 3 colors (`_type_color()`, designed for the simple 2-color healthy/struggle split elsewhere on the page) — 5 of 7 types were indistinguishable blue. Added a dedicated 7-color `_COMPOSITION_COLORS` palette for this chart only, left `_type_color()` untouched for its original purpose. (3) Y-axis labels were raw first-70-chars truncation, sometimes cutting mid-word. Added `_short_theme()` — truncates at the nearest word boundary. Considered LLM-generated short titles (extending the consolidation prompt to also return a `theme` field) as a higher-quality alternative; not implemented yet since it needs a backfill pass over the existing 317 clusters — noted as a follow-up if the heuristic truncation isn't good enough.

**New: session connection graph (Agent Compare page), using all three `session_graph_edges` types.** Node-link diagram (via `networkx` — already a transitive dependency, no new install) with automatic community detection (`greedy_modularity_communities`). Multi-edges between the same session pair (a pair can be connected by more than one edge type) collapse to one graph edge with weight = number of distinct edge types, avoiding a 3000+-edge hairball across only 128 nodes. Isolated sessions (no edge of the selected type) are dropped from the view rather than cluttering it. Real result on this corpus: 8 communities, mostly aligned with workspace as expected, but the largest (43 sessions) spans multiple SLR-agent-related workspaces *and* three different harnesses (claude_code, gemini_cli, opencode) — a direct, visible demonstration of what the `shared_memory` edge type adds that `workspace`/`structural` alone couldn't surface. Edge types are toggleable (multiselect) so `shared_memory`-only views (the cross-project signal) can be inspected separately from `workspace`/`structural` (which mostly just recreate project silos, as expected). Hit and fixed one bug while building this: `st.cache_data` failed with `UnserializableReturnValueError` on the raw `sqlite3.Row` objects returned from the cached query function — `conn.row_factory = sqlite3.Row` isn't reliably picklable for Streamlit's cache storage; fixed by converting to plain tuples before returning (the existing `load_aggregate`/`load_umap` cached functions were already safe, since they convert to a DataFrame before returning).

## 2026-07-13 — Memory-level UMAP, memory-yield-by-session-type chart, dot-size fixes, selectable scatter axes

A third round of same-day follow-ups, driven by four specific critiques of the charts shipped in the previous round.

**Memories page had no corpus-wide view of the embedding space — only per-cluster bar charts.** Added a "Memory map — embedding UMAP" section (`2_Memories.py`) projecting all 4647 embedded memories (raw + consolidated cluster memories) to 2D via UMAP, with a `color_by` selector offering four dimensions: **Memory type** (the 8 D-prompt types + `cluster`), **Cluster status** (`Consolidated cluster memory` / `Cluster member` / `Unclustered` — deliberately collapsed to 3 categories rather than coloring by the 317 individual cluster ids, which would be illegible), **Harness**, and **Workspace**. Reuses `_short_theme()` for hover-preview truncation. Cached via `st.cache_data(ttl=600)` since UMAP fit on ~4600 points is not free.

**"Chunks vs memories" scatter on Agent Compare was near-useless** — with ~130 sessions the raw scatter was just noise with no aggregation to reveal any pattern. Replaced with "Memory yield by structural session type": a box plot of memories-produced-per-chunk, grouped by `session_type` (always the structural clustering, independent of the page's own `group_by` toggle, since this chart is specifically about session type as a variable). Points overlaid (`points="all"`) show individual sessions against the box's median/quartiles. A "Split by memory type" expander adds a faceted version (one small box plot per memory type) for anyone wanting the finer breakdown.

**Dot size on both session-level scatters (health quadrant, structural UMAP) was misleading** — larger dots looked like they represented clusters or aggregated multiple sessions, when every dot is exactly one session and size only ever encoded `chunk_count`. Fixed by capping `size_max` lower (quadrant: 20→14, UMAP: 18→13) and adding an explicit caption on both charts stating "each dot is exactly one session; dot size = chunk count" — pointing to the community graph below as the actual place to see session-to-session groupings.

**Health quadrant's axes were hardcoded to tool-calls-per-turn (x) vs recovery-rate (y).** Generalized into "Session scatter — pick any two metrics": two `st.selectbox` dropdowns backed by a `_METRIC_OPTIONS` dict (tool calls/turn, recovery rate %, chunks, memories, entities, mem/chunk, cache hit %, turns, tool calls), defaulting to the original autonomy-vs-struggle pairing but letting anyone explore other combinations (e.g. chunks vs cache-hit% to look for a length/caching relationship) without code changes.

All four verified live in the browser against the real 4647-memory / 128-session corpus: the memory UMAP renders distinct type clusters with a working color-by legend, the yield-by-session-type box plot shows real variation across the 8 labeled session types, and the community graph (unaffected by this round but re-checked) still shows the same 8-community structure with visibly smaller, more proportionate node markers throughout Agent Compare.

## 2026-07-13 — Cluster memories: auto-embed on mint, missing FTS index; community graph: discrete palette + tunable granularity

Two follow-up gaps surfaced by direct questions after using the previous rounds' features.

**Minted cluster memories weren't embedded until the next manual `ats embed` run.** `consolidate_memories()` inserts new rows into `memories` (`memory_type='cluster'`) but never touched `memory_embeddings` — by design, since embedding was always a separate deferred step (`ats embed`) to avoid evicting large LLMs from Ollama's GPU memory mid-ingest. But `ats cluster-memories` is a standalone command run well after ingestion, not mid-pipeline, so that GPU-eviction concern doesn't apply here — there's no reason to force a manual follow-up step. Added an automatic embed pass at the end of `cluster_memories_cmd` (`cli.py`): reuses `store.get_unembedded_memories()` (already scoped to any memory type lacking an embedding row, so it naturally picks up only the newly minted ones) + `Embedder.embed_batch()` + `store.write_embeddings()`. Gated behind `--embed/--no-embed` (default on).

**Cluster memories were also never written to `memories_fts`**, so they were lexically unsearchable (`recall` with method=lexical or hybrid) until the next process restart triggered schema.py's lazy `mem_count > fts_count` backfill — which does a full `DELETE`+rebuild of the whole FTS table, wasteful for what should be an incremental insert. The ingest-time D-prompt memory writer already inserts into `memories_fts` alongside `memories`; `consolidate_memories()`'s raw `INSERT INTO memories` never did. Fixed by adding the matching `INSERT OR REPLACE INTO memories_fts` right next to the mint's `memories` insert, so cluster memories are both semantically (once embedded) and lexically searchable immediately, with no dependency on the next process restart.

**Session connection graph's node coloring used a continuous colorscale (`Turbo`) for a categorical value (community id).** Two adjacent community ids (e.g. 3 and 4) could render as visually similar colors purely from being numerically close, undermining the graph's whole point — the user specifically asked what the color meant, which meant the encoding wasn't self-evident even though the caption said "color = community". Switched to a discrete qualitative palette (`px.colors.qualitative.Bold + Pastel`, cycling by `community_id % len(palette)`), rendered as one Plotly trace per community so the legend doubles as a community key (`community 0 (25)`, etc.) instead of relying solely on the caption below the chart.

**Large communities were hard to interpret** — greedy modularity's default resolution (1.0) merged loosely-connected sessions into big blobs. `networkx`'s `greedy_modularity_communities` already exposes a `resolution` parameter (confirmed via direct testing on the real edge graph: resolution 0.5→1.0→1.3→2.0→3.0 produced 6→8→7→12→27 communities, with the largest community shrinking from 68→43→28→21 sessions respectively) — just never exposed to the UI. Added a `st.slider("Community granularity", 0.5, 3.0, default=1.3)` next to the edge-type multiselect; the chart, caption, and community table all recompute from the same resolution value.

## 2026-07-13 — Agent Compare scatters: dot-size explanation still unclear, added opacity + workspace in hover

A follow-up to the dot-size fix two rounds prior — captions alone didn't land; the user asked again what dot size meant on both the session scatter and structural UMAP, and separately asked for `memories_fts` (added same day) to be explained.

**Root cause of the "dots look like they're overlapping/clustered" perception wasn't really about size, it was about opacity.** Dot size is `chunks` (`session.chunk_count`, the number of text chunks that session was split into at ingest — a rough proxy for session length) on both charts, and had already been capped low in the prior round. But both scatters rendered at full opacity, so when two same-colored, same-sized dots landed at literally the same (x, y) — common, since metrics like `tool_per_turn` and `recovery_rate` take a limited set of near-identical values across many sessions, and structurally similar sessions land in the same UMAP neighborhood — the top dot fully occludes the one(s) beneath it. A stack of 5 overlapping sessions was visually indistinguishable from 1 session with a slightly bigger dot, which is exactly the "cluster" misreading both rounds of captions were trying to head off. Added `opacity=0.6` to both `px.scatter` calls (matching the pattern already used on the Memories page's memory-level UMAP) so real overlap now reads as a darker/denser patch instead of a single opaque dot. Reworded both captions to name the mechanism directly ("same-colored dots often stack directly on top of each other... dots are semi-transparent so stacked points look darker/denser") rather than just restating what size means again.

**Added `workspace` to both charts' hover tooltips**, per direct request. `load_aggregate()`'s query didn't select `s.workspace_id` at all — added it, formatted through the existing `format_workspace()` helper into a new `workspace` column, and included it in `hover_data` for both the session scatter and the UMAP (which merges in columns from `agg`). Verified via the rendered Plotly `hovertemplate` strings (not just visual hover, which is flaky under browser automation) that `workspace` now appears as `customdata[1]` / `customdata[2]` respectively on both charts.

## 2026-07-13 — Session connection graph: node size was degree — made uniform

Same underlying confusion as the two scatter charts, raised as a follow-up once those were fixed — this time on the session connection graph (which uses `go.Scatter` traces directly, not `px.scatter`, so it needed a separate fix). Node size there was `8 + min(G.degree(n), 20)` — degree being how many other sessions that node connects to for the currently-selected edge types. A session that happens to share a workspace with 20 others (common — big shared-workspace communities dominate the graph) rendered as a visibly larger dot than one with only 1–2 connections, which read as "this is a cluster of sessions" rather than "this one session happens to be well-connected" — the exact same misreading the size-based scatter dots caused, just via a different variable. Set marker size to a flat `10` for every node; degree is still surfaced in the hover text (`community {i} · degree {G.degree(n)}`) for anyone who wants it, it's just no longer visually encoded.

## 2026-07-13 — "Agent Compare" renamed to "Explore"; scatter/UMAP dot size removed entirely; Memories UMAP explains "Unclustered"

Third follow-up round on the same theme, plus a naming fix.

**Page renamed: `5_Agent_Compare.py` → `5_Explore.py`, title "🧬 Agent Compare" → "🔭 Explore".** The name "Agent Compare" was a holdover from before the page split (see the earlier "Split `4_Compare.py` into two pages" entry) and never quite fit — the page isn't comparing specific agents/sessions against each other (that's Session Compare's job), it's open-ended EDA: pick a metric pair, pick a grouping, look for patterns across the whole corpus. User suggested "Explore" or "Insight"; went with **Explore** since it matches the page's own framing (rewrote the module docstring and page caption to say "exploratory analysis" / "what patterns exist" instead of "how do configurations differ"). Updated the cross-reference in `4_Compare.py` and the README page table; grepped for any other `Agent_Compare`/`Agent Compare` references first — none left in code.

**Dot size removed entirely from the session scatter and structural UMAP, not just capped/explained.** Two prior rounds tried captions ("dot size = chunk count, not a cluster") and then opacity (to make real overlap visible as a darker patch) — the user asked a third time what dot size meant and why it made sessions "seem overlapping," which meant the encoding itself, not the explanation, was the problem. Consistent with the community-graph fix earlier the same day (node size = degree → uniform), removed `size="chunks"`/`size_max=...` from both `px.scatter` calls and replaced with `fig.update_traces(marker=dict(size=10))` (session scatter) / `size=8` (UMAP, more points per unit area). Chunk count is still available on hover — added explicitly to `hover_data` on the UMAP chart, since previously it was only implicitly present as the size-mapped variable and would have silently disappeared from the tooltip too. Captions reworded to drop "dot size = chunk count" and just state "all drawn the same size."

**Memories page memory-level UMAP: "why are so many memories Unclustered?"** Queried the real numbers directly against `traces.db`: of 4330 raw (non-cluster) embedded memories, HDBSCAN (`min_cluster_size=3`, `cluster_selection_method="eom"`) placed 2456 (56.7%) in no cluster at all (label `-1`, HDBSCAN's noise designation) and grouped the remaining 1874 into 317 clusters — confirmed by directly reproducing `consolidate_memories()`'s HDBSCAN call against the corpus's actual memory embeddings. This matched the UI's live counts exactly, confirming it isn't a display bug: `consolidate_memories()` (`clustering_analytics.py:266-268`) explicitly drops noise-labeled points (`if label >= 0`) before clustering, by design — HDBSCAN doesn't force every point into a cluster the way k-means would, and a memory that's semantically unlike the rest of the corpus is still a valid standalone memory, just not part of a *recurring* pattern (which is specifically what cluster memories represent). Added a caption on the Memories page UMAP stating the real breakdown (consolidated / member / unclustered counts and the unclustered %) plus the HDBSCAN-noise explanation and a pointer to `ats cluster-memories --min-cluster-size 2` for anyone who wants looser (noisier) clustering instead of leaving this as something a user has to ask about each time.

## 2026-07-13 — Five UI pages consolidated to four: Explore split into Explore Sessions / Explore Memories, Session Compare folded into Explore Sessions

A restructuring pass on top of the day's earlier renames, prompted by the observation that the growing "Explore" page mixed two different subjects (session structure vs. memory content) and that "Session Compare" — a full standalone page — largely duplicated ground Explore was already covering, just for a hand-picked subset of sessions instead of the whole corpus.

**Split `5_Explore.py` by subject, not just by section order.** Renamed to `2_Explore_Sessions.py` (🔭): kept everything that's fundamentally about session-level structure/metrics — session scatter, token efficiency over time, cache hit % distribution, recovery leaderboard, structural embedding UMAP, group summary, session connection graph. The one section that was really about memory *content* despite being grouped by session type — "Memory yield by structural session type" — moved to `3_Explore_Memories.py` (was `2_Memories.py`, renamed but kept its 💾 icon), landing right after the existing memory-type-breakdown/memories-over-time section and before Cluster themes. Both pages' docstrings now state this split explicitly so it's discoverable by reading the source, not just by the sidebar order.

**Deleted `4_Compare.py` ("Session Compare") — folded its entire contents into a new bottom section of Explore Sessions, "Compare specific sessions."** The page's whole reason to exist was hand-picked-session comparison (summary scorecards, memory/entity-type breakdown, token usage, cache hit rate, pairwise structural-embedding-similarity heatmap) — none of that changed, it's the same code, just moved into a function (`_render_session_compare_section()`) called at the very end of Explore Sessions instead of being its own page. Wrapped in a function specifically to avoid variable-name collisions with the rest of the merged page (e.g. both the old Compare page and the new page independently used `sessions`, `fig_ent`, `col_x`/`col_y`-shaped names) — Python function scope handles that for free instead of hand-prefixing every variable. Default session preselection (top 4 most recent) kept from the original page so the section isn't empty on first load.

**Added two new default (all-sessions) charts to Explore Sessions that didn't exist anywhere before**, per direct request to surface Compare's "informative charts" outside the hand-picked-session context: "Memory type breakdown by session type" and "Entity type breakdown by session type," both stacked bars grouped by structural session type. Deliberately computed as **average per session**, not summed totals — session-type cohorts range from 4 to 25 sessions in this corpus, and a raw sum would just reproduce cohort size rather than showing composition differences. (Token usage and cache hit rate were explicitly *not* duplicated here — "Token efficiency over time" and "Cache hit % distribution" already existed on the old Explore page and cover the same ground at the corpus level.)

Verified all of this live: both new stacked-bar charts render with real per-session-type data on the live corpus, the "Compare specific sessions" section at the bottom of Explore Sessions renders its default 4-session comparison including the pairwise similarity heatmap, and the moved "Memory yield by structural session type" chart renders correctly in its new spot on Explore Memories. Updated the README's page table and grepped for any leftover `2_Memories`/`4_Compare`/`5_Explore` or "Memory Browser"/"Session Compare" references in code — none left outside of explanatory comments pointing at the old page names for context.

## 2026-07-13 — Home page: intro section + pipeline diagram + technical-approach cards (demo prep)

Added for a live demo the next day, so the Home page opens with orientation instead of straight into a raw sessions table.

**New section between the title and the Sessions table**: a 2-3 sentence plain-language purpose statement, an inline SVG pipeline diagram (Traces → Ingest → Embed → Cluster → Retrieve → Serve, one colored box per stage with a one-line description each), an "offline, no cloud dependency" caption, and a 4-card "Key technical approaches" row (one-call D-prompt extraction, dual semantic/structural embeddings, HDBSCAN-based emergent pattern discovery, multi-signal session graph). The four cards were deliberately chosen to mirror the diagram's Ingest/Embed/Cluster/Retrieve+Serve stages by color, so the two sections read as one coherent story rather than two disconnected blocks.

Implementation note: the SVG is a plain string rendered via `st.markdown(..., unsafe_allow_html=True)` rather than a Plotly figure — a static, precisely-labeled box-and-arrow diagram doesn't need a charting library, and a raw SVG scales cleanly at any window width via `viewBox` (verified at both desktop and a 1024px-wide viewport). Boxes use solid saturated fills with white text rather than a theme-matched transparent style, specifically so the diagram reads correctly regardless of whether the demo machine's Streamlit is in light or dark mode — the rest of the app's charts hardcode colors the same way (e.g. `_type_color()` on Explore Memories), so this matches existing convention rather than introducing a new one.

Also fixed a stale doc bug found while updating the README for this: the page-count blurb still said "Five pages" after the earlier same-day consolidation from 5 pages to 4 (Explore Sessions / Explore Memories / Query / Home) — corrected to "Four pages."

## 2026-07-13 — Home page: "What we learned" + "Built with" sections (talk-prep addition)

Added two more sections to the Home page intro, sourced directly from the user's AI Tinkerers talk proposal ("what will another builder learn") so the demo page and the talk narrative match.

**"What we learned"** — three bullets, kept close to the talk proposal's own phrasing rather than paraphrased into something generic: (1) one structured extraction call beating a 4-step heuristics pipeline (~50% fewer LLM calls, higher quality — this stat was also folded into the existing "One-call extraction" card above it, since the two sections serve different audiences/framings — architecture summary vs. talk-style retrospective insight — so some overlap is intentional, not accidental duplication); (2) per-exchange scoring/boundary detection produced low-precision noisy signal, motivating semantic pre-filtering before structural signals become usable; (3) entity-centric memory can't answer "how did I handle failures" — recovery strategies have no entity anchor, hence the separate pattern-extraction memory types.

**"Built with"** — a compact flex-wrapped chip row (not another 4-column card grid, since 7 items doesn't divide evenly and chip width should match content length, not a fixed column): Claude Code/Gemini CLI/OpenCode (data source), Ollama+nomic-embed-text (embeddings), Ollama+gemma3:12b (extraction LLM), SQLite+sqlite-vec (storage/vector search), BM25(FTS5)+RRF (hybrid retrieval), HDBSCAN (clustering), Streamlit+Plotly (UI). Chip accent colors reuse the pipeline diagram's stage colors where the mapping is natural (e.g. the extraction LLM chip is the same blue as the diagram's "Ingest" box) for visual continuity across the whole intro block.

Source for both sections: the user's submitted talk proposal page (gated behind AI Tinkerers event registration login — not independently fetchable — so content was taken directly from what the user pasted into chat, not re-derived).

## 2026-07-14 — Home page: "What we learned" removed, folded into "Key technical approaches" (5 cards)

Follow-up to the previous entry, per direct feedback: the "What we learned" section's first bullet (one-call extraction beats a heuristics pipeline) was purely redundant with the existing "One-call extraction" card, and the other two bullets fit better as cards than as a separate bulleted list.

Removed the "What we learned" section entirely. Bullet 3 (entities capture nouns, behaviors need patterns) merged into the "One-call extraction" card's description, since that card already explains what the combined prompt extracts — a natural place to explain *why* patterns exist as their own type. Bullet 2 (analysis granularity / semantic pre-filtering) became a new standalone 5th card ("Granularity over noise", orange `#f97316` — the one color in the row not already used elsewhere on the page), since it didn't have a natural home in any existing card. `st.columns(4)` → `st.columns(5)` for the card row. Trimmed the merged "One-call extraction" card's wording after an initial pass rendered visibly taller than the other four cards in the row — verified in the browser that all 5 cards now read at comparable heights.

## 2026-07-14 — Query page "Generate Answer" AttributeError diagnosed as stale process; query_answer_model switched to gemma4:e4b

User hit `AttributeError: 'ModelConfig' object has no attribute 'query_answer_model'` on the Query page. Confirmed via direct import (`Config().models.query_answer_model`) that the field exists and works correctly in the current source — `git status` showed `config.py` has this session's uncommitted local changes, and `ps aux` showed the user's `ats ui` process (port 8501) had been running since Sunday 11AM, well before `query_answer_model` was added earlier in this session. Root cause: Streamlit's dev server only re-executes the top-level page script on each interaction — it does not reload already-imported library modules like `config.py`, so a long-running process keeps stale class definitions in memory even after the source file on disk changes. Not a code bug; fix is restarting the `ats ui` process.

**Also benchmarked `query_answer_model` speed as requested.** Ran the actual answer-generation prompt shape (retrieved-context RAG synthesis) through both models directly via `OllamaProvider.complete_text`: `gemma4:31b` took ~79s vs. `gemma4:e4b`'s ~11-15s on equivalent prompts (~7x slower) — consistent with the earlier cluster-memories consolidation finding (2026-07-13 entry) that gemma4:31b showed zero server-side parallelism and no measurable quality advantage over gemma4:e4b for this kind of grounded-synthesis task. Confirmed `query_answer_max_tokens=2048` is already sufficient for `gemma4:e4b` — responses completed with well-formed, non-truncated text well under budget (checked full untruncated output, not just a display-cropped preview). Switched `ModelConfig.query_answer_model` default to `gemma4:e4b`; `query_answer_max_tokens` left at 2048 (no change needed). Verified end-to-end in the browser against a fresh server process: "Generate Answer" now completes in ~15-20s total (search + generation) and produces a well-grounded, correctly-cited answer.

## 2026-07-14 — Recovery leaderboard ranking bias + major duplicate-session ingestion bug found and partially fixed

User felt the Recovery rate leaderboard's top 3 didn't match sessions they remembered as problematic, and that some Gemini sessions they recalled as struggle-heavy weren't showing up. Investigation found two independent, compounding issues.

**Issue 1 — leaderboard ranking bias (fixed).** The leaderboard sorted purely by `recovery_rate` (%) with no floor on session size. A 1-chunk session with 1 recovery memory out of 2 total memories showed "50%", ranking above a 45-chunk session with 17 recovery memories out of 179 (9.5%) — classic small-sample-size inflation dominating a percentage-only sort. Fixed in `2_Explore_Sessions.py`: added a "Min memories to qualify" number input (default 10) to exclude low-signal tiny sessions before ranking, a "Sort by" toggle (Recovery % vs. raw Recovery+Ineff count) for whoever wants total-struggle-regardless-of-size instead of rate, and added the `Workspace` column so sessions are recognizable by project name instead of just a session-id prefix.

**Issue 2 — duplicate session ingestion (root cause fixed for new ingests; existing duplicates NOT yet cleaned up).** Investigating why specific remembered Gemini sessions ranked lower than expected led to comparing `ingestion_state.content_hash` across all ingested files: **33 distinct files are ingested twice each, at two different paths under `data/archive/`** — e.g. `data/archive/gemini_cli/session-X.json` (flat) and `data/archive/gemini_cli/personal-kb/session-X.json` (nested under a project subfolder), byte-identical content (same content_hash), different DB session rows. **17 gemini_cli + 16 claude_code duplicate pairs — 66 of 128 total session rows (51.6%) are involved.** True unique session count is ~95, not 128; every corpus-wide total shown in the UI (Home page, Explore Sessions/Memories aggregates) is inflated by this.

Root cause: `_compute_session_id()` (`pipeline/ingestion.py`) hashes `f"{source_plugin}:{abs_path}:{content_hash}"` — `abs_path` is part of the key, so identical content at two different archive paths always produces two different session IDs, and `session_exists()` never catches it. `_scan_archive_files()`'s own docstring claimed this couldn't happen ("resolves to the same abs_path/content_hash/session_id... no duplicate sessions") — true only when the *same* path recurs across scans, not when the archive directory itself independently holds the same content at two different paths (likely from historical rsync/copy operations across machines, or files pulled into both a flat location and a project-name subfolder).

Fixed prospectively: added `SQLiteStore.get_ingested_state_by_content_hash()` and used it in `_scan_archive_files()` to skip archiving/processing a newly-discovered path if a file with identical content_hash is already ingested anywhere — regardless of path. This prevents any *new* instance of this bug but does **not** retroactively clean up the 33 pairs (66 rows) already in the DB — deleting session rows and all their cascading records/entities/memories/embeddings is destructive and most FK relationships in this schema lack `ON DELETE CASCADE` (only `session_metadata` and `session_structural_embeddings` have it), so a cleanup needs a deliberate multi-table script, not a one-line DELETE. Flagged to the user rather than run without confirmation.

## 2026-07-14 — UI crash: numba "workqueue... accessed concurrently by multiple threads"

User hit a hard crash with numba's threading-layer error while using the UI. Root cause: `umap-learn` (used for the structural and memory embedding UMAP charts on Explore Sessions/Explore Memories) JIT-compiles via numba, and numba's default `workqueue` threading layer is explicitly documented as not safe to invoke concurrently from multiple OS threads. Streamlit runs each connected browser session in its own thread within a single process — two tabs/sessions both triggering a UMAP computation around the same time (e.g. Explore Sessions and Explore Memories open together, or a fresh page load overlapping a still-running previous one) call into numba's parallel dispatcher from two threads at once, which is exactly the failure mode in numba's own docs.

Fixed by setting `os.environ.setdefault("NUMBA_NUM_THREADS", "1")` at the top of `ui/common.py`, before any other import in that module. Every page's first import is `from agent_trace_signals.ui.common import ...`, and both pages' `import umap` calls are lazy (inside `st.cache_data`-wrapped functions, not at module top-level), so this reliably runs before numba's parallel dispatcher is ever initialized in the process, regardless of which page/thread runs first. `setdefault` (not a hard override) respects an explicit env var if the user later installs `tbb` and sets `NUMBA_THREADING_LAYER=tbb` themselves — the actually-threadsafe alternative numba's error message suggests, not installed here since it's an extra dependency for a background chart computation.

Forcing 1 thread removes numba's internal parallelism entirely rather than solving the concurrency conflict a smarter way (e.g. serializing UMAP calls via a lock) — acceptable since every UMAP call in this codebase already passes `random_state=42` for reproducibility, which UMAP itself already forces to single-threaded execution (confirmed via direct test: "n_jobs value 1 overridden to 1 by setting random_state" warning) — so this fix has no additional performance cost beyond what every UMAP call already paid.

Verified by loading Explore Sessions and Explore Memories in two separate browser tabs simultaneously — both UMAP charts (128-session structural embedding, 4647-point memory embedding) computed successfully with no crash and no server errors.

## 2026-07-14 — Query page: example-question picker + top_k default raised to 15

Added a "Try an example question" selectbox above the Query input on `1_Query.py`, prepopulated with 5 example questions covering different projects/topics in the corpus (Isaac's chess app, SLR pipeline LLM parallelism, SLR HITL gates, agent-trace-signals scoring experiments, the veritract package). Standard Streamlit `on_change` + `st.session_state` pattern: selecting an example writes directly into the Query text input's session_state key (`query_input`) via a callback, so picking an example populates the box without an extra confirm step. Also raised the default `top_k` from 10 to 15.

Verified via direct DOM/session-state inspection (not just visual screenshot) that selecting an example correctly populates the Query input. Browser-automation clicking on this page's example selectbox was unusually unreliable during verification — traced to a coordinate-space mismatch between the actual page viewport (1280px) and the tool's screenshot capture (800px, ~0.625x scale) compounding with element position drift between screenshot and click; resolved by reading the option's live `getBoundingClientRect()` and scaling to screenshot-space before clicking. Not an app bug — worth remembering for future browser-based verification on this page.

## 2026-07-14 — Home page: memory-bank schematic (extraction richness → RAG)

Added a second, more detailed SVG diagram below the existing 6-stage pipeline overview, specifically illustrating: agent traces → the "memory bank" (entities + 3 memory types + 4 pattern types, each its own box, to make the extraction richness visually explicit rather than a single generic "extraction" blob) → consolidation (memory themes via cross-session HDBSCAN clustering, session clusters via structural-embedding HDBSCAN) → RAG, with a separate "user / agent query" input arrow into the RAG box.

**Bug found and fixed while building it**: the diagram silently truncated mid-render — everything past the "Inefficient" pattern box (last element before a run of blank lines deeper into the SVG) rendered as unstyled floating text instead of colored boxes, while everything before it rendered correctly. Root cause, confirmed via direct DOM inspection (`document.querySelectorAll('svg')` — the live element had only 10 of the intended 13 `<rect>`s and 24 of 36 `<text>`s): Streamlit's markdown parser treats a bare `<svg>`-opening line as a CommonMark HTML block, which terminates at a blank line; a blank line partway through the SVG string split it into two separate HTML fragments, so the tag stream after the split no longer had a `<svg>` ancestor. Browsers still HTML-parse (not XML-parse) stray `<rect>`/`<text>` tags leniently — unrecognized tags outside SVG namespace render no shape but still display their text content and apply matching CSS classes, which is exactly the "bold text with no box" symptom observed. Adding explicit `width`/`height` attributes to the `<svg>` tag (the first thing tried, on the theory it was an intrinsic-sizing/clipping issue) did not fix it — confirming the real cause was the HTML-block split, not CSS sizing. Fix: strip all blank lines from within both SVG strings (`_PIPELINE_SVG` and `_MEMORY_BANK_SVG`) so the HTML block is never interrupted. Any future large inline SVG added via `st.markdown(..., unsafe_allow_html=True)` in this app should keep this in mind — no blank lines inside the string.

## 2026-07-14 — Home page diagram polish + standalone SVG export to README + doc accuracy pass

Follow-up requests after the memory-bank schematic landed: bigger font size relative to box size, and get both diagrams into the README since they're useful for understanding the architecture.

**Font sizes increased** across both `_PIPELINE_SVG` and `_MEMORY_BANK_SVG`: `.ats-box-title` 20→24px, `.ats-box-sub` 13→15px (shared classes, affects both diagrams), and all memory-bank-specific classes (`ats-title-sm` 15→18px, `ats-sub-sm` 10.5→13px, `ats-title-xs` 12.5→15px, `ats-sub-xs` 9.5→11.5px, `ats-container-label` 15→18px, `ats-layer-label` 12→14px). Renamed "Agent Traces"→"Traces" and "Inefficient"→"Wasteful" (both to fit the larger font within their existing box widths without overflow — verified by hand-checking char-width estimates against box widths, then confirming visually in the browser), and shortened the RAG box's 3-line subtext to 2 lines for the same reason. Also renamed the "Balance granularity and signal to noise" card and reordered it to the 2nd position (swapped with "Dual embeddings") per direct request — the 2026-07-14 "What we learned" entry above still shows this card under its earlier name/position; this entry is the accurate current state.

**Extracted both diagrams as standalone files** (`docs/assets/pipeline_diagram.svg`, `docs/assets/memory_bank_diagram.svg`) and embedded them in the README under a new "Architecture" section. Found a second real bug during extraction: `_MEMORY_BANK_SVG`'s `<style>` block only defines its own classes (`ats-title-sm` etc.) — the `Traces`/`RAG`/`Memory Themes`/`Session Clusters` box titles use `.ats-box-title`/`.ats-box-sub`, which are only defined in `_PIPELINE_SVG`'s `<style>` block. On the Home page this silently worked because CSS classes are document-global and both SVGs render on the same page — but as a standalone file, `memory_bank_diagram.svg` had no definition for those classes, so the browser fell back to default serif/black text for exactly those 4 boxes (confirmed visually via a local `python3 -m http.server` preview before shipping). Fixed by copying `.ats-box-title`/`.ats-box-sub` into the standalone file's own `<style>` block. Both standalone files also got an explicit `#0e1117` background rect + 24px padding (via a `<g transform="translate(24,24)">` wrapper) since the inline Home.py versions rely on transparency + Streamlit's dark page background, which GitHub's README rendering doesn't provide.

**Doc accuracy pass** (requested alongside the diagram work): README had two stale defaults from before recent CLI changes — `ats cluster-memories`'s `--model` default was documented as `gemma3:12b` in two places (pipeline table and the ModelConfig note) but the actual code default is `gemma4:31b`; the `--min-recurrence` usage example showed `2` but the actual default is `1`. Both fixed. Added `ats cluster-memories --no-embed` to the usage examples now that auto-embed-on-mint exists.

## 2026-07-15 to 2026-07-16 — D-prompt extraction audit: four bugs found and fixed in production

Full manual audit of what the D-prompt actually extracts, prompted by wanting to validate the
episodic/procedural/semantic taxonomy hypothesis stated in `exploration.md` ("each memory type must
demonstrate distinct retrieval utility... a type that retrieves nothing useful gets consolidated or
removed"). Queried the live corpus directly (2,067 entities, 4,647 memories across 128 sessions)
and ran a fresh 6-chunk × 6-model comparison (`experiments/exp06_extraction_model_comparison/`) —
production gemma3:12b, gemma4:e4b, gemma4:12b, olmo-3:7b-think, Gemini 2.5 Flash, Claude Sonnet 5 (no
raw Anthropic API key in this harness, ran as a subagent instead).

**Four bugs found, all confirmed with query-backed evidence, not opinion:**

1. **Prompt-example leakage.** The D-prompt's own "GOOD:" few-shot examples for `decision`/
   `strategy`/`recovery`/`inefficiency` patterns got copied back verbatim instead of grounding in
   the actual chunk — confirmed by exact string match: the "chose a mid-size instruction-following
   model" example appeared in 12 unrelated sessions; the shopping-cart inefficiency example appeared
   twice despite nothing in the corpus involving a shopping cart. This is the model defaulting to the
   fluent example instead of returning `[]` when it can't find a real instance.
2. **Entity fragmentation.** `entity_resolver.py` resolved structured entities by `(entity_type,
   canonical_name)`, but the D-prompt classifies the same real-world entity inconsistently across
   chunks (`.env` as both `file` and `technology`) — 231 of 2,067 entities (11%) were silently split
   into duplicate rows, each diluting `degree_count`, the signal that gates memory promotion.
3. **Assertion-vs-citation confusion.** When a chunk shows the agent reviewing/quoting prior output
   (e.g. inspecting old memory records for quality), the prompt extracted the quoted content as if
   newly observed — traced to a concrete case: a `LangGraph` "memory" fabricated from an illustrative
   quote in a chunk that never touches LangGraph.
4. **`technology` catch-all overuse.** 54% of all entities typed `technology`, including
   hyperparameters, UI elements, and internal code symbols that should have been skipped per the
   prompt's own rules.

**Fixed all four in production**, verified with real before/after diffs on the exact chunks each bug
came from (not just re-running and hoping): `chunk_analyzer.py` — added an explicit priority-ordered
tie-break instruction before the four pattern shapes (never seen anywhere before this), a
same-chunk re-check reminder inside the entities field pointing back at the citation rule, and a
negative rule tightening `technology`'s definition (`config.py`). `entity_resolver.py` — added a
name-only cache/lookup (`get_entity_by_name` in `store.py`) checked before the type-specific
hash lookup, so a name that gets a different type on a later chunk reuses the first entity instead of
forking a new one; 4 new tests reproduce the exact `.env` collision. All 246 existing tests still pass
(6 pre-existing unrelated failures from the already-deprecated light-analytics/opencode-plugin code,
confirmed identical on `main` before this session's changes via `git stash`).

**A second bug found in the harness itself while testing, not the pipeline**: `DChunkAnalyzer.analyze()`
hardcodes `max_tokens=1024`. Fine for gemma3:12b, but gemma4:e4b/gemma4:12b/olmo-3:7b-think/Gemini 2.5
Flash all spend reasoning tokens before the JSON answer — at 1024 tokens the reasoning alone can
consume the whole budget, and the call silently returns empty (no error, no logged truncation).
Raising the budget to ~10-12K tokens for these models in the *test harness* fixed it; production
`chunk_analyzer.py`'s hardcoded 1024 was **not** changed, since the shipped default `chunk_summarizer`
(gemma3:12b) isn't affected — flagged as a real gap for whoever changes that default later.

**Full artifact + writeup**: `experiments/exp06_extraction_model_comparison/results.md`, plus two
published comparison pages (pre-fix and post-fix) referenced there. Local-model testing hit repeated
Ollama server crashes/timeouts from GPU contention with an unrelated concurrent job on the same
machine — required two full server restarts; noted since it cost significant wall-clock time and
will recur for anyone else running heavy local-model comparisons on a shared machine.

## 2026-07-20 — Full audit: 1,200 ATS-project memories vs. the hand-vetted design log — and two methodology bugs in the audit itself

Extended the same validation idea — "does extracted content agree with what actually happened" — into
a full audit rather than a spot-check: every memory/pattern extracted from ATS's own 22 self-hosted
dev sessions (not 45 — most of that count was the generic `claude_code` fallback workspace bucket,
mixing in other projects), judged against `design/design_decisions.md` + `design/exploration.md` by a
single continued Claude subagent (reads both logs once, then judges 12 batches of ~100 via
`SendMessage` continuation so criteria stay consistent). `experiments/exp07_ats_log_audit/`.

**Headline: 39.9% CORROBORATED, 6.4% CONTRADICTED, 53.7% UNCOVERED** (expected — the log only records
major decisions, not implementation detail). **13.8% error rate among the 556 memories the log
actually covers.** 44% of the 77 contradictions turned out to be the same 11 wrong claims independently
re-derived across different sessions (the deprecated segmentation/decision-point taxonomy alone was
wrongly presented as current guidance 7 separate times) — each extraction has zero visibility into
memories already extracted or corrected elsewhere in the corpus, so a stale claim, once wrong, gets
re-minted every time a later session touches that topic.

**Two real problems in the audit's own methodology, both caught by direct pushback, not by me
noticing on my own:**

1. **Wrong benchmark direction.** The 1,200 memories audited were extraction *output* from the
   pre-fix pipeline — re-extract with a different model or prompt and you get a different 1,200 rows
   with different IDs, meaning the whole expensive judging pass would need to be redone rather than
   reused. A real benchmark needs ground truth independent of any one extraction run — the log-derived
   facts are that, the extracted memories aren't.
2. **No temporal validity check.** A memory can be completely accurate at the moment it was
   extracted, even if the system later changed and the log's current entry says something different —
   normal knowledge decay, not a pipeline defect. The original audit's single CONTRADICTED verdict
   conflated "wrong" with "outdated." Worse: comparing a memory's `session_timestamp` against a log
   entry's date isn't enough either — a single Claude Code session can span *weeks* (confirmed
   directly: one session used in this audit ran 2026-06-08 to 2026-06-29, 21 days), so the session's
   start date can be wildly wrong for content from deep inside a long-running session. Pulling the raw
   JSONL transcript and searching for the actual conversation (not index-math against `turn_descriptors`,
   which don't line up 1:1 with raw message order) was the only reliable way to check — did this for
   two "hallucination" claims and found both were **real, accurate, same-session reports of live
   decisions being made**, not fabrications at all. See the 2026-07-21 entry below for the corrected
   re-run.

## 2026-07-21 — Post-fix re-extraction audit with temporal correction; two hallucinations survive the fix

Rebuilt the audit correctly per both corrections above: re-extracted fresh memories from the
*current* (fixed) pipeline on the single richest ATS session by historical memory yield (`2763d25c`,
2026-06-08, 51 chunks) using `gemma4:e4b`, `gemma4:12b`, and Gemini 2.5 Flash, then judged 884
memories/patterns with a Haiku subagent (cheaper, per explicit request) given an explicit temporal
rule: compare each memory's session date against the *actual dated log entry* it would contradict, not
just "does the log's current state agree." `experiments/exp08_postfix_reextraction_audit/`.

**479 CORROBORATED (35.0%), 26 HALLUCINATION (2.9%, wrong even at extraction time), 58 STALE (6.6%,
right then, superseded later), 491 UNCOVERED (55.5%).** Genuine error rate 7.8% — down from exp07's
conflated 13.8%, but that's not a clean improvement claim: applying exp07's old (no-temporal-split)
methodology to this same data gives 21.4%, *worse* than exp07. The real finding is that temporal
correction changes the read entirely — 45% of what would have looked like contradictions were normal
staleness, not bugs. `semantic` memories remained the worst type by far (14.6% hallucination rate,
independently confirmed on a second dataset) and accounted for 83% of all staleness — the same
mechanism flagged in exp07, now doubly confirmed.

**Two hallucination patterns survived the shipped fix untouched, recurring across all three models
independently**: "Track 2 can generate all three memory types" (contradicts a pre-session design
decision scoping Track 2 to procedural-only) and "`min_tool_using_exchanges` was raised from 4 to 10"
(verified via `git log -S` that the value was only ever 4, never 10, before being replaced entirely).
Neither is a citation-confusion or leakage bug — a different failure mode (misremembered architecture
facts / misremembered numbers) that the shipped fixes don't address.

## 2026-07-21 to 2026-07-22 — Taxonomy v2 redesign (experimental, NOT merged to production)

Following exp08's data, and a direct discussion of what's actually worth extracting given how
transient project-specific "facts" are in an actively-developed system: designed and iteratively
tested a redesigned taxonomy in `experiments/exp09_taxonomy_v2/v2_chunk_analyzer.py`. **Not wired into
production `chunk_analyzer.py`** — this is validated experimental work, kept separate pending a
decision on whether/how to ship it.

**Taxonomy changes:**
- **`semantic` removed entirely.** Per-chunk extraction structurally cannot verify "not a
  one-session observation" (`exploration.md`'s own stated criterion) — it never had visibility into
  other sessions to check. Content that would have been semantic must now be phrased as a
  point-in-time `episodic` observation instead of a timeless claim.
- **`episodic` gains a `status` field**: `resolved | open | reverted`. Drops the old "skip if no
  resolution" filter, which was silently discarding all unresolved deliberation.
- **`procedural` tightened** to durable public interfaces only (CLI commands, documented APIs), not
  internal implementation detail dressed up as a how-to.
- **New `preferences` field** for user-stated instructions/corrections — durable because they're
  about the user, not the project, and structurally absent from the old taxonomy.
- **`decision` and `strategy` patterns merged into one `strategy` type** — the boundary between
  "one-off choice" and "stated repeatable rule" was too soft to classify consistently; production data
  showed the same underlying event (an `OR`-vs-`AND` bug fix) split across both types.
  `recovery`/`inefficiency` unchanged (already the most reliable pattern types in both audits).
- **`recovery` now requires verifying evidence** in the chunk (test output, a passing check, a diff)
  that a fix actually worked, not just the agent's own stated belief — a fix suggested by a parallel
  session's review of a paper on agent trace failure fabrication, applied directly since it targets a
  failure mode already visible in the exp08 data (`recovery` had the second-worst hallucination rate,
  13.3%, behind only `semantic`).

**Reliability findings, from directly testing rather than assuming:**
- Added a `seed` parameter (missing before — only `temperature=0.0` was ever set) to separate genuine
  prompt ambiguity from GPU inference noise. Same chunk, same seed, 3 repeats: pattern *type* choice
  is now stable, but exact wording still varies slightly — Ollama/llama.cpp isn't fully deterministic
  even at temp=0+seed (matches community-reported behavior for Gemini's `seed` param too). Category
  stability is achievable and is what actually matters for storage; byte-identical output isn't.
- **Diagnosed an entity-completeness gap as a prompt problem, not a token-budget problem**, by
  directly testing rather than assuming: `gemma4:e4b` at `num_predict` 4096/8192/12288/16384 all
  produced `done_reason="stop"` at exactly 1473 tokens on the same chunk — under 10% of the largest
  budget. One added line clarifying "extract sparingly" bounds quality, not coverage, took entities
  3→5 on the test chunk (matching gemma3:12b exactly) with no new noise.
- **Model-split test** (entities from gemma3:12b, memories/patterns from gemma4:e4b, same chunk):
  genuinely combines each model's strength — split's entity count exactly matched gemma3:12b solo,
  memory/pattern counts closely tracked gemma4:e4b solo.
- **Same-model task-split test** (gemma4:e4b, one call for entities only, one call for
  memories/patterns/preferences only — isolating "focused prompt" from "different model" as the
  variable): did *not* win. Marginal entity/memory gains (+2/+1) cost a real pattern-count drop (7→5)
  and a preferences false-positive problem (all 3 "preferences" the dedicated call found were
  actually a design decision and two feature requests, misclassified without the other fields'
  definitions to anchor against). **The combined single-call prompt, with the thoroughness-wording
  fix, remains the best cost/quality tradeoff found** — beats the 2-model split on memories (9 vs 7)
  and patterns (7 vs 6) at half the inference cost, closing most of the entity gap too (24 vs 27).

**Open question, not yet resolved**: the `preferences` field works reliably on `gemma4:e4b`
(caught 2/2 hand-picked real preference statements, one near-verbatim) but not on `gemma3:12b`
(0/2, didn't just miscategorize — missed the content entirely). The `open` episodic status is
implemented and confirmed working (produced one real example) but hard to deliberately target — a
keyword search for "open question"/"trade-off" phrasing finds chunks that *discuss* a decision, not
reliably ones still unresolved at the chunk boundary.

**Consolidation design change agreed but not implemented**: `clustering_analytics.py`'s
`consolidate_memories()` currently accepts `min_recurrence=1` by default, meaning a claim repeated
3+ times within a *single* long session (a real risk — confirmed one ATS session spans 21 days) can
satisfy the cross-session confidence gate while the LLM consolidation prompt still says "extracted
from N different agent sessions," overstating corroboration that isn't there. Agreed direction: split
into two explicit levels — within-session consolidation (dedup/digest, no cross-session confidence
claim) and cross-session consolidation (the only place `min_recurrence≥2` and "stable fact" framing
should apply). Not yet built.

## 2026-07-22 — Decision: adopt taxonomy v2 prompt + switch default model to gemma4:e4b

Reviewed the exp09 final-round numbers (`v4_comparison.json`, 6 chunks) and decided to ship. Verified
the comparison directly rather than trusting the round-1 `results.md` summary (written before the
thoroughness fix and never updated): `gemma4:e4b` solo with the thoroughness wording fix scores
24 entities / 9 memories / 7 patterns / 1 preference, vs. the 2-model split's 27/7/6/0 and
`gemma3:12b` solo's 28/9/5/0 — ties or beats both alternatives on memories/patterns/preferences at
half the split's inference cost, closing most (not all) of the entity gap.

**Adopting, pending implementation** (not yet ported to production `chunk_analyzer.py` as of this
entry): the full `v2_chunk_analyzer.py` prompt — `semantic` removed, `episodic` gains `status`
(resolved/open/reverted), `procedural` tightened to public interfaces, new `preferences` field,
`decision`+`strategy` patterns merged into `strategy`, `recovery` evidence-gated, entity thoroughness
wording, `seed` param — with `gemma4:e4b` as the new default `chunk_summarizer`, replacing
`gemma3:12b`.

**Correction to the max_tokens finding reported earlier** (caught on review, not self-caught): the
"~10-12K tokens for thinking models" figure cited when recommending this switch was
`THINKING_MODEL_MAX_TOKENS` in the exp09 harness (`run_comparison.py`) — a blanket, never-tuned safety
ceiling applied to every thinking model under test, not a measured requirement. The actual measurement
(`gemma4:e4b`, full v2 combined prompt, `num_predict` swept 4096/8192/12288/16384) showed
`done_reason="stop"` at exactly `eval_count=1473` every time — the model chooses to stop, it isn't
being truncated, and 12288 was far more headroom than needed. That measurement is n=1 (one chunk,
3 entities/1 memory/1 pattern output) and not even the highest-output chunk in the 6-chunk set
(`opencode_embgeo` c27 produced 7 entities/3 memories/1 pattern for the same model) — so the true
per-chunk ceiling is somewhat above 1473, not 10x above it. Recommended production value: **~4096**,
comfortable headroom over anything observed without inheriting the unjustified 12288 figure.
Production's current hardcoded `max_tokens=1024` in `chunk_analyzer.py` must change together with the
model default — it was never touched during the exp06 fixes because the previous default
(`gemma3:12b`) doesn't need the headroom; `gemma4:e4b` does.

Not yet implemented as of this entry — `preferences`/`open`/`reverted` status remain lightly validated
(near-zero occurrence in the 6-chunk test set), and the DB `memory_type` CHECK constraint, migration,
and any downstream code referencing `semantic` still need updating before this can ship.

## 2026-07-22 — Taxonomy v2 shipped to production; two-level consolidation implemented; first live test ingestion

Ported `experiments/exp09_taxonomy_v2/v2_chunk_analyzer.py` into production `chunk_analyzer.py`,
per the decision above. `memories.memory_type` CHECK constraint now allows
`episodic|procedural|preference|pattern_strategy|pattern_recovery|pattern_inefficiency|cluster`
(`semantic` and `pattern_decision` dropped); a new nullable `status` column stores
`resolved|open|reverted` for episodic rows. `ModelConfig.chunk_summarizer` default is now
`gemma4:e4b`; `d_prompt_max_tokens=4096` replaces the old hardcoded `max_tokens=1024`. Every
place in the codebase that enumerated memory types (`Home.py`, `2_Explore_Sessions.py`,
`3_Explore_Memories.py`, `1_Query.py`, `annotation/memory_viewer.py`, `cli.py`, `mcp/server.py`,
`eval/level1a.py`, tests) updated to match — `light_analytics.py` deliberately left untouched
(dead code, no longer called from `cli.py`).

**Real bug caught during the first live test run, not in the experiment**: `result.get(key, "")`
only substitutes the default when a key is *missing*, not when the model returns an explicit JSON
`null` — `gemma4:e4b` did exactly that for the new `status` field on some `procedural` memories
(where the prompt says "leave it empty"), and `None.strip()` crashed the whole chunk, silently
dropping every entity/memory/pattern in it via the `except` handler. 3 of 51 chunks in the first
ingestion run were lost this way before the fix (`(x.get(k) or "").strip()` throughout the parsing
block, guards both missing-key and explicit-null). The exp09 experiment runs never hit this because
none of the six test chunks' procedural memories happened to trigger a null `status` — a reminder
that a 6-chunk validation set doesn't exercise every code path a schema change touches.

**Two-level consolidation implemented** in `clustering_analytics.py`, exactly as designed in the
2026-07-21/22 entry above: `consolidate_within_session()` (new) clusters each session's own
memories independently, prompt-framed as "repeated N times within this single agent session"
(never claims cross-session corroboration, since `distinct_sessions` is 1 by construction); the
existing `consolidate_memories()` is now explicitly the cross-session pass, `min_recurrence`
default raised `1→2` so it only fires on clusters that actually span multiple sessions. Both share
a `_cluster_and_mint()` core (HDBSCAN + LLM consolidation + DB write) to avoid duplicating that
logic. `ats cluster-memories` gained `--level {within,cross,both}` (default `both`).

**First live ingestion with the new pipeline** (5 sessions, `gemma4:e4b`, after backing up the old
DB to `traces_backup_pre_taxonomy_v2_20260722.db` — 128 sessions, 4,647 memories, kept as an
untouched comparison reference): 198 entities, 227 memories (116 episodic — 97 resolved / 19 open,
0 reverted; 7 procedural; 81 `pattern_strategy`; 12 `pattern_recovery`; 11 `preference`), then
`ats embed` + `ats cluster-memories --level both` → 17 within-session + 1 cross-session
consolidated memories (18 total, expected to be within-session-heavy with only 5 sessions
ingested — little chance of genuine cross-session overlap at this sample size). Spot-checked the
`preference` rows against this actual session's own content (e.g. "the user prefers gemm3:12b for
the current task", "the extraction process must adhere to... 'less is more'") — genuinely accurate,
not fabricated. `open` episodic memories also read as real unresolved deliberation, not noise.
Full test suite: same 6 pre-existing unrelated failures as before this change (`light_analytics`
dead-code stubs, one `opencode` bad-path test), confirmed via direct comparison — no regressions.
Streamlit UI (`Home`, `Explore Memories`, `Query` pages) spot-checked in-browser: no console errors,
type breakdowns render cleanly with the new taxonomy.

**Known follow-up, not blocking**: the Home page's "Memory Bank" SVG diagram is a static
illustration (not data-driven) still showing the old 8-box taxonomy (`Semantic`, `Decision`
included) — needs a manual relayout to the new 6-type set, out of scope for this change since it's
a visual-design task, not a mechanical list edit like the rest of the UI updates. One stale
one-line mention (`"Ollama + gemma3:12b — extraction LLM backbone"` in the "Built with" section)
was fixed to `gemma4:e4b` in passing since it was a trivial string swap.

## 2026-07-23 — Follow-ups: conflicts table hookup, reverted-status example, cluster-level UI, Memory Bank diagram

Addressed several items from the 2026-07-22 "known follow-up" list, prompted by direct questions
about each one rather than letting them sit as vague TODOs.

**Conflicts table gets its first real writer.** The `conflicts` table has existed in the schema
since early on with zero writers. Rather than build a separate detection pass, hooked it into
`clustering_analytics.py`'s existing consolidation LLM call: that call already reads every cluster
member's content to write a merged summary, so it was extended to also flag genuine contradictions
between members — not just "same topic, different phrasing," but actual incompatible claims (a
config value that changed between two entries, a decision reported one way then the opposite way,
a reverted approach still cited as current) — at zero extra inference cost. When flagged, writes
`memory_id_a`/`memory_id_b`/`conflict_type` to `conflicts`, leaving `resolution` unresolved.
Deliberately basic — this only catches conflicts between memories HDBSCAN actually clusters
together (a contradiction between two memories that never land in the same cluster is invisible to
it), and nothing in the UI surfaces flagged conflicts yet. Both limits, plus the still-unused
`conflict_verifier` config field, are tracked in the new `notes/TODO.md`.

**Added a worked example for episodic `status="reverted"`.** Direct question about the prompt
surfaced that `resolved` and `open` both had illustrative examples but `reverted` had only a
one-line definition — and `reverted` had exactly 0 occurrences across every test run (exp09) and
the first real ingestion (25 sessions). Added a concrete example matching the same style as `open`'s.
Not yet re-verified against a larger ingestion.

**Two-level consolidation now visible in the UI**, not just in the database. `3_Explore_Memories.py`'s
"Cluster themes" section gained a Cross-session / Within-session / Both toggle (previously showed
every `cluster`-type memory undifferentiated); the embedding UMAP's "Cluster status" coloring now
splits "Consolidated cluster memory" into "Cross-session cluster" vs. "Within-session cluster"
instead of lumping both levels into one bucket.

**Home page's "Memory Bank" SVG diagram updated** to match the shipped taxonomy — removed the
`Semantic`/`Decision` boxes, added `Preference`, rebalanced the pattern-type row from 4 boxes to 3
(Strategy/Recovery/Wasteful) to fill the same width. This diagram is a static illustration, not
data-driven, so it silently drifted out of date when the taxonomy shipped on 2026-07-22 — nothing
would have caught this without a visual check.

**Started a durable `notes/TODO.md`** to track the above limits plus pre-existing unrelated gaps
(the old backed-up DB's un-cleaned duplicate-session rows from before the 2026-07-14 fix,
`retrieval.py`'s lack of temporal-recency awareness, `migrate()` not extended for the new schema)
so they persist across sessions instead of living only in chat history.

**Confirmed the archive-duplicate-ingestion bug (found and root-caused 2026-07-14, commit
`dc4eb47`) is holding** on the current ingestion: 0 duplicate `content_hash` pairs across all
sessions ingested into the fresh `traces.db` so far. The old backed-up DB
(`traces_backup_pre_taxonomy_v2_20260722.db`) still carries the original 33 duplicate pairs (66
rows) from before that fix — never retroactively cleaned, now low-priority since it's a passive
reference snapshot rather than something being built on further.

---

## 2026-07-23 — Removed dead `same_entity` session-graph edge code, completing the 2026-07-11 analytics-light deprecation

Prompted by an audit of all session-session/chunk-session/memory-session/memory-memory edge
mechanisms in the codebase (in response to a question about building a graph layer on top of the
vector DB + clustering). Found `session_graph_edges` actually has **four** historical edge-type
mechanisms, not three: `structural` and `workspace` (both ingestion-time, live), `shared_memory`
(clustering-time, live), and `same_entity` — a fourth mechanism in `light_analytics.py`
(`build_same_entity_edges()`, Pipeline 2a / entity-frequency promotion) that survived the
2026-07-11 cleanup (`## 2026-07-11 — Cleanup: deprecate Track 2 CLI, analytics light, graph walk`)
as unreachable dead code. That cleanup gutted the CLI command (`ats analytics light` now only
prints a deprecation notice) and the `SQLiteStore` side (`reset_light_analytics()` /
`delete_entity_light_analytics()` now raise `NotImplementedError`), but missed
`build_same_entity_edges()` itself and its call site inside `LightAnalyticsPipeline.run()` —
harmless because nothing calls `run()` anymore, confirmed by grepping `cli.py` (zero references)
and the live corpus (`traces.db`: `structural`=316, `workspace`=450, `shared_memory`=52,
`same_entity`=0 rows).

**Fix:** deleted `build_same_entity_edges()`, its call site (replaced with a comment noting the
merge into `structural`), and the 3 tests that directly exercised it
(`test_build_same_entity_edges_*` in `test_light_analytics.py`). Also corrected two now-stale
references left over from `same_entity` actually having existed in code (not just the earlier,
never-implemented Decision-21-era design): `models.py`'s `SessionGraphEdge.edge_type` comment
(`"structural" | "same_entity" | "enrichment"` → the real three live types) and
`exploration.md`'s Pipeline 2a diagram, which still listed "Build same_entity edges" as a live
step. Left `write_analytics_batch`, `SessionGraphEdge`, and the rest of the
deprecated-but-still-imported `LightAnalyticsPipeline` class untouched — those are pre-existing
dead-code-adjacent surface (5 unrelated pre-existing test failures in `test_light_analytics.py`,
all stemming from the 2026-07-11 CLI deprecation, confirmed unchanged before/after this fix) and
out of scope for this specific cleanup. (That whole module was removed later the same day — see
below.)

**Decision on merge scope:** considered widening `structural`'s ingestion-time trigger
(`config.pipeline.structural_entity_types`, currently `file`/`commit`/`pr` only) to cover more
entity types, matching what `same_entity` originally intended (any promoted entity, not just
structural ones). Declined — there's no entity-promotion threshold left at ingest time (that logic
lived entirely in the now-dead `get_entities_for_promotion`), so broadening would mean either
re-implementing a threshold gate or creating an edge for every shared entity of any type/frequency,
a real scope increase with no immediate driving use case. `structural` stays as-is.

**Follow-ups surfaced but not done** (candidates for future work, not committed to):
- ~~The `session.structural_embeddings`-based cosine-similarity session neighbours shown in Home's
  Structural tab are computed ad hoc at page-render time and never persisted~~ — **fixed later the
  same day**, see the next entry below (`structural_similarity` edge type).
- No index exists on `session_graph_edges` beyond the auto-index on its composite primary key
  (`source_session_id, target_session_id, edge_type, via_entity_id`) — fine for the current
  `WHERE source_session_id = ?` access pattern at ~800 rows, but not for `edge_type`- or
  `via_entity_id`-scoped queries at larger scale. **Still open.**
- ~~`chunk_search()` in `retrieval.py` has no graph-expansion option at all~~ — **fixed later the
  same day**, see the next entry below (`chunk_search(include_graph=True)`).
- `clusters`/`cluster_edges` (schema since Decision 17, "design for it now, build it later") are
  fully dead — 0 rows, 0 writers anywhere. HDBSCAN clustering writes directly to
  `sessions.cluster_id`/`session_type_labels` instead, so there's no `clusters` node table for
  `cluster_edges` to reference. Would need the clustering step to mint hierarchical `clusters` rows
  first before this table means anything. **Still open** — see `notes/TODO.md` for the live-query
  design agreed on instead of resurrecting this table as-is.

---

## 2026-07-23 — Persisted structural_similarity session edges + chunk_search graph expansion

Two follow-ups from the same-day graph-edge audit (see the entry above), picked up as concrete
"close the gap" work rather than left as documented-but-not-done.

**`structural_similarity` — the 5th session_graph_edges type.** The Home page's Structural tab
computed cosine similarity over `session_structural_embeddings` live, at page-render time, and
never wrote it anywhere — a real session-session relation that existed only in the UI, invisible to
`graph_walk()`/`recall(include_graph=True)`. Added `build_structural_similarity_edges()` (pure
function, `pipeline/utils.py`): full pairwise cosine recompute, top-8 neighbours per session above
`min_similarity=0.5`, both directions, weight = cosine value. Wired into `ats embed` (after the
existing structural/topic embedding backfill), writing via a new
`store.replace_session_graph_edges_of_type()` — a full delete-then-reinsert of that one edge_type,
not an incremental accumulation.

That full-replace design directly answers the "wouldn't this go stale on every rerun?" question
raised about `cluster_edges` (see above): there's no per-run snapshot to go stale, because nothing
is versioned by `analytics_run_id` — each `ats embed` run recomputes the complete current edge set
from whatever structural embeddings exist *right now* and replaces the old set wholesale. A pair
that drops out of top-8, or whose embedding changed, simply isn't in the new set — no orphan rows,
no run-to-run drift to reconcile. Verified end-to-end against a throwaway copy of the live
`traces.db`: 50 sessions with structural embeddings → 400 edges in 0.18s, confirming this is
computationally trivial at current scale. Home page's live computation was left as-is (not
refactored to read the new persisted edges) — it's cheap per-session and correct; the point of
persisting was to make the relation available to graph traversal, not to deduplicate the UI's own
display logic.

**`chunk_search(include_graph=True)`.** Filled the "chunk retrieval can't hop" gap identified in
the edge audit: added one-hop expansion via `occurrences(record_id, entity_id)` — after RRF fusion,
the top 3 hits' chunks are expanded to any other chunk (in any session, regardless of the caller's
`session_id` filter) that mentions the same entity, found with a plain self-join on `occurrences`.
No new table needed; `occurrences` already carries chunk-granularity entity links. Unlike
`recall()`'s existing `include_graph` (which gave expanded memories a flat `rrf_score=0.0`, flagged
in the entry above as a real ranking gap), expanded chunks here get `seed_score * 0.5` — decayed
relative to the chunk that found them, but still able to outrank a weak original hit, and
comparable against each other. `recall()` itself was left unchanged this pass — fixing its
flat-zero scoring was picked up later the same day (see the "Cluster-memory supersession..." entry
below). `mcp/server.py`'s `chunk_search` tool also gained the `include_graph` param, so this is
reachable from Claude Code, not just the engine layer.

Both changes: 12 new tests (`test_pipeline_utils.py`, new classes in `test_retrieval.py`), full
suite at 255 passed / 6 pre-existing failures (same ones as before this work, all from the
2026-07-11 `analytics light` deprecation plus one unrelated opencode plugin test — confirmed
unchanged).

---

## 2026-07-23 — Removed the now-fully-dead LightAnalyticsPipeline module + its tests

Follow-up to the same-day `same_entity` edge removal: confirmed `process_entity`,
`compress_to_facts`, `extract_memories`, `generate_entity_profile`, and `LightAnalyticsPipeline`
itself have zero callers anywhere outside `pipeline/light_analytics.py`'s own test file (grepped
`src/` and `tests/`) — the whole module has been unreachable since the 2026-07-11 `analytics light`
CLI deprecation, not just the one method removed earlier today. Deleted
`pipeline/light_analytics.py` and `tests/test_light_analytics.py` outright.

**Deliberately left in place:** the 5 deprecated CLI command stubs in `cli.py`
(`analytics signals/step-scoring/critical-steps/light/reset-light`) — these still print a useful
"here's what replaced this" message rather than a bare "no such command" error, and cost nothing to
keep. Also left `LightAnalyticsConfig`/`ModelConfig.light_analytics_llm` in `config.py` untouched —
confirmed dead (only referenced by the now-deleted test file) but removing config schema is a
separate, unrequested scope; low-risk to leave as inert unused fields.

---

## 2026-07-23 — Cluster-memory supersession, decayed graph-expansion scoring, session_search graph expansion

Closed out the three concrete follow-ups from today's graph-edge audit that were flagged as
open (`notes/TODO.md`) rather than left there.

**Cluster memory supersession.** `ats cluster-memories`' resume-safety check only skips minting a
new cluster memory when ≥80% of its members are already covered by a prior one — below that
threshold, a brand-new cluster memory gets minted from scratch even though most of its members
already had an older, less-evidenced cluster memory describing essentially the same theme. That
old row never got marked as superseded, so both surfaced independently in retrieval as if they
were distinct insights. Added `ClusteringAnalyticsPipeline.mark_superseded_clusters()`: after
minting a new cluster memory, finds prior cluster memories sharing members with it (via a
`memory_cluster_members` self-join) and marks any where a **majority of the prior cluster's own
members** are now in the new one (`_SUPERSEDE_OVERLAP_THRESHOLD = 0.5`) as `status='superseded'`,
with `metadata={"superseded_by": <new_id>}` — reusing the existing `status`/`metadata` columns
already on `memories`, no schema change. Guarded so a memory already superseded isn't reassigned to
a later, unrelated cluster (`WHERE status != 'superseded'` in the UPDATE). `recall()`'s final
memory fetch now excludes `status='superseded'` rows. Extracted as its own testable method rather
than left inline in `_cluster_and_mint()`, since forcing a specific HDBSCAN rerun boundary case in
a test is fragile — 4 direct unit tests instead (`tests/pipeline/test_clustering_analytics.py`).

**Decayed graph-expansion scoring, unified across all three retrieval units.** `recall()`'s
`include_graph` gave expanded memories a flat `rrf_score=0.0` (found and flagged, not fixed, in the
first audit entry today) — now decays from the seed's own score via a new shared
`RetrievalEngine._GRAPH_EXPANSION_DECAY = 0.5` constant (same one `chunk_search()` already used
under the name `_CHUNK_GRAPH_DECAY`, renamed for reuse). Expansions can never outrank the seed that
found them but now compete honestly against each other and against weak direct hits, instead of
being invisible to ranking.

**`session_search(include_graph=True)`.** The only one of the three retrieval units (memory,
chunk, session) still missing graph expansion. Simpler than `recall()`'s version: sessions are
already `session_graph_edges`' own nodes, so no `memory_sources`/`occurrences` pivot is needed —
`_session_graph_expand()` reads `session_graph_edges` directly for the top-3 fused hits. Uses
`MAX(weight)` per neighbour to collapse multiple edge types between the same pair, and multiplies
that weight into the decay — meaning `structural_similarity`'s continuous cosine value (added
earlier today) now directly scales how strongly a similarity-based neighbour ranks, while
`workspace`/`structural`/`shared_memory` (always weight=1.0) leave the plain decay unchanged.
Wired a `--include-graph` flag onto `ats session-search`; not exposed via MCP, since no
`session_search` MCP tool exists at all (independent gap, `recall`/`chunk_search`/`graph_walk` are
the only three tools today).

Verified session_search end-to-end against a throwaway copy of `traces.db`: 5 results without
graph expansion → 22 with it, expanded results correctly scored below their seeds (0.0164 vs.
0.0328/0.0315 for the top two seeds).

10 new tests across `test_clustering_analytics.py` and `test_retrieval.py`. Full suite: 247
passed, 1 pre-existing unrelated failure (`test_opencode_plugin.py`, predates all of today's work).

Full suite: 238 passed, 1 pre-existing unrelated failure (`test_opencode_plugin.py`, bad-path
handling, predates all of today's work) — the 5 `analytics light`-related failures that existed
before this pass are gone along with the tests that produced them.

## 2026-07-24 — The 2026-07-14 duplicate-session fix was incomplete; closed the gap

Surfaced during the full 113-remaining-session ingestion kicked off to replace the old DB: a
"0 duplicate `content_hash` pairs, confirmed clean" check run right before that ingestion started
was accurate in the moment, but the ingestion itself surfaced **33 new duplicate pairs** — 100% the
same flat-archive-path-vs.-project-nested-path shape as the original 2026-07-14 bug
(`dc4eb47`).

Root cause: that fix added a content-hash-across-all-paths guard
(`get_ingested_state_by_content_hash()`) to `_scan_archive_files()` only. `_process_file()` — the
shared entry point for the *live* scanners (`_scan_claude_code_files`, `_scan_gemini_files`) — never
got the same guard. So a file discovered live whose content already existed under a different
already-archived path (e.g. a flat copy an earlier archive-only scan found, later seen live again
under its real project-nested path) still re-archived and re-ingested as a second, fully duplicate
session, with its own duplicate entities/memories/occurrences. The original fix closed exactly one
of the two paths that can produce this shape, not both.

Fixed by adding the identical guard to `_process_file()` (`scanner/corpus_scanner.py`) — `dup.id !=
file_id` still allows a genuine content update to a path's own already-tracked file_id to proceed
normally, only skipping when the content is a real duplicate of a *different* path. Added
`tests/scanner/test_corpus_scanner_dedup.py` — two tests, one confirming the skip (verified it fails
against the pre-fix code, passes against the fix), one confirming an in-place content update to an
already-tracked path still isn't blocked by the new guard.

**Not yet cleaned up**: the 33 duplicate pairs (66 rows) already sitting in the current `traces.db`
from before this fix landed. Same reasoning as 2026-07-14 — deleting session rows and all their
cascading records/entities/memories/embeddings is destructive, and most FK relationships in this
schema lack `ON DELETE CASCADE`, so it needs a deliberate multi-table script, not run without the
user's confirmation.

## 2026-07-24 — Cleaned up the 33 duplicate-session pairs, with explicit confirmation

User confirmed: clean up before continuing. Backed up `traces.db` to `/tmp/traces_pre_dedup_cleanup_backup.db`
first (destructive operation, no `ON DELETE CASCADE` on most FKs — see above).

Verified a clean, mechanical keep/drop rule across all 33 pairs before touching anything: every
pair was exactly one flat archive path (`archive/<type>/<file>`) and one project-nested path
(`archive/<type>/<project_dir>/<file>`) — the flat-vs-nested shape both the 2026-07-14 and
2026-07-24 bugs produce. Checked the resulting `sessions.workspace_id` for both sides of a few
pairs: the flat one always fell back to a broken generic value (`'claude_code'`/`'gemini_cli'`,
the source-plugin name, not a real workspace), the nested one always had the actual workspace path.
Verified this held for **all 33 pairs**, not just the sample, before running anything.

Dropped all 33 flat-path duplicates via the existing `ats drop-session` command — reused its
already-tested cascade-delete (FTS cleanup, orphaned memory/entity pruning, degree_count
recalculation, vec0 orphan cleanup) rather than hand-rolling new deletion logic. One real gotcha
caught before it caused a repeat bug: `drop-session` resets the dropped file's `ingestion_state` to
`status='pending'` (correct for its actual use case — "fix a bad ingestion and re-run it") — for a
permanent duplicate this would have looped it right back into being re-ingested as the identical
duplicate on the very next `ats ingest`. Explicitly set those 33 rows to `status='skipped'`
afterward to prevent that.

**Verified clean**: `ats ingest --dry-run` afterward shows only genuinely new/updated files (this
session's own still-growing transcript, 2 previously-known empty sessions), no duplicate paths;
0 active (`ingested`/`pending`) duplicate `content_hash` pairs remain (36 total groups by
content_hash, but all explained: 33× `(ingested, skipped)` — resolved, no longer actionable — plus
3 pre-existing groups among already-`failed` empty sessions, harmless). Sessions: 99 (132 − 33,
exact). Full test suite: same 1 pre-existing unrelated failure, no regressions.

## 2026-07-24 — `providers/ollama.py`'s HTTP timeout was wrong on both dimensions; fixed the real one

Discovered while running `ats cluster-memories --level both` on the full post-cleanup 103-session
corpus (~3,000 base memories, by far the largest consolidation pass run so far). The cross-session
pass's one remaining candidate cluster failed with `"Ollama server not reachable... timed out"` —
repeatedly, across 5 straight retries, while every other cluster and the entire within-session pass
(75 new mints) succeeded cleanly in the same run.

**Misdiagnosed twice before finding the real cause** — worth recording since both wrong turns were
each individually plausible and each "fixed" nothing:
1. First theory: `gemma4:31b` (21GB) cold-loading, hitting the 30s connect timeout. Direct `curl`
   tests landed at 29.2-29.4s — suspiciously close to 30s — so bumped connect 30s→60s. Failed again,
   identically, 3 more times, including once run immediately after confirming via `ollama ps` that
   the model was already resident (ruling out a fresh reload).
2. Second theory: `ats cluster-memories` runs a real HDBSCAN pass over the full embedding set before
   the LLM call fires; on a much larger corpus that pass takes long enough that Ollama's ~4min
   idle-unload could trigger a genuine cold reload mid-pipeline, exceeding even 60s. Bumped connect
   60s→180s. Failed a 5th time, identically.
3. **Actual cause, found by reproducing `ClusteringAnalyticsPipeline.consolidate_memories()` directly
   in Python** (not through the CLI, so the raised exception and elapsed time were both visible) —
   the call succeeded in exactly **310.4 seconds**, landing right on the *old 300s read timeout*
   that neither fix had touched. It was never a connection problem — `gemma4:31b` genuinely takes
   this long to generate a real consolidation response (many cluster members + the conflict-detection
   instructions added on 2026-07-23) on this hardware. Every earlier diagnostic call that "succeeded
   quickly" (27-38s) used a short, simple test prompt, not a real cluster's full content — not
   representative of the actual workload.

**Fixed**: read timeout `300s → 900s` (connect timeout left at 180s, which is still reasonable
headroom for a genuine cold reload — that part of the second theory wasn't wrong, just not what was
actually happening this time). Retried once more: succeeded, minting the 1 remaining cross-session
cluster memory that every prior attempt had missed.

**Lesson for next time a "timed out" error shows up**: check *how close* the failure lands to an
existing timeout value before assuming which one — 29-30s against a 30s connect timeout look exactly
as suspicious as 310s against a 300s read timeout, but they're different problems with different
fixes, and bumping the wrong one burns real time without changing the outcome.

**Final state after this pass** (full taxonomy v2 rollout, complete): 103 sessions, 3,988 memories
— 1,458 episodic (1,279 resolved / 177 open / 2 unlabeled edge cases, content intact), 180
procedural, 184 preference, 1,007 `pattern_strategy`, 148 `pattern_recovery`, 9
`pattern_inefficiency`, 162 `cluster` (148 within-session + 14 cross-session). **First real-data
exercise of two features built but previously untested at scale**: `conflicts` table now has 7 rows
(the consolidation-time contradiction detector added 2026-07-23 firing on genuine data for the first
time); 159 cluster memories are marked `status='superseded'` — expected and correct given this run's
corpus roughly quadrupled (25→103 sessions) since the last consolidation pass, so most prior clusters
were legitimately absorbed into larger, more complete ones rather than being stale duplicates.
0 active duplicate `content_hash` pairs, full test suite: 1 pre-existing unrelated failure only.

## 2026-07-25 — UI audit: 2 real bugs, 4 built-but-unshowcased features, all fixed

Systematic pass across all four Streamlit pages, prompted by "check the UI — anything stale, any new
feature not showcased?" No stale taxonomy references remained (already cleaned up 2026-07-22/23), but
found real gaps between what the backend now does and what the UI shows for it.

**Bug 1 — Home page's "Related Sessions" tab would render `"None (None)"`.** The fallback branch for
unrecognized `session_graph_edges.edge_type` values did `f"{entity_name} ({entity_type})"` — correct
for `structural` edges (via_entity_id points at a real entity), but `structural_similarity` edges
(added 2026-07-23, 824 of them, the single most numerous edge type in the corpus) have
`via_entity_id=""`, so the LEFT JOIN finds no entity and both fields are `None`. Fixed: added an
explicit `structural_similarity` branch (`f"similar interaction shape ({weight:.2f} cosine)"`,
selecting `sge.weight`), and made the fallback check `entity_name` truthiness before using it —
falls back to the raw `edge_type` string for anything future rather than repeating this failure mode.
A second, separate stale caption below the same table (listing connection reasons but never
mentioning structural similarity) was also missed and fixed.

**Bug 2 — Explore Sessions' session-relationship graph couldn't show `structural_similarity` at
all.** The edge-type multiselect only offered `["workspace", "structural", "shared_memory"]` —
`structural_similarity` wasn't an option, so the graph visualization was structurally blind to its
own densest edge type. Added it as a 4th option, deliberately **not** in the default selection (it's
a soft, continuous-similarity signal rather than a hard shared-thing connection, and would dominate
the graph's density if defaulted on) — the underlying graph-rendering code was already
edge-type-agnostic, so no other change was needed once the option existed.

**4 features built but with zero UI presence, all given one:**
- **`include_graph`** (cross-session graph expansion on `recall`/`chunk_search`/`session_search`,
  exposed via CLI flags and MCP but never in Streamlit) — added a checkbox on the Query page, wired
  through to all three search calls.
- **`conflicts` table** (7 real rows) — new "Detected conflicts" section on Explore Memories, listing
  each flagged pair's two full contents side by side, `conflict_type`, and resolution status.
- **Cluster supersession** (`status='superseded'`, 159 rows) — Cluster themes chart gained an
  "Include superseded" checkbox (off by default, matching what `recall()` actually returns) with a
  `[superseded]` label on shown rows; the UMAP's "Cluster status" coloring gained a dedicated
  "Superseded cluster" category instead of lumping them in with live clusters.
- **Episodic `status`** (resolved/open/reverted — one of taxonomy v2's headline additions, 177 open
  memories exist right now) — added a "Status" column to the Query page's recall results table plus a
  client-side status filter (so "show me what's still open" is one dropdown away), a `[status]` prefix
  badge on Explore Memories' per-type preview, and a new "Open episodic" metric on that page's top
  stat row. Also added `status` to `RetrievalEngine.recall()`'s SELECT — it was computing the
  superseded-exclusion filter on this column already but never returning it to the caller.

Full test suite clean (1 pre-existing unrelated failure only) after each change; visually verified in
the browser for 5 of 6 (Home page load, Explore Memories new sections rendering with real data,
Query page checkbox/filter present) — the "Related Sessions" tab's exact cell text under the
`structural_similarity` fix specifically hit repeated Streamlit-tab browser-automation flakiness
(React synthetic events not firing from raw DOM `.click()`), not verified pixel-for-pixel in-browser,
but confirmed correct via direct code review, a successful compile, and direct DB confirmation that
the test session has 8 real `structural_similarity` edges to exercise the fixed code path — the same
code pattern already proven correct for the parallel `shared_memory` branch.

## 2026-07-27 — `recall()`'s `memory_type` filter was applied after RRF truncation, not before

`RetrievalEngine.recall()` (`pipeline/retrieval.py`) takes an optional `memory_type` filter, but it
was only ever wired into the semantic leg (`search_memories_semantic`'s `memory_type` param, JOIN to
`memories`). `search_memories_bm25` had no such filter — it searched `memories_fts` unconditionally.
`recall()` then fused both legs via `_rrf()`, truncated to `top_k`, and only *afterward* applied
`memory_type` via a `WHERE memory_type = ?` on the final SQL fetch that hydrates the fused ids.

**The gap this created:** a memory of the wrong type could still rank well on the BM25 leg alone
(no filter to stop it), earn a reciprocal-rank score, and occupy one of the `top_k` slots in `_rrf()`'s
truncated output. The post-hoc `WHERE memory_type=...` then silently dropped it — but by that point
the truncation had already happened, so a correctly-typed memory that would have made the cutoff had
the wrong-type one been excluded upfront never got the chance. Net effect: `recall(memory_type=...)`
could return fewer than `top_k` results even when enough correctly-typed memories existed just below
the RRF cutoff — a silent under-return, not a crash, so easy to miss without noticing the count.

**Fix:** gave `search_memories_bm25` the same optional `memory_type` param `search_memories_semantic`
already had, with a JOIN to `memories` to filter before FTS5 ranks and `LIMIT`s — the same pattern
`search_records_bm25` already uses for its optional `session_id` filter. `recall()` now passes
`memory_type` into both legs, so filtering happens before `_rrf()` truncates, not after. No interface
change — `recall()`'s signature and the CLI-facing behavior are unchanged, only which memories are
eligible to occupy a slot before truncation.

## 2026-07-25 — Cluster memory retrieval-health view; `memory_retrievals` goes from orphan producer to live consumer

Prompted by reading the HarnessX paper (arXiv 2606.14249, "A Composable, Adaptive, and Evolvable
Agent Harness Foundry") and looking for transferable ideas. Its most portable mechanism: every
harness edit ships with a manifest predicting which tasks should flip, and the pipeline tracks what
fraction of predicted flips actually materialize — a decaying "ship-prediction accuracy" is their
leading indicator that an evolution strategy has gone stale, detected before the system would
otherwise plateau silently. Looking for an ATS analog of "does a change's predicted effect actually
materialize" surfaced a real gap.

**The gap:** `memory_retrievals` (Stage 6, added 2026-06-11) logs one row every time `recall()` or
`graph_walk()` surfaces a memory — `(session_id, exchange_idx, memory_id, query_text,
relevance_rank, relevance_score, surfaced)`. It was built explicitly as "instrumentation, no
posterior" — a producer laid down ahead of its planned consumer, the Bayesian-scored procedural
memory spec in `deprecated_memory_evolution.md` (`memory_evidence` table, `posterior_mean`,
promotion/retirement thresholds). That spec was deprecated 2026-07-22 along with the rest of the
Track 2 lineage it was built on top of (`trajectory_signals.md`, hard-deprecated 2026-07-11, 0.33
precision against an 0.80 ship-gate, never shipped). Nobody revisited the producer once its only
planned consumer died: `retrieval.py` kept writing to `memory_retrievals` on every call, `store.py`
kept an insert path, `drop-session`'s cleanup kept pruning orphaned rows — all still live — but
nothing anywhere read the table. Not documented as a known gap in `notes/TODO.md` either; it was
genuinely missed, not a deliberate deferral.

**Decision: wire up a lightweight consumer, not the full posterior.** Reviving
`deprecated_memory_evolution.md`'s Bayesian scoring wholesale isn't warranted — it was built on
per-step evidence labels (`critical_steps.tag`, `step_scores.progress_vector`) that no longer exist.
But the raw retrieval log needs no such inputs to answer a much smaller, still-useful question: does
a minted cluster memory keep getting surfaced, or did it mint once and go silent? New "Cluster memory
retrieval health" section on Explore Memories (`ui/pages/3_Explore_Memories.py`, right after Cluster
themes): a pure read-side aggregate — `memories` (type `cluster`, excluding `status='superseded'`
since supersession already answers staleness for those) LEFT JOINed against `memory_retrievals`,
grouped per cluster memory into retrieval count, days since mint, days since last retrieval — sorted
least-retrieved-first, with a headline "N / total never retrieved" line. No schema change, no new
writes, no posterior math.

**Empirical result, not just a hypothetical:** verified live against the production DB (99 sessions,
3148 memories) via a scratch Streamlit instance — all 3 currently-active cluster memories show zero
logged retrievals. The table wasn't merely dormant in theory; it's producing essentially no data for
cluster memories specifically in practice either, which is itself useful context before trusting any
future "staleness" reading built on top of it.

**Two cluster-lifecycle invariants confirmed along the way (no code change):**

- `memory_cluster_members` has no timestamp column, and that's not a gap. The only write site
  (`clustering_analytics.py`'s `_cluster_and_mint`) inserts a cluster's full membership in one batch
  at mint time, under a freshly generated `cluster_memory_id`; no code path ever appends a member to
  an existing cluster row afterward. Every re-run of `ats cluster-memories` either skips a cluster
  (≥80% already covered by an existing cluster memory — resume safety) or mints an entirely new
  `cluster_memory_id` with its own one-shot membership write. A per-member timestamp would just
  duplicate `memories.created_at` on every row, since all of a cluster's members are written in the
  same transaction. "Evolution" of a cluster is represented at a coarser grain — a chain of
  mint/supersede events (`status='superseded'` → `metadata.superseded_by` → the next cluster's
  `created_at`), not in-place membership growth.
- `mark_superseded_clusters()`'s check (added 2026-07-23) is intentionally overlap-based —
  `len(old_members & new_member_ids) / len(old_members) >= 0.5` — not diff-based, which was worth
  re-confirming explicitly since "supersession" reads at first glance like it should hinge on what
  *changed*. It doesn't, because HDBSCAN re-clusters the entire embedded-memory corpus from scratch
  on every run with no persistent cluster identity across runs — shared membership is the only signal
  available to tell "this new cluster is a regrown version of that old one" (high overlap, correctly
  superseded) apart from "this new cluster just happens to sit near an unrelated old one" (low
  overlap, both stay `active`). A diff-based signal would have inverted the logic. No change needed —
  the existing 2026-07-23 log entry already described this correctly.

**Attribution note:** this landed inside commit `25448f1` ("Surface 4 built-but-unshowcased features
in UI; fix 2 structural_similarity bugs") alongside a concurrent session's independent UI-parity
audit (`structural_similarity` edge-type fixes on Home and Explore Sessions, the `include_graph`
checkbox, and the `conflicts`/cluster-supersession/episodic-`status` UI surfacing described in the
entry directly above this one) — both sessions were editing the same working tree, and a single
`git commit` swept up both sets of changes together. Recorded here separately since the retrieval-
health work and its motivating analysis weren't part of that commit's message or its own log entry.

## 2026-07-29 — Exp10 retrieval head-to-head: raw grep vs. design log vs. ats (MCP), rerun with both fixes

Reran the multi-session retrieval-quality investigation's head-to-head after both the RRF
guaranteed-slot fix and workspace-scoped filtering landed, this time using the full ats MCP
surface (`recall` + `chunk_search`, `workspace_id` restricted to this project) instead of just
`recall` alone, which the investigation's first pass had mistakenly treated as "ats" in full.
`experiments/exp10_retrieval_h2h/` — script, verified ground truth (`manifest.json`), raw
results (`h2h_results.json`), full writeup (`FINDINGS.md`).

**Reused the same 14 facts** the investigation had already settled on (sampled by stride across
`design_decisions.md`'s timeline, not cherry-picked), each with a precise and a fuzzy
(paraphrased) query — 28 cases. Ground truth was the hand-verified chunk/memory ids from
earlier in the investigation, not fresh regex — that distinction had already caught the
investigation's own false negatives once (memories that paraphrase away from a literal code
symbol or number don't match a literal-symbol regex; corrected the earlier ~54% claimed
extraction-miss rate down to ~29%, logged in the entry above).

**Headline: ats hits 71.4% (20/28) at a mean 3,121 tokens and 0.06s per fact. Raw session grep
"hits" 100% of the time but that's a hollow number** — the keyword existing somewhere in 118MB
of transcript isn't the same as finding the fact — at a mean 97,978 tokens (max 817,311, i.e.
often exceeding what's practical to hand an agent) and 1.86s. Design log grep hits 32.1% at
near-zero cost, hurt by both its inherent scope limit (only covers what got manually written up)
and literal-phrase brittleness (`"33 duplicate session pairs"` doesn't match the doc's actual
`"33 duplicate-session pairs"` hyphenation — a deliberately naive single-phrase grep, not a
permissive OR-regex, to keep the comparison honest about how fragile literal matching really is).

**`recall` and `chunk_search` are complementary, not redundant** — of ats's 20 hits, 12 were
found by both tools, 6 by `chunk_search` only (the fact was never captured as a memory but the
raw chunk is still searchable), 2 by `recall` only. Confirms and quantifies what the investigation
found anecdotally earlier: measuring "ats" via `recall` alone materially understates what the
full MCP surface can actually do.

**Fuzzy queries remain the one weakness the RRF fix doesn't touch**: 13/14 precise hit vs. 7/14
fuzzy. The RRF fix corrected how the two legs get *fused*; it doesn't make either leg
individually better at bridging a query that shares zero vocabulary with its target.

**`include_graph` measured separately, and it's not a fact-lookup tool**: reran all 28 cases with
graph expansion on both tools. It never once rescued a miss into a reasonable read window (0/28)
while inflating token cost 627× on `recall` (625 → 392,094 mean) and 23× on `chunk_search` (2,496
→ 58,460 mean) — the BFS hop has no size cap, appending every graph-connected item rather than a
bounded top-N. Matches the feature's documented intent (exploratory cross-session context, not
precision retrieval) but the uncapped size is worth its own follow-up regardless of use case: a
single MCP call currently has no ceiling on response size once expansion is on.

## 2026-07-28 — RRF buries a leg's #1 pick behind two mediocre-on-both-legs candidates: guaranteed-slot fix

**The weakness:** `_rrf()` (`pipeline/retrieval.py`) fuses BM25 and semantic legs additively —
`score = 1/(k+bm25_rank) + 1/(k+sem_rank)`, absent leg contributing 0, `k=60`. This structurally
favors a candidate that's *moderately* ranked on **both** legs over one that's *perfectly* ranked on
**one** leg and absent from the other, because two mid-size fractions can out-sum one large fraction
plus zero. Reproduced concretely: query "what changed in the taxonomy v2 redesign" against the real
`traces.db` corpus (3148 memories) — the correct memory ranks semantic **#1** of 25 but is **absent**
from BM25's top-25 (zero lexical overlap), yet several other memories rank moderately on both legs
(~#5 and ~#8) and their summed RRF scores (≈0.0317) beat the correct memory's (≈0.0164), pushing it to
fused rank **#7** — outside any top-5 request.

**Scoped to how often this actually matters:** a single reproduction case isn't enough to justify
touching shared fusion logic, so before deciding anything, ran the same methodology (regex-match a
design-log entry's distinctive fact to a real memory's content, then check whether a natural-language
query about that fact survives top-5 on each leg individually vs. after hybrid fusion) across 23
additional sampled entries spread across `design/design_decisions.md`'s ~95-entry timeline (picked
for having an identifiable distinctive keyword; entries without one, or with no matching memory in
the corpus, were skipped — not cherry-picked for outcome). Result: **2/23 demoted** (taxonomy_v2
above, plus a "symmetric success/recovery/failure induction" case where the correct memory was
semantic-rank #4 and hybrid pushed it to #7), **19/23 unaffected** (already top-5 on the leg(s) that
found them, and stayed top-5 after fusion — including a documented case where fusion *helps*: query
"hallucination in extracted memories" is BM25 #1 / semantic #5, hybrid correctly keeps it at #1), and
**2/23 not-top-5 on any signal** (weak queries, not a fusion artifact). ~9% of a keyword-identifiable
sample being silently demoted out of top-5 is not rare enough to ignore, but also not so common that
the whole scoring scheme is broken.

**Rejected: tuning `k` down.** Lower `k` widens the score gap between rank-1 and rank-5+, which should
make a strong single-leg #1 harder to bury. Swept `k` from 60 down to 1 against both demoted cases:
the taxonomy_v2 case (semantic **#1**, BM25 absent) does recover into top-5 once `k≤8`, but the
symmetric-induction case (semantic **#4**, BM25 absent) never enters top-5 at any `k` down to the
extreme `k=1` — a rank-4 single-leg placement just isn't a strong enough signal for global rank-gap
widening to rescue, no matter how aggressively `k` is dropped. Since `k=60` is also the standard
value from the original Cormack et al. RRF paper (chosen to smooth rank noise generally, not tuned to
this corpus), and dropping it that far would move every other query's fusion behavior too on the
strength of one reproduction case, this option was dropped as fragile and not narrowly scoped to the
actual failure mode.

**Rejected: weighted RRF.** Which leg is more reliable is query-dependent (lexical wins on exact
jargon like `taxonomy v2`, semantic wins on paraphrase like "hallucination in extracted memories") —
a fixed per-leg weight would help one query type and hurt the other with no principled way to pick
the weight from the query alone.

**Chosen: guaranteed slot for each leg's #1, and *only* #1.** `_rrf(..., top_k=N)` now guarantees
that whichever id is ranked #1 on the BM25 leg and whichever id is ranked #1 on the semantic leg both
survive truncation to `top_k`, evicting the lowest-scoring non-guaranteed entry to make room if
necessary (scores themselves are untouched — this only changes which ids survive the truncation cut,
not the fused ranking math). Deliberately **not** extended to top-2 or top-3 per leg: swept
`guarantee_n` up to 3 against the same 23-case sample and the symmetric-induction case (rank #4) still
never recovers — fixing it would need `guarantee_n=4`, which on a `top_k=5` request means 4 of 5 slots
are just each leg's own ranking with fusion barely participating, defeating the reason hybrid search
exists. Rank-1 is the one placement unambiguous enough ("clearly the single best match on this leg")
to justify overriding the fused score; rank-3/4 is exactly the "moderately confident" territory RRF is
supposed to arbitrate between legs on. Verified against the real corpus: taxonomy_v2 now lands at
fused rank #5 (top-5, previously #7); all 21 other sampled cases (including the 19 that were already
fine and the tie/near-tie cases like "route all search methods through RRF" at bm25=sem=5) are
unchanged — zero regressions. `symmetric_induction` remains unfixed by design, per above. Full test
suite green (39 tests in `test_retrieval.py`, including 4 new tests covering the guarantee, its
no-op case when both legs already agree, and that scores are unaffected; 1 pre-existing unrelated
`test_opencode_plugin.py` failure, confirmed present on `main` before this change too).

## 2026-07-27 — Workspace-scoped filtering added to the retrieval engine

`recall`/`chunk-search`/`session-search` previously always searched across every ingested project.
Added an optional `workspace_id` filter, following the exact "push the filter into SQL before RRF
truncation" fix pattern from the `memory_type` entry earlier today — `search_memories_bm25`,
`search_memories_semantic`, `search_records_bm25`, `search_records_semantic`, and
`search_sessions_bm25` (`store.py`) all gained a `workspace_id` param that's applied via `WHERE` (and
a `JOIN` to `sessions` for `records`, which has no `workspace_id` column of its own) before `LIMIT`,
so it composes cleanly with the existing `session_id`/`memory_type` filters instead of fighting them
for which candidates survive truncation. `RetrievalEngine.recall()` / `.chunk_search()` /
`.session_search()` thread the param through to both BM25 and semantic legs (including
`session_search`'s avg-chunk-embedding semantic leg, which needed its own `JOIN sessions` added
since it queries `record_embeddings` directly rather than going through a store method). Exposed as
`--workspace`/`-w` on all three CLI commands and `workspace` on the `recall`/`chunk_search` MCP
tools; `graph_walk` left unfiltered (BFS is over `session_graph_edges`, which isn't naturally scoped
by workspace the same way).

**Match style: `LIKE '%' || ? || '%'`, not exact equality.** `workspace_id` is inconsistent across
ingestion paths for the same real project — `claude_code.py`'s main path produces
`-Users-you-src-agent-trace-signals`, but its own fallback branch (and `gemini_cli.py` /
`opencode.py`'s independent `_workspace_id` implementations) can produce a bare `agent-trace-signals`
for the same repo (27 sessions vs. 2, confirmed against the production DB). An exact match would
silently exclude the minority variant from every workspace-scoped query. Mirrors what
`session-search`'s lexical leg already does for free-text workspace queries (`"coral ai"` matching
`-Users-you-src-coral-ai`, documented in `README.md`), just as an explicit substring filter instead
of FTS5 tokenization — confirmed empirically against `traces.db` that both variants match `LIKE
'%agent-trace-signals%'` and a bogus workspace string correctly returns zero rows on every leg
(BM25 and semantic, memories/records/sessions).

## 2026-07-30 — `recall(include_graph=True)`'s 627x fan-out: fixed at the clustering, edge-weight, and BFS layers

**The bug** (flagged, not yet fixed, in the 2026-07-29 entry above): `recall(include_graph=True)`
calls `graph_walk(seed_id, depth=1)` for each of its top-3 seeds. `graph_walk` BFS'd
`session_graph_edges` with a flat `weight > 0.2` threshold treating `workspace`/`structural`/
`shared_memory`/`structural_similarity` identically, then pulled *every* memory from *every* reached
session, uncapped. Measured on the real corpus (103 sessions, 3148 memories): one seed's depth-1 walk
reached 77 sessions (75%) and ~3060 memories (97% of the corpus) — 625 → 392,094 mean tokens (627×)
across the 28-query exp10 sample, for 0/28 rescued misses.

**Root cause turned out to be one layer deeper than edge weighting.** Isolating each edge type's
contribution to that same seed's fan-out (real corpus): `shared_memory` alone reached 75 sessions,
`structural` 33, `workspace` 29, `structural_similarity` 9 (already capped by design — `top_k=8` per
session, `pipeline/utils.py`). `shared_memory`'s 75-session reach traced to one pathological HDBSCAN
cluster: `_cluster_and_mint()` (`clustering_analytics.py`) used `cluster_selection_method="eom"`,
which — on this embedding space — collapsed 2926 of 2986 embedded memories (98%) into a single
75-session "cluster" whose minted content (`"Maintain secure, reproducible development environments
using virtual environments, SSH authentication..."`) was a vague non-insight papering over completely
unrelated member memories (biomedical research env setup, an unrelated game-state bug, WANDS
re-ranking evaluation, Ollama CLI usage, SLR-agent HITL gates — no shared theme). This is the *exact*
failure mode `tag_session_types()` in the same file already diagnosed and fixed on 2026-07-13 (`eom`
"strongly prefers fewer, larger, more 'stable' clusters, which... collapsed ~70% of sessions into a
single blob") — but that fix (`cluster_selection_method="leaf"`) was never applied to
`_cluster_and_mint()`, the call site that actually mints `shared_memory` edges. `itertools.combinations`
over a 75-session cluster's `seen_sessions` is what produced the edge explosion — O(n²) in cluster
size, same weight (1.0) as a tight 2-session cluster.

**Fix 1 — `_cluster_and_mint()`: `eom` → `leaf`.** Verified on the real corpus (`.venv` + `hdbscan`
against `memory_embeddings`, no DB mutation — measurement only): `leaf` produces 134 clusters, max 20
members / 7 sessions, and sampled clusters are qualitatively coherent (one cluster was entirely
SLR-agent HITL checkpointing memories, another entirely hybrid-retrieval/RRF-fusion memories, another
entirely batch-vs-sequential LLM extraction-call memories) — a direct, independent quality win for
consolidated-memory content, not just a graph side effect. Of 134 clusters, 108 survive the existing
`min_recurrence>=2` filter; total shared_memory edge rows drop from 5602 to 1104 (80%) as a result.

**Fix 2 — weight `shared_memory` edges by inverse cluster specificity**, not flat 1.0:
`weight = 1.0 / distinct_sessions`. A 2-session cluster (0.5) or 3-session cluster (0.33) clears
`graph_walk`'s existing `weight > 0.2` BFS threshold; a 5+-session cluster (≤0.2) doesn't — reuses the
existing threshold instead of adding a new magic number. Defense-in-depth on top of fix 1: with `leaf`,
`distinct_sessions` tops out at 7 in the current corpus, but the weighting holds even if a future
cluster is larger.

**Fix 3 (found during verification, not in the original diagnosis) — the same specificity problem
existed in `workspace` and `structural` edges, both still flat weight=1.0.** Verifying fix 1+2 with "does
`include_graph` still surface genuinely related cross-session memories" (not just "did it get smaller")
surfaced this directly: from a seed in the now-coherent hybrid-retrieval/RRF cluster, `graph_walk`
returned 0/30 on-theme results — the seed session's `structural` edges (35 candidates, all flat weight
1.0, mostly via near-universal files like `README.md`/`design_decisions.md` referenced by 17-24 of 103
sessions) filled the entire per-hop candidate pool ahead of the correctly-downweighted `shared_memory`
edge (max 0.5). `workspace` edges have the identical problem — one flat edge type connecting every
session in a shared workspace (max 28 sessions in the current corpus) regardless of how large that
workspace is. Applied the same `1/n` formula at both edge-creation sites in `ingestion.py`: `workspace`
weight = `1/(sibling_count+1)`, `structural` weight = `1/(sessions_referencing_this_entity+1)`. Verified
after the fix: a seed with no competing `structural_similarity`/`structural` edges correctly surfaced
13/30 results from its actual 2-session `shared_memory` cluster-sibling session; the original
crowded-out seed's candidate pool dropped from 50 to 21 with `structural`/`shared_memory` now
competing honestly against `structural_similarity`. Left `structural_similarity` untouched — it already
has a real continuous weight (cosine similarity) and an existing `top_k=8` cap, i.e. it was never part
of this bug.

**Fix 4 — bounded `graph_walk` fan-out, independent of edge weighting.** Even with fixes 1-3, an
unbounded BFS over honestly-reweighted edges still isn't small: measured on 14 real sampled seeds,
reweighted-but-uncapped depth-1 walks averaged 768 memories (max 1693) — much better than the original
~3060-memory worst case, but still not "tens to low hundreds." Added `_MAX_GRAPH_NEIGHBOURS_PER_HOP=10`
(keep only the top-10 unvisited neighbour sessions by weight per hop, across the whole frontier, not
per source session) and `_MAX_GRAPH_EXPANSION_MEMORIES=30` (final memory pull capped, ordered by
`evidence_count DESC, access_count DESC` as a relevance proxy) in `RetrievalEngine.graph_walk()`
(`pipeline/retrieval.py`). This is the backstop, not the primary fix — kept because `workspace` cliques
in particular will keep growing as the corpus grows, independent of clustering quality. With all four
fixes: the same 14 seeds now average 20 memories per depth-1 walk (max 30, the cap) — recall()'s 3-seed
`include_graph=True` call is bounded to ~90 extra memories before dedup, down from the measured
~9000-equivalent (3 × ~3060) that produced the 392,094-token blowup.

**Left unchanged (checked, not broken):** `session_search`'s own `_session_graph_expand()` (one hop,
no BFS loop, already scales its decay by `weight`) and the standalone `graph_walk` MCP tool (same
function, now capped) both benefit from fixes 1-3 automatically since they read the same
`session_graph_edges` table — no code change needed there, and neither has its own uncapped-BFS-loop
failure mode fix 4 addresses. `tests/test_retrieval.py`'s full suite (39 tests) and the full project
suite (254 tests, 1 pre-existing unrelated failure in `test_opencode_plugin.py`) pass unchanged.
Verification above was measurement-only against a scratch copy of the real `traces.db` (never the
original — confirmed unchanged via mtime/checksum after).

**Production migration, applied same day.** No re-ingestion needed — re-parsing raw transcripts and
re-running LLM extraction was never necessary, since `workspace`/`structural` edges only depend on
data already in the DB (`sessions.workspace_id`, `occurrences`) and `shared_memory` edges only depend
on existing memory embeddings. Two landmines in the old `eom`-era state would have silently defeated a
naive "just call `consolidate_memories()` again," both re-checked directly against the code before
running anything: `_cluster_and_mint()`'s resume-skip (`already_covered_ids`, line ~416) reads from
*all* `memory_cluster_members` rows regardless of which cluster they belong to, so with ~98% of the
corpus already marked "covered" by the old blob, a fresh HDBSCAN pass would find correct `leaf`
clusters and then skip minting nearly all of them; `mark_superseded_clusters()` only marks an old
cluster superseded when a *single* new cluster covers ≥50% of its members, which a 2926-member blob can
never satisfy against `leaf`'s max-20-member output, so the blob would never get cleaned up on its own.

Steps actually taken against production `traces.db` (103 sessions, 3148 memories):
1. Stopped the running `ats ui` Streamlit process (had the DB open 3+ days, 36MB of un-checkpointed
   WAL) — confirmed via `lsof` before touching anything.
2. `PRAGMA wal_checkpoint(TRUNCATE)`, then a full file-copy backup
   (`traces_backup_pre_graph_fix_20260803.db`, checksum-verified identical to the checkpointed DB).
3. Found the actual pre-fix cluster state: 14 cross-session cluster memories existed, not just the one
   blob — 13 already `status='superseded'` (evidence 2–5, 3–211 members each) and one active
   (the 75-session, 2926-member blob, never superseded, because of the ≥50%-single-cluster rule above).
   All 13 superseded ones' `superseded_by` pointed at the blob. Deleted all 14 (member rows in
   `memory_cluster_members`, `memory_sources`, `memories_fts`, `memory_embeddings`, `memories`, plus
   `memory_retrievals` — the one FK without `ON DELETE CASCADE`, caught by a first attempt that
   correctly rolled back rather than partially applying) — keeping any of the 13 would have left
   dangling `superseded_by` references to a deleted id, and their members needed freeing from
   `already_covered_ids` regardless. Deleted all 5602 `shared_memory` edges (tied to the deleted
   clusters' `via_entity_id`, no longer reachable).
4. Recomputed `workspace` (1626 edges, 8 workspaces) and `structural` (2610 edges, 179 entities) with
   the new `1/n` weighting, no LLM involved — same edge topology, corrected weights only.
5. Re-ran `consolidate_memories(min_cluster_size=3, min_recurrence=2, model="gemma4:31b")` for real
   (local Ollama, matching `ats cluster-memories`'s defaults) — genuine LLM consolidation calls, not a
   simulation. **Minted 102 new cross-session cluster memories in 8129s (~2.3h)**, max 20 members / 7
   sessions (matches the scratch prediction almost exactly), 1084 `shared_memory` edges (weight range
   0.14–0.5, mean 0.24) replacing the old 5602 flat-1.0 ones. Embedded all 102 immediately after.
6. Restarted `ats ui`.

**Verified against the real, now-migrated production DB** (not scratch): 20 random seed sessions,
depth-1 `graph_walk` — mean 28.9 memories, median 30 (the cap), min 15, max 30. Sampled 5 of the new
cluster memories' content — all coherent single-theme summaries (e.g. "the `ats cluster-memories`
process groups recurring memories..." at 2 sessions, "OPERA is a biomedical evaluation framework..." at
3 sessions), consistent with the scratch-copy quality check earlier in this entry.

## 2026-08-04 — `chunk_search(include_graph=True)`'s 23x fan-out: fixed with inverse-specificity entity weighting + caps

**The bug** (flagged as untouched by the fix above, in the 2026-07-29/07-30 entries): `chunk_search`'s
graph expansion is a structurally separate code path from `recall`'s. It doesn't call
`graph_walk`/`session_graph_edges` at all — `_chunk_graph_expand()` (`pipeline/retrieval.py`) pivots via
`occurrences.entity_id`, pulling every chunk that mentions *any* entity the seed chunk mentions, for
each of the top-3 seed chunks, with no cap anywhere. Reconfirmed via a fresh rerun of
`experiments/exp10_retrieval_h2h/run_h2h.py` against the (post-2026-07-30-fix) production `traces.db`:
unchanged at 23.4x inflation (2,496 → 58,460 mean tokens across the 28-query exp10 sample), 0/28 rescued
misses, up to 414 extra chunks for one query.

**Characterizing the driver, before picking a fix shape.** Queried `occurrences` grouped by
`entity_id` on the real corpus (1176 chunks, 1744 entities, 4917 occurrences): the distribution is a
smooth long tail, not one pathological blob like the `eom`-cluster case. The top entities are
`agent-trace-signals`/`agent_trace_signals` (the project's own name, two spellings — 179 and 83 chunks,
15.2% and 7.1% of the whole corpus), `gemma4:e4b`/`gemma3:12b`/`gemma4:12b` (the LLM models used for
extraction — 120/109/42 chunks), `Ollama` (92 chunks, 7.8%), `README.md` (68 chunks, 5.8%), and
`traces.db` (51 chunks, 4.3%) — infrastructure/self-referential entities that show up in a large
fraction of the corpus simply because this corpus is a project about instrumenting itself with these
exact tools, not because they carry topical signal. Below that handful, the drop-off is gradual
(42, 41, 35, 35, 29, 26, 26, 25, 24, 23...) with no clean knee — genuinely specific entities
(`veritract`, `GLiNER`, `LangExtract`, `SLR-agent`, `retrieval_crowding`) sit at similar chunk-counts to
less-specific ones. A seed chunk mentions ~4-5 distinct entities on average (only 16/1176 chunks mention
just one), so nearly every seed touches at least one near-universal entity alongside more specific ones.
**This shape argues for continuous inverse-specificity weighting, not a hard exclusion cutoff or a flat
cap alone** — the same reasoning `structural`/`workspace` edge reweighting used on 2026-07-30 for a
similar (if less extreme) gradient, as opposed to the binary `eom`→`leaf` clustering fix that `recall`
needed for its actual single-blob pathology.

**Fix — weight each matched entity by `1 / chunks_mentioning_it`, plus caps as a backstop.**
`_chunk_graph_expand()` now computes, per seed chunk, the chunk-count of each of its matched entities,
weights that entity's contribution `1.0 / n_chunks`, and scores every neighbour chunk
`seed_score * _GRAPH_EXPANSION_DECAY * weight` (taking the max weight across all matched entities for a
chunk reached via more than one) — same mechanism `_session_graph_expand` already used for
`structural_similarity`'s cosine weight, just computed at query time instead of read from a stored edge
table (there is no chunk-chunk edge table; `occurrences` is queried directly, as before). Added
`_MAX_CHUNK_NEIGHBOURS_PER_ENTITY = 10` (cap per matched entity, mirroring
`_MAX_GRAPH_NEIGHBOURS_PER_HOP`) and `_MAX_CHUNK_EXPANSION_RECORDS = 30` (cap on the final ranked pool
per seed chunk, mirroring `_MAX_GRAPH_EXPANSION_MEMORIES`) in `RetrievalEngine`
(`pipeline/retrieval.py`) — explicit backstop, not the primary fix, same division of labor as the
2026-07-30 fix's caps.

**Verified against the real corpus** (a focused rerun of the exp10 28-query sample, `chunk_search`-only
since `chunk_search` never mutates the DB — no `increment_access_count`/retrieval-logging side effect
the way `recall`/`graph_walk` have — so this ran directly against production `traces.db`, confirmed
byte-identical via checksum before and after): mean tokens dropped from 58,460 to **12,971 (5.2x
inflation, down from 23.4x)**, max extra chunks from 414 to **66**. Rescued misses went from 0/28 to
**1/28** (`min_entity_name_length`, fuzzy variant) — expansion didn't just get smaller, it got more
useful. Spot-checked the `eom_vs_leaf` case's top-ranked expanded chunks directly: the highest-scoring
extras pivot through entities like `clustering_analytics.py`, `HDBSCAN`, and the `episodic`/`procedural`/
`semantic` memory-taxonomy concepts — genuinely on-theme for a clustering-mechanism query — while chunks
reached only through the near-universal `agent-trace-signals`/`agent_trace_signals` entities score an
order of magnitude lower and sink to the bottom of the capped pool, confirming the weighting is doing
its intended job rather than the caps alone doing all the work.

**No production data migration needed** — unlike the 2026-07-30 fix, which had to recompute and
re-store `session_graph_edges` weights, this fix is pure query-time logic: `_chunk_graph_expand` reads
`occurrences`/`entities` directly, computes specificity on the fly, and there's no stored entity-graph
edge table to be stale. The code change alone is the complete fix.

`tests/test_retrieval.py` (39 tests) and the full project suite (254 tests, same 1 pre-existing
unrelated failure in `test_opencode_plugin.py` noted in the 2026-07-30 entry) pass unchanged.

## 2026-08-04 — Query page "Generate Answer" felt slow: measured, then streamed + kept the model warm

Investigated a report that local `gemma4:e4b` generation on the Query page feels slow. Measured the
actual Ollama call directly rather than guessing: warm, GPU-resident (`ollama ps` confirmed 100%
GPU), short-context generation runs ~43 tok/s; a realistic `chunk_search`-style context (`top_k`=15,
up to 1200 raw chars each) pushes prompt-eval alone to ~5s; a cold model reload (after Ollama's
default 5-minute idle-unload) adds ~3s before generation even starts. None of that is catastrophic on
its own, but `OllamaProvider.complete()` sent `"stream": false` — the entire generation (worst case,
close to the full `query_answer_max_tokens=2048` budget, since `gemma4:e4b` is a thinking model whose
reasoning tokens count against that budget) happened behind a single blank Streamlit spinner with zero
incremental feedback. That's very likely the dominant "feels slow" factor, independent of the model's
actual per-token speed.

**Fixed two things, no model/quality change:**
1. **Streaming.** Added `OllamaProvider.complete_text_stream()` (`providers/ollama.py`) — same
   `/api/generate` endpoint with `"stream": true`, yielding each NDJSON chunk's `response` text as it
   arrives. `ModelProvider` (`providers/base.py`) gained a default `complete_text_stream()` that just
   yields the full `complete_text()` result once, so `AnthropicProvider` and any other future provider
   keep working via duck typing without needing their own implementation. The Query page
   (`ui/pages/1_Query.py`) now calls `st.write_stream()` on the new streaming generator instead of
   blocking on the old spinner — tokens render as they're produced.
2. **`keep_alive`.** `OllamaProvider.complete()` and `complete_text_stream()` both gained an optional
   `keep_alive` param (Ollama's own request field, not an `options` sub-field); the Query page passes
   `"30m"` so a normal think/type gap between queries in an interactive session doesn't pay the ~3s
   cold-reload tax on every single answer. Left as opt-in per call site (default `None` = Ollama's own
   5-minute default) rather than applied globally to every `OllamaProvider` caller — batch pipeline
   stages (`ats cluster-memories`, ingestion) can use different models back-to-back, and forcing a long
   keep_alive there risks the exact multi-model-residency OOM pattern already fixed once (see the
   2026-06-13 `OLLAMA_NUM_PARALLEL` entries) by making them run serially.

Verified directly against the live Ollama server: `complete_text_stream()` yields real incremental
chunks matching the non-streaming call's final text; `keep_alive="30m"` confirmed via `ollama ps`
showing the model's unload countdown jump from the default ~5 minutes to ~29 minutes remaining after a
call. Verified live in the browser: "Generate Answer" now renders the answer progressively instead of
appearing all at once after a long blank wait. Full test suite unaffected (254 tests, same 1
pre-existing unrelated failure).

**Not done, flagged instead:** trimming `chunk_search`'s answer-context size (currently up to
`top_k`=15 × 1200 raw chars when no summary exists) is a further, real lever — the display table and
the LLM prompt don't need the same item count — but changes what the model actually sees, so left for
a follow-up decision rather than bundled into this fix.

**Same-day follow-up:** switching to streaming removed the old spinner entirely, so the
connection/cold-load/prompt-eval gap before the first token arrives had *no* status indicator at
all — looked identical to nothing happening, undermining the whole point of the fix. Fixed by
peeking the stream's first chunk inside a `st.spinner("Generating answer with \`{model}\`…")` block
(blocks exactly until real output is about to start, since the generator doesn't execute past its
first `yield` until iterated), then re-chaining that chunk back onto the stream via
`itertools.chain` so `st.write_stream` doesn't drop it. Verified live: spinner shows during the
wait, disappears right as streamed text begins.

## 2026-08-18 — Explore Sessions' memory/entity-type breakdown charts were biased by session length

While reworking the demo video to add a scene on structural session clustering, checked whether the
underlying claim ("session type predicts what kind of memories get extracted") actually held up in
the data behind Explore Sessions' "Memory type breakdown by session type" and "Entity type breakdown
by session type" charts. It didn't. Both charts averaged raw counts **per session**, but structural
session types vary enormously in length — `tool-driven` sessions average 40.8 chunks, `focused
extraction` sessions average 1.2 — so a cohort's per-session count mostly reflected how long its
sessions happened to run, not any real memory-type signature.

**Fixed:** both charts (`ui/pages/2_Explore_Sessions.py`) now normalize by chunk count instead of
session count (`avg_per_chunk` in place of `avg_per_session`), with captions updated to explain why.
Recomputed on the live corpus, the apparent per-session spread (55.5–100.4 memories/session across
clusters) mostly evaporates once corrected: the three well-populated clusters (`cluster_3`,
`cluster_5`, `cluster_6`) converge to ~2.5–2.8 memories/chunk with a nearly identical composition
(~48% episodic / ~33% strategy each) — essentially indistinguishable once length is controlled for.
The remaining clusters (`cluster_4`, `cluster_7`, `cluster_8`) each have only 4–7 total memories
across all their sessions, too little data to draw a real conclusion from. The pipeline's extraction
rate turns out to be stable across working styles — a real, if less dramatic, finding than the raw
chart implied.

**Also cut:** a demo-video scene ("How You Worked Predicts What Got Remembered") built on the
debunked per-session premise was dropped before shipping, once both the per-chunk-density and the
percentage-composition versions of the same data showed no real effect to visualize. Replaced with an
`EvaluationScene` presenting the exp10 grep-vs-ats head-to-head instead (see 2026-07-29 entry above) —
a benchmark result that *does* hold up, placed right before the "lessons learned" scene.

---

## 2026-09-27 — Open-source prep: raw trace data untracked, repo published with fresh history

**Problem:** Making the repo public would have exposed (a) verbatim session trace text in tracked
experiment outputs — `chunks*.json`, result JSON, review HTML across exp01–09, the raw chunk dumps in
`exp01/02/05_results/results.md`, and root-level `labeled_critical_steps_cohort2*.html` — including
absolute paths and content from unrelated private projects; and (b) git history containing
`demo/node_modules` (a 160 MB Chrome binary), `demo/out/demo.mp4`, and deleted `annotations/` files
with labeled trace content. A pattern scan found no API keys or tokens, but that is not proof of absence.

**Decision:**
- Experiments ship **scripts + write-ups only** (`*.py`, `*.sh`, findings/results `.md` that summarize
  rather than dump). Outputs are regenerable from a local `traces.db`; `.gitignore` now blocks
  `experiments/**/*.{json,html,npz,log,txt}` so they can't be re-added.
- Internal working docs untracked: `notes/TODO.md` (open items moved to README "Known limitations"),
  empty `notes/MEMORY.md`, `review/`, `experiments/deprecated_*`, one-off backfill/annotation scripts.
  Research notes under `notes/` and `design/deprecated_*` are kept — other design docs link to them.
- Personal absolute paths in kept docs replaced with `/Users/you/...` placeholders.
- MIT license; pyproject metadata filled in.
- **Fresh history for the public repo** rather than `git filter-repo` on the existing one: simpler and
  leaves nothing to miss. The existing private repo remains the full-history archive.

**Side fixes found during the audit:** opencode plugin raised on an unopenable DB while the Gemini
plugin (and the test) expect an empty session → aligned to log + return empty; ruff brought to clean.

