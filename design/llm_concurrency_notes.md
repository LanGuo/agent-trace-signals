# LLM Serving: Batching vs Parallelization

Local reference — not committed.

---

## What Ollama supports

Ollama has **no batch completion API**. `/api/generate` and `/api/chat` are single-prompt
endpoints. `/api/embed` IS a true batch endpoint (sends N texts, gets N vectors back in one
call) — that's why `ats embed` is efficient.

When `OLLAMA_NUM_PARALLEL > 1`, Ollama (via llama.cpp) internally interleaves decode steps
from concurrent requests — this is server-side batching, not something the caller controls.
You get the benefit by sending requests concurrently (ThreadPoolExecutor), not by changing
the API call shape.

## True batching vs multithreaded parallel

| | ThreadPoolExecutor + OLLAMA_NUM_PARALLEL | True batching (vLLM) |
|---|---|---|
| GPU utilization | Moderate — decode steps interleaved | High — all sequences in one forward pass |
| KV cache memory | N × per-slot KV cache | N × KV cache (same total, better managed) |
| Model weights | Shared across slots | Shared |
| Throughput | Good at N=2–4 on consumer GPU | Near-linear with batch size |
| Latency per request | Higher under contention | Lower |
| Variable-length prompts | Fine — requests are independent | Harder (padding wastes compute) |
| Setup | `OLLAMA_NUM_PARALLEL=N` env var | Separate vLLM server, different API |

**Verdict for this project**: ThreadPoolExecutor + Ollama parallel is the right call.
Variable prompt lengths, moderate volume, local dev tool — vLLM adds complexity with
little gain. The main lever is `OLLAMA_NUM_PARALLEL=2–4`.

## Manual prompt batching (what we already do)

The entity verifier packs 5 chunks into one prompt and parses one JSON response.
That's the practical equivalent of batching on a single-endpoint API.
Could extend to chunk summarizers (send 3 chunks → ask for 3 summaries as JSON array)
but parsing gets fragile. Current approach is the right tradeoff.

## Memory arithmetic (M4, 16 GB)

- gemma3:12b weights (4-bit): ~8 GB
- KV cache per parallel slot at ctx=8192: ~0.5–1 GB
- OLLAMA_NUM_PARALLEL=2: weights (8 GB) + 2 × KV (~1.5 GB) + nomic-embed-text (~270 MB)
  ≈ 10 GB peak. Safe on 16 GB with ~6 GB left for macOS + app.
- OLLAMA_NUM_PARALLEL=4: ≈ 12–13 GB. Tight; may cause swap on a loaded machine.
- Recommended: NUM_PARALLEL=2, MAX_LOADED_MODELS=2.

---

## Recommended ingestion workflow

### Ollama server (start once)
```bash
OLLAMA_NUM_PARALLEL=2 OLLAMA_MAX_LOADED_MODELS=2 ollama serve
```

### Fast pass — eval / first look (no summaries, no memories)
```bash
uv run ats ingest --no-summaries   # entity verifier only hits Ollama; GLiNER is local
uv run ats embed                   # batch embed everything
```
Skips: chunk summarizer, memory extractor, session summarizer.
Keeps: entity extraction (GLiNER + batch verifier), inflection detection, embeddings.
Use when: iterating on entity/inflection quality, running eval, large corpus first pass.

### Production pass — full quality, skip redundant step 8
```bash
uv run ats ingest --no-memories    # summaries on, per-chunk memories off
uv run ats embed
uv run ats analytics light --workers 2
```
Skips: per-chunk ExplicitMemoryExtractor (redundant — analytics light does this better).
Keeps: chunk summarizer (N parallel calls), entity verifier, session summarizer, embeddings,
       analytics light memory synthesis (corpus-level, higher quality).
Use when: production corpus, retrieval quality matters, want Memory table populated.

### Full ingest (everything, rarely needed)
```bash
uv run ats ingest
uv run ats embed
uv run ats analytics light --workers 2
```
Includes per-chunk memory extraction at step 8. Only adds noise vs --no-memories.
Not recommended unless explicitly debugging step 8.

---

## Why --no-memories is the sweet spot

Step 8 (ExplicitMemoryExtractor):
- Sees one chunk at a time (1500 chars max)
- Prompt: "Only extract what is clearly stated"
- Produces chunk-level, context-free Memory records
- Parallelized (up to 8 concurrent Ollama calls during ingest)

Light analytics memory extraction:
- Sees top-20 occurrence contexts for one entity across the whole corpus
- Single combined LLM call: fact compression + memory synthesis + entity profile
- Produces corpus-aware, generalizable Memory records
- Run once per entity when `degree_count >= threshold`

Analytics light memories are strictly better. Step 8 exists for "immediate availability
before analytics runs" — not a real constraint since you always run analytics after ingest.
