"""Home page — session overview with quality signals."""

from __future__ import annotations
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from agent_trace_signals.ui.common import PAGE_CONFIG, get_store, format_workspace

st.set_page_config(**PAGE_CONFIG)
st.title("🧠 Agent Trace Signals")
st.caption("Memory analytics layer for coding agent traces.")

store = get_store()

# ── Intro: what this is + how it works ───────────────────────────────────────
st.markdown(
    "**Agent Trace Signals (ATS)** turns the history of your coding-agent sessions into a "
    "searchable, cross-session memory. It ingests traces from Claude Code, Gemini CLI, and "
    "OpenCode, extracts what happened and what was learned, discovers recurring patterns "
    "automatically, and serves that memory back into your coding agent via MCP — so it stops "
    "re-learning the same lessons every session."
)

_PIPELINE_SVG = """
<svg viewBox="0 0 1400 220" width="1400" height="220" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;">
  <defs>
    <marker id="ats-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="#9ca3af"/>
    </marker>
  </defs>
  <style>
    .ats-box-title { font: bold 24px -apple-system, Segoe UI, sans-serif; fill: #ffffff; }
    .ats-box-sub   { font: 15px -apple-system, Segoe UI, sans-serif; fill: rgba(255,255,255,0.88); }
    .ats-arrow-line { stroke: #9ca3af; stroke-width: 2; marker-end: url(#ats-arrow); }
  </style>
  <!-- connecting arrows -->
  <line x1="220" y1="110" x2="258" y2="110" class="ats-arrow-line"/>
  <line x1="458" y1="110" x2="496" y2="110" class="ats-arrow-line"/>
  <line x1="696" y1="110" x2="734" y2="110" class="ats-arrow-line"/>
  <line x1="934" y1="110" x2="972" y2="110" class="ats-arrow-line"/>
  <line x1="1172" y1="110" x2="1210" y2="110" class="ats-arrow-line"/>
  <!-- 1. Traces -->
  <rect x="10" y="40" width="210" height="140" rx="10" fill="#64748b"/>
  <text x="115" y="92" text-anchor="middle" class="ats-box-title">Traces</text>
  <text x="115" y="120" text-anchor="middle" class="ats-box-sub">Claude Code</text>
  <text x="115" y="139" text-anchor="middle" class="ats-box-sub">Gemini CLI</text>
  <text x="115" y="158" text-anchor="middle" class="ats-box-sub">OpenCode</text>
  <!-- 2. Ingest -->
  <rect x="258" y="40" width="200" height="140" rx="10" fill="#3b82f6"/>
  <text x="358" y="92" text-anchor="middle" class="ats-box-title">Ingest</text>
  <text x="358" y="120" text-anchor="middle" class="ats-box-sub">Combined prompt: 1</text>
  <text x="358" y="139" text-anchor="middle" class="ats-box-sub">LLM call/chunk → summary</text>
  <text x="358" y="158" text-anchor="middle" class="ats-box-sub">+ entities + memories</text>
  <!-- 3. Embed -->
  <rect x="496" y="40" width="200" height="140" rx="10" fill="#8b5cf6"/>
  <text x="596" y="92" text-anchor="middle" class="ats-box-title">Embed</text>
  <text x="596" y="120" text-anchor="middle" class="ats-box-sub">Semantic (content)</text>
  <text x="596" y="139" text-anchor="middle" class="ats-box-sub">+ structural</text>
  <text x="596" y="158" text-anchor="middle" class="ats-box-sub">(tool-call rhythm)</text>
  <!-- 4. Cluster -->
  <rect x="734" y="40" width="200" height="140" rx="10" fill="#10b981"/>
  <text x="834" y="92" text-anchor="middle" class="ats-box-title">Cluster</text>
  <text x="834" y="120" text-anchor="middle" class="ats-box-sub">HDBSCAN: session types +</text>
  <text x="834" y="139" text-anchor="middle" class="ats-box-sub">2-level memory consolidation</text>
  <text x="834" y="158" text-anchor="middle" class="ats-box-sub">(within-session, cross-session)</text>
  <!-- 5. Retrieve -->
  <rect x="972" y="40" width="200" height="140" rx="10" fill="#f59e0b"/>
  <text x="1072" y="92" text-anchor="middle" class="ats-box-title">Retrieve</text>
  <text x="1072" y="120" text-anchor="middle" class="ats-box-sub">Hybrid BM25 +</text>
  <text x="1072" y="139" text-anchor="middle" class="ats-box-sub">semantic search</text>
  <text x="1072" y="158" text-anchor="middle" class="ats-box-sub">(RRF fusion)</text>
  <!-- 6. Serve -->
  <rect x="1210" y="40" width="180" height="140" rx="10" fill="#06b6d4"/>
  <text x="1300" y="92" text-anchor="middle" class="ats-box-title">Serve</text>
  <text x="1300" y="120" text-anchor="middle" class="ats-box-sub">MCP server →</text>
  <text x="1300" y="139" text-anchor="middle" class="ats-box-sub">back into your</text>
  <text x="1300" y="158" text-anchor="middle" class="ats-box-sub">coding agent</text>
</svg>
"""
st.markdown(_PIPELINE_SVG, unsafe_allow_html=True)
st.caption("Runs entirely offline — local SQLite + Ollama, no cloud dependency.")

st.markdown("**What's inside the memory bank**")
_MEMORY_BANK_SVG = """
<svg viewBox="0 0 1400 460" width="1400" height="460" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;">
  <defs>
    <marker id="ats-arrow2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="#9ca3af"/>
    </marker>
  </defs>
  <style>
    .ats-title-sm  { font: bold 18px -apple-system, Segoe UI, sans-serif; fill: #ffffff; }
    .ats-sub-sm    { font: 13px -apple-system, Segoe UI, sans-serif; fill: rgba(255,255,255,0.88); }
    .ats-title-xs  { font: bold 15px -apple-system, Segoe UI, sans-serif; fill: #ffffff; }
    .ats-sub-xs    { font: 11.5px -apple-system, Segoe UI, sans-serif; fill: rgba(255,255,255,0.88); }
    .ats-container-label { font: bold 18px -apple-system, Segoe UI, sans-serif; fill: #d1d5db; letter-spacing: 1px; }
    .ats-layer-label { font: italic 14px -apple-system, Segoe UI, sans-serif; fill: #9ca3af; }
    .ats-arrow-line2 { stroke: #9ca3af; stroke-width: 2; marker-end: url(#ats-arrow2); }
  </style>
  <!-- Traces -> Memory Bank -->
  <line x1="180" y1="220" x2="228" y2="220" class="ats-arrow-line2"/>
  <!-- Memory Bank -> RAG -->
  <line x1="1172" y1="220" x2="1208" y2="220" class="ats-arrow-line2"/>
  <!-- query -> RAG -->
  <line x1="1295" y1="114" x2="1295" y2="148" class="ats-arrow-line2"/>
  <!-- Traces (left) -->
  <rect x="10" y="150" width="170" height="140" rx="10" fill="#64748b"/>
  <text x="95" y="192" text-anchor="middle" class="ats-box-title">Traces</text>
  <text x="95" y="220" text-anchor="middle" class="ats-box-sub">Claude Code</text>
  <text x="95" y="239" text-anchor="middle" class="ats-box-sub">Gemini CLI</text>
  <text x="95" y="258" text-anchor="middle" class="ats-box-sub">OpenCode</text>
  <!-- Memory Bank container -->
  <rect x="230" y="20" width="940" height="400" rx="14" fill="rgba(255,255,255,0.03)" stroke="#6b7280" stroke-width="2" stroke-dasharray="8,5"/>
  <text x="250" y="45" class="ats-container-label">MEMORY BANK</text>
  <text x="250" y="66" class="ats-layer-label">Extraction — one combined LLM call per chunk, every type kept separate</text>
  <!-- Entities (tall, spans both extraction rows) -->
  <rect x="260" y="75" width="150" height="180" rx="8" fill="#0ea5e9"/>
  <text x="335" y="110" text-anchor="middle" class="ats-title-sm">Entities</text>
  <text x="335" y="138" text-anchor="middle" class="ats-sub-sm">files · commits</text>
  <text x="335" y="158" text-anchor="middle" class="ats-sub-sm">PRs · packages</text>
  <text x="335" y="178" text-anchor="middle" class="ats-sub-sm">errors · concepts</text>
  <!-- Memory types row -->
  <rect x="430" y="75" width="140" height="85" rx="8" fill="#3b82f6"/>
  <text x="500" y="113" text-anchor="middle" class="ats-title-sm">Episodic</text>
  <text x="500" y="135" text-anchor="middle" class="ats-sub-sm">what happened</text>
  <rect x="585" y="75" width="140" height="85" rx="8" fill="#10b981"/>
  <text x="655" y="113" text-anchor="middle" class="ats-title-sm">Procedural</text>
  <text x="655" y="135" text-anchor="middle" class="ats-sub-sm">how it was done</text>
  <rect x="740" y="75" width="140" height="85" rx="8" fill="#06b6d4"/>
  <text x="810" y="113" text-anchor="middle" class="ats-title-sm">Preference</text>
  <text x="810" y="135" text-anchor="middle" class="ats-sub-sm">user's stated wishes</text>
  <!-- Pattern types row (strategy/decision merged 2026-07-22 — see design_decisions.md) -->
  <rect x="430" y="170" width="147" height="85" rx="8" fill="#8b5cf6"/>
  <text x="504" y="208" text-anchor="middle" class="ats-title-xs">Strategy</text>
  <text x="504" y="228" text-anchor="middle" class="ats-sub-xs">approach + tradeoffs</text>
  <rect x="585" y="170" width="147" height="85" rx="8" fill="#ef4444"/>
  <text x="659" y="208" text-anchor="middle" class="ats-title-xs">Recovery</text>
  <text x="659" y="228" text-anchor="middle" class="ats-sub-xs">stuck → fixed</text>
  <rect x="740" y="170" width="147" height="85" rx="8" fill="#f97316"/>
  <text x="814" y="208" text-anchor="middle" class="ats-title-xs">Wasteful</text>
  <text x="814" y="228" text-anchor="middle" class="ats-sub-xs">wasted effort</text>
  <text x="250" y="288" class="ats-layer-label">Consolidation — two levels via HDBSCAN clustering: within-session, then cross-session</text>
  <!-- Consolidation row -->
  <rect x="260" y="300" width="395" height="95" rx="8" fill="#a855f7"/>
  <text x="457" y="338" text-anchor="middle" class="ats-box-title" font-size="20">Memory Themes</text>
  <text x="457" y="363" text-anchor="middle" class="ats-box-sub">Within-session repeats +</text>
  <text x="457" y="382" text-anchor="middle" class="ats-box-sub">cross-session recurring insights</text>
  <rect x="675" y="300" width="395" height="95" rx="8" fill="#14b8a6"/>
  <text x="872" y="338" text-anchor="middle" class="ats-box-title" font-size="20">Session Clusters</text>
  <text x="872" y="363" text-anchor="middle" class="ats-box-sub">Interaction-shape types from</text>
  <text x="872" y="382" text-anchor="middle" class="ats-box-sub">structural embeddings</text>
  <!-- RAG (right) -->
  <rect x="1210" y="150" width="170" height="140" rx="10" fill="#6366f1"/>
  <text x="1295" y="192" text-anchor="middle" class="ats-box-title">RAG</text>
  <text x="1295" y="225" text-anchor="middle" class="ats-box-sub">Retrieval +</text>
  <text x="1295" y="248" text-anchor="middle" class="ats-box-sub">answer generation</text>
  <text x="1295" y="105" text-anchor="middle" class="ats-layer-label">user / agent query</text>
</svg>
"""
st.markdown(_MEMORY_BANK_SVG, unsafe_allow_html=True)
st.caption(
    "7 extraction outputs kept as distinct types (not collapsed into one blob), consolidated "
    "into reusable cross-session structure, then retrieved on demand — by you or by the agent itself."
)

st.markdown("**Key technical approaches**")
_INNOVATION_CARDS = [
    ("#3b82f6", "One-call extraction",
     "A single combined-extraction prompt per chunk extracts summary, entities, memories, "
     "and patterns together — cutting LLM calls ~50% vs. 4 separate regex/NER/verifier passes. "
     "Patterns get their own type since entities capture nouns, not behaviors like recovery strategies."),
    ("#f97316", "Balance granularity and signal to noise",
     "Per-exchange scoring and boundary detection produced low precision, high noise — "
     "structural signals in agent traces need semantic pre-filtering to exclude harness-injected "
     "noise before they're usable."),
    ("#ec4899", "Memory and behavioral patterns",
     "After trying several categorization schemes (a stable-fact type, separate decision/strategy "
     "patterns), settled on a leaner taxonomy: <b>less is more</b> (extract sparingly, raise the bar) "
     "and <b>keep it real</b> (only claim what a single chunk can actually verify)."),
    ("#8b5cf6", "Dual embeddings",
     "Semantic embeddings capture <i>what</i> you worked on; separate structural embeddings "
     "(from raw tool-call rhythm) capture <i>how</i> the session unfolded — independent of topic."),
    ("#10b981", "Emergent pattern discovery",
     "HDBSCAN clustering — not a fixed taxonomy — groups sessions by interaction shape. Memory "
     "consolidation runs in two levels: within a single session first (no cross-session claim), "
     "then across sessions (requires the same insight recurring in ≥2 distinct sessions) — "
     "surfacing patterns nobody asked to see, without overstating corroboration that isn't there."),
    ("#06b6d4", "Multi-signal session graph",
     "Sessions connect via shared workspace, shared file/commit/PR entities, <i>or</i> shared "
     "consolidated memory — surfacing cross-project relationships pure semantic similarity would miss."),
]
card_cols = st.columns(6)
for col, (color, title, desc) in zip(card_cols, _INNOVATION_CARDS):
    col.markdown(
        f"""<div style="border-top:3px solid {color}; padding:10px 4px 0 4px;">
        <div style="font-weight:600; margin-bottom:4px;">{title}</div>
        <div style="font-size:13px; opacity:0.85; line-height:1.4;">{desc}</div>
        </div>""",
        unsafe_allow_html=True,
    )

st.markdown("**Built with**")
_TECH_STACK = [
    ("#64748b", "Claude Code / Gemini CLI / OpenCode", "data source — session transcripts"),
    ("#8b5cf6", "Ollama + nomic-embed-text", "local embeddings"),
    ("#3b82f6", "Ollama + gemma4:e4b", "extraction LLM backbone"),
    ("#14b8a6", "SQLite + sqlite-vec", "storage & vector search (vec0)"),
    ("#f59e0b", "BM25 (FTS5) + RRF", "hybrid retrieval fusion"),
    ("#10b981", "HDBSCAN", "similarity clustering"),
    ("#06b6d4", "Streamlit + Plotly", "UI"),
]
_chips = "".join(
    f'<div style="border:1px solid {color}55; border-left:3px solid {color}; '
    f'border-radius:6px; padding:6px 12px; font-size:13px;">'
    f'<span style="font-weight:600;">{name}</span>'
    f'<span style="opacity:0.7;"> — {role}</span></div>'
    for color, name, role in _TECH_STACK
)
st.markdown(
    f'<div style="display:flex; flex-wrap:wrap; gap:8px; margin-top:4px;">{_chips}</div>',
    unsafe_allow_html=True,
)

st.divider()

# ── Session quality table ────────────────────────────────────────────────────
st.header("Sessions")

rows = store.conn.execute(
    """SELECT s.id, s.source_plugin, s.workspace_id, s.session_timestamp, s.chunk_count,
              LENGTH(COALESCE(s.session_summary,'')) > 0 AS has_summary,
              sm.dominant_model, sm.permission_mode,
              sm.total_output_tokens, sm.user_turn_count, sm.tool_call_count
       FROM sessions s
       LEFT JOIN session_metadata sm ON sm.session_id = s.id
       ORDER BY s.session_timestamp DESC"""
).fetchall()

if not rows:
    st.warning("No sessions ingested yet. Run `ats ingest` first.")
    st.stop()

total_sessions = len(rows)
total_memories = store.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
total_chunks = store.conn.execute("SELECT SUM(chunk_count) FROM sessions").fetchone()[0] or 0
c1, c2, c3 = st.columns(3)
c1.metric("Sessions", total_sessions)
c2.metric("Chunks", total_chunks)
c3.metric("Memories", total_memories)

mem_rows = store.conn.execute(
    """SELECT ms.session_id, COUNT(DISTINCT m.id)
       FROM memory_sources ms JOIN memories m ON m.id = ms.memory_id
       GROUP BY ms.session_id"""
).fetchall()
mem_counts = {r[0]: r[1] for r in mem_rows}

recovery_rows = store.conn.execute(
    """SELECT ms.session_id, COUNT(DISTINCT m.id)
       FROM memory_sources ms JOIN memories m ON m.id = ms.memory_id
       WHERE m.memory_type IN ('pattern_recovery', 'pattern_inefficiency')
       GROUP BY ms.session_id"""
).fetchall()
recovery_counts = {r[0]: r[1] for r in recovery_rows}

data = []
for r in rows:
    sid = r[0]
    data.append({
        "ID": sid[:12],
        "Plugin": r[1] or "",
        "Workspace": format_workspace(r[2] or "")[-30:],
        "Timestamp": (r[3] or "")[:19],
        "Chunks": r[4] or 0,
        "Memories": mem_counts.get(sid, 0),
        "Recovery": recovery_counts.get(sid, 0),
        "Summary": "✓" if r[5] else "—",
        "Model": r[6] or "—",
        "PermMode": r[7] or "—",
        "OutTok": r[8] or None,
        "Turns": r[9] or None,
        "ToolCalls": r[10] or None,
        "_id": sid,
    })

df = pd.DataFrame(data)
display_df = df.drop(columns=["_id"])

table_event = st.dataframe(
    display_df,
    use_container_width=True,
    column_config={
        "Recovery": st.column_config.NumberColumn(label="Recovery+Ineff", help="pattern_recovery + pattern_inefficiency memory count — higher = more struggle"),
        "OutTok": st.column_config.NumberColumn(label="Out Tokens", format="%d"),
        "Turns": st.column_config.NumberColumn(label="Turns"),
        "ToolCalls": st.column_config.NumberColumn(label="Tool Calls"),
    },
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    key="session_table",
)

# ── Session drill-down ───────────────────────────────────────────────────────
st.header("Session detail")
session_options = {f"{r['Timestamp']} | {r['Plugin']} | {r['Workspace']} | {r['ID']}": r["_id"]
                   for r in data}
option_labels = list(session_options.keys())
selected_rows = table_event.selection.rows if table_event and table_event.selection else []
default_index = selected_rows[0] if selected_rows else 0
selected_label = st.selectbox("Select session", option_labels, index=default_index)
sid = session_options[selected_label]

# ── Session metrics strip ─────────────────────────────────────────────────────
detail_meta = store.conn.execute(
    """SELECT sm.dominant_model, sm.user_turn_count, sm.tool_call_count,
              sm.total_input_tokens, sm.total_output_tokens,
              sm.total_cache_read_tokens, sm.total_cache_creation_tokens,
              sm.duration_ms
       FROM session_metadata sm WHERE sm.session_id=?""",
    (sid,),
).fetchone()

detail_mem_types = store.conn.execute(
    """SELECT m.memory_type, COUNT(*) FROM memories m
       JOIN memory_sources ms ON ms.memory_id = m.id
       WHERE ms.session_id=? GROUP BY m.memory_type""",
    (sid,),
).fetchall()
mem_by_type = {r[0]: r[1] for r in detail_mem_types}
total_mems = sum(mem_by_type.values())
recovery_n = mem_by_type.get("pattern_recovery", 0) + mem_by_type.get("pattern_inefficiency", 0)
recovery_rate = round(recovery_n / total_mems * 100, 1) if total_mems > 0 else 0.0

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total memories", total_mems)
m2.metric("Recovery patterns", recovery_n)
m3.metric("Recovery rate", f"{recovery_rate}%", help="(pattern_recovery + pattern_inefficiency) / total memories")
if detail_meta:
    turns = detail_meta[1] or 0
    tool_calls = detail_meta[2] or 0
    inp = detail_meta[3] or 0
    cache_read = detail_meta[5] or 0
    cache_create = detail_meta[6] or 0
    cache_denom = inp + cache_read + cache_create
    m4.metric("Tool calls / turn", f"{tool_calls/turns:.2f}" if turns > 0 else "—")
    m5.metric("Cache hit %", f"{cache_read/cache_denom*100:.1f}%" if cache_denom > 0 else "—")
else:
    m4.metric("Tool calls / turn", "—")
    m5.metric("Cache hit %", "—")

# ── Memory type breakdown + chunk density ─────────────────────────────────────
if mem_by_type:
    _MEM_ORDER = ["episodic", "procedural", "preference",
                  "pattern_strategy", "pattern_recovery", "pattern_inefficiency", "cluster"]
    ordered_types = [t for t in _MEM_ORDER if t in mem_by_type]
    ordered_types += sorted(k for k in mem_by_type if k not in _MEM_ORDER)

    col_memtype, col_density = st.columns(2)

    with col_memtype:
        st.markdown("**Memory type breakdown**")
        st.caption("Click a bar to inspect its memories")
        fig_mt = go.Figure(go.Bar(
            x=ordered_types,
            y=[mem_by_type[t] for t in ordered_types],
            marker_color=["#ef4444" if t in ("pattern_recovery", "pattern_inefficiency") else "#3b82f6" for t in ordered_types],
        ))
        fig_mt.update_layout(height=220, margin=dict(t=10, b=10), yaxis_title="Count")
        mt_event = st.plotly_chart(
            fig_mt, use_container_width=True,
            on_select="rerun", selection_mode="points", key="memtype_chart",
        )

    with col_density:
        st.markdown("**Memories per chunk**")
        chunk_mem_rows = store.conn.execute(
            """SELECT r.chunk_index, COUNT(DISTINCT m.id)
               FROM records r
               JOIN memory_sources ms ON ms.record_id = r.id
               JOIN memories m ON m.id = ms.memory_id
               WHERE r.session_id=?
               GROUP BY r.chunk_index ORDER BY r.chunk_index""",
            (sid,),
        ).fetchall()
        if chunk_mem_rows:
            cdf = pd.DataFrame(chunk_mem_rows, columns=["chunk", "memories"])
            fig_cd = go.Figure(go.Bar(
                x=cdf["chunk"], y=cdf["memories"],
                marker_color="#3b82f6",
            ))
            fig_cd.update_layout(height=220, margin=dict(t=10, b=10),
                                 xaxis_title="Chunk", yaxis_title="Memories")
            st.plotly_chart(fig_cd, use_container_width=True)
        else:
            st.caption("No per-chunk memory attribution available.")

    # ── Memory inspector: clicking a bar above shows that type's memories ─────
    clicked_points = mt_event.selection.points if mt_event and mt_event.selection else []
    if clicked_points:
        clicked_type = clicked_points[0]["x"]
        st.markdown(f"**Memories — `{clicked_type}`**")
        type_mems = store.conn.execute(
            """SELECT m.id, m.content, m.evidence_count, m.first_observed, m.last_observed, ms.relevance_score
               FROM memories m JOIN memory_sources ms ON ms.memory_id = m.id
               WHERE ms.session_id=? AND m.memory_type=?
               ORDER BY ms.relevance_score DESC""",
            (sid, clicked_type),
        ).fetchall()
        if not type_mems:
            st.caption("No memories of this type found for this session — try clicking the bar again.")
        else:
            for mem_id, content, evidence_count, first_obs, last_obs, relevance in type_mems:
                preview = (content[:100] + "…") if len(content) > 100 else content
                with st.expander(preview):
                    st.markdown(content)
                    st.caption(
                        f"evidence_count={evidence_count} · relevance={relevance:.2f} · "
                        f"first_observed={first_obs} · last_observed={last_obs} · id={mem_id[:12]}"
                    )

tab_entities, tab_related, tab_structural = st.tabs(["Entities", "Related Sessions", "Structural"])

with tab_entities:
    ents = store.conn.execute(
        """SELECT e.canonical_name, e.entity_type, e.confidence, e.degree_count,
                  COUNT(o.id) as occ_count
           FROM entities e JOIN occurrences o ON o.entity_id = e.id
           WHERE o.session_id = ?
           GROUP BY e.id ORDER BY occ_count DESC""",
        (sid,)
    ).fetchall()
    if not ents:
        st.info("No entities found.")
    else:
        edf = pd.DataFrame(ents, columns=["Name", "Type", "Confidence", "Degree", "Occurrences"])
        st.dataframe(edf, use_container_width=True, hide_index=True)

with tab_related:
    from collections import defaultdict
    # LEFT JOIN entities and memories: "workspace" edges have via_entity_id=''
    # (no matching entity row), "shared_memory" edges have via_entity_id pointing
    # at a memories.id, not entities.id, "structural_similarity" edges also have
    # via_entity_id='' (no pivot entity — it's a direct embedding-similarity
    # score, carried in `weight`) — an INNER JOIN would silently drop rows for
    # whichever edge type doesn't match.
    rel_edges = store.conn.execute(
        """SELECT sge.target_session_id, s.source_plugin, s.workspace_id, s.session_timestamp,
                  sge.edge_type, e.canonical_name, e.entity_type, m2.content, sge.weight
           FROM session_graph_edges sge
           JOIN sessions s ON s.id = sge.target_session_id
           LEFT JOIN entities e ON e.id = sge.via_entity_id
           LEFT JOIN memories m2 ON m2.id = sge.via_entity_id
           WHERE sge.source_session_id = ?
           ORDER BY s.session_timestamp DESC""",
        (sid,),
    ).fetchall()
    if not rel_edges:
        st.info(
            "No connections found. Edges are created from a shared project/workspace, "
            "a shared file/commit/PR entity, a shared consolidated memory pattern, or "
            "structural similarity (similar tool-call rhythm, from `ats embed`)."
        )
    else:
        session_reasons: dict[str, list] = defaultdict(list)
        session_meta_rel: dict[str, tuple] = {}
        for target_id, plugin, workspace, ts, edge_type, entity_name, entity_type, mem_content, weight in rel_edges:
            if edge_type == "workspace":
                reason = "same project"
            elif edge_type == "shared_memory":
                preview = (mem_content[:60] + "…") if mem_content and len(mem_content) > 60 else (mem_content or "")
                reason = f"shared pattern: {preview}"
            elif edge_type == "structural_similarity":
                reason = f"similar interaction shape ({weight:.2f} cosine)"
            elif entity_name:
                reason = f"{entity_name} ({entity_type})"
            else:
                reason = edge_type
            if reason not in session_reasons[target_id]:
                session_reasons[target_id].append(reason)
            session_meta_rel[target_id] = (plugin, workspace, ts)
        rel_data = []
        for target_id, (plugin, workspace, ts) in session_meta_rel.items():
            reasons = session_reasons[target_id]
            rel_data.append({
                "Session": target_id[:12],
                "Plugin": plugin or "",
                "Workspace": format_workspace(workspace or "")[-30:],
                "Date": (ts or "")[:10],
                "Why connected": ", ".join(reasons[:5]) + (f" +{len(reasons)-5} more" if len(reasons) > 5 else ""),
                "# Reasons": len(reasons),
            })
        rel_df = pd.DataFrame(rel_data).sort_values("# Reasons", ascending=False)
        st.dataframe(
            rel_df,
            use_container_width=True,
            hide_index=True,
            column_config={"Why connected": st.column_config.TextColumn(width="large")},
        )
        st.caption(
            f"{len(rel_data)} connected session(s) — 'same project' means matching workspace, "
            "'shared pattern' means both sessions fed the same consolidated cluster memory, "
            "'similar interaction shape' means top-8 nearest neighbours by structural embedding "
            "cosine similarity, others are shared file/commit/PR entities."
        )

with tab_structural:
    session_type = store.conn.execute(
        "SELECT session_type FROM sessions WHERE id=?", (sid,)
    ).fetchone()
    session_type = session_type[0] if session_type else None
    if not session_type:
        st.info("No structural cluster assigned yet. Run `ats cluster-sessions` to compute this.")
    else:
        n_members, avg_chunks = store.conn.execute(
            "SELECT COUNT(*), AVG(chunk_count) FROM sessions WHERE session_type=?",
            (session_type,),
        ).fetchone()
        label_row = store.conn.execute(
            "SELECT label, description FROM session_type_labels WHERE session_type=?",
            (session_type,),
        ).fetchone()
        if label_row:
            label, description = label_row
        else:
            label, description = session_type, "Not labeled yet — run `ats cluster-sessions` to generate a label."
        st.markdown(f"**Structural cluster:** {label} — {n_members} session(s), avg {avg_chunks:.1f} chunks")
        st.caption(description)
        st.caption(
            "Clusters group sessions by raw interaction shape (tool-call rhythm, turn patterns), "
            "not topic — see the Related Sessions tab for topic/entity-based connections."
        )

    import struct
    import math

    def _decode_emb(blob: bytes) -> list[float]:
        n = len(blob) // 4
        return list(struct.unpack(f"{n}f", blob))

    def _cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0

    own_emb_row = store.conn.execute(
        "SELECT embedding FROM session_structural_embeddings WHERE session_id=?", (sid,)
    ).fetchone()
    if not own_emb_row:
        st.caption("No structural embedding for this session yet — run `ats embed` to compute it.")
    else:
        own_vec = _decode_emb(own_emb_row[0])
        other_rows = store.conn.execute(
            """SELECT se.session_id, se.embedding, s.source_plugin, s.workspace_id,
                      s.session_timestamp, s.chunk_count
               FROM session_structural_embeddings se JOIN sessions s ON s.id = se.session_id
               WHERE se.session_id != ?""",
            (sid,),
        ).fetchall()
        sims = sorted(
            (
                (_cosine_sim(own_vec, _decode_emb(blob)), other_id, plugin, ws, ts, cc)
                for other_id, blob, plugin, ws, ts, cc in other_rows
            ),
            key=lambda x: x[0], reverse=True,
        )[:8]
        if sims:
            st.markdown("**Structurally similar sessions**")
            st.caption(
                "Ranked by cosine similarity of structural embeddings (raw head+tail text) — "
                "similar interaction shape, not necessarily similar topic."
            )
            sim_data = [
                {
                    "Similarity": f"{s:.3f}",
                    "Session": oid[:12],
                    "Plugin": plugin or "",
                    "Workspace": format_workspace(ws or "")[-25:],
                    "Date": (ts or "")[:10],
                    "Chunks": cc,
                }
                for s, oid, plugin, ws, ts, cc in sims
            ]
            st.dataframe(pd.DataFrame(sim_data), use_container_width=True, hide_index=True)

