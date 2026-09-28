"""Exploratory analysis over memories — corpus overview, composition by session type,
cluster themes, embedding UMAP, plus filter/browse/inspect for individual memories.

Split from the old combined "Explore" page: this page is everything memory-content-scoped
(what the memories say, how they cluster, how they're typed). Session-structure-scoped
analytics (metrics, connections between sessions) moved to Explore Sessions.
"""

from __future__ import annotations
from collections import Counter
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from agent_trace_signals.ui.common import PAGE_CONFIG, get_store, format_workspace

st.set_page_config(**PAGE_CONFIG)
st.title("💾 Explore Memories")

store = get_store()

total_memories = store.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
if total_memories == 0:
    st.warning("No memories extracted yet. Run `ats ingest` first.")
    st.stop()

# ── Corpus overview ──────────────────────────────────────────────────────────
n_sessions_with_mem = store.conn.execute(
    "SELECT COUNT(DISTINCT session_id) FROM memory_sources"
).fetchone()[0]
n_workspaces = store.conn.execute(
    "SELECT COUNT(DISTINCT s.workspace_id) FROM sessions s "
    "JOIN memory_sources ms ON ms.session_id = s.id"
).fetchone()[0]
type_counts = dict(store.conn.execute(
    "SELECT memory_type, COUNT(*) FROM memories GROUP BY memory_type"
).fetchall())
recovery_n = type_counts.get("pattern_recovery", 0) + type_counts.get("pattern_inefficiency", 0)
recovery_rate = round(recovery_n / total_memories * 100, 1) if total_memories else 0.0

n_open_episodic = store.conn.execute(
    "SELECT COUNT(*) FROM memories WHERE memory_type='episodic' AND status='open'"
).fetchone()[0]

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total memories", total_memories)
m2.metric("Sessions with memories", n_sessions_with_mem)
m3.metric("Workspaces", n_workspaces)
m4.metric(
    "Recovery rate", f"{recovery_rate}%",
    help="(pattern_recovery + pattern_inefficiency) / total memories — higher = more struggle captured",
)
m5.metric(
    "Open episodic", n_open_episodic,
    help="episodic memories with status='open' — still being decided/discussed as of extraction, "
         "no resolution captured yet. Filter for these on the Query page's recall search.",
)

_MEM_ORDER = [
    "episodic", "procedural", "preference",
    "pattern_strategy", "pattern_recovery", "pattern_inefficiency", "cluster",
]
ordered_types = [t for t in _MEM_ORDER if t in type_counts]
ordered_types += sorted(k for k in type_counts if k not in _MEM_ORDER)
# "cluster" is a derived/meta category (consolidated across sessions), not a peer
# of the 7 base extraction types — it gets its own chart below instead of sitting
# in this breakdown, and its evidence_count (distinct source sessions) is a
# meaningful ranking axis unlike the base types (see caption note below).
chart_types = [t for t in ordered_types if t != "cluster"]


def _type_color(t: str) -> str:
    if t in ("pattern_recovery", "pattern_inefficiency"):
        return "#ef4444"
    if t == "cluster":
        return "#a855f7"
    return "#3b82f6"


# Distinct per-type palette for the cluster-composition chart, where all 7 base
# types can appear stacked in the same bar — _type_color()'s red/blue/purple
# scheme collapses 5 of the 7 types to the same blue, indistinguishable in a
# stack. Not reused for the simple type-breakdown chart above, where the
# blue-vs-red split (healthy vs. struggle) is the intentional, meaningful signal.
_COMPOSITION_COLORS = {
    "episodic": "#3b82f6",
    "procedural": "#10b981",
    "preference": "#06b6d4",
    "pattern_strategy": "#8b5cf6",
    "pattern_recovery": "#ef4444",
    "pattern_inefficiency": "#f97316",
}


def _short_theme(content: str, max_len: int = 60) -> str:
    """Truncate at a word boundary rather than mid-word, for chart axis labels."""
    if len(content) <= max_len:
        return content
    truncated = content[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len * 0.6:
        truncated = truncated[:last_space]
    return truncated + "…"


col_type, col_time = st.columns(2)

with col_type:
    st.markdown("**Memory type breakdown**")
    st.caption("Click a bar to preview examples (excludes `cluster` — see below)")
    fig_type = go.Figure(go.Bar(
        x=chart_types,
        y=[type_counts[t] for t in chart_types],
        marker_color=[_type_color(t) for t in chart_types],
    ))
    fig_type.update_layout(height=260, margin=dict(t=10, b=10), yaxis_title="Count")
    type_event = st.plotly_chart(
        fig_type, use_container_width=True,
        on_select="rerun", selection_mode="points", key="memtype_overview_chart",
    )

with col_time:
    st.markdown("**Memories over time**")
    st.caption("By week, from when the underlying work happened")
    obs_rows = store.conn.execute(
        "SELECT first_observed, memory_type FROM memories "
        "WHERE first_observed IS NOT NULL AND first_observed != ''"
    ).fetchall()
    odf = pd.DataFrame(obs_rows, columns=["first_observed", "memory_type"])
    odf["date"] = pd.to_datetime(odf["first_observed"], format="ISO8601", utc=True, errors="coerce")
    odf = odf.dropna(subset=["date"])
    if not odf.empty:
        odf["bucket"] = odf["memory_type"].isin(["pattern_recovery", "pattern_inefficiency"])
        odf["week"] = odf["date"].dt.to_period("W").dt.start_time
        weekly = odf.groupby(["week", "bucket"]).size().unstack(fill_value=0)
        fig_time = go.Figure()
        if False in weekly.columns:
            fig_time.add_trace(go.Bar(x=weekly.index, y=weekly[False], name="Other", marker_color="#3b82f6"))
        if True in weekly.columns:
            fig_time.add_trace(go.Bar(x=weekly.index, y=weekly[True], name="Recovery/Inefficiency", marker_color="#ef4444"))
        fig_time.update_layout(
            height=260, margin=dict(t=10, b=10), barmode="stack",
            yaxis_title="Count", legend=dict(orientation="h", y=1.18),
        )
        st.plotly_chart(fig_time, use_container_width=True)
    else:
        st.caption("No timestamped memories available.")

clicked_pts = type_event.selection.points if type_event and type_event.selection else []
if clicked_pts:
    clicked_type = clicked_pts[0]["x"]
    st.markdown(f"**Preview — `{clicked_type}`** (5 random examples)")
    st.caption(
        "Random, not ranked — every memory of this type has evidence_count=1 before "
        "clustering, so any ordering (e.g. by evidence) would be arbitrary, not meaningful."
    )
    preview_rows = store.conn.execute(
        "SELECT content, status FROM memories WHERE memory_type=? ORDER BY RANDOM() LIMIT 5",
        (clicked_type,),
    ).fetchall()
    for content, status in preview_rows:
        prefix = f"`{status}` " if status else ""
        st.caption(prefix + content)

st.divider()

# ── Memory yield by structural session type ────────────────────────────────
# Moved here from the old combined "Explore" page — this is fundamentally a
# memory-content question (how many memories does a session type actually
# produce), even though it's grouped by session type.
st.markdown("**Memory yield by structural session type**")
st.caption(
    "Memories produced per chunk, by structural session type. Box = median/quartiles "
    "across sessions in that cluster; points = individual sessions."
)

@st.cache_data(ttl=60)
def load_memory_yield(_conn_id):
    rows = store.conn.execute("""
        SELECT s.id, s.session_type, s.chunk_count, m.memory_type, COUNT(*) as cnt
        FROM memory_sources ms JOIN memories m ON m.id = ms.memory_id
        JOIN sessions s ON s.id = ms.session_id
        WHERE m.memory_type != 'cluster'
        GROUP BY s.id, m.memory_type
    """).fetchall()
    label_map = {r[0]: r[1] for r in store.conn.execute("SELECT session_type, label FROM session_type_labels").fetchall()}
    df = pd.DataFrame(rows, columns=["session_id", "session_type", "chunks", "memory_type", "count"])
    df["session_type_label"] = df["session_type"].map(label_map).fillna(df["session_type"]).fillna("unlabeled")
    df["count_per_chunk"] = df["count"] / df["chunks"].replace(0, float("nan"))
    return df

yield_df = load_memory_yield(id(store.conn))
if yield_df.empty:
    st.info("No memory data available yet.")
else:
    overall = yield_df.groupby(["session_id", "session_type_label", "chunks"])["count"].sum().reset_index()
    overall["count_per_chunk"] = overall["count"] / overall["chunks"].replace(0, float("nan"))
    fig_yield = px.box(
        overall, x="session_type_label", y="count_per_chunk", points="all",
        labels={"session_type_label": "Session type", "count_per_chunk": "Memories / chunk"},
        height=380,
    )
    fig_yield.update_layout(margin=dict(t=10, b=80), xaxis_tickangle=-30)
    st.plotly_chart(fig_yield, use_container_width=True)

    with st.expander("Split by memory type"):
        fig_facet = px.box(
            yield_df, x="session_type_label", y="count_per_chunk", facet_col="memory_type",
            facet_col_wrap=4, points=False,
            labels={"session_type_label": "", "count_per_chunk": "Mem/chunk"},
            height=520,
        )
        fig_facet.update_xaxes(tickangle=-45)
        fig_facet.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
        st.plotly_chart(fig_facet, use_container_width=True)

st.divider()

st.markdown("**Cluster themes**")
st.caption(
    "Consolidation runs in two levels (`ats cluster-memories --level`): **cross-session** "
    "clusters require the same insight to recur in ≥2 distinct sessions before minting — "
    "`evidence_count` is the real number of distinct sessions. **Within-session** clusters "
    "consolidate repeated content inside a single session and never claim cross-session "
    "corroboration — `evidence_count` there is just the member count, all from one session. "
    "Each bar's color segments show which original memory types (episodic, procedural, etc.) "
    "fed that pattern."
)
_col_level, _col_superseded = st.columns([3, 1])
with _col_level:
    _level_choice = st.radio(
        "Show", ["Cross-session", "Within-session", "Both"], horizontal=True, index=0,
        key="cluster_theme_level",
    )
with _col_superseded:
    _show_superseded = st.checkbox(
        "Include superseded", value=False,
        help="A cluster memory is marked superseded when a later `ats cluster-memories` run "
             "absorbs most of its members into a new, more complete cluster — the old one is kept "
             "(not deleted) but excluded from search/recall. Off by default here too, so this view "
             "matches what retrieval actually returns.",
    )
_level_filter = {
    "Cross-session": "extraction_method = 'cluster'",
    "Within-session": "extraction_method = 'cluster_within_session'",
    "Both": "extraction_method IN ('cluster','cluster_within_session')",
}[_level_choice]
_superseded_filter = "" if _show_superseded else "AND (status IS NULL OR status != 'superseded')"
_n_superseded_hidden = store.conn.execute(
    f"SELECT COUNT(*) FROM memories WHERE memory_type='cluster' AND {_level_filter} "
    f"AND status='superseded'"
).fetchone()[0]
cluster_rows = store.conn.execute(
    f"SELECT id, content, evidence_count, extraction_method, status FROM memories "
    f"WHERE memory_type='cluster' AND {_level_filter} {_superseded_filter} "
    f"ORDER BY evidence_count DESC LIMIT 15"
).fetchall()
if _n_superseded_hidden and not _show_superseded:
    st.caption(f"{_n_superseded_hidden} superseded cluster(s) hidden — check \"Include superseded\" to see them.")
if cluster_rows:
    cluster_ids = [r[0] for r in cluster_rows]
    placeholders = ",".join("?" * len(cluster_ids))
    comp_rows = store.conn.execute(
        f"SELECT cluster_memory_id, member_memory_type, COUNT(*) FROM memory_cluster_members "
        f"WHERE cluster_memory_id IN ({placeholders}) GROUP BY cluster_memory_id, member_memory_type",
        cluster_ids,
    ).fetchall()
    comp_by_cluster: dict[str, dict[str, int]] = {}
    for cid, mtype2, cnt in comp_rows:
        comp_by_cluster.setdefault(cid, {})[mtype2] = cnt

    labels = [
        _short_theme(r[1]) + (" [superseded]" if r[4] == "superseded" else "")
        for r in cluster_rows
    ]
    full_content = {cid: content for cid, content, _, _, _ in cluster_rows}
    level_by_id = {cid: ("cross-session" if em == "cluster" else "within-session")
                   for cid, _, _, em, _ in cluster_rows}
    present_types = sorted({t for comp in comp_by_cluster.values() for t in comp})
    fig_cl = go.Figure()
    for t in present_types:
        fig_cl.add_trace(go.Bar(
            name=t,
            y=labels,
            x=[comp_by_cluster.get(cid, {}).get(t, 0) for cid in cluster_ids],
            orientation="h",
            marker_color=_COMPOSITION_COLORS.get(t, "#6b7280"),
            customdata=[[full_content[cid], level_by_id[cid]] for cid in cluster_ids],
            hovertemplate="%{customdata[0]}<br>(%{customdata[1]}) " + t + ": %{x}<extra></extra>",
        ))
    fig_cl.update_layout(
        height=max(320, 32 * len(cluster_rows)), margin=dict(t=10, b=10),
        barmode="stack", xaxis_title="Original memories in cluster",
        yaxis=dict(autorange="reversed"), legend=dict(orientation="h", y=1.05),
    )
    st.plotly_chart(fig_cl, use_container_width=True)
    if not comp_by_cluster:
        st.caption(
            "No membership records found — these clusters were minted before "
            "membership tracking was added; re-run `ats cluster-memories` to backfill."
        )
else:
    st.caption(f"No {_level_choice.lower()} cluster memories yet — run `ats cluster-memories --level both`.")

st.divider()

st.markdown("**Cluster memory retrieval health**")
st.caption(
    "Every `recall()`/`graph_walk()` call logs which memories it surfaced to `memory_retrievals` "
    "— this reuses that log, no new instrumentation. A cluster memory that keeps getting retrieved "
    "is corroborated as useful in practice; one that minted once and has never been surfaced since "
    "is a candidate to re-check — either the pattern is real but under-queried, or the corpus region "
    "it came from has gone stale. Superseded clusters are excluded: supersession already answers "
    "the staleness question for them directly."
)
_health_rows = store.conn.execute(
    """SELECT m.id, m.content, m.evidence_count, m.extraction_method, m.created_at,
              COUNT(mr.id) AS retrieval_count, MAX(mr.retrieved_at) AS last_retrieved_at
       FROM memories m
       LEFT JOIN memory_retrievals mr ON mr.memory_id = m.id
       WHERE m.memory_type = 'cluster' AND (m.status IS NULL OR m.status != 'superseded')
       GROUP BY m.id
       ORDER BY retrieval_count ASC, m.created_at ASC"""
).fetchall()
if not _health_rows:
    st.caption("No active cluster memories yet.")
else:
    _now_ts = pd.Timestamp.now(tz="UTC")

    def _age_days(ts: str | None) -> int | None:
        if not ts:
            return None
        try:
            parsed = pd.Timestamp(ts)
            if parsed.tzinfo is None:
                parsed = parsed.tz_localize("UTC")
            return (_now_ts - parsed).days
        except (ValueError, TypeError):
            return None

    health_df = pd.DataFrame([
        {
            "Theme": _short_theme(content),
            "Level": "cross-session" if em == "cluster" else "within-session",
            "Sessions": evidence_count,
            "Retrievals": retrieval_count,
            "Minted": (age_mint if (age_mint := _age_days(created_at)) is not None else "—"),
            "Since last retrieval": (age_ret if (age_ret := _age_days(last_retrieved_at)) is not None else "never"),
        }
        for _, content, evidence_count, em, created_at, retrieval_count, last_retrieved_at in _health_rows
    ])
    n_never = int((health_df["Retrievals"] == 0).sum())
    st.caption(
        f"{n_never} / {len(health_df)} active cluster memories have never been retrieved "
        f"(sorted below, least-retrieved first)."
    )
    st.dataframe(health_df, use_container_width=True, hide_index=True)

st.divider()

st.markdown("**Detected conflicts**")
st.caption(
    "When consolidating a cluster, the same LLM call that writes the merged summary also checks "
    "whether any two members genuinely contradict each other — not just phrase the same fact "
    "differently, but assert incompatible things (a config value that changed, a decision reported "
    "one way then the opposite way). Flagged pairs land here, unresolved by default — nothing "
    "currently auto-resolves them. This only catches conflicts between memories that end up in the "
    "same cluster; two contradicting memories that never cluster together are invisible to it."
)
conflict_rows = store.conn.execute(
    """SELECT c.id, c.conflict_type, c.detected_at, c.resolution,
              ma.content, ma.memory_type, mb.content, mb.memory_type
       FROM conflicts c
       JOIN memories ma ON ma.id = c.memory_id_a
       JOIN memories mb ON mb.id = c.memory_id_b
       ORDER BY c.detected_at DESC"""
).fetchall()
if not conflict_rows:
    st.caption("No conflicts detected yet.")
else:
    st.caption(f"{len(conflict_rows)} conflict(s) detected, {sum(1 for r in conflict_rows if not r[3])} unresolved.")
    for cid, ctype, detected_at, resolution, content_a, type_a, content_b, type_b in conflict_rows:
        status_label = "resolved" if resolution else "unresolved"
        with st.expander(f"[{ctype or 'unspecified'}] — {status_label} — detected {detected_at[:19] if detected_at else ''}"):
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**{type_a}**")
                st.markdown(content_a)
            with col_b:
                st.markdown(f"**{type_b}**")
                st.markdown(content_b)
            if resolution:
                st.caption(f"Resolution: {resolution}")

st.divider()

st.markdown("**Memory map — embedding UMAP**")
st.caption(
    "Every embedded memory (raw + consolidated cluster memories) projected to 2D by semantic "
    "similarity. Pick what to color by to see how memories group — by type, by which cluster "
    "(if any) consolidated them, or by where they came from."
)
color_by = st.selectbox("Color by", ["Memory type", "Cluster status", "Harness", "Workspace"], key="mem_umap_color")


@st.cache_data(ttl=600)
def load_memory_umap(_conn_id):
    emb_rows = store.conn.execute("SELECT memory_id, embedding FROM memory_embeddings").fetchall()
    if len(emb_rows) < 4:
        return None
    import numpy as np
    mids = [r[0] for r in emb_rows]
    vecs = np.array([np.frombuffer(r[1], dtype=np.float32) for r in emb_rows])
    import umap
    reducer = umap.UMAP(n_components=2, random_state=42, n_neighbors=min(15, len(mids) - 1))
    coords = reducer.fit_transform(vecs)
    return pd.DataFrame({"memory_id": mids, "x": coords[:, 0], "y": coords[:, 1]})


umap_mem_df = load_memory_umap(id(store.conn))
if umap_mem_df is None:
    st.info("Need ≥4 embedded memories. Run `ats embed`.")
else:
    meta_map = {r[0]: (r[1], r[2], r[3], r[4]) for r in store.conn.execute("SELECT id, memory_type, content, extraction_method, status FROM memories").fetchall()}
    # A memory can have multiple sources (cluster memories always do, by design) —
    # take the first-seen session as a representative for harness/workspace coloring
    # rather than trying to encode all of them.
    rep_map: dict[str, tuple] = {}
    for mid, ws, plugin in store.conn.execute(
        "SELECT ms.memory_id, s.workspace_id, s.source_plugin FROM memory_sources ms "
        "JOIN sessions s ON s.id = ms.session_id"
    ).fetchall():
        rep_map.setdefault(mid, (ws, plugin))
    member_of = {
        r[0]: r[1] for r in store.conn.execute("SELECT member_memory_id, cluster_memory_id FROM memory_cluster_members").fetchall()
    }

    def _cluster_status(mid: str) -> str:
        mtype, _, extraction_method, status = meta_map.get(mid, (None, None, None, None))
        if mtype == "cluster":
            if status == "superseded":
                return "Superseded cluster"
            return "Cross-session cluster" if extraction_method == "cluster" else "Within-session cluster"
        return "Cluster member" if mid in member_of else "Unclustered"

    umap_mem_df["memory_type"] = umap_mem_df["memory_id"].map(lambda m: meta_map.get(m, (None, None, None, None))[0] or "unknown")
    content_map = {m: (meta_map.get(m, (None, "", None, None))[1] or "") for m in umap_mem_df["memory_id"]}
    umap_mem_df["content_preview"] = umap_mem_df["memory_id"].map(lambda m: _short_theme(content_map[m], 90))
    umap_mem_df["workspace"] = umap_mem_df["memory_id"].map(lambda m: format_workspace(rep_map.get(m, (None, None))[0] or "") or "unknown")
    umap_mem_df["harness"] = umap_mem_df["memory_id"].map(lambda m: rep_map.get(m, (None, "unknown"))[1] or "unknown")
    umap_mem_df["cluster_status"] = umap_mem_df["memory_id"].map(_cluster_status)

    color_col = {
        "Memory type": "memory_type", "Cluster status": "cluster_status",
        "Harness": "harness", "Workspace": "workspace",
    }[color_by]

    fig_mem_umap = px.scatter(
        umap_mem_df, x="x", y="y", color=color_col,
        hover_data={"content_preview": True, "x": False, "y": False},
        labels={"x": "UMAP-1", "y": "UMAP-2"},
        height=520, opacity=0.65, render_mode="webgl",
    )
    fig_mem_umap.update_traces(marker=dict(size=5))
    fig_mem_umap.update_layout(margin=dict(t=10, b=10), legend=dict(orientation="h", y=-0.12))
    st.plotly_chart(fig_mem_umap, use_container_width=True)

    status_counts = umap_mem_df["cluster_status"].value_counts()
    n_unclustered = int(status_counts.get("Unclustered", 0))
    n_raw = int((umap_mem_df["memory_type"] != "cluster").sum())
    pct_unclustered = round(100 * n_unclustered / n_raw, 1) if n_raw else 0
    st.caption(
        f"{len(umap_mem_df)} embedded memories — "
        f"{int(status_counts.get('Cross-session cluster', 0))} cross-session + "
        f"{int(status_counts.get('Within-session cluster', 0))} within-session consolidated memories "
        f"({int(status_counts.get('Superseded cluster', 0))} superseded, still shown here for context "
        f"but excluded from search/recall), "
        f"{int(status_counts.get('Cluster member', 0))} cluster members, "
        f"{n_unclustered} unclustered ({pct_unclustered}% of raw memories). "
        f"'Unclustered' is expected, not a bug: cluster-memories only groups memories that HDBSCAN finds "
        f"≥3 semantically close together — a memory that's the only one of its kind in the corpus stays "
        f"unclustered and is still a perfectly valid standalone memory, just not part of a recurring "
        f"pattern. Run `ats cluster-memories --min-cluster-size 2` to cluster more aggressively if you "
        f"want fewer unclustered memories (tradeoff: looser, noisier clusters)."
    )

st.divider()

st.markdown("**Memories by workspace**")
ws_rows = store.conn.execute(
    "SELECT s.workspace_id, COUNT(DISTINCT ms.memory_id) as cnt "
    "FROM memory_sources ms JOIN sessions s ON s.id = ms.session_id "
    "GROUP BY s.workspace_id ORDER BY cnt DESC LIMIT 15"
).fetchall()
if ws_rows:
    wdf = pd.DataFrame(
        [{"Workspace": format_workspace(r[0] or "") or "(unknown)", "Memories": r[1]} for r in ws_rows]
    )
    fig_ws = go.Figure(go.Bar(
        x=wdf["Memories"], y=wdf["Workspace"], orientation="h", marker_color="#3b82f6",
    ))
    fig_ws.update_layout(
        height=max(260, 28 * len(wdf)), margin=dict(t=10, b=10),
        xaxis_title="Memories", yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig_ws, use_container_width=True)

st.divider()

# ── Filters ──────────────────────────────────────────────────────────────────
st.header("Browse")
col1, col2, col3, col4 = st.columns(4)
with col1:
    mtype = st.selectbox("Memory type", ["(all)"] + ordered_types)
with col2:
    method = st.selectbox("Extraction method", ["(all)", "d_combined"])
with col3:
    sessions = store.conn.execute(
        "SELECT id, source_plugin, workspace_id, session_timestamp FROM sessions ORDER BY session_timestamp DESC"
    ).fetchall()
    sess_opts = {"(all sessions)": None} | {
        f"{r[3][:10]} | {r[1]} | {(r[2] or '').split('/')[-1][-20:]} | {r[0][:8]}": r[0]
        for r in sessions
    }
    sess_label = st.selectbox("Session", list(sess_opts.keys()))
    session_id = sess_opts[sess_label]
with col4:
    search_text = st.text_input("Content search", placeholder="filter by keyword")

# ── Query ────────────────────────────────────────────────────────────────────
where = ["1=1"]
params: list = []

if mtype != "(all)":
    where.append("m.memory_type = ?")
    params.append(mtype)
if method != "(all)":
    where.append("m.extraction_method = ?")
    params.append(method)
if session_id:
    where.append("EXISTS (SELECT 1 FROM memory_sources ms WHERE ms.memory_id = m.id AND ms.session_id = ?)")
    params.append(session_id)
if search_text:
    where.append("m.content LIKE ?")
    params.append(f"%{search_text}%")

rows = store.conn.execute(
    f"""SELECT m.id, m.memory_type, m.extraction_method, m.content,
               m.evidence_count, m.access_count, m.created_by_pipeline, m.first_observed,
               (SELECT s.workspace_id FROM memory_sources ms2 JOIN sessions s ON s.id = ms2.session_id
                WHERE ms2.memory_id = m.id LIMIT 1) as workspace_id
        FROM memories m WHERE {' AND '.join(where)}
        ORDER BY m.evidence_count DESC, m.access_count DESC
        LIMIT 200""",
    params,
).fetchall()

st.caption(f"{len(rows)} memories (capped at 200)")

if not rows:
    st.info("No memories match the current filters.")
    st.stop()

# ── Table ────────────────────────────────────────────────────────────────────
data = [
    {
        "ID": r[0][:8],
        "Type": r[1],
        "Workspace": format_workspace(r[8] or "")[-25:],
        "Content": r[3],
        "Evidence": r[4],
        "Accessed": r[5],
        "First observed": (r[7] or "")[:10],
        "Pipeline": r[6] or "",
        "_id": r[0],
    }
    for r in rows
]
df = pd.DataFrame(data)

event = st.dataframe(
    df.drop(columns=["_id"]),
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={"Content": st.column_config.TextColumn(width="large")},
)

# ── Detail panel ─────────────────────────────────────────────────────────────
selected = event.selection.get("rows", []) if event and event.selection else []
if selected:
    row = data[selected[0]]
    mid = row["_id"]
    st.divider()
    st.subheader(f"Memory detail — {mid[:8]}")
    st.write(row["Content"])
    st.caption(
        f"Type: `{row['Type']}` · Evidence: {row['Evidence']} · Accessed: {row['Accessed']} · "
        f"First observed: {row['First observed']} · Workspace: {row['Workspace'] or '—'}"
    )

    sources = store.conn.execute(
        """SELECT ms.session_id, s.source_plugin, s.workspace_id, s.session_timestamp
           FROM memory_sources ms JOIN sessions s ON s.id = ms.session_id
           WHERE ms.memory_id = ?""",
        (mid,),
    ).fetchall()
    if sources:
        st.caption("**Source sessions:**")
        for src in sources:
            st.caption(f"  {src[3][:10]} | {src[1]} | {(src[2] or '').split('/')[-1][-30:]} | {src[0][:8]}")

    if row["Type"] == "cluster":
        cluster_members = store.conn.execute(
            """SELECT m.id, cm.member_memory_type, m.content
               FROM memory_cluster_members cm JOIN memories m ON m.id = cm.member_memory_id
               WHERE cm.cluster_memory_id = ?
               ORDER BY cm.member_memory_type""",
            (mid,),
        ).fetchall()
        if cluster_members:
            type_counts_cluster = Counter(r[1] for r in cluster_members)
            st.caption(
                "**This consolidated memory came from "
                + ", ".join(f"{v} {k}" for k, v in type_counts_cluster.most_common())
                + f" ({len(cluster_members)} original memories total).**"
            )
            with st.expander("Show original memories"):
                for orig_id, orig_type, orig_content in cluster_members:
                    st.caption(f"`{orig_type}` — {orig_content}")
        else:
            st.caption(
                "No membership record for this cluster memory — it was minted before "
                "membership tracking was added; re-run `ats cluster-memories` to backfill."
            )
