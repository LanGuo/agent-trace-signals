# Experiment Findings: Ingestion Pipeline Signal Quality

**Date:** 2026-06-05  
**Experiments:** EXP01–EXP04  
**Session used:** SLR-gemma4, claude_code, chunks 25–29  
**Central principle:** Evaluation has to be aligned with real use cases.

---

## EXP01 — Baseline: current pipeline on raw chunks

**What we measured:** Per-chunk: pre-LLM entities (regex+GLiNER), after LLM verifier, chunk summary, explicit memories — all from raw chunk text. Plus stored originals from initial ingestion.

**Key findings:**

1. **Chunk summaries are stable and near-identical across runs.** Fresh vs stored summaries were nearly word-for-word the same on all 5 chunks. The summarizer is consistent but optimized for prose narrative, not structured knowledge extraction.

2. **Summaries contain more named concepts than entity extraction found.** Chunk 27 summary mentioned PubMed, PICO, HITL gate, ProgressEmitter, LangGraph — none appeared in the entity list. The summarizer "sees" these entities but doesn't surface them structurally.

3. **Stored explicit memories (pre-prompt-fix) were verbose and trivial.** Chunk 26 produced 11 stored memories including "n_retrieved is 170", "n_duplicates_removed is 0", "Stage 2 completed" — raw facts with no synthesis. Fresh run (post-prompt-fix) produced 2 cleaner ones.

4. **Fresh memories are better but miss the "why".** Chunk 27 fresh: "PubMed searches do not apply any date filter" — useful. But misses the decision: "HITL gate should expose search_sources, date_range, and max_results before search runs."

5. **Entity noise is high from raw chunk text.** Stored entities included `nguo`, `r_agent`, `kpointBroker`, `emma4`, `guo`, `name` — path fragmentation and NER hallucination. The verifier cleaned some but not all.

6. **Session-level summary is the best single artifact** — captures the narrative arc coherently. Degrades badly above ~30 chunks (model produces meta-commentary instead of summary).

7. **LLM verifier failure on batched summaries.** `gemma4:e4b` returned empty response for the batched verifier call across 5 summaries. Per-chunk calls worked. Model limit on the combined prompt.

---

## EXP02 — Chunk summarizer prompt variants + combined prompt (D)

**What we measured:** 4 prompt variants on the same 5 chunks:
- Current: "In 2-3 sentences, summarize what happened..."
- A: entity-anchored
- B: decision-focused ("what problem, what decision, why, what changed")
- C: structured instructions
- D: combined prompt → JSON {summary, entities, memories}

**Key findings:**

1. **B wins as chunk summarizer.** Consistently names systems and explains the *why* without noise. Best on 4/5 chunks. On chunk 28 (low-signal implementation chunk) it correctly inferred the purpose (persisting `run_id`) beyond what was explicit in the text.

2. **A is noisy.** Adds a bullet-point breakdown of tools after the narrative — reads like two separate outputs. Mentions `Read` and `Edit` tool calls as meaningful content.

3. **D is the strongest per-chunk structured output.** The procedural memory on chunk 25 ("use `SqliteSaver(conn)` directly") is genuinely actionable. The dual-gap episodic on chunk 27 (Stage 1 + Stage 3 gaps) captures both issues in one memory. D correctly returned no memories for chunk 28 (nothing meets the bar) while B produced three weak memories including "`orchestrator.py` is a file."

4. **D entity extraction is weaker than memory extraction.** D includes file paths (`orchestrator.py` as a project type) and conflates file names with project names. The skip list in the D prompt needs tightening. But D's *concept* entities (search_sources, date_range, cfg, Stage 1 gate) are real signal missed by NER.

5. **Current chunk summarizer prompt is under-specified.** "Key actions, decisions, or topics" is too vague — leads to narrative paraphrase, not knowledge extraction. No guidance on named entities, no requirement for the "why."

---

## EXP03 — NER + memory extraction on B summaries vs D; cross-chunk unified (E-over-B)

**What we measured:** NER (regex+GLiNER+verifier) and memory extraction on B summaries. Cross-chunk unified call (E) over all 5 B summaries. Comparison to D per-chunk.

**Key findings:**

1. **NER on B summaries dramatically reduces noise.** Pre-LLM on B summary: 0–2 entities per chunk. Verified (when verifier worked): 4–5 high-quality entities per chunk. Zero garbage entities (no `nguo`, `r_agent`, `kpointBroker`). The verifier correctly added `SqliteSaver`, `ProgressEmitter`, `PubMed`, `bioRxiv`, `README.md`.

2. **Verifier fails on batched 5-summary call.** `gemma4:e4b` returns empty. Works fine per-chunk. Consistent with EXP01 finding — batching across 5 chunks exceeds this model's reliable structured output range.

3. **B+batched memories = B+per-chunk memories.** Batching the memory call made zero difference to output quality. Same memories, fewer calls. Batching is a cost win but not a quality win.

4. **E-over-B (cross-chunk unified) produced the best memory set.** 5 memories for the whole window, zero repetition, each spanning the right abstraction level. Procedural memories are directly actionable. The `ProgressEmitter.log()` pattern and the HITL gate gap are captured as window-level insights rather than per-chunk facts.

5. **E-over-B missed the Stage 3 screening criteria gap.** D chunk 27 caught it ("LLM invents its own screening criteria"). E-over-B missed it. One quality miss across 5 chunks from the E approach.

6. **Memory extraction on B summaries: per-chunk produces more memories, more noise.** 19 total vs E's 5. Chunk 29 alone produced 6 memories including "the project's tech stack includes grounding and audit trail capabilities" — low signal.

---

## EXP04 — D per-chunk → E synthesis over D outputs

**What we measured:** E synthesis over structured D JSON outputs (summary+entities+memories per chunk) vs E-over-B (synthesis over B prose summaries).

**Key findings:**

1. **E-over-D failed to synthesize — it just passed through per-chunk D memories.** Every memory tagged `[chunks [0]]`, `[chunks [1]]` etc. Zero cross-chunk synthesis. The model received ready-made memories and selected/reformatted them instead of merging.

2. **E-over-B synthesizes; E-over-D passes through.** When E gets prose summaries it must actively construct memories (synthesis). When it gets pre-extracted memories it takes the path of least resistance (pass-through). The SqliteSaver episodic + procedural pair from D chunks 0 was correctly merged into one episodic in E-over-B.

3. **E-over-D entity coverage is better than E-over-B for domain concepts.** E-over-D found `search_sources`, `date range`, `cfg` — real signal from the Stage 1 gap discussion that E-over-B missed. E-over-B found `LangGraph`, `db`, `uuid.uuid4()` that E-over-D missed. Neither merges `SLR system`, `SLR_gemma4`, `SLR agent` — three separate project entities when there should be one.

4. **D→E pipeline is not better than B→E.** 6 calls (5D+1E) vs 2 calls (5B+1E), and E-over-B produces better synthesized memories. The D per-chunk outputs are valuable for per-chunk entities and memories, but E synthesis should run over B prose summaries, not D structured output.

---

## Summary: What works, what doesn't

| Approach | Entity quality | Memory quality | Cost |
|---|---|---|---|
| Current (raw chunk NER + explicit) | High noise, misses concepts | Verbose, trivial before prompt fix | 2.2N+1 calls |
| B+NER on summaries | Low noise, good coverage when verifier works | More memories, some noise | 2 calls for verifier+memory |
| D per-chunk | Good concepts, some file/path noise | Best per-chunk, captures why | N calls |
| E-over-B (cross-chunk) | 11 deduplicated, with roles | 5 synthesized, no repetition | 1 call |
| **D per-chunk + E-over-B** | Best of both | Best | **N+1 calls** |

**Recommended design:** D per-chunk (replaces summarizer + NER + verifier + memory extraction) + B-style hierarchical session summary. See INGESTION_REDESIGN.md.
