# Experiment: Embedding Signals on Raw Traces

**Date:** 2026-06-12  
**Scripts:** `embed_cluster_eda.py`, `embed_raw_traces.py`, `harness_features_eda.py`  
**Model:** `qwen3-embedding:0.6b` via Ollama  

---

## Setup

Two levels of analysis:

1. **Chunk-level** (`embed_cluster_eda.py`): embed the 677 pre-processed `records` chunks already in `traces.db` (avg 3.8k chars each).
2. **Session-level** (`embed_raw_traces.py`, `harness_features_eda.py`): embed 66 raw sessions from `data/archive` (claude_code `.jsonl` + gemini_cli `.json`), using head+tail truncation (4k+2k chars) to stay within the model's effective window.

Dim reduction: UMAP (cosine metric, n_neighbors=8/10, min_dist=0.05–0.1).  
Clustering: HDBSCAN (min_cluster_size=3–5, EOM selection).

---

## Chunk-level results (677 chunks, 60 sessions)

- **55 clusters**, 71 noise points (10.5%)
- The embedding space is fine-grained enough to separate sub-topics within a single project.
  - ATS work (same repo, same project) splits into ~10 sub-clusters by theme: memory extraction, entity types, GLiNER, pipeline design, eval labeling, etc.
  - Chess sessions (clusters 2–3) cleanly separate from all coding sessions.
- Cluster 0 (mean chunk len = 21 chars) and cluster 1 (mean = 712 chars) flag near-empty / stub chunks worth filtering before downstream use.

---

## Session-level results (66 sessions)

### Cluster overview (12 clusters, 0 noise)

| Cluster | n | Theme |
|---------|---|-------|
| 6 | 8 | ATS core pipeline — ingestion, opencode, memory, analytics |
| 7 | 5 | ATS eval — annotations, quality, pipeline labeling |
| 2 | 5 | ATS design/visualization — query shape, visual design |
| 5 | 8 | Tool-heavy long-horizon / opera agents — tooluniverse, execute |
| 11 | 6 | LLM review / scoring — sonnet, transcript, score |
| 0 | 4 | Gemma/Kaggle ML hackathon |
| 8 | 4 | Hybrid search benchmarking — wands, ESCI |
| 9 | 6 | Structured extraction / knowledge — wiki, coral, inference |
| 10 | 4 | Chess + UI (cross-source: claude_code + gemini_cli) |
| 3 | 6 | Health/clinical + misc |
| 4 | 4 | Short/sparse sessions (very few tokens) |
| 1 | 6 | Tool-result-heavy sessions (little readable prose in head/tail) |

**Within-cluster cosine similarity: 0.734** vs **between-cluster: 0.415** — strong separation.

---

## Q: Is clustering driven purely by topic, or also by agent harness structure?

**Answer: both, and they're separable in the UMAP space.**

### Structural features extracted per session

- `real_human_turns` — user messages that are not tool results
- `n_tool_calls` — total tool calls made by the agent
- `n_unique_tools` — distinct tool types used
- `tool_call_rate` — tool calls per assistant turn
- `human_tool_ratio` — real human turns / tool calls
- `n_assistant_turns`, `assistant_text_len`
- `n_sidechain` — sidechain/thinking turns (gemini: thoughts count)

### Key findings

**1. Every structural feature predicts cluster membership (Kruskal-Wallis, all p < 0.0001).**  
Clusters are not pure topic groups — harness structure co-varies significantly.

**2. Random Forest feature importance (predicting cluster label):**

| Feature | Importance |
|---------|-----------|
| `human_tool_ratio` | **0.199** ← single top predictor |
| `real_human_turns` | 0.163 |
| `assistant_text_len` | 0.153 |
| `n_assistant_turns` | 0.131 |
| `tool_call_rate` | 0.121 |
| `n_tool_calls` | 0.101 |
| `n_unique_tools` | 0.077 |
| `n_sidechain` | 0.054 |

The **ratio of human turns to tool calls** is a stronger cluster predictor than raw tool count — the model is sensitive to the *balance* between human direction and autonomous action, not just volume.

**3. The two UMAP axes encode different things:**

| Axis | Strongest correlate | r | Interpretation |
|------|--------------------|----|----------------|
| umap_x | `tool_call_rate` | +0.61*** | How tool-heavy the session is |
| umap_y | `human_tool_ratio` | +0.75*** | How human-driven vs autonomous |

Topic content drives cluster identity at a coarser level; harness structure further separates sessions within topic neighborhoods.

**4. Structural extremes by cluster:**

| Cluster | human turns | tool calls | tool_rate | character |
|---------|-------------|------------|-----------|-----------|
| 2 | 3 | 0 | 0.0 | Pure conversation, zero tool use |
| 5 | 67 | 0 | 0.0 | Gemini thinking-only (no external tool calls) |
| 9 | 22 | 103 | **0.9** | Very high tool density per turn |
| 8 | 25 | 122 | 0.7 | High tool use + many thinking steps |
| 0, 6 | ~167 | ~950 | ~0.55 | Long autonomous coding sessions |

The ATS project's sessions split into clusters 2 (design conversations), 6 (heavy pipeline coding), and 7 (eval/annotation work) — distinguishable by both topic and harness structure simultaneously.

---

## Implications for ATS pipeline

- **Filtering**: clusters with `n_tool_calls = 0` and low `human_tool_ratio` are conversation-only sessions — different ingestion treatment may be warranted (no tool-call parsing needed).
- **Eval stratification**: when sampling sessions for labeling, stratify by `human_tool_ratio` and `tool_call_rate` buckets, not just project/topic, to get coverage of different harness patterns.
- **Signal quality**: very high `tool_call_rate` (cluster 9, r=0.9) sessions may yield lower-quality memory extraction because most turns are tool results, not prose reasoning.
- **Stub chunk detection**: embedding-based clustering at the chunk level reliably surfaces near-empty chunks (cluster 0, mean=21 chars) — can feed back into ingestion as a filter.

---

## Artifacts

| File | Description |
|------|-------------|
| `embed_cache_qwen3.npz` | Chunk-level embeddings (677 × 1024, cached) |
| `raw_session_embeddings.npz` | Session-level embeddings (66 × 1024, cached) |
| `embed_raw_traces.py` | Session-level embed + UMAP + HDBSCAN |
| `embed_cluster_eda.py` | Chunk-level embed + UMAP + HDBSCAN |
| `harness_features_eda.py` | Structural feature extraction + statistical analysis |
| `raw_session_clusters.html` | UMAP scatter by cluster (labelled) |
| `harness_umap_tool_calls.html` | UMAP coloured by tool call count |
| `harness_umap_tool_rate.html` | UMAP coloured by tool_call_rate |
| `harness_umap_human_turns.html` | UMAP coloured by human turns |
| `harness_human_vs_tools_scatter.html` | Human turns vs tool calls scatter |
| `harness_rf_importance.html` | RF feature importance bar chart |
| `harness_box_*.html` | Per-cluster boxplots for each structural feature |
