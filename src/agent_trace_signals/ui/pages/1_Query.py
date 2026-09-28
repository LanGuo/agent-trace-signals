"""Query page — recall / chunk-search / session-search with optional RAG answer generation."""

from __future__ import annotations
import itertools
import streamlit as st
import pandas as pd
from agent_trace_signals.ui.common import PAGE_CONFIG, get_store, get_engine

st.set_page_config(**PAGE_CONFIG)
st.title("🔍 Query")

store = get_store()

_EXAMPLE_QUERIES = [
    "what did we work on in Isaac's chess app?",
    "how did we solve llm parallelism problem in SLR pipeline?",
    "what are the hitl gates in the SLR pipeline",
    "what are some agent trace scoring we experimented with in agent-trace-signals?",
    "what does the veritract package do?",
]


def _apply_example_query() -> None:
    choice = st.session_state.get("example_query_select")
    if choice and choice != "— pick an example —":
        st.session_state["query_input"] = choice


st.selectbox(
    "Try an example question",
    ["— pick an example —"] + _EXAMPLE_QUERIES,
    key="example_query_select",
    on_change=_apply_example_query,
)

col_left, col_right = st.columns([3, 1])
with col_left:
    query = st.text_input("Query", placeholder="e.g. what did I work on in SLR-gemma4?", key="query_input")
with col_right:
    search_type = st.selectbox("Search type", ["recall", "chunk-search", "session-search"])

col_method, col_k, col_graph, col_filter = st.columns([2, 1, 1, 2])
with col_method:
    method = st.radio("Method", ["hybrid", "semantic", "lexical"], horizontal=True)
with col_k:
    top_k = st.number_input("Top-k", min_value=1, max_value=50, value=15)
with col_graph:
    include_graph = st.checkbox(
        "Expand via graph",
        value=False,
        help="After the top hits, pull in one hop of graph-connected results too: for recall, "
             "memories from sessions linked via session_graph_edges; for chunk-search, other "
             "chunks (any session) mentioning the same entity; for session-search, neighbouring "
             "sessions via any edge type. Expanded results score lower than a direct hit — they "
             "can't outrank one, only add cross-session context you'd otherwise miss.",
    )
with col_filter:
    if search_type == "recall":
        memory_type = st.selectbox(
            "Memory type",
            [
                "(all)",
                "episodic",
                "procedural",
                "preference",
                "pattern_strategy",
                "pattern_recovery",
                "pattern_inefficiency",
                "cluster",
            ],
        )
        memory_type = None if memory_type == "(all)" else memory_type
        status_filter = st.selectbox(
            "Status (episodic only)", ["(all)", "resolved", "open", "reverted"],
            help="Filters results client-side after retrieval — narrows what's shown, doesn't "
                 "change ranking. Non-episodic memories have no status and are dropped by any "
                 "filter other than (all).",
        )
        status_filter = None if status_filter == "(all)" else status_filter
        session_id = None
    elif search_type == "chunk-search":
        sessions = store.conn.execute(
            "SELECT id, source_plugin, workspace_id, session_timestamp FROM sessions ORDER BY session_timestamp DESC"
        ).fetchall()
        sess_opts = {"(all sessions)": None} | {
            f"{r[3][:10]} | {r[1]} | {(r[2] or '').split('/')[-1][-20:]} | {r[0][:8]}": r[0]
            for r in sessions
        }
        sess_label = st.selectbox("Session", list(sess_opts.keys()))
        session_id = sess_opts[sess_label]
        memory_type = None
        status_filter = None
    else:
        session_id = None
        memory_type = None
        status_filter = None

col_search, col_answer = st.columns([1, 1])
with col_search:
    run_search = st.button("Search", type="primary", disabled=not query)
with col_answer:
    run_answer = st.button("Generate Answer", type="secondary", disabled=not query)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_context(search_type: str, results: list) -> str:
    """Build LLM context string from search results."""
    parts = []
    if search_type == "recall":
        for i, m in enumerate(results, 1):
            parts.append(f"[{i}] ({m.get('memory_type','')}) {m['content']}")
    elif search_type == "chunk-search":
        for i, r in enumerate(results, 1):
            summary = r.get("chunk_summary", "")
            raw = r.get("chunk_text", "")
            if summary:
                parts.append(f"[{i}] Summary: {summary}\nFull text: {raw[:800]}")
            else:
                parts.append(f"[{i}] {raw[:1200]}")
    else:  # session-search
        for i, r in enumerate(results, 1):
            summary = r.get("session_summary", "")
            ws = r.get("workspace_id", "")
            parts.append(f"[{i}] Workspace: {ws}\n{summary[:400]}")
    return "\n\n".join(parts)


@st.cache_resource
def get_provider():
    from agent_trace_signals.config import Config
    from agent_trace_signals.providers.ollama import OllamaProvider
    return OllamaProvider(Config().models)


# Keeps the model resident between queries so a normal think/type gap between
# searches doesn't pay Ollama's idle-unload reload cost (measured ~3s for this
# model) on every single answer — see design_decisions.md for the measurement.
_QUERY_MODEL_KEEP_ALIVE = "30m"


def generate_answer_stream(query: str, context: str, model: str, max_tokens: int):
    """Yields answer text incrementally for st.write_stream."""
    provider = get_provider()
    prompt = f"""Answer the following question based only on the retrieved context below.
Be concise and specific. If the context doesn't contain enough information, say so.

Question: {query}

Context:
{context}

Answer:"""
    try:
        yield from provider.complete_text_stream(
            prompt, model=model, max_tokens=max_tokens, keep_alive=_QUERY_MODEL_KEEP_ALIVE,
        )
    except Exception as e:
        yield f"Error generating answer: {e}"


# ── Search ────────────────────────────────────────────────────────────────────

results = None

if (run_search or run_answer) and query:
    engine = get_engine()

    if search_type == "recall":
        with st.spinner("Recalling…"):
            results = engine.recall(query, memory_type=memory_type, top_k=top_k, method=method, include_graph=include_graph)
        if status_filter:
            results = [m for m in results if m.get("status") == status_filter]
    elif search_type == "chunk-search":
        with st.spinner("Searching chunks…"):
            results = engine.chunk_search(query, session_id=session_id, top_k=top_k, method=method, include_graph=include_graph)
    else:
        with st.spinner("Searching sessions…"):
            results = engine.session_search(query, top_k=top_k, method=method, include_graph=include_graph)

# ── Generate Answer ───────────────────────────────────────────────────────────

if run_answer and results:
    from agent_trace_signals.config import Config
    models_cfg = Config().models
    model = models_cfg.query_answer_model
    context = _build_context(search_type, results)
    st.subheader("Answer")
    stream = generate_answer_stream(query, context, model, models_cfg.query_answer_max_tokens)
    # Block on the first chunk inside the spinner — covers connection/cold-load/prompt-eval
    # time (previously invisible in one long blank wait) — then hand off to write_stream so
    # the spinner disappears right as real token-by-token output actually starts.
    with st.spinner(f"Generating answer with `{model}`…"):
        first_chunk = next(stream, "")
    st.write_stream(itertools.chain([first_chunk], stream))
    st.divider()

# ── Display results ───────────────────────────────────────────────────────────

if results is not None:
    if not results:
        st.info("No results.")
    elif search_type == "recall":
        rows = []
        for m in results:
            rows.append({
                "Score": f"{m['rrf_score']:.4f}",
                "Type": m.get("memory_type", ""),
                "Status": m.get("status") or "",
                "Method": m.get("extraction_method", ""),
                "Content": m["content"],
                "ID": m["id"][:8],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                     column_config={"Content": st.column_config.TextColumn(width="large")})

    elif search_type == "chunk-search":
        for r in results:
            with st.expander(f"Score {r['rrf_score']:.4f} | Session {r['session_id'][:8]} | Chunk {r['chunk_index']}"):
                if r.get("chunk_summary"):
                    st.markdown(r["chunk_summary"])
                    with st.expander("Raw chunk text", expanded=False):
                        st.text(r["chunk_text"])
                else:
                    st.text(r["chunk_text"])

    else:  # session-search
        rows = []
        for r in results:
            summary = r.get("session_summary") or ""
            rows.append({
                "Score": f"{r['score']:.4f}",
                "ID": r["id"][:12],
                "Plugin": r.get("source_plugin", ""),
                "Timestamp": (r.get("session_timestamp") or "")[:19],
                "Chunks": r.get("chunk_count", 0),
                "Summary": summary[:200] + ("…" if len(summary) > 200 else ""),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                     column_config={"Summary": st.column_config.TextColumn(width="large")})
