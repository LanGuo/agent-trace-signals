# Agentic Trajectory Memory & Analytics — Design Exploration

> Living document. Not committed to git. Updated as decisions evolve.
> Started: 2026-05-14

---

## Primary Use Case

**"Chat with all your agent traces"** — query the full history of your Claude Code work sessions in natural language.

Example queries:
- *"What was I working on when I got stuck on the auth bug?"*
- *"Which projects did I spend the most time debugging in?"*
- *"What patterns show up in how I approach new codebases?"*
- *"What decisions have I made about database schema design?"*
- *"When did I last work with React 18, and what came up?"*

**What this requires end-to-end:**
1. **Automatic discovery**: scan `~/.claude/projects/` and ingest every session JSONL with no manual intervention
2. **Ingestion pipeline** (Pipeline 1): chunk sessions → embed → extract entities → extract explicit memories
3. **Analytics** (Pipelines 2a/2b): surface procedural patterns (how you work) and semantic facts (what you know) across the full corpus
4. **Retrieval** (Pipeline 3): hybrid search (BM25 + semantic + graph walk) answering natural-language queries over memories + chunks

**Secondary use case — shared memory across coding agents**: ingest traces from Claude Code, Gemini CLI, OpenCode, and Codex into the same corpus. The shared memory layer (MCP) serves any of these agents — each benefits from work done in another, and cross-agent patterns surface in analytics. The `app_id` field distinguishes agent type; `source_plugin` distinguishes the trace format. Same pipeline, pluggable parsers.

**Future use case (deferred)**: proactive context assistant for human-agent teams — push relevant memories before they are asked for, via session-start injection and anticipatory retrieval.

---

## Source Inspiration

**Article**: [Hierarchical Clustering of Agent Traces for Discovering Unknown Failure Modes](https://saulius.io/blog/hierarchical-clustering-agent-traces-unknown-failure-modes)

**Reference system**: [MemWire](https://github.com/memoryoss/memwire) — open-source AI memory infrastructure
**Industry reference**: [Glean Context Data Platform](https://www.glean.com/blog/context-data-platform) — enterprise context graph + memory
**Academic reference**: [TriMem](https://arxiv.org/pdf/2605.19952) ([code](https://github.com/tmlr-group/TriMem)) — tripartite memory system for lifelong LLM agents

### Core ideas extracted

- **Observability hierarchy**: Telemetry → Monitoring → **Analytics** (finding what you didn't know to look for)
- **Clio pipeline** (5 stages):
  1. Facet extraction — cheap LLM (Haiku) extracts structured attributes per conversation
  2. Embedding + k-means — `all-mpnet-base-v2`, deliberately over-segment with large k
  3. Cluster labeling — Sonnet reads sanitized summaries, produces titles/descriptions
  4. Bottom-up hierarchy — embed cluster labels, re-cluster coarser, re-label (emergent taxonomy)
  5. Privacy auditing — min cluster sizes, sanitized summaries, privacy-preserving prompts
- **Cost**: ~$0.0005/conversation, ~$0.0075/trace (15× heavier)
- **Validation**: Synthetic reconstruction, targeting 80–90% recovery vs 5% random baseline
- **Key shift**: "What cluster is the misbehavior in, when did it appear, what intervention shrinks it?"

### Failure modes analytics finds that monitoring cannot

- Tool-error swallowing (plausible answers despite tool failures)
- Sub-agent delegation pattern shifts (prompt regressions)
- Efficiency gains masking quality loss
- Safety-evasion via non-English routing / sub-agent stripping
- Prompt injection vulnerability signatures
- Model drift appearing as new population clusters

---

## Guiding Principles & Success Criteria

### Principles

**P1 — Multi-level memory, tested for utility**: Memory must not collapse to a single granularity. The episodic/procedural/semantic taxonomy is a hypothesis. Each memory type must demonstrate distinct retrieval utility in evaluation before it is treated as load-bearing. A type that retrieves nothing useful gets consolidated or removed.

> **Update 2026-07-22**: this hypothesis has now been tested. Audits in `experiments/exp07_ats_log_audit/` and `experiments/exp08_postfix_reextraction_audit/` found `semantic` is the weakest type by a wide margin (14.6% hallucination rate, 83% of all staleness) — structurally, a single chunk cannot verify "not a one-session observation," which is exactly what `semantic` claims to be. `experiments/exp09_taxonomy_v2/` is an experimental redesign that removes `semantic` entirely, adds a `status` field (resolved/open/reverted) to `episodic` instead, tightens `procedural` to durable public interfaces, merges the `decision`/`strategy` pattern types, and adds a new `preferences` field for user-stated instructions (a durable category the original taxonomy had no home for). See `design/design_decisions.md`'s 2026-07-21/22 entries for full results. **Not yet merged to production** — `chunk_analyzer.py` still ships the original 8-type taxonomy.

**P2 — Ingest-rich, query-cheap**: Structurally expensive work (chunking, entity resolution, embedding) is paid once at ingest. LLM budget is concentrated at ingestion and analytics; retrieval is a lookup, not a computation.

**P3 — Lightweight by default, scalable by design**: SQLite + Ollama, no external dependencies, usable on a laptop. Every choice has a documented upgrade path (Postgres, cloud LLMs, TurboVec) requiring configuration changes only, not architectural rework. `Store` and `ModelProvider` interfaces enforce this.

**P4 — Evidence-gated confidence**: Explicit (single-source) = hypothesis. Frequency-promoted = supported claim. Cluster-derived = validated pattern. Confidence governs retrieval ranking and conflict resolution.

**P5 — Cross-agent, cross-human continuity**: Memory is tool-agnostic. Claude Code, Gemini, meeting transcripts all contribute to and draw from the same corpus. `source_plugin` and `app_id` distinguish provenance without fragmenting memory.

**P6 — Complexity must be earned at every layer**: Every component — memory types, analytics pipelines, ingestion steps, graph structures — must justify its existence through measurable improvement. "Seems useful" is not sufficient. Applies uniformly across all layers.

**P7 — Proactive over reactive (design target, deferred implementation)**: The system is designed to push context before it's asked for. Memory schema and retrieval interface support this even though the proactive delivery mechanism is deferred.

**P8 — Generalizable extraction, no session-specific tuning**: Extraction quality improvements must work across all session and conversation types. Per-session threshold tweaks, domain-specific blocklists, and prompt patches written to fix observed failures in one session are not acceptable. If a problem appears in one session, the fix must demonstrably generalize — either through architecture (cross-layer agreement, LLM-as-verifier with context-only prompts, frequency-based promotion) or through eval evidence across diverse sessions. A fix that works on session A by breaking session B is not a fix.

### Success Criteria

| Stage | Criterion | Threshold |
|---|---|---|
| L0 | Entity recall | > 0.80 structured, > 0.70 concepts |
| L0 | Resolution precision | > 0.90 |
| L0 | Explicit memory precision | > 0.70 |
| L1a | Frequency memory recall | > 0.60 |
| L1a | False positive rate | < 0.25 |
| L1b | Cluster recovery | > 0.80 (Clio method) |
| L3 | Analytics boost delta | > 0.05 P@10 for at least one memory type |
| Taxonomy | Distinct utility | ≥ 2 of 3 memory types show meaningfully different retrieval patterns |
| End-to-end | Task performance | ≥ 1 real coding session demonstrably improved |

### Termination Points

- **MVP**: Pipeline 1 + 2a + Level 0 + 1a passing — useful without clustering. Ship here if 2b doesn't earn its complexity.
- **Done**: Pipeline 3 MCP integrated, L3 boost delta > 5% P@10, one real session demonstrably improved.
- **Simplification triggers**: 2b boost delta < 3% after two tuning rounds → drop clustering. Memory type shows no distinct utility → consolidate. Any analytics step whose removal doesn't degrade metrics → remove.

---

## Design Decisions Log

### Decision 1: Both analytics + memory, tightly integrated
Analytics pipeline labels clusters → labeled clusters become the memory store agents query at runtime. Two-way flow: ingestion enriches memory, memory informs future agent behavior.

### Decision 2: Framework-agnostic ingestion via OTel
Agent traces ingested via OpenTelemetry (GenAI semantic conventions). Not tied to a single framework (Claude Code, Codex, ox, LangChain all compatible).

### Decision 3: Memory access — at session start AND mid-session on demand
- **Session start**: relevant context injected automatically (via CLAUDE.md or hook)
- **Mid-session**: agent calls memory tools explicitly when needed

### Decision 4: Three separate pipelines — ingestion, analytics (two tiers), retrieval
*(Supersedes original single-pipeline thinking. Revised 2026-05-19, updated 2026-05-21.)*

**Pipeline 1 — Ingestion** (per-source-file, event-driven, stateless):
parse → chunk → chunk_summarize → session_summarize → embed (chunks + session) → entity extract + resolve → occurrence extract → structural cross-session edges → explicit memory extraction (Path A) → write sessions + records + entities + occurrences

**Pipeline 2a — Light Analytics** (corpus-level, nightly, graph-query-based):
query entity nodes where degree_count crossed threshold → fetch occurrence contexts → LLM compresses contexts to atomic facts → LLM extracts memories from facts (multiple types per entity) → memories[frequency] → build cross-session edges via shared entities → deduplicate overlapping explicit memories

**Pipeline 2b — Full Analytics** (corpus-level, weekly or threshold-triggered):
cluster sessions → label → hierarchy → cluster-derived memory extraction (Path C, latent patterns) → build cluster graph (similarity, PMI, transition, lag edges) → conflict detection across all memories

**Pipeline 3 — Retrieval** (query-time, read-only):
reads from sessions + records + memories + both graphs; no writes

Each pipeline is independently deployable and independently evaluatable.

### Decision 5: Three-tier memory taxonomy
| Tier | Content | Update frequency | Primary sources |
|---|---|---|---|
| **Episodic** | Time-stamped events, experiences, decisions, learnings with rough time points | Per session | Agent traces, meeting transcripts |
| **Procedural** | How-tos, behaviors, preferences, patterns | Slow drift | Agent traces, docs |
| **Semantic** | General knowledge, facts, stable reference | Rare | Documents, transcripts |

### Decision 6: Python-first prototype
Build as standalone Python experiment. Reads source data directly. Adopt into a larger system later if it proves out. No persistent server initially.

### Decision 7: Primary consumer = Claude Code / Codex (not ox)
Widened from ox-specific to general agentic harnesses. Both Claude Code and Codex support MCP natively → **MCP server** is the right retrieval interface.
- Session start injection via CLAUDE.md or `UserPromptSubmit` hook
- Mid-session retrieval via MCP tool calls (`recall_episodic`, `recall_procedural`, `search_semantic`)
- ox remains a potential future consumer via its adapter protocol

### Decision 8: Multi-source ingestion
Three source types, different shapes, different memory tier targets:
| Source | Shape | Primary memory tier |
|---|---|---|
| Agent traces (CC, Codex, ox, OTel) | Structured tool-call sequences, outcomes | Episodic + Procedural |
| Human meeting transcripts | Unstructured speech, decisions, action items | Episodic + Semantic |
| Documents (specs, wikis, READMEs) | Stable reference text | Semantic |

### Decision 9: Focused first slice
**In scope now**: Ingestion + analytics for agent/human traces and meeting transcripts.
**Deferred**: Document ingestion, MCP retrieval server, full memory taxonomy implementation, ox integration.

### Decision 10: raw_facets + embedding_text relationship
`raw_facets` is NOT ignored by analytics. Analytics reads it to produce `embedding_text` — a computed text serialization of `summary` + key structured facets (tool sequences for traces, decision lists for transcripts). `embedding_text` is what gets embedded, not `summary` alone. `summary` is human-readable; `embedding_text` is optimized for embedding quality. `raw_facets` is never used directly for clustering — only via this serialization step.

### Decision 11: Two-level hierarchical summarization (revised 2026-05-21)
Summaries are generated at two granularities, both by a shared `Summarizer` component using fixed prompt templates for consistency across source types:

**Chunk summary** (2–3 sentences, local model via Ollama):
> "In 2-3 sentences, summarise what happened in this segment: key actions, decisions, or topics covered."
Generated per chunk. Used for: entity extraction context, chunk-level retrieval.

**Session summary** (4–6 sentences, local model or Haiku for long sessions):
> "Given these consecutive segment summaries from a [agent session / meeting], write a 4-6 sentence summary covering: overall task or discussion, key decisions or outcomes, what went wrong (if anything), and the final state."
Generated from chunk summaries (hierarchical, not from raw text). Used for: clustering, session-level retrieval, session embedding_text.

Plugin responsibility: produce well-structured `segments`. Summarization quality is owned by `Summarizer`, not plugins.

### Decision 12: Three-speed analytics, not full rerun on every new record
Separates embedding (cheap, incremental) from clustering (periodic):
| Operation | Trigger | Cost |
|---|---|---|
| **Embed new records** | After each ingestion batch | O(new) — cached by content hash; unchanged records skipped |
| **Incremental assignment** | Continuously | O(1)/record — nearest centroid to existing clusters |
| **Full rerun** | Nightly or when record count grows >20% | O(n) — refreshes centroids, discovers new clusters, rebuilds hierarchy |
Embedding cache key: `sha256(embedding_model + embedding_text)`. Model changes invalidate the cache for affected records only.

### Decision 13: SQLite + sqlite-vec as prototype storage backend, Postgres + pgvector as production path
Single-file, zero-infrastructure store for prototype. sqlite-vec enables filtered vector search within SQL (`WHERE memory_tier = 'episodic' ORDER BY vec_distance`), avoiding the ID-mapping fragility of a separate FAISS index. A `Store` abstract interface wraps all reads/writes so the backend is swappable without application changes.

**Against**: SQLite + FAISS — two systems to sync, fragile embedding_id mapping, FAISS requires full rebuild on update, no filtered vector search in SQL.
**Future**: Postgres + pgvector — same `Store` interface, HNSW index, full SQL expressiveness, ACID across structured + vector data.

### Decision 14: Adopt multi-tenancy hierarchy from MemWire
Add `org_id → workspace_id → app_id → user_id` hierarchy to the `records` schema now. Adding it later requires painful migrations and breaks retrieval isolation. For prototype: all fields default to `"default"` so single-user usage requires no configuration.

### Decision 15: Anchor-based memory tier classification (borrowed from MemWire)
Rather than having source plugins decide `memory_tier`, use an `AnchorClassifier`: compute centroid embeddings from example texts for each tier, then classify records by argmax cosine similarity to tier centroids. Consistent across all source types, no LLM call needed, easily reconfigurable by swapping example texts.
- Episodic anchors: "what happened in this session", "a decision was made", "the agent encountered an error"
- Procedural anchors: "how to do this task", "preferred approach", "pattern observed repeatedly"
- Semantic anchors: "general fact", "stable reference knowledge", "definition"
This resolves the open question: tier is not decided by plugins, it is classified centrally.

### Decision 16: Add access_count for retrieval-weighted recall
Add `access_count INTEGER DEFAULT 0` to `records`. Incremented each time a record is retrieved. Enables recency × usage scoring at retrieval time (borrowed from MemWire's `access_count` field). Surfaces which memories are actually useful vs. merely stored.

### Decision 17: Graph retrieval layer — design for it now, build it later
MemWire's BFS path scoring (`relevance × coherence × recency`) is substantially better than pure cosine similarity for retrieval. We will not build it in the first slice, but the data model must not prevent it. Design: cluster nodes will have edges weighted by co-occurrence frequency across sessions (how often records from cluster A and cluster B appear in related sessions). This enables BFS traversal at retrieval time. Requires no changes to the current `records` schema — only an additional `cluster_edges` table when we build it.

### Decision 17a: What NOT to borrow from MemWire
- **Token-level graph**: MemWire's `DisplacementGraph` operates on individual tokens/phrases extracted from memories. It is clever but opaque — hard to audit why two tokens are connected, and conflict detection is essentially absent. Our cluster-level graph is more interpretable.
- **Qdrant as separate vector store**: MemWire splits structured metadata (SQLAlchemy) from vectors (Qdrant). This is the two-system fragility we already rejected. sqlite-vec keeps everything in one file.

### Decision 18: Schema future-proofing — four mechanisms
1. **`raw_facets` JSON blob** — no migration needed for new source types; schema evolution is additive
2. **`metadata` JSON column** — escape hatch for cross-source structured fields not yet worth a real column (`project_id`, `language`, `user_id`, `repo`)
3. **`analytics_run_id` on every enrichment field** — cluster assignments are versioned; re-running replaces enrichment without losing history or core record data
4. **`embedding_model` stored per analytics run** — switching models invalidates only embeddings from prior runs; the system knows which records need re-embedding

New required columns will need `ALTER TABLE ADD COLUMN ... DEFAULT NULL` migrations. The rule: if a field is uncertain, put it in `metadata` until it earns a real column through use.

---

## Resolved Questions

- **Transcript formats**: Zoom/Meet VTT or Otter.ai/Fireflies/Granola structured exports
- **Agent trace formats**: Claude Code session JSONL, OTel spans JSON, ox `.sageox/` — all three
- **Storage backend**: SQLite + sqlite-vec (prototype) → Postgres + pgvector (production), behind `Store` interface
- **Analytics output**: extracted `memories`, `memory_sources` links, `clusters`, `cluster_edges`, `conflicts` tables

### Decision 19: Three ingestion layers + memories (revised 2026-05-21)
Ingestion now produces three levels, all immutable after write:

- **`sessions`** — one per source file; carries session-level summary, session embedding (for clustering), raw_facets, source metadata
- **`records`** — chunks of sessions; carries chunk text, chunk embedding (for retrieval + entity extraction), chunk summary. One session → N records.
- **`occurrences`** — entity mentions within chunks; carries mention_text, context_text, context embedding. One record → M occurrences.

**`memories`** — abstracted units derived by analytics pipelines; variable granularity:
- Sub-chunk: a specific event or fact within one chunk
- Session-spanning: a pattern from multiple sessions  
- Abstract: a stable rule from many sessions over time

Many-to-many: one memory references N sessions/records; one session/record contributes to M memories. `memory_sources` carries `relevance_score` and `span_hint`.

### Decision 20: Three paths to memory extraction — type and pipeline depend on path (revised 2026-05-19)
Procedural and semantic memory extraction does NOT always require clustering. Three extraction paths:

| Path | Trigger | Pipeline | Memory type | Confidence |
|---|---|---|---|---|
| **A — Explicit** | Single record contains stated rule/fact/preference | Ingestion (Pipeline 1) | Any | Lower — single source |
| **B — Frequency** | Pattern count crosses threshold across corpus | Light analytics (2a) | Procedural, Semantic | Medium — repeated evidence |
| **C — Cluster-derived** | Latent pattern surfaces from clustering | Full analytics (2b) | Procedural, Semantic | Highest for latent patterns |

**Episodic memories** are exclusively Path A — time-anchored, single-record, extracted at ingest time.
**Procedural + semantic** use all three paths. Clustering (Path C) is specifically for latent patterns that explicit or frequency methods miss.

Each memory records its `extraction_method` ("explicit" | "frequency" | "cluster"). A frequency-promoted memory can be upgraded to cluster-confirmed. An explicit memory with low evidence is re-evaluated when clustering runs.

LLM (Haiku) confirms and articulates content + type given evidence. No classifier, no training. Supersedes Decision 15.

### Decision 24: Multi-level memory abstraction within types (from Glean, 2026-05-18)
Glean's platform infers a hierarchy: atomic actions → tasks → projects → initiatives. Our memories should support this within each memory type, via an `abstraction_level` field:
- **Level 0** (atomic): a single observed event, decision, or failure — closest to the raw record
- **Level 1** (task): what one session or a small cluster of related events accomplished
- **Level 2** (project): a recurring pattern abstracted from a cluster at recurrence threshold
- **Level 3** (initiative): a stable, cross-cluster, long-running theme

This is orthogonal to memory type: a procedural memory could be level 1 (one session reveals a preference) or level 3 (a preference stable across 100 sessions). The level is determined by `evidence_count` and `temporal_variance` at extraction time. Higher levels require more evidence.

### Decision 25: Memory scope — personal vs team vs org (from Glean, 2026-05-18)
Glean distinguishes personal memory (user-specific) from enterprise memory (cross-user patterns). Add `scope` to `memories`:
- **personal**: evidenced by a single `user_id`, not generalizable beyond that user
- **team**: evidenced by 2–10 distinct `user_id`s within same `workspace_id`
- **org**: evidenced by >10 distinct `user_id`s or cross-workspace patterns

Scope is computed from `COUNT(DISTINCT user_id)` across `memory_sources → records`. It upgrades automatically as more users contribute supporting evidence. Personal memories stay personal until corroborated. This is more semantically meaningful than raw multi-tenancy scoping.

### Decision 26: Two distinct graph structures, four node types (revised 2026-05-21)
Graph construction has three approaches (structural/referential, enrichment, unsupervised ML). These produce two subgraphs:

**Entity/session graph** (built incrementally at ingest + light analytics):
Node types:
- `session` nodes — one per source file
- `record` (chunk) nodes — one per chunk, child of session
- `entity` nodes — canonical resolved entities (person, tool, technology, commit, PR, concept)
- `occurrence` nodes — one per entity mention in a chunk; stores contextual embedding

Edge types (built at ingest unless noted):
| Edge | From → To | When built | Meaning |
|---|---|---|---|
| `has_chunk` | session → record | Ingestion | Session contains chunk (ordered) |
| `mentions` | record → entity | Ingestion | Chunk references entity |
| `instance_of` | occurrence → entity | Ingestion | Occurrence is a mention of entity |
| `has_occurrence` | record → occurrence | Ingestion | Chunk contains this entity mention |
| `co_mention` | occurrence → occurrence | Ingestion | Two entities co-occur in same chunk |
| `structural` | session → session | Ingestion (structural entities only) | Same PR/commit/file across sessions |
| `same_entity` | session → session | Light analytics (promoted entities) | Sessions share a significant entity |
| `enrichment` | any → any | Human / LLM annotation | Manually or LLM-derived relationship |

**Cluster graph** (built in full analytics 2b):
- Nodes: cluster nodes
- Edges: ML-derived (centroid similarity, PMI, temporal transition, lag)
- Schema: `cluster_edges` (existing)
- Purpose: neighbourhood navigation, BFS path scoring, pattern discovery

Cross-record edge generation happens in three places:
1. **Ingestion**: structural entities (PR#, commit, file) → immediate session→session edges via `structural`
2. **Light Analytics**: promoted entities → `same_entity` edges between sessions sharing that entity
3. **Full Analytics**: cluster co-occurrence → `cluster_edges`

### Decision 21: Multi-edge cluster graph built during full analytics (2026-05-16, confirmed 2026-05-19)
Clustering produces a neighbourhood graph with multiple edge types:
| Edge type | Direction | Weight | What it reveals |
|---|---|---|---|
| Centroid similarity | Undirected | cosine(centroid_A, centroid_B) | Semantic neighbourhood |
| Co-occurrence (PMI) | Undirected | log P(A∧B)/(P(A)·P(B)) | Failure modes that cluster together |
| Temporal transition | Directed A→B | P(B seen in session after A) | Agent trajectory patterns, failure cascades |
| Cross-session lag | Directed A→B | P(B within N sessions of A) | Lagged causal patterns |

Combined edge weight: `w = α·cosine + β·PMI + γ·transition_prob` with α=β=γ=⅓ initially.
Graph is rebuilt on each full analytics rerun. Directed edges apply only to agent-trace clusters.

### Decision 27: Chunking strategy (2026-05-21)
Source files split into two levels: sessions (file-level metadata + summary) and records (chunks).

**Chunk sizes**: max 350 tokens (fits all-mpnet-base-v2 context window), 10% token overlap between adjacent chunks to avoid cutting context at boundaries.

**Chunking strategy per source type**:
| Source | Strategy | Approximate size |
|---|---|---|
| Agent traces | Task boundary detection (tool-call sequence groups) or every ~50 tool calls | ~200–350 tokens |
| Meeting transcripts | Topic segment boundary or every 5 minutes of transcript | ~400–600 words → truncated to 350 tokens |

**Two-step summarization**:
1. Chunk summary (2–3 sentences) — generated from raw chunk text by local model
2. Session summary (4–6 sentences) — generated from chunk summaries (hierarchical, not from raw) by local or Haiku model

Clustering operates on **session-level embeddings**. Retrieval and entity extraction operate on **record (chunk) level embeddings**.

### Decision 28: ModelProvider abstraction — Ollama + Anthropic (2026-05-21)
LLM calls abstracted behind a `ModelProvider` interface so Ollama (local) and Anthropic API are interchangeable. Reduces API cost; enables offline operation for most ingestion steps.

| Task | Model tier | Default |
|---|---|---|
| Entity extraction, chunk summarization, explicit memory extraction | Local (fast, cheap) | Ollama: Llama 3.2 3B or Qwen2.5 3B |
| Session summarization, memory content generation | Mid-tier | Ollama or Haiku |
| Cluster labeling, conflict verification | Higher-capability | Haiku or Sonnet via API |

`ModelProvider` interface: `complete(prompt, schema?) → str | dict`. Implementations: `AnthropicProvider`, `OllamaProvider`. Configured per pipeline step in settings; no application code changes to swap.

### Decision 29: Entity identification — three-layer cascade (2026-05-21)
All three layers run on every chunk; results merged by `(type, canonical_form)` deduplication:

| Layer | Method | Input | Runs for | Cost |
|---|---|---|---|---|
| 1 — Regex | Pattern matching | chunk_text | All sources | ~0ms, free |
| 2 — spaCy NER | `en_core_web_sm` | chunk_text | All sources | ~10ms/chunk, free |
| 3 — LLM concepts | Structured output | chunk_text | Transcripts + prose-heavy sessions; skip structured traces | ~200ms, local model |

Entity resolution after extraction:
- **Structured** (commits, PRs, files, packages): hash of `(org_id, entity_type, canonical_form)` → exact SQL lookup, O(1)
- **Named** (people, orgs): lowercase normalize + Levenshtein < 2 against existing canonical names within org, O(existing_entities)
- **Concepts** (technologies, topics): cosine similarity of `resolution_embedding` against `entity_embeddings`; merge if similarity > 0.92; else create new node

### Decision 30: Occurrence nodes as contextual entity instances (2026-05-21)
Each entity mention in a chunk = one occurrence node. This separates two distinct embeddings for each entity:

- **`entity.resolution_embedding`**: embed(`f"{entity_type}: {canonical_name}"`) — stable, never changes, used for entity resolution and deduplication
- **`occurrence.context_embedding`**: embed(surrounding 2–3 sentences) — captures how the entity is used in context; varies per mention

Centroid of all occurrence embeddings for an entity = semantic profile of how this entity is used in this corpus. This is what Pipeline 2a passes to the LLM for memory extraction — not full session summaries.

Pipeline 2a memory extraction prompt:
> "Entity: [canonical_name] ([entity_type]). Here are [N] mentions from [N] sessions:\n[occurrence.context_text list, ranked by recency]\n\nExtract any generalizable patterns (procedural), specific notable events (episodic), or stable facts (semantic). Return each as a separate memory with type and content."

One entity → zero or more memories, potentially mixed types.

`occurrences` replaces `record_entity_edges` — occurrences carry all the same join information plus the context signal needed for memory extraction.

### Decision 34: Claude Code corpus discovery — file structure + CorpusScanner (2026-05-21)

**Actual file structure** (verified on disk):
```
~/.claude/projects/
  <project-dir>/                       ← one dir per project
    <session-uuid>.jsonl               ← top-level session (the primary unit)
    <session-uuid>/
      subagents/
        agent-a<agent-id>.jsonl        ← subagent sessions (spawned by Agent tool calls)
```

Where `<project-dir>` = project absolute path with all `/` replaced by `-` (e.g., `/Users/you/src/foo` → `-Users-you-src-foo`). Not perfectly reversible (ambiguous with paths containing `-`), so store the raw dir name as `workspace_id`.

**Corpus scale** (verified): 15 projects, 12 top-level session files, 333 subagent files = 345 total.

**First slice**: top-level sessions only. Subagents are deferred — they are fragments spawned by `Agent` tool calls within parent sessions, and ingesting them without the parent context produces low-quality chunks.

**CorpusScanner component** (runs before Pipeline 1):
1. Walk scan paths (default: `~/.claude/projects/`) recursively
2. Match `*.jsonl` files NOT in a `subagents/` directory
3. For each file: compute `content_hash = sha256(file_bytes)`
4. Compare against `ingestion_state` table:
   - New file (no entry): queue as `pending`
   - Known file, same hash: skip (already ingested)
   - Known file, changed hash: queue for re-ingest
5. Return ordered list of `(abs_path, file_type, metadata)` tuples for Pipeline 1

**Scan paths** are configurable per source type:
```yaml
scanner:
  claude_code:
    paths: ["~/.claude/projects/"]
    include_subagents: false   # deferred
  zoom: ["~/Downloads/Zoom/"]
  otter: ["~/Downloads/otter_exports/"]
```

**ingestion_state table** (tracks scanner state, not in main analytics DB):
```sql
CREATE TABLE ingestion_state (
  id              TEXT PRIMARY KEY,   -- sha256(abs_path)
  abs_path        TEXT NOT NULL UNIQUE,
  file_type       TEXT NOT NULL,      -- "claude_code"|"otel"|"zoom"|"otter"
  content_hash    TEXT NOT NULL,      -- sha256(file bytes); changes trigger re-ingest
  session_id      TEXT,               -- FK → sessions.id after successful ingest
  last_seen_at    TEXT NOT NULL,
  last_ingested_at TEXT,
  status          TEXT NOT NULL DEFAULT 'pending',  -- "pending"|"ingested"|"failed"|"skipped"
  error           TEXT,
  metadata        TEXT                -- JSON: {project_dir, git_branch_at_scan, ...}
);
```

The `ingestion_state` table lives in the same SQLite file as the main DB (separate schema/table, not a separate file).

### Decision 35: ClaudeCodeSource plugin — JSONL parsing + entity extraction (2026-05-21)

**Session-level metadata** extracted from the JSONL (not per-message):
| Field | Source | Maps to |
|---|---|---|
| `sessionId` (any message) | First occurrence | `sessions.id` construction |
| `cwd` | First `user`/`assistant` message | `sessions.metadata.cwd` |
| `gitBranch` | First `user`/`assistant` message | `sessions.metadata.git_branch` |
| `version` | Any message | `sessions.metadata.cc_version` |
| min(`timestamp`) | First message | `sessions.session_timestamp` |
| max(`timestamp`) | Last message | `sessions.metadata.session_end` |

**Useful message types** (keep):
| Type | `message.content` form | What to extract |
|---|---|---|
| `user` (string) | Plain text | User prompt text → chunk content |
| `user` (array with `tool_result`) | `[{type:"tool_result", tool_use_id, content}]` | Tool output → append to preceding exchange |
| `assistant` (text block) | `[{type:"text", text}]` | Assistant response text |
| `assistant` (tool_use block) | `[{type:"tool_use", name, input}]` | Tool call + structured entity extraction |

**Filter out** (skip entirely, not useful for memory):
`file-history-snapshot`, `permission-mode`, `attachment`, `queue-operation`, `last-prompt`, `system`

**Chunk strategy for agent traces** (exchange-based):
A natural semantic unit is one *exchange*: one user prompt + all following assistant turns (text + tool calls + tool results) until the next user prompt. This captures one atomic user intent and the agent's complete response.

Steps:
1. Group lines into exchanges by `user` message boundaries
2. Serialize each exchange as readable text (see below)
3. Apply 350-token max splitting with 10% overlap within large exchanges

Exchange serialization format:
```
USER: <user_message_text>

ASSISTANT: <assistant_text_if_any>
[TOOL: <tool_name>(<key_inputs>)]
[RESULT: <tool_result_summary_or_truncated>]
...
```

**Entity extraction from tool calls** (structured, no NER needed):
Tool calls carry directly structured entity signals — extract before/during chunking:

| Tool | Entity extracted | Type |
|---|---|---|
| `Read`, `Edit`, `Write`, `Glob` | `input.file_path` | `file` |
| `Bash` | file paths from command (regex), git refs (commit hash, branch), package names | `file`, `commit`, `technology` |
| `WebFetch` | URL domain | `technology` |
| `Agent` | `input.subagent_type`, `description` summary (first 10 words) | `concept` |
| `TaskCreate`/`TaskUpdate` | task title | `concept` |
| Any tool | `tool_name` itself | `tool` (tracks tool usage patterns) |

Git ref regex: `\b[0-9a-f]{7,40}\b` (commit hashes), `PR\s*#\d+`, `branch:\s*[\w/-]+`.

Tool-call-based entity extraction runs as the Layer 1 step (before regex/NER) for `claude_code` source type. It is cheaper and higher-precision than NER for structured trace data.

### Decision 32: Embedding model — nomic-embed-text via Ollama (2026-05-21)
Default embedding model: **`nomic-embed-text`** served by Ollama locally.

| Property | Value |
|---|---|
| Dimensions | 768 (compatible with all FLOAT[768] vector tables — no schema change) |
| Max context | 8192 tokens (well above our 350-token chunk max) |
| Runtime | Ollama — same process that serves LLMs; no separate infrastructure |
| License | Apache 2.0 |
| MTEB score | Competitive with `all-mpnet-base-v2` on retrieval tasks; slightly stronger on longer texts |

Ollama call: `ollama run nomic-embed-text "text"` or via `/api/embeddings` HTTP endpoint. The `Embedder` component in the pipeline calls Ollama for all embedding work — no internet required after `ollama pull nomic-embed-text`.

**Upgrade path**: `mxbai-embed-large` (1024-dim, Ollama) — higher capacity, better for multilingual or longer semantic content. Requires changing vector tables to FLOAT[1024] and re-embedding all stored data. The `Store` interface abstracts this; the change is a migration, not an application rewrite.

**Dropped**: `all-mpnet-base-v2` (previously mentioned as default). It requires a Python sentence-transformers install, not Ollama. Replacing it with nomic-embed-text unifies all model serving under one Ollama process.

### Decision 33: Pipeline 3 hybrid retrieval — BM25 + semantic + RRF + graph walk (2026-05-21)
Pipeline 3 is deferred but its interface and scoring design are captured now so Pipeline 1/2a/2b build the right foundations.

**Three retrieval views, fused:**
| View | Index | Targets | Latency |
|---|---|---|---|
| Lexical (BM25) | SQLite FTS5 on `memories.content` + `records.chunk_text` | Exact keyword match, entity names, tool names | ~5ms |
| Semantic (vec) | sqlite-vec on `memory_embeddings` + `record_embeddings` | Conceptual similarity, paraphrase match | ~20ms |
| Graph (BFS) | `cluster_edges` + `session_graph_edges` | Neighbourhood traversal, indirect connections | ~50ms |

**Fusion**: Reciprocal Rank Fusion (RRF) of lexical + semantic results:
`score = 1/(k + rank_bm25) + 1/(k + rank_vec)`, k=60 (standard). Graph walk is a post-retrieval expansion step, not a ranked list — it adds neighbouring memories to the candidate set after initial fusion.

**Graph walk scoring** (from MemWire BFS path scoring):
`path_score = relevance(node, query) × coherence(edge) × recency(node.last_observed)`
Walk terminates at depth 2 or when path_score drops below threshold (0.2).

**MCP tool interface (deferred)**:
```
recall(query, memory_type?, workspace_id?, top_k=10, include_graph=False)
  → [Memory + score + provenance]

chunk_search(query, session_id?, workspace_id?, top_k=20)
  → [Record + chunk_text + score]

graph_walk(seed_memory_id, depth=2)
  → [Memory + path_edges + path_score]

resolve_conflict(conflict_id)
  → Conflict + both memories + resolution options
```

**Prerequisites Pipeline 1/2 must satisfy** for hybrid retrieval to work:
- FTS5 index on `memories.content` and `records.chunk_text` (add at schema creation time)
- Cluster edges with `weight` populated correctly (PMI + cosine, Decision 21)
- `session_graph_edges.weight` set at ingest/light analytics time
- `memories.last_observed` and `records.span_start` populated for recency scoring

Add FTS5 tables at schema creation:
```sql
CREATE VIRTUAL TABLE memories_fts USING fts5(memory_id UNINDEXED, content, tokenize='porter ascii');
CREATE VIRTUAL TABLE records_fts USING fts5(record_id UNINDEXED, chunk_text, tokenize='porter ascii');
```
These are updated at write time alongside their parent tables.

### Decision 31: Two-step fact compression in Pipeline 2a (from TriMem, 2026-05-21)
Occurrence contexts are NOT passed directly to memory extraction. Instead, Pipeline 2a uses a two-step chain:

1. **Atomic fact compression** (local model, fast): occurrence context_texts → list of atomic declarative statements about the entity. Prompt: "Given these mentions of [entity], extract a list of distinct atomic facts — one claim per line, each self-contained and specific."
2. **Memory extraction** (local/Haiku): atomic facts → 0+ memories (type + content). Prompt: "Given these atomic facts about [entity], extract any generalizable patterns (procedural), notable events (episodic), or stable facts (semantic). One memory per line."

Atomic facts are ephemeral — they are an in-memory intermediate, not persisted to the database. They improve extraction quality by:
- Deduplicating redundant context before the LLM sees it
- Normalizing noisy passage text into clean declarative form
- Reducing hallucination risk from long unstructured context

This adds one local LLM call per promoted entity in Pipeline 2a (~200ms, negligible cost). Memory units produced are significantly cleaner and more uniform.

### Decision 22: Conflict detection designed from the start (2026-05-16)
Three detection mechanisms, run during analytics:
1. **Proximity + opposition**: memories with cosine similarity >0.85 that land in different clusters → candidate conflict
2. **LLM verification**: Haiku pairwise check on candidates: "Do these contradict? Which has more evidence?"
3. **Temporal supersession**: newer memory in same cluster as older → soft-supersede (not hard conflict)

Resolution lifecycle: `none → candidate → confirmed → resolved`. Superseded memories are soft-deleted (status="superseded"), never hard-deleted (auditability).

### Decision 23: TurboVec as scale-out ANN backend (2026-05-16)
TurboVec achieves 16× compression (2-bit) via data-oblivious quantization (no training, no rebuilds as corpus grows). At 768 dims (all-mpnet-base-v2 sweet spot), recall within 0–1 points of FAISS. `IdMapIndex` supports O(1) deletion by ID — important for conflict resolution.

**Role**: sqlite-vec remains primary for SQL-integrated filtered search. TurboVec is the upgrade path when scale demands pure ANN at >1M memories and memory compression matters. `Store` interface abstracts both.

**Not suitable for**: filtered search requiring SQL predicates (WHERE memory_tier=X) — needs two-step approach. For most retrieval paths in this system, sqlite-vec's single-query filtered ANN is preferable.

### Decision 36: Two-track memory extraction architecture (2026-05-29)

Entity-centric extraction (Track 1, existing Pipeline 2a) and action-pattern-centric extraction (Track 2, new) address fundamentally different questions and should not be collapsed into one mechanism.

**Track 1 — Entity-centric (existing)**
What: entity mentions → atomic facts → episodic/procedural/semantic memories
Unit: canonical entity (person, tool, technology, file, concept)
Answers: "What does the corpus know about X?" "How is X used?" "What happened with X?"
Memory types: all three (episodic, procedural, semantic)
Limitation: project-level entities (SLR Agent, ATS) are omnipresent in sessions working on those projects — their distribution relative to inflection points is flat, carrying no diagnostic signal.

**Track 2 — Action-pattern-centric (new)**
What: tool call sequences + surrounding text near inflection points → root-cause attribution → corrective procedural memories
Unit: inflection context tuple — `(precipitating_action, user_signal, agent_reasoning, interaction_type)`
- `precipitating_action`: `(tool, target, outcome)` triple — what the agent did mechanically
- `user_signal`: verbatim/summarized user message at or near the inflection (content matters, not just sentiment polarity)
- `agent_reasoning`: agent text between tool calls immediately before the inflection (captures the agent's wrong mental model)
- `interaction_type`: `tool_call` | `clarifying_exchange` | `self_correction`
Answers: "What action pattern caused this stuck state?" "What did the user correct?" "What did the agent misunderstand?"
Memory type: procedural only, with `action_orientation` dimension

`interaction_type` distinguishes failure modes:
- `tool_call`: agent acted, outcome was bad → knowledge or environment gap
- `clarifying_exchange`: agent asked, user answered, then course-corrected → ambiguity or misread request
- `self_correction`: agent caught its own error before user intervened → successful metacognition (positive signal)

The two tracks are complementary. Track 1 memories ground future sessions in project knowledge. Track 2 memories prevent repeated behavioral mistakes.

### Decision 37: Inflection point detection replaces session-level outcome labels (2026-05-29)

*Supersedes the earlier session_outcome classifier design.*

Session-level success/failure labels are the wrong granularity. Most sessions produce some outcome — a coarse `success` label discards the internal structure containing the real learning signal. A session classified as `success` overall can contain failed attempts, user corrections, and pivots. The lesson is in those turning points, not the terminal state.

**Inflection points** are turn-level events where the trajectory changes direction. Types:

| Type | Signal pattern |
|---|---|
| `user_correction` | Short user message with negation/redirect following agent tool calls |
| `agent_retraction` | Agent re-reads a file just edited; undoes a tool call; emits uncertainty markers |
| `loop_entry` | Same tool called 3+ times on same target without progress |
| `loop_exit` | After a loop or correction, a commit or user approval signal |
| `escalation` | Increasing failed Bash exit codes; agent switches file or strategy |

Inflection detection runs **in the same pass as chunking** — both operate on the parsed turn stream immediately after parsing. No additional pass. The existing `_build_records()` loop can emit `session_inflections` rows as a side output.

**`session_inflections` table**:
```sql
CREATE TABLE session_inflections (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    turn_index      INT  NOT NULL,
    inflection_type TEXT NOT NULL,   -- user_correction | agent_retraction |
                                     --   loop_entry | loop_exit | escalation
    user_sentiment  TEXT,            -- positive | negative | neutral
    agent_sentiment TEXT,            -- confident | uncertain | retracting
    signal_detail   TEXT,            -- what specifically triggered detection
    detected_at     TEXT NOT NULL
);
```

Segment boundaries are a derived concept — a segment is the span between two inflection points. They don't need to be detected independently.

### Decision 38: action_orientation as a second dimension on memories (2026-05-29)

*Adds to, does not replace, the episodic/procedural/semantic taxonomy.*

The existing `memory_type` (episodic/procedural/semantic) describes *what kind of knowledge* a memory captures. `action_orientation` describes *what behavioral context it came from*. They are orthogonal.

`action_orientation` values:
- `recovery` — entity/action near `loop_exit` inflections; "when this pattern fails, try..."
- `optimization` — entity/action near `loop_entry` inflections; "this usage pattern tends to cause loops"
- `strategy` — entity/action in stable turns with no inflections; "this approach works in context Y"
- `observation` — no clear inflection context; factual, lower retrieval priority

`action_orientation` applies primarily to **Track 2 corrective procedural memories**. Track 1 memories (semantic, episodic, non-corrective procedural) carry `NULL` — backward compatible.

The 2D characterization `(memory_type, action_orientation)`:
- High-value quadrant: `procedural × recovery` and `procedural × strategy`
- `action_orientation` is derived from the entity/action's position relative to inflection points, not from a coarse session label

Schema: `memories.action_orientation TEXT` (nullable, default NULL).

### Decision 39: Multi-judge annotation ensemble (2026-05-29)

Two annotation surfaces use a 3-provider LLM ensemble (Ollama OSS + Anthropic + Gemini) with human review only on disagreements:

**Surface 1 — Memory quality (eval automation, not a quality gate)**
Judges auto-label extracted memories as `correct` / `partial` / `incorrect`. Replaces manual annotation for Level 1a eval. The extractor model (gemma3:12b) is never used as a judge — circular validation. Entity judging is not needed (covered by the existing LLM verifier at ingest).

**Surface 2 — Inflection validation (precision estimation)**
Judges verify whether heuristically detected inflections are genuine and correctly typed. A 20% sample per session gives a reliable precision estimate before using inflection labels in Track 2 memory extraction.

Decision rule: 3/3 agree → auto-label; 2/3 agree → majority label + 10% spot-check; 1-1-1 split → human review queue.

**`judge_verdicts` table**:
```sql
CREATE TABLE judge_verdicts (
    id             TEXT PRIMARY KEY,
    surface        TEXT NOT NULL,    -- 'memory' | 'inflection'
    target_id      TEXT NOT NULL,    -- memory_id | session_inflection_id
    judge_model    TEXT NOT NULL,
    judge_provider TEXT NOT NULL,    -- 'ollama' | 'anthropic' | 'google'
    label          TEXT NOT NULL,
    reasoning      TEXT,
    judged_at      TEXT NOT NULL,
    run_id         TEXT
);
```

Open gap: atomic facts from `compress_to_facts()` are currently ephemeral. Persisting them (as `source_facts` column on `memories` or a `memory_facts` table) would let memory judges distinguish extraction failure from corpus sparsity. Deferred.

---

## Open Questions

- [ ] What triggers the heavy clustering run? (Schedule? Session count threshold? Manual?)
- [ ] Privacy: are these personal/team traces or multi-tenant?
- [ ] What does `embedding_text` serialization look like per source type? (need concrete templates)
- [x] ~~How should procedural/episodic be distinguished at ingestion?~~ → Type emerges from abstraction (Decision 20)

## MemWire Comparison Notes (updated 2026-05-16)

| Dimension | MemWire | Our design |
|---|---|---|
| Unit of memory | Atomic facts/messages | Variable-granularity memories extracted from trajectories |
| Graph layer | Token-level word graph | Multi-edge cluster neighbourhood graph |
| Analytics | None | Clio-style clustering → memory abstraction |
| Multi-tenancy | org → workspace → app → user | Same — borrowed (Decision 14) |
| Conflict detection | Weak (embedding divergence) | Designed: proximity+LLM+temporal (Decision 22) |
| Vector store | Qdrant (separate process) | sqlite-vec + TurboVec as scale backend (Decision 23) |

**Core difference**: MemWire stores and retrieves facts. We abstract memories from trajectories and discover patterns. Retrieval sophistication (BFS path scoring) still worth borrowing for the future MCP layer.

## Glean Context Data Platform Notes (2026-05-18)

Glean's architecture: Connectors → Indexes → Graphs → Memory (Personal + Enterprise). Maps directly onto ours: Source Plugins → Records → Cluster Graph → Memories.

**Key ideas absorbed:**
- **Multi-level abstraction** (atomic events → tasks → projects → initiatives) → `abstraction_level` field on memories (Decision 24)
- **Personal vs enterprise memory** as a semantic split → `scope` field on memories (Decision 25)
- **"Capture the how, not the why"** — validates trace-based approach; intent is inferred from behavioral patterns, not captured at ingestion

**What Glean doesn't publish** (marketing-level only): graph node/edge structure, task/project boundary inference algorithm, retrieval mechanism details. ~80% task-understanding accuracy cited without methodology.

**What we do that Glean doesn't**: hierarchical clustering for unknown failure mode discovery, conflict detection, open-source multi-source ingestion pipeline with typed adapters.

## TriMem Analysis Notes (2026-05-21)

**What TriMem is**: Python library (LanceDB + SQLite, Qwen3-Embedding 1024-dim) implementing a tripartite memory system. Three tiers: raw dialogues → atomic facts (`memory_builder.py`) → per-entity profiles (`profile_manager.py`). `HybridRetriever` combines semantic + lexical + symbolic views. TextGrad-style prompt evolution loop. Evaluates on LoCoMo benchmark.

**Validates our decisions:**
| Our decision | TriMem evidence |
|---|---|
| Three-tier taxonomy (D5) | Independently arrives at same episodic/semantic/procedural split |
| Sessions ≠ records ≠ memories (D19) | Raw dialogues → atomic facts → profiles = same three-level stack |
| Chunking with overlap (D27) | Configurable sliding window with overlap |
| memory_sources many-to-many (D19) | "Cross-memory links between episodic events and semantic abstractions" |
| Conflict detection + temporal decay (D22) | "Interference resolution using confidence scores and temporal decay" |
| Hierarchical memory extraction (2b) | Episodic → semantic via clustering and abstraction |
| access_count / recency (D16) | Temporal decay in retrieval weighting |

**Design additions from TriMem:**

**Entity profiles**: TriMem's `profile_manager.py` maintains per-entity LLM-generated prose summaries, updated incrementally. Our entity nodes only carry `canonical_name` + counts. Add `entity_profile TEXT` to entities table — synthesized prose description of the entity, generated by local LLM when degree_count first crosses threshold, updated when new occurrences arrive. Makes entities first-class memory objects, not just graph anchors.

**Two-step fact compression before memory extraction**: TriMem compresses dialogue windows into atomic facts before building memories. Implication: Pipeline 2a should not pass occurrence contexts directly to memory extraction. Instead: occurrence contexts → atomic fact compression (local LLM) → memory extraction from facts. Occurrence contexts are the raw input; atomic facts are the intermediate; memories are the output. Adds one step but produces cleaner, more uniform memory units.

**Hybrid retrieval (lexical + semantic + symbolic)**: TriMem's `HybridRetriever` adds BM25 (lexical) to semantic vec search + SQL filters. BM25 over `memories.content` + `records.chunk_text` is cheap and substantially improves recall for exact-match queries. Add as a future retrieval enhancement; design the retrieval interface to accommodate it.

**Prompt evolution**: TextGrad-style loop (diagnose failures → rewrite extraction prompts) is a systematic approach to Level 0 evaluation improvements. Relevant for evaluation harness design.

**LoCoMo benchmark**: use as one of our Level 2/3 evaluation datasets rather than building entirely custom benchmark from scratch.

**Embedding model**: Qwen3-Embedding (1024-dim) is newer, multilingual, higher capacity vs our all-mpnet-base-v2 (768-dim). Keep all-mpnet-base-v2 for prototype (faster, lighter); evaluate Qwen3-Embedding for production, especially if multilingual matters.

**Resolved challenge**: we initially assumed occurrence contexts are the final input for Pipeline 2a memory extraction. TriMem confirms occurrence contexts → atomic fact compression → memory is the right chain (see Decision 31). The fact compression step produces cleaner, more uniform memory units than raw context passages.

---

## Data Model (updated 2026-05-21)

### sessions table — source-file level (immutable after write)
```sql
CREATE TABLE sessions (
  id              TEXT PRIMARY KEY,   -- sha256(plugin+path+content_hash)
  org_id          TEXT NOT NULL DEFAULT 'default',
  workspace_id    TEXT NOT NULL DEFAULT 'default',
  app_id          TEXT NOT NULL DEFAULT 'default',
  user_id         TEXT NOT NULL DEFAULT 'default',
  source_type     TEXT NOT NULL,      -- "agent_trace" | "meeting_transcript"
  source_plugin   TEXT NOT NULL,      -- "claude_code" | "otel" | "ox" | "zoom" | "otter"
  session_timestamp TEXT,             -- ISO8601; start time of session
  ingested_at     TEXT NOT NULL,
  session_summary TEXT NOT NULL,      -- hierarchical summary from chunk summaries
  embedding_text  TEXT NOT NULL,      -- structured prose for session embedding (clustering)
  raw_facets      TEXT NOT NULL,      -- JSON blob, source-specific session metadata
  tags            TEXT,               -- JSON array
  metadata        TEXT,               -- JSON escape hatch
  chunk_count     INTEGER DEFAULT 0,
  -- set by full analytics:
  cluster_id      TEXT,               -- FK → clusters.id
  analytics_run_id TEXT
);
-- indexes: (org_id, workspace_id), source_type, session_timestamp, cluster_id
```

### records table — chunk level (immutable after write)
```sql
CREATE TABLE records (
  id              TEXT PRIMARY KEY,   -- sha256(session_id+chunk_index)
  session_id      TEXT NOT NULL,      -- FK → sessions.id
  chunk_index     INTEGER NOT NULL,   -- ordered position within session
  chunk_text      TEXT NOT NULL,      -- raw chunk text
  chunk_summary   TEXT,               -- 2-3 sentence summary (local model)
  embedding_text  TEXT NOT NULL,      -- structured text for chunk embedding
  token_count     INTEGER,
  span_start      TEXT,               -- timestamp or char offset into source
  span_end        TEXT
);
-- indexes: session_id, (session_id, chunk_index)
```

### entities table — canonical entity nodes
```sql
CREATE TABLE entities (
  id              TEXT PRIMARY KEY,   -- hash(org_id+entity_type+canonical_name) for structured;
                                      -- uuid for named/concept
  org_id          TEXT NOT NULL DEFAULT 'default',
  workspace_id    TEXT NOT NULL DEFAULT 'default',
  canonical_name  TEXT NOT NULL,      -- "Alice Chen", "PR #1234", "React 18"
  entity_type     TEXT NOT NULL,      -- "person"|"tool"|"technology"|"file"|"commit"|"pr"|"concept"
  degree_count    INTEGER DEFAULT 0,  -- sessions referencing this entity; drives 2a promotion
  first_seen      TEXT NOT NULL,
  last_seen       TEXT NOT NULL,
  source_diversity TEXT,              -- JSON {source_types:[..], user_ids:[..]}
  entity_profile  TEXT,               -- LLM-generated prose summary of this entity;
                                      -- generated when degree_count first crosses threshold,
                                      -- updated as new occurrences arrive (from TriMem)
  memory_promoted TEXT,               -- ISO8601 when last promoted to memory; null if never
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
-- indexes: (org_id, workspace_id, entity_type), degree_count, memory_promoted
```

### occurrences table — entity mention instances in chunks (replaces record_entity_edges)
```sql
CREATE TABLE occurrences (
  id              TEXT PRIMARY KEY,   -- sha256(record_id+entity_id+span_start)
  record_id       TEXT NOT NULL,      -- FK → records.id (the chunk)
  session_id      TEXT NOT NULL,      -- FK → sessions.id (denormalized for fast joins)
  entity_id       TEXT NOT NULL,      -- FK → entities.id
  role            TEXT NOT NULL,      -- "subject"|"tool_used"|"person_mentioned"|
                                      -- "file_modified"|"commit_ref"|"topic"
  mention_text    TEXT NOT NULL,      -- exact text span of the mention
  context_text    TEXT NOT NULL,      -- surrounding 2-3 sentences (for memory extraction)
  span_start      TEXT,
  span_end        TEXT,
  created_at      TEXT NOT NULL
);
-- indexes: entity_id, session_id, record_id
```

### session_graph_edges table — structural + workspace + shared-memory graph on sessions
```sql
CREATE TABLE session_graph_edges (
  source_session_id TEXT NOT NULL,    -- FK → sessions.id
  target_session_id TEXT NOT NULL,    -- FK → sessions.id
  edge_type         TEXT NOT NULL,    -- "structural" | "workspace" | "shared_memory"
  via_entity_id     TEXT NOT NULL DEFAULT '',  -- FK → entities.id ("structural"), memories.id ("shared_memory"), or '' ("workspace")
  weight            REAL DEFAULT 1.0,
  direction         TEXT DEFAULT 'undirected',
  created_by        TEXT NOT NULL,    -- "ingestion"|"clustering_analytics"|"backfill"
  created_at        TEXT NOT NULL,
  PRIMARY KEY (source_session_id, target_session_id, edge_type, via_entity_id)
);
```
Every edge is inserted in **both directions** (`A→B` and `B→A`) at creation time — despite
`direction='undirected'` always being the value, edges used to only be written one-directional per
insert, making them invisible from whichever session was ingested first. See `design_decisions.md`
2026-07-13 entry ("Session graph edges: two independent bugs fixed") for the full story.

Three edge types, each a different connection signal:
- `workspace` — same project, computed directly from `sessions.workspace_id` (normalized via
  `format_workspace()` to match across harnesses), no LLM/entity dependency. Created at ingest time.
- `structural` — shared `file`/`commit`/`pr` entity, i.e. these sessions touched the exact same file.
  Created at ingest time.
- `shared_memory` — both sessions contributed a member to the same consolidated `cluster` memory
  (`memory_cluster_members`), i.e. they independently exhibited the same recurring pattern/insight.
  `via_entity_id` is the cluster memory's own id, so multiple shared clusters between the same session
  pair produce multiple distinct rows, same as multiple shared-file `structural` edges. Created by
  `ats cluster-memories`, not ingest — run that command for this edge type to exist. Often the only
  signal connecting sessions in *different* projects/harnesses, since it's topic/reasoning-based
  rather than file- or workspace-scoped.

`same_entity`/`enrichment` edge types mentioned in older versions of this doc were never implemented.

### memories table — abstraction layer (written by all three pipelines)
```sql
CREATE TABLE memories (
  id              TEXT PRIMARY KEY,   -- uuid
  org_id          TEXT NOT NULL DEFAULT 'default',
  workspace_id    TEXT NOT NULL DEFAULT 'default',
  app_id          TEXT NOT NULL DEFAULT 'default',
  memory_type     TEXT NOT NULL,      -- "episodic" | "procedural" | "semantic"
  extraction_method TEXT NOT NULL,    -- "explicit" | "frequency" | "cluster"
  content         TEXT NOT NULL,      -- prose statement (LLM-generated)
  evidence_count  INTEGER DEFAULT 1,  -- distinct sessions supporting this memory
  first_observed  TEXT,               -- ISO8601 earliest source session timestamp
  last_observed   TEXT,               -- ISO8601 most recent source session timestamp
  tags            TEXT,               -- JSON array
  conflict_status TEXT DEFAULT 'none',-- "none"|"candidate"|"confirmed"|"resolved"|"superseded"
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  created_by_pipeline TEXT NOT NULL,  -- "ingestion"|"light_analytics"|"full_analytics"
  analytics_run_id TEXT,              -- nullable; null for ingestion-time memories
  access_count    INTEGER DEFAULT 0,
  last_accessed_at TEXT,
  action_orientation TEXT,            -- "strategy"|"recovery"|"optimization"|"observation"|NULL
                                      -- NULL = Track 1 memory or not yet classified (Decision 38)
  -- DEFERRED fields (add after Level 0+1 validation):
  -- scope TEXT              -- "personal"|"team"|"org"    (Decision 25)
  -- abstraction_level INT   -- 0=atomic..3=initiative     (Decision 24)
  -- temporal_variance REAL  -- stddev of source timestamps
  -- distinct_users INT      -- drives scope computation
  metadata        TEXT                -- JSON escape hatch
);
```

### session_inflections table — turn-level trajectory change events (Decision 37)
```sql
CREATE TABLE session_inflections (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,     -- FK → sessions.id
    turn_index      INT  NOT NULL,     -- position in the turn stream
    inflection_type TEXT NOT NULL,     -- 'user_correction' | 'agent_retraction' |
                                       --   'loop_entry' | 'loop_exit' | 'escalation'
    user_sentiment  TEXT,              -- 'positive' | 'negative' | 'neutral'
    agent_sentiment TEXT,              -- 'confident' | 'uncertain' | 'retracting'
    signal_detail   TEXT,              -- what triggered detection (for debugging)
    detected_at     TEXT NOT NULL
);
-- indexes: session_id, (session_id, turn_index), inflection_type
```

### judge_verdicts table — multi-judge annotation ensemble (Decision 39)
```sql
CREATE TABLE judge_verdicts (
    id             TEXT PRIMARY KEY,
    surface        TEXT NOT NULL,      -- 'memory' | 'inflection'
    target_id      TEXT NOT NULL,      -- memory_id | session_inflection_id
    judge_model    TEXT NOT NULL,      -- e.g. 'llama3.2', 'claude-haiku-4-5'
    judge_provider TEXT NOT NULL,      -- 'ollama' | 'anthropic' | 'google'
    label          TEXT NOT NULL,
    reasoning      TEXT,
    judged_at      TEXT NOT NULL,
    run_id         TEXT                -- links to analytics_runs for cost attribution
);
-- indexes: (surface, target_id), judge_provider
```

### memory_sources table — many-to-many: memories ↔ sessions/records
```sql
CREATE TABLE memory_sources (
  memory_id       TEXT NOT NULL,      -- FK → memories.id
  session_id      TEXT NOT NULL,      -- FK → sessions.id
  record_id       TEXT,               -- FK → records.id; nullable (session-level link)
  occurrence_id   TEXT,               -- FK → occurrences.id; nullable (chunk-level link)
  relevance_score REAL DEFAULT 1.0,
  span_hint       TEXT,               -- JSON {start, end} for sub-chunk memories
  PRIMARY KEY (memory_id, session_id)
);
```

### conflicts table
```sql
CREATE TABLE conflicts (
  id              TEXT PRIMARY KEY,
  memory_id_a     TEXT NOT NULL,      -- FK → memories.id
  memory_id_b     TEXT NOT NULL,      -- FK → memories.id
  conflict_type   TEXT NOT NULL,      -- "contradiction"|"supersession"|"refinement"
  detected_at     TEXT NOT NULL,
  resolution      TEXT,               -- "a_wins"|"b_wins"|"merged"|"context_dependent"|null
  resolved_at     TEXT,
  resolver        TEXT,               -- "system"|"user"
  analytics_run_id TEXT NOT NULL
);
```

### clusters table
```sql
CREATE TABLE clusters (
  id              TEXT PRIMARY KEY,
  analytics_run_id TEXT NOT NULL,
  label           TEXT NOT NULL,      -- Sonnet/Haiku-generated short title
  description     TEXT,
  level           INTEGER NOT NULL,   -- 0=leaf, 1=mid, 2=top
  parent_id       TEXT,               -- FK → clusters.id; null at top level
  member_count    INTEGER DEFAULT 0
);
```

### cluster_edges table — ML-derived neighbourhood graph
```sql
CREATE TABLE cluster_edges (
  source_cluster_id TEXT NOT NULL,
  target_cluster_id TEXT NOT NULL,
  edge_type         TEXT NOT NULL,    -- "similarity"|"pmi"|"transition"|"lag"
  weight            REAL NOT NULL,
  direction         TEXT NOT NULL,    -- "undirected"|"directed"
  analytics_run_id  TEXT NOT NULL,
  PRIMARY KEY (source_cluster_id, target_cluster_id, edge_type, analytics_run_id)
);
```

### analytics_runs table
```sql
CREATE TABLE analytics_runs (
  id              TEXT PRIMARY KEY,
  pipeline        TEXT NOT NULL,      -- "light"|"full"
  started_at      TEXT NOT NULL,
  completed_at    TEXT,
  session_count   INTEGER,
  memory_count    INTEGER,
  cluster_count   INTEGER,
  conflict_count  INTEGER,
  k_value         INTEGER,            -- null for light analytics
  embedding_model TEXT NOT NULL,
  labeler_model   TEXT
);
```

### FTS5 tables — lexical search (for Pipeline 3 BM25, Decision 33)
```sql
-- Porter-stemmed full-text search over memory content
CREATE VIRTUAL TABLE memories_fts USING fts5(
  memory_id UNINDEXED,
  content,
  tokenize='porter ascii'
);
-- Full-text search over raw chunk text
CREATE VIRTUAL TABLE records_fts USING fts5(
  record_id UNINDEXED,
  chunk_text,
  tokenize='porter ascii'
);
```
These are populated at write time alongside their parent tables (INSERT INTO memories_fts when writing memories, etc.).

### Vector storage (sqlite-vec) — all in same SQLite file
```sql
-- session-level embeddings for clustering
CREATE VIRTUAL TABLE session_embeddings USING vec0(
  session_id TEXT PRIMARY KEY,
  embedding FLOAT[768]
);
-- chunk-level embeddings for retrieval
CREATE VIRTUAL TABLE record_embeddings USING vec0(
  record_id TEXT PRIMARY KEY,
  embedding FLOAT[768]
);
-- entity resolution embeddings (stable canonical form)
CREATE VIRTUAL TABLE entity_embeddings USING vec0(
  entity_id TEXT PRIMARY KEY,
  embedding FLOAT[768]       -- embed(f"{entity_type}: {canonical_name}")
);
-- occurrence context embeddings (contextual, per mention)
CREATE VIRTUAL TABLE occurrence_embeddings USING vec0(
  occurrence_id TEXT PRIMARY KEY,
  embedding FLOAT[768]       -- embed(context_text)
);
-- memory embeddings for retrieval
CREATE VIRTUAL TABLE memory_embeddings USING vec0(
  memory_id TEXT PRIMARY KEY,
  embedding FLOAT[768]
);
-- cluster centroid embeddings
CREATE VIRTUAL TABLE cluster_embeddings USING vec0(
  cluster_id TEXT PRIMARY KEY,
  embedding FLOAT[768]
);
```

Example filtered memory search:
```sql
SELECT m.*, vec_distance_cosine(e.embedding, ?) AS score
FROM memories m
JOIN memory_embeddings e ON m.id = e.memory_id
WHERE m.memory_type = 'procedural'
  AND m.org_id = 'myorg'
ORDER BY score LIMIT 10;
```

---

## System Sketch (updated 2026-05-21)

```
CORPUS DISCOVERY (runs before Pipeline 1)
───────────────────────────────────────────────────────────────────────
CorpusScanner
  walk scan_paths → find *.jsonl (skip subagents/) + transcript files
  compare content_hash vs ingestion_state
  → queue of (abs_path, file_type) for Pipeline 1

PIPELINE 1: INGESTION (per source file, event-driven, stateless)
───────────────────────────────────────────────────────────────────────
Source file
  └─ Plugin (ClaudeCodeSource / OtelSource / OxSource / ZoomSource / OtterSource)
       │
       ▼
  parse() → raw turn stream
       │
       ├─ single pass over turn stream (chunk + inflect simultaneously):
       │    chunk()          → exchanges / topic segments, 350-token max, 10% overlap
       │    inflect_detect() → session_inflections [heuristic, no LLM — Decision 37]
       │                        (user_correction, agent_retraction, loop_entry/exit, escalation)
       │
       ├─ per chunk:
       │    chunk_summarize()     → chunk_summary        [local model]
       │    embed(embedding_text) → record_embeddings    [nomic-embed-text via Ollama]
       │    entity_extract()      → tool_call parse (claude_code) OR
       │                            regex + NER + LLM cascade (others) [Decisions 29, 35]
       │    entity_resolve()      → upsert entities, increment degree_count
       │    occurrence_extract()  → occurrences + occurrence_embeddings
       │    structural_edges()    → session_graph_edges  [structural entities only]
       │    explicit_memories()   → memories[explicit]   [local model, Path A]
       │
       └─ session level:
            session_summarize()  → session_summary      [hierarchical from chunks]
            embed(session_embedding_text) → session_embeddings
            write sessions, records, entities, occurrences, session_inflections
            (all in one transaction)

PIPELINE 2a: LIGHT ANALYTICS (corpus-level, nightly, graph-query-based)
───────────────────────────────────────────────────────────────────────
  TRACK 1 — Entity-centric (existing, Decision 36):
  1. Query: entities WHERE degree_count >= threshold OR occurrence_count >= occ_threshold
               AND (memory_promoted IS NULL OR last_seen > memory_promoted)
  2. For each candidate entity:
       fetch occurrences (context_text, ranked by recency, top 10-20)
       LLM → compress to atomic facts (ephemeral)             [local model]
       LLM → extract 0+ memories (episodic/procedural/semantic, action_orientation=NULL)
       LLM → generate/update entity_profile prose             [local model]
       write memories[frequency] + memory_sources
       update entities.entity_profile, entities.memory_promoted
  3. Deduplicate explicit memories with high embedding similarity

  TRACK 2 — Action-pattern-centric (new, Decision 36+37):
  1. Query: session_inflections grouped by session + turn proximity
  2. For each inflection cluster:
       extract inflection_context tuple:
         (precipitating_action=(tool,target,outcome),
          user_signal, agent_reasoning, interaction_type)
       LLM → extract corrective procedural memories with action_orientation
               recovery memories ← near loop_exit inflections
               optimization memories ← near loop_entry inflections
               strategy memories ← stable turns, no inflections
       write memories[frequency] with action_orientation populated
  → analytics_runs record (pipeline="light")

PIPELINE 2b: FULL ANALYTICS (corpus-level, weekly / threshold-triggered)
───────────────────────────────────────────────────────────────────────
  1. cluster(session_embeddings) → k-means large k → cluster assignments
  2. label_clusters(Sonnet/Haiku) → clusters.label + description
  3. build_hierarchy() → re-embed cluster labels → coarser clusters → parent_id
  4. extract_memories() → memories[cluster]
       latent procedural patterns (high recurrence, implicit)
       latent semantic facts (cross-session stability)
  5. build_cluster_graph() → cluster_edges
       similarity (cosine centroid), PMI, temporal transition, lag
  6. detect_conflicts() → conflicts + memory.conflict_status
  → analytics_runs record (pipeline="full")

PIPELINE 3: RETRIEVAL (query-time, read-only — deferred, design captured in Decision 33)
───────────────────────────────────────────────────────────────────────
MCP server ◄── Claude Code / Codex

  recall(query, memory_type?, workspace_id?, top_k=10, include_graph=False)
    → BM25 (FTS5 on memories.content)
    + vec search (memory_embeddings, filtered)
    → RRF fusion: score = 1/(60+rank_bm25) + 1/(60+rank_vec)
    → optional graph_walk expansion (depth=2, path_score = relevance×coherence×recency)

  chunk_search(query, session_id?, workspace_id?, top_k=20)
    → BM25 (FTS5 on records.chunk_text)
    + vec search (record_embeddings)
    → RRF fusion

  graph_walk(seed_memory_id, depth=2)
    → BFS on cluster_edges + session_graph_edges

  resolve_conflict(id)   → surface conflict for user resolution

MODEL PROVIDER ABSTRACTION (Decision 28)
───────────────────────────────────────────────────────────────────────
ModelProvider interface: complete(prompt, schema?) → str | dict
  OllamaProvider  → Llama 3.2 3B / Qwen2.5 3B  (entity extract, chunk summary,
                                                  explicit memory)
  AnthropicProvider → Haiku / Sonnet             (session summary, cluster labels,
                                                  conflict verification)
```

---

## Evaluation Framework (2026-05-19)

**Core principle**: each pipeline is evaluated independently before the next pipeline is trusted. The delta between retrieval-with-analytics and retrieval-baseline is the measured value of the analytics investment.

**Required prerequisite**: a hand-labeled ground-truth corpus of 20–30 sessions and transcripts with annotated expected memories, types, and relationships. This is not optional — it is the measurement instrument.

### Level 0 — Ingestion quality (Pipeline 1)
*Evaluated against a hand-labeled corpus. Must pass before Level 1 is meaningful.*

**Bad entity extraction → bad occurrence contexts → bad frequency memories → bad analytics.**

#### Dataset shape and sourcing

Target: **20–30 sessions** (mix of source types). Annotated format is a JSON sidecar per session:

```json
{
  "session_id": "sha256...",
  "source_type": "agent_trace",
  "expected_entities": [
    {"canonical_name": "PR #1234", "entity_type": "pr"},
    {"canonical_name": "Alice Chen", "entity_type": "person"}
  ],
  "expected_occurrences": [
    {
      "chunk_index": 2,
      "entity_canonical": "PR #1234",
      "role": "subject",
      "mention_text": "merged PR #1234",
      "context_summary": "deployment discussion"
    }
  ],
  "expected_explicit_memories": [
    {"memory_type": "episodic", "content": "PR #1234 was merged on ...", "evidence_chunk": 2}
  ],
  "expected_structural_edges": [
    {"edge_type": "structural", "via_entity": "PR #1234", "note": "any session referencing this PR"}
  ]
}
```

**Source recommendations:**

| Component | Source | Count | Notes |
|---|---|---|---|
| Agent traces | Your own Claude Code sessions (`~/.claude/projects/`) | 10–15 | Ingest all, then label a subset; familiar ground truth, representative of real use |
| Meeting transcripts | LoCoMo sessions (snap-research/locomo, Apache 2.0) | 5–10 | Select from the 10-session dataset; annotate entity/occurrence layer manually |

**Workflow**: ingest the full Claude Code corpus first (all 12 top-level sessions, Decision 34). Then select the 10–15 most substantive sessions (by tool_call count or session duration) as the annotation target. This means the eval corpus is a labeled subset of the actual production corpus — no separate data collection step.

**LoCoMo for Level 0**: LoCoMo sessions contain PERSON, LOCATION, DATE/TIME annotations and event graphs — these map to our `entity_type` taxonomy. Select 5–10 sessions, strip the existing QA layer, annotate expected occurrences and explicit memories in our format. Annotation effort: ~1–2 hours/session. The 10-session `locomo10.json` file covers 300–600 turns each across 6–12 simulated months.

**LoCoMo for Level 2/3**: LoCoMo's 7,500+ QA pairs (single-hop, multi-hop, temporal, adversarial) are the right instrument for retrieval quality evaluation — use directly as the Level 2/3 query set. This is LoCoMo's primary design target.

**Annotation effort estimate**: ~2–3 hours/session for agent traces (tool calls require careful reading), ~1.5 hours/session for transcripts. Total: ~35–50 hours for a 20-session corpus.

#### Metrics
- **Chunk summary faithfulness**: ROUGE-L chunk_summary vs chunk_text (target >0.4), plus human spot-check of 10 random samples
- **Session summary faithfulness**: human eval — does summary capture key decisions/outcomes? (3-point scale, target avg ≥ 2.5)
- **Entity extraction recall**: `|extracted ∩ labeled| / |labeled|` per entity_type (target >0.80 for structured types, >0.70 for concepts)
- **Entity resolution precision**: `|correct_resolutions| / |total_resolutions|` — no spurious merges, no missed merges (target >0.90)
- **Occurrence context quality**: human spot-check — does context_text contain enough signal for memory extraction? (binary, target >0.85)
- **Structural edge precision**: `|correct_structural_edges| / |extracted_structural_edges|` (target >0.95 — low false-positive tolerance)
- **Explicit memory precision**: of Path A memories, what fraction are genuine vs trivial/hallucinated? (human eval, target >0.70)
- **Embedding coverage**: 100% of sessions and records embedded (hard requirement)

### Level 1a — Light analytics quality (Pipeline 2a)
- **Frequency memory recall**: what fraction of ground-truth procedural/semantic memories does frequency promotion surface?
- **False positive rate**: of frequency-promoted memories, what fraction are spurious patterns?
- **Deduplication accuracy**: do merged explicit memories preserve the correct content?

### Level 1b — Full analytics quality (Pipeline 2b)
- **Cluster recovery rate**: synthetic reconstruction with K known categories → target 80-90% vs ~5% random baseline (Clio method)
- **Cross-run stability**: adjusted Rand index between two runs on same corpus with different seeds
- **Cluster coherence**: avg pairwise cosine similarity within clusters
- **Memory extraction recall**: of ground-truth memories, what fraction does Path C surface that Paths A+B missed?

### Level 2 — Retrieval baseline (Pipeline 1 output only, no analytics)
*Control condition. Establishes the floor that analytics must beat.*
- P@K and R@K on a query set with known relevant records/memories
- Measured on records-only retrieval (no memories table, no cluster graph)

### Level 3 — Retrieval with analytics boost
- Same query set, same metrics, retrieval on memories + graph
- **Light analytics boost** = Level 3(2a only) − Level 2
- **Full analytics boost** = Level 3(2a+2b) − Level 2
- **Downstream task performance**: agent with memory vs without on a fixed task set (ultimate test)

*If the delta is small or negative, the analytics pipeline does not justify its complexity.*

---

## Vector Backend Decision Matrix

| Scenario | Backend | Reason |
|---|---|---|
| Prototype, <100K memories | sqlite-vec | SQL-integrated, zero infra, filtered search |
| Production, filtered search | Postgres + pgvector (HNSW) | SQL + vector in one system |
| Scale >1M memories, pure ANN | TurboVec | 16× compression, no training, fast deletion |
| Filtered + scale | Postgres + pgvector + TurboVec sidecar | pgvector for filter, TurboVec for bulk ANN |

`Store` interface abstracts all backends. Backend is a configuration choice, not an application change.

---

## Deferred Sub-systems

1. **Document ingestion** — semantic chunking, stable reference material → semantic memory
2. **Additional source plugins** (TODO — enables shared memory across agents):
   - `GeminiSource`: Gemini CLI session traces (format TBD)
   - `OpenCodeSource`: OpenCode session traces (format TBD)
   - `CodexSource`: OpenAI Codex CLI session traces (format TBD)
   All map to `source_type="agent_trace"`, distinguished by `source_plugin` and `app_id`.
2. **MCP retrieval server** — hybrid BM25 + semantic + RRF + graph walk (design captured in Decision 33; FTS5 tables in schema)
3. **Session-start injection** — CLAUDE.md or hook to pre-load relevant memories
4. **BFS graph retrieval** — path scoring (relevance × coherence × recency) over cluster_edges
5. **ox adapter** — native integration once prototype proves out
6. **Privacy/multi-tenancy** — per-tenant thresholds, sanitization pipeline
7. **Conflict resolution UI** — surface confirmed conflicts for human resolution

## Deferred Schema Fields (add after Level 0 + Level 1 validation)

These fields are designed but premature — add only once evaluation shows they're needed:
- `memories.scope` ("personal"|"team"|"org") — requires multi-user corpus to validate (Decision 25)
- `memories.abstraction_level` (0–3) — requires abstraction hierarchy to be validated (Decision 24)
- `memories.temporal_variance` — requires sufficient episodic data volume
- `memories.distinct_users` — add together with scope field

## Build Order (what to build first)

**Completed:**
1. ✅ CorpusScanner + ingestion_state table
2. ✅ ClaudeCodeSource plugin + JSONL parsing
3. ✅ ModelProvider abstraction (OllamaProvider + AnthropicProvider)
4. ✅ Pipeline 1: Ingestion (chunking, summarization, entity extraction, occurrence tracking)
5. ✅ Level 0 evaluation framework (entity recall/precision annotation harness)
6. ✅ Pipeline 2a: Light Analytics Track 1 (entity promotion, fact compression, memory extraction)
7. ✅ Level 1a evaluation + human annotation UI (memory viewer, labeled_memories FPR)

**Next — Learning Loop (see `design/learning_loop.md`):**
8. Inflection point detector — heuristic, in same pass as chunking (Decision 37)
9. `action_orientation` field + Track 2 memory extraction (Decision 36, 38)
10. Level 1a evaluation extended — inflection precision, action_orientation distribution
11. Multi-judge annotation ensemble — memory quality + inflection validation (Decision 39)
    - GeminiProvider implementation required
12. Pipeline 2b: Full Analytics — clustering, labeling, hierarchy, cluster-derived memories
13. Level 1b evaluation — cluster recovery, coherence
14. Level 2 + 3 evaluation — retrieval baseline vs analytics boost
15. Pipeline 3: Retrieval (MCP) — only after analytics value is demonstrated
