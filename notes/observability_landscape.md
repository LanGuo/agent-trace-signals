# Agent Observability Landscape — Survey & Comparison with ATS

_Last updated: 2026-06-12_

## Purpose

Survey of existing agent/LLM observability tooling, with a comparison against this project (`agent_trace_signals`, "ATS"). The goal is to identify where ATS sits in the landscape, what patterns are worth borrowing, and what gap ATS uniquely fills.

---

## 1. Tool-by-tool summary

### claude-tap ([github.com/liaohch3/claude-tap](https://github.com/liaohch3/claude-tap))

- **Capture**: Local reverse/forward HTTP proxy. Intercepts Anthropic Messages API (and other providers) before forwarding upstream.
- **Storage**: JSONL on disk under `.traces/`; exports self-contained HTML viewers and portable `.ctap.json` bundles. Auth headers redacted.
- **Data model**: Per-request rows with timestamp, request/response payload, tool schemas, reconstructed streaming output, token usage, cache stats.
- **Distinctive feature**: **Hierarchical request diffs** — compares consecutive requests, highlights added messages, system-prompt deltas, character-level inline diffs. Excellent for debugging multi-turn agent behavior.
- **Mode**: Live capture, offline review. No analytics layer.

### LangSmith ([github.com/langchain-ai/langsmith-sdk](https://github.com/langchain-ai/langsmith-sdk))

- **Capture**: `@traceable` decorator + SDK wrappers (`wrap_openai`, `wrap_anthropic`). Batched async POST to cloud.
- **Data model**: `RunTree` — tree of runs with `run_type: llm|chain|tool`, `inputs`, `outputs`, `parent_run_id`, token usage, latency.
- **Analytics**: Token/cost/latency dashboards, error categorization by run type, dataset construction from top-level runs.
- **Evaluation coupling**: First-class — datasets feed eval runs, evaluator scores attach back to spans, regressions tracked across prompt/model versions.
- **Mode**: Online, synchronous (capture during execution).

### Langfuse

- **Capture**: SDKs + integrations; spans batched over HTTPS.
- **Data model**: Trace → span DAG. Fields per span: id, name, parent_span_id, input, output, usage, cost, metadata; optional fine-grained events (streaming chunks, tool calls).
- **Storage (self-hosted)**: Supabase (ops), ClickHouse (analytics), Minio (artifacts).
- **Analytics**: Latency percentiles, token/cost trends, error rates, async LLM-judge eval scoring over captured traces.
- **Mode**: Online capture; eval scoring runs async.

### Helicone

- **Capture**: AI gateway — Cloudflare Workers proxy LLM traffic. Zero SDK changes (one API-key swap).
- **Storage**: Supabase + ClickHouse + Minio; same split as Langfuse.
- **Data model**: Provider-agnostic request/response telemetry, automatic cost calculation, user/session association, tool-call tracking.
- **Distinctive**: Gateway-as-instrumentation. Strong cost analytics; model-routing intelligence built in.
- **Mode**: Online proxy.

### Phoenix / Arize ([github.com/Arize-ai/phoenix](https://github.com/Arize-ai/phoenix))

- **Capture**: OpenTelemetry SDK. Auto-instrumentation packages for LangChain/LlamaIndex/OpenAI/etc., OTLP over gRPC/HTTP.
- **Data model**: OTel spans with **OpenInference semantic conventions** — standard attribute names (`llm.input_messages`, `llm.output_messages`, `tool.calls`, `retrieval.documents`). Vendor-agnostic.
- **Analytics**: Span duration analysis, token aggregation, async LLM evals, experiment comparison across prompts/models.
- **Mode**: Online capture; evals async. Self-hostable.
- **Strength**: Standardized schema → interoperable across frameworks.

### Braintrust

- **Capture**: SDK decorators + context managers, batched to cloud.
- **Data model**: Traces + versioned **datasets** + **experiments** (prompt/model/param variants) + scores.
- **Analytics**: Score aggregation (mean/variance/percentiles), A/B deltas between experiment variants, regression detection on score drops, fine-tuning data export.
- **Mode**: Online capture; eval-first product positioning.

---

## 2. Are these tools online or offline?

**Yes — almost all of them are online/synchronous.** They instrument the agent at runtime and stream telemetry to a backend (cloud or self-hosted) as the agent executes. The "analysis" they offer is largely:

- Realtime dashboards (latency, tokens, cost, errors)
- Async evaluators that re-score recent traces
- Per-trace drill-down for debugging

The unit of insight is the **individual span/run** or aggregate metrics across them. They are essentially **APM for LLM calls** — patterned after Datadog/Honeycomb but with LLM-specific fields (tokens, cost, tool calls, prompt diffs).

**claude-tap** is the partial exception: it captures online via proxy but is consumed offline (export HTML, eyeball diffs). It has no analytics layer — it's a glorified trace recorder.

**No tool in this survey does what ATS does**: cold, post-hoc, semantic analysis of completed agent sessions to extract reusable knowledge artifacts (entities, memories, critical steps, failure modes).

---

## 3. Comparison table

| Axis | claude-tap | LangSmith | Langfuse | Helicone | Phoenix | Braintrust | **ATS (this project)** |
|---|---|---|---|---|---|---|---|
| **Approach** | Local HTTP proxy; JSONL on disk | SDK decorator + wrapper objects → cloud | SDK → self-hosted backend | Gateway proxy (Cloudflare Workers) → cloud | OpenTelemetry auto-instrumentation → OTLP | SDK decorator → cloud, eval-centric | **Post-hoc batch ingestion** of native agent log files (Claude Code JSONL, Gemini CLI, Opencode SQLite); local SQLite |
| **Online vs offline** | Online capture, offline view | Online, sync | Online; evals async | Online proxy | Online; evals async | Online; evals async | **Fully offline / batch**. No runtime instrumentation. Reads existing logs. |
| **Unit of analysis** | Single HTTP request; diff between adjacent requests | Run (LLM/chain/tool node in tree) | Span in DAG | Single request | OTel span | Run + experiment variants | **Session → exchange → step**, plus extracted **entities** and **critical steps** (gradient inflections on observer-scored step trajectory) |
| **Features** | Request capture; turn-by-turn diff viewer; portable export | Tracing, datasets, evals, prompt hub | Tracing, prompt mgmt, LLM-judge evals, cost | Gateway, cost tracking, caching, model routing | OTel tracing, evals, experiments | Tracing, datasets, experiments, score regression | Entity extraction (regex + GLiNER + LLM verifier with confidence tiers), explicit-memory induction, frequency-promoted memory, per-step observer scoring (cost vs. progress vectors), critical-step detection w/ failure-mode tagging, hybrid BM25+vector retrieval, MCP serve-back to Claude Code, session-graph walk |
| **Types of insight** | "What changed between turn N and N+1" | Latency / tokens / cost / errors per run; eval scores | Same + cost trends, LLM-judge scores | Cost optimization, latency, model-routing decisions | Span timing, eval scores, OTel-standard metrics | Score deltas, regressions across experiments | **Semantic/structural**: what entities the agent touched, where it got stuck (critical steps), what failure mode (stuck-loop / wrong-tool / no-progress), what reusable knowledge emerged (frequency memories), cross-session links via shared files/commits/PRs |
| **Downstream use cases** | Debugging individual sessions | Eval, dataset curation, debugging, prompt iteration | Eval, cost analytics, prompt iteration | Cost ROI, gateway features (caching, routing) | Eval, debugging, regression tracking | **Eval & experiment tracking** (primary), fine-tune dataset export | **Data curation** (memories surfaced via MCP), **developer context + insights** (what the agent learned across sessions), **anomaly detection** (critical steps / failure-mode tagging), **eval** (Level 0 entity precision; critical-step TP/FP precision ship-gate ≥0.80); ROI tracking not in scope |
| **Storage** | JSONL files | Cloud (LangSmith hosted) | Supabase + ClickHouse + Minio | Supabase + ClickHouse + Minio | Local/self-hosted (Postgres) | Cloud | **Local SQLite + sqlite-vec** (offline-first; runs entirely via Ollama) |
| **Schema standardization** | None (Anthropic-native) | LangSmith proprietary | Langfuse proprietary | Provider-agnostic, proprietary schema | **OpenInference (OTel)** — standardized | Proprietary | ATS-internal; ingestion adapters normalize per-provider log shapes into a common chunk/exchange model |
| **Evaluation loop** | None | Strong (built-in datasets + evals) | Strong (LLM-judge) | Minimal | Strong (experiments) | Strongest (eval-first product) | **Internal eval harness** (`ats eval0`, `critical-steps-precision`) for measuring the analyzer's own precision, not the agent's task performance |
| **Privacy model** | Local-first | Cloud (data leaves machine) | Self-hostable | Cloud-first | Self-hostable | Cloud | **Local-first**, fully offline |

---

## 4. Where ATS is differentiated

1. **Post-hoc, not runtime.** Every other tool requires instrumenting the agent before it runs. ATS reads logs that Claude Code / Gemini CLI / Opencode already write to disk. This means zero integration cost and retroactive analysis of past sessions.
2. **Unit of analysis is the session, not the LLM call.** Other tools optimize for "what did this single call cost / take / return." ATS optimizes for "what did this agent learn / get stuck on / accomplish across an entire trajectory."
3. **Knowledge extraction, not telemetry.** ATS outputs entities, memories, critical steps — durable, queryable artifacts. Other tools output spans, dashboards, and eval scores.
4. **Critical-step detection** (gradient-based inflection finding on observer-scored progress vectors, with failure-mode tagging) has no analogue in the other tools. They surface raw latency/error metrics; ATS surfaces semantically tagged turning points in agent behavior.
5. **Cross-session graph.** ATS links sessions via shared files/commits/PRs, enabling graph-walk over agent history. Other tools treat each trace as independent.

## 5. Patterns worth borrowing

1. **OpenInference / OpenTelemetry semantic conventions** — adopting standard span field names (`llm.input_messages`, `tool.calls`) for our internal exchange model would make ATS interoperable with Phoenix-compatible viewers and any future OTel-based ecosystem.
2. **Turn-by-turn diff view** (claude-tap) — explicitly visualizing "what was added to context between turn N and N+1" would be a strong addition to the labeler/HTML viewer. Particularly useful around critical steps.
3. **Eval-first feedback loop** (Braintrust, LangSmith) — formalizing the pattern of "extracted artifact → score → attach back to source span → track over time" would give ATS a way to measure the *agent's* trajectory quality, not just the analyzer's precision. Currently ATS only measures the latter.
4. **ClickHouse split for analytics** — only relevant at scale, but if ATS ever ingests thousands of sessions, keeping the operational store (SQLite/Postgres) separate from the analytics store (ClickHouse) is the proven pattern.

## 6. The gap ATS fills

Every observability tool in this survey answers **"what is the agent doing right now / what did it just do?"** at the level of API calls.

ATS answers **"what did the agent know, where did it struggle, what's reusable from this session for the next one?"** at the level of trajectories.

These are complementary, not competing. A mature agent stack could use Helicone or LangSmith for cost/latency telemetry *and* ATS for trajectory knowledge extraction.
