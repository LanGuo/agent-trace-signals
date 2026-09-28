# Experiment 01: Session Summary vs Entity + Memory Extraction

**Status:** awaiting LLM output (run `uv run python experiments/run_exp01.py` to generate)
**Date:** 2026-06-04  
**Session used:** `ses_194830353ffevKR8nlziJKWRU0` — opencode, `embedding_geometry/` dir, title "Evaluating dense retrieval limitation remedies"

---

## Central principle

> **Evaluation has to be aligned with real use cases.**
>
> A pipeline step that looks good in isolation (e.g. entity extraction with GLiNER disabled, or memories that pass a trivial length filter) is meaningless if it doesn't improve the answers a user actually gets. Every measurement in this experiment is anchored to the question: *would this help someone recall what they worked on?* If a metric doesn't connect to that, it's noise.

---

## Background

### Current ingestion pipeline (Pipeline 1) — step order

```
Parse → Chunk → Summarize (LLM/chunk) → Pre-LLM entity extraction (regex + GLiNER)
→ LLM verifier (batched, 5 chunks/call) → Entity resolution → Occurrence extraction
→ Structural edges → Explicit memory extraction (LLM/chunk) → Session summary (LLM)
→ Write DB
```

LLM calls per session of N chunks:
- N calls for chunk summaries
- ceil(N/5) calls for the LLM verifier
- up to N calls for explicit memory extraction (gated by chunk length ≥ 300 chars)
- 1 call for session summary

### Problems that triggered this experiment

**Recall quality issues observed in `ats recall`:**

1. Low scores (0.014–0.016 RRF = single-source hits, BM25 and semantic not agreeing)
2. Trivial explicit memories: "The code works correctly.", "Option 1 works.", "A file was created successfully."
3. Frequency memories with problems: surface-level events with no resolution, spurious correlations stated as features, incomplete issue reports with no root cause

### Fixes applied before this experiment

- Memory extractor prompt: require named entity + context; `_is_trivial()` code-level filter
- Light analytics prompt: forbid surface-level events, correlation observations, incomplete issue reports
- BM25 query: min token length >2; added `ai`, `ml` to stop words

### Architectural question motivating this experiment

The LLM verifier and summarizer both read the full chunk text. If the summary already captures the same entities and knowledge as explicit extraction, we could:
- Replace 3 LLM calls per chunk (summarize + verify + memory) with 1 combined call
- Or drop the summarizer and run a richer "chunk analysis" prompt through the verifier

### Hypothesis

The current chunk summarizer prompt targets human-readable prose, not structured knowledge extraction. A combined prompt producing `{summary, entities, memories}` in one call could:
1. Reduce LLM calls per chunk from 3 → 1
2. Improve entity coverage (summarizer already "sees" entities; making them explicit captures what GLiNER misses)
3. Improve memory quality (memories extracted with summary context, not just raw chunk text)

---

## What we're measuring

For 5 consecutive exchanges (19–23) from the embedding_geometry session:

1. **Raw chunk text** — actual serialized exchange content
2. **Pre-LLM entities** (regex + GLiNER, full pipeline) — baseline before LLM
3. **After LLM verifier** — what did the verifier add/reject?
4. **Chunk summary** — does it contain the same entities? Does it contain memory-quality information?
5. **Explicit memories** — do these add value beyond the summary?

### Why these exchanges are a good test case

All 5 exchanges are rich in domain-specific concepts that regex won't find:
SOFTCENTROID, DACS, SPEC-SC, ArrowSpace, minority retrieval collapse, crowded neighborhoods,
Ashrafi & Roth (paper), 20 Newsgroups, Wikipedia Movie Plots, Film Noir, Rayleigh quotient, geometric crowding.

The regex layer finds almost nothing (only "BM25" in exchange 21) — so the LLM is doing all the heavy lifting. This is a clean comparison between summarizer coverage and entity-extractor coverage.

---

## Raw chunk content (pre-populated)

### Exchange 19 (1065 chars)
```
USER: summarize what you tested so far and the main findings, looks like we reproduced the minority 
retrieval collapse under increasing majority interference using pure semantic/vector search - how many 
minority and majority docs? how are the documents indexed/inserted? what are the queries? how many queries 
tested? and in addition hybrid search also showed the collapse so bm25/lexical search did not rescue 
this collapse. and further more all inference time interventions (what are they in simply language?)...
[TOOL: task({"description": "Summarize experimental setup"})]
A: Yes, that's exactly right. Here's the full picture:

## Experimental Setup
| Dataset | Minority | Majority | Minority Queries (50%) | Minority Docs (50%) | Majority Pool |
|---------|----------|----------|----------------------|--------------------|---------------|
| 20 Newsgroups | comp.sys.mac.hardware (Mac hardware) | 19 other topics | 481 | 482 | 17,883 |
| Wikipedia Movie Plots | Film Noir | action/thriller/crime | 50 | 50 | 5,358 |

Protocol: M[inority split 50/50 train/test...]
```

**Entities a human would extract:**
- `minority retrieval collapse` (concept)
- `semantic/vector search` (concept/technology)  
- `hybrid search` (concept)
- `BM25` (technology)
- `20 Newsgroups` (dataset)
- `comp.sys.mac.hardware` (concept)
- `Wikipedia Movie Plots` (dataset)
- `Film Noir` (concept)
- inference time interventions (concept, unnamed in this exchange)

---

### Exchange 20 (1211 chars)
```
USER: wait with this "Protocol: Minority split 50/50 train/test. Queries are NEVER in the index. 
Index = minority docs + N_maj randomly sampled majority docs. Sweep N_maj ∈ {0,10,...,max} with 5 
random trials. Measure Recall@k (k=1,5)." — did I understand correctly that Film Noir docs are split 
into docs in index (50%) and queries (50%), so for queries what is ground truth?
[TOOL: read(.../retrieval_crowding/src/data.py)]
[TOOL: read(.../retrieval_crowding/experiments/sweep_minority_recall.py)]
A: The ground truth is NOT one-to-one. No specific correct document per query.
Metric: does any minority-class document appear in top-k?
```

**Entities a human would extract:**
- `retrieval_crowding` (project/repo name)
- `data.py`, `sweep_minority_recall.py` (file)
- `Film Noir` (concept/dataset)
- `Recall@k` (concept/metric)
- `minority retrieval collapse` (concept)

---

### Exchange 21 (1099 chars)
```
USER: for this "Hybrid (50/50 BM25+dense) still collapses." how is hybrid implemented? are scores 
combined with RRF? does BM25 alone suffer collapse? what is SOFTCENTROID (Pull query toward its 
nearest neighbors' centroid)? all other interventions look like modified versions of this algo?
[TOOL: read(.../src/hybrid.py)]
[TOOL: read(.../src/sofcentroid.py)]
[TOOL: read(.../results/tier0_hybrid_search.md)]
A: Hybrid implementation (NOT RRF):
  scores = (1 - w) * dense_normalized + w * bm25_normalized
Per-query min-max normalization, linear fusion w=0.5. Simple.
BM25 alone: never tested. Shouldn't suffer geometric crowding but has its own failure modes.
```

**Entities a human would extract:**
- `SOFTCENTROID` (algorithm/concept)
- `BM25` (technology)
- `hybrid.py`, `sofcentroid.py`, `tier0_hybrid_search.md` (file)
- `RRF` (algorithm/concept)
- `geometric crowding` (concept)
- `retrieval_crowding` (project)

---

### Exchange 22 (937 chars)
```
USER: are we doing rerank with hybrid search and all interventions? For DACS, why does 
"crowded neighborhoods (large gap between top-1 and top-10 scores)" define a crowded neighborhood? 
For SPEC-SC what is "graph smoothness (Rayleigh quotient)"?
[TOOL: read(.../src/dacs.py)]
[TOOL: read(.../src/spec_sc.py)]
A: No 2-stage re-ranking. Every intervention does full re-scoring: compute refined query embedding q', 
then dot-product with all N docs. They modify the scoring function, not the candidate pool.

DACS gap intuition: gap = top-1 score − top-10 score measures how rapidly similarity drops.
Large gap → query is isolated, 1 close neighbor then a cliff.
Small gap → query is in a crowded neighborhood (many similarly-distant docs).
```

**Entities a human would extract:**
- `DACS` (algorithm)
- `SPEC-SC` (algorithm)
- `crowded neighborhoods` (concept)
- `Rayleigh quotient` (concept/metric)
- `dacs.py`, `spec_sc.py` (file)
- `gap score` / top-1 minus top-10 heuristic (concept)

---

### Exchange 23 (1010 chars)
```
USER: I looked into the Ashrafi and Roth paper, Figure 4(b) Stylistic Retrieval (Wikipedia Movie Plots).
Narrative style exhibits the most rapid collapse. This trend suggests nuanced attributes are highly 
vulnerable to erasure by majority content. It seems training won't necessarily remedy this collapse as 
it's assaying semantic embedding on a nuance attribute (style/film genre) related but distinct from 
semantic/lexical dimensions.
A: Sharp observation. This is the deeper problem:

20 Newsgroups = same-dimension crowding. Embedding space IS organized by topic. Mac hardware crowded 
by IBM hardware, sci.electronics — neighbors along the "technology topic" axis. Cosine measures the 
right thing, there's just too much majority. Training-time weighting could help here.

Wiki Movie Plots = cross-dimension crowding. The embedding space is organized by narrative/plot — 
but Film Noir is a STYLE, not a plot type. The embedding doesn't separate stylistic from narrative 
dimensions. Training can't fix this without a style-aware loss or contrastive setup.
```

**Entities a human would extract:**
- `Ashrafi and Roth` (paper/person)
- `Wikipedia Movie Plots` (dataset)
- `Film Noir` (concept)
- `20 Newsgroups` (dataset)
- `same-dimension crowding` vs `cross-dimension crowding` (concepts)
- `style-aware loss`, `contrastive setup` (technique)
- `comp.sys.mac.hardware`, `comp.sys.ibm.pc.hardware` (concept)

---

## Questions to answer from LLM outputs

After running `uv run python experiments/run_exp01.py`:

1. **Entity coverage**: Do the LLM verifier's `added` entities match the "entities a human would extract" lists above? What does it miss?
2. **Summary coverage**: Does the chunk summary mention the same entities? Which entities appear in the summary but NOT in the verifier output?
3. **Memory quality**: Do the explicit memories capture the key insights (same-dimension vs cross-dimension crowding, DACS gap intuition, etc.)? Or are they trivial even with the new prompt?
4. **Combined prompt opportunity**: If the summary captures 80%+ of the same entities as the verifier, that's strong evidence a combined prompt can replace both.

---

## Conclusions / Next steps

_(Fill in after reviewing LLM outputs)_
