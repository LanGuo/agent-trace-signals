"""Exploratory analysis over sessions — corpus-wide aggregate views grouped by harness,
model, permission mode, entrypoint, or structural session type, a session connection
graph, and (at the bottom) a hand-picked-session side-by-side comparison.

Split from the old combined "Explore" page: this page is everything session-scoped
(structure, metrics, connections between sessions). Memory-content-scoped analytics
(what the memories themselves look like) moved to Explore Memories.
"""

from __future__ import annotations
import math
import struct
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from agent_trace_signals.ui.common import PAGE_CONFIG, get_store, format_workspace, session_label

st.set_page_config(**PAGE_CONFIG)
st.title("🔭 Explore Sessions")
st.caption("Corpus-wide exploratory analysis — patterns across harnesses, models, and configurations, not tied to a specific session selection. Pick specific sessions to compare at the bottom.")

store = get_store()

sessions = store.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
if not sessions:
    st.warning("No sessions ingested yet.")
    st.stop()


@st.cache_data(ttl=60)
def load_aggregate(_conn_id):
    rows = store.conn.execute("""
        SELECT
            s.id,
            s.source_plugin,
            s.workspace_id,
            s.session_timestamp,
            s.chunk_count,
            s.session_type,
            COUNT(DISTINCT ms.memory_id)                        AS memories,
            COUNT(DISTINCT o.entity_id)                         AS entities,
            COALESCE(sm.dominant_model, 'unknown')              AS model,
            COALESCE(sm.user_turn_count, 0)                     AS turns,
            COALESCE(sm.tool_call_count, 0)                     AS tool_calls,
            COALESCE(sm.total_input_tokens, 0)                  AS input_tok,
            COALESCE(sm.total_cache_read_tokens, 0)             AS cache_read_tok,
            COALESCE(sm.total_cache_creation_tokens, 0)         AS cache_create_tok,
            sm.permission_mode                                  AS permission_mode_raw,
            COALESCE(sm.entrypoint, 'unknown')                  AS entrypoint
        FROM sessions s
        LEFT JOIN memory_sources ms ON ms.session_id = s.id
        LEFT JOIN occurrences o     ON o.session_id = s.id
        LEFT JOIN session_metadata sm ON sm.session_id = s.id
        GROUP BY s.id
    """).fetchall()
    cols = ["session_id", "plugin", "workspace_id", "timestamp", "chunks", "session_type", "memories", "entities",
            "model", "turns", "tool_calls", "input_tok", "cache_read_tok", "cache_create_tok",
            "permission_mode_raw", "entrypoint"]
    df = pd.DataFrame(rows, columns=cols)
    df["workspace"] = df["workspace_id"].map(lambda w: format_workspace(w or "") or "unknown")

    label_rows = store.conn.execute("SELECT session_type, label FROM session_type_labels").fetchall()
    label_map = {r[0]: r[1] for r in label_rows}
    df["session_type_label"] = df["session_type"].map(label_map).fillna(df["session_type"]).fillna("unlabeled")

    def _norm_permission(v):
        # SQL NULL surfaces here as either None or float NaN depending on the
        # column's inferred dtype — `not v` alone doesn't catch NaN (NaN is truthy).
        if not isinstance(v, str) or not v:
            return "unknown"
        # Newer CC versions emit a JSON array of granular permission rules instead
        # of a single mode string — collapse those to one label rather than
        # fragmenting the group-by on near-duplicate JSON strings.
        return "custom_restricted" if v.startswith("[") else v

    df["permission_mode"] = df["permission_mode_raw"].map(_norm_permission)
    df["mem_per_chunk"] = (df["memories"] / df["chunks"].replace(0, float("nan"))).round(2)
    cache_denom = df["input_tok"] + df["cache_read_tok"] + df["cache_create_tok"]
    df["cache_hit_pct"] = (df["cache_read_tok"] / cache_denom.replace(0, float("nan")) * 100).round(1)
    df["tool_per_turn"] = (df["tool_calls"] / df["turns"].replace(0, float("nan"))).round(2)

    # recovery patterns per session
    rec_rows = store.conn.execute("""
        SELECT ms.session_id, COUNT(DISTINCT m.id)
        FROM memory_sources ms JOIN memories m ON m.id = ms.memory_id
        WHERE m.memory_type IN ('pattern_recovery', 'pattern_inefficiency')
        GROUP BY ms.session_id
    """).fetchall()
    rec_map = {r[0]: r[1] for r in rec_rows}
    df["recovery_n"] = df["session_id"].map(rec_map).fillna(0).astype(int)
    df["recovery_rate"] = (df["recovery_n"] / df["memories"].replace(0, float("nan")) * 100).round(1)

    return df


agg = load_aggregate(id(store.conn))

if agg.empty:
    st.info("No data yet.")
else:
    group_by = st.radio(
        "Group by",
        ["harness (source_plugin)", "model", "permission mode", "entrypoint", "structural session type"],
        horizontal=True,
    )
    group_col = {
        "harness (source_plugin)": "plugin",
        "model": "model",
        "permission mode": "permission_mode",
        "entrypoint": "entrypoint",
        "structural session type": "session_type_label",
    }[group_by]

    # ── Health quadrant ───────────────────────────────────────────────────────
    st.markdown("**Session scatter** — pick any two metrics")
    _METRIC_OPTIONS = {
        "Tool calls / turn": "tool_per_turn", "Recovery rate %": "recovery_rate",
        "Chunks": "chunks", "Memories": "memories", "Entities": "entities",
        "Mem / chunk": "mem_per_chunk", "Cache hit %": "cache_hit_pct",
        "Turns": "turns", "Tool calls": "tool_calls",
    }
    col_x, col_y = st.columns(2)
    with col_x:
        x_metric = st.selectbox("X axis", list(_METRIC_OPTIONS.keys()), index=0)
    with col_y:
        y_metric = st.selectbox("Y axis", list(_METRIC_OPTIONS.keys()), index=1)
    x_col, y_col = _METRIC_OPTIONS[x_metric], _METRIC_OPTIONS[y_metric]
    st.caption(
        "Default (Tool calls/turn vs Recovery rate) reads as autonomy vs struggle — higher x = more "
        "autonomous, higher y = more struggle. Each dot is exactly one session, all drawn the same "
        "size. Many sessions share near-identical x/y values (e.g. an integer tool-calls/turn ratio), "
        "so same-colored dots often stack directly on top of each other — that's real overlap, not one "
        "big session; dots are semi-transparent so stacked points look darker/denser. Hover any dot "
        "for its workspace, chunk count, and exact values."
    )
    quad_df = agg.dropna(subset=[x_col, y_col])
    if not quad_df.empty:
        fig_q = px.scatter(
            quad_df, x=x_col, y=y_col, color=group_col,
            opacity=0.6,
            hover_data={"session_id": True, "workspace": True, "memories": True,
                        "recovery_n": True, "chunks": True},
            labels={x_col: x_metric, y_col: y_metric, group_col: group_by},
            height=350,
        )
        fig_q.update_traces(marker=dict(size=10))
        # reference lines at medians
        med_x = quad_df[x_col].median()
        med_y = quad_df[y_col].median()
        fig_q.add_hline(y=med_y, line_dash="dot", line_color="gray", opacity=0.5)
        fig_q.add_vline(x=med_x, line_dash="dot", line_color="gray", opacity=0.5)
        fig_q.update_layout(margin=dict(t=10, b=20))
        st.plotly_chart(fig_q, use_container_width=True)

    # ── Token efficiency over time ─────────────────────────────────────────────
    st.markdown("**Token efficiency over time**")
    time_df = agg.dropna(subset=["timestamp"]).sort_values("timestamp")
    time_df = time_df[time_df["cache_hit_pct"].notna() | time_df["mem_per_chunk"].notna()]
    if not time_df.empty:
        fig_time = go.Figure()
        fig_time.add_trace(go.Scatter(
            x=time_df["timestamp"].str[:10], y=time_df["cache_hit_pct"],
            mode="markers+lines", name="Cache hit %", yaxis="y1",
            marker=dict(symbol="circle"),
        ))
        fig_time.add_trace(go.Scatter(
            x=time_df["timestamp"].str[:10], y=time_df["mem_per_chunk"],
            mode="markers+lines", name="Mem / chunk", yaxis="y2",
            marker=dict(symbol="diamond"),
        ))
        fig_time.update_layout(
            height=280, margin=dict(t=10, b=20),
            yaxis=dict(title="Cache hit %", rangemode="tozero"),
            yaxis2=dict(title="Mem / chunk", overlaying="y", side="right", rangemode="tozero"),
            legend=dict(orientation="h", y=1.08),
        )
        st.plotly_chart(fig_time, use_container_width=True)

    st.markdown("**Cache hit % distribution**")
    fig_ch = px.histogram(
        agg.dropna(subset=["cache_hit_pct"]), x="cache_hit_pct", color=group_col,
        barmode="overlay", nbins=20, opacity=0.75,
        labels={"cache_hit_pct": "Cache hit %", group_col: group_by},
        height=300,
    )
    fig_ch.update_layout(margin=dict(t=10, b=20))
    st.plotly_chart(fig_ch, use_container_width=True)

    # ── Memory type breakdown by session type ────────────────────────────────
    # Moved here from the old "Session Compare" page — that page showed this same
    # composition per hand-picked session; this is the default, corpus-wide version,
    # aggregated to structural-session-type cohorts instead. Normalized per chunk,
    # not per session: session types vary wildly in length (e.g. "tool-driven" averages
    # 40+ chunks/session, "focused extraction" ~1) so a per-session average mostly just
    # reflects length, not a real memory-type signature. Per-chunk controls for that.
    st.markdown("**Memory type breakdown by session type**")
    st.caption(
        "Average memory-type density per chunk (not per session — session types vary a lot in "
        "length, so a per-session count would mostly just reflect that), grouped by structural "
        "session type. For a hand-picked-session version of this same chart, see 'Compare "
        "specific sessions' at the bottom of this page."
    )

    @st.cache_data(ttl=60)
    def load_memtype_by_sessiontype(_conn_id):
        rows = store.conn.execute("""
            SELECT s.id, s.session_type, m.memory_type, COUNT(*) as cnt
            FROM memory_sources ms JOIN memories m ON m.id = ms.memory_id
            JOIN sessions s ON s.id = ms.session_id
            WHERE m.memory_type != 'cluster'
            GROUP BY s.id, m.memory_type
        """).fetchall()
        label_map = {r[0]: r[1] for r in store.conn.execute("SELECT session_type, label FROM session_type_labels").fetchall()}
        df = pd.DataFrame(rows, columns=["session_id", "session_type", "memory_type", "count"])
        df["session_type_label"] = df["session_type"].map(label_map).fillna(df["session_type"]).fillna("unlabeled")
        return df

    mt_df = load_memtype_by_sessiontype(id(store.conn))
    if mt_df.empty:
        st.info("No memory data available yet.")
    else:
        chunks_per_type = agg.groupby("session_type_label")["chunks"].sum()
        mt_comp = mt_df.groupby(["session_type_label", "memory_type"])["count"].sum().reset_index()
        mt_comp["avg_per_chunk"] = mt_comp.apply(
            lambda r: r["count"] / max(1, chunks_per_type.get(r["session_type_label"], 1)), axis=1,
        )
        fig_mt = px.bar(
            mt_comp, x="session_type_label", y="avg_per_chunk", color="memory_type",
            barmode="stack",
            labels={"session_type_label": "Session type", "avg_per_chunk": "Avg memories / chunk"},
            height=380,
        )
        fig_mt.update_layout(margin=dict(t=10, b=80), xaxis_tickangle=-30)
        st.plotly_chart(fig_mt, use_container_width=True)

    # ── Entity type breakdown by session type ────────────────────────────────
    # Same per-chunk normalization as the memory-type chart above, and for the same
    # reason — session types differ wildly in length, so per-session counts would
    # mostly reflect that instead of a real entity-density signature.
    st.markdown("**Entity type breakdown by session type**")
    st.caption("Average distinct-entity density per chunk, grouped by structural session type.")

    @st.cache_data(ttl=60)
    def load_enttype_by_sessiontype(_conn_id):
        rows = store.conn.execute("""
            SELECT s.id, s.session_type, e.entity_type, COUNT(DISTINCT e.id) as cnt
            FROM occurrences o JOIN entities e ON e.id = o.entity_id
            JOIN sessions s ON s.id = o.session_id
            GROUP BY s.id, e.entity_type
        """).fetchall()
        label_map = {r[0]: r[1] for r in store.conn.execute("SELECT session_type, label FROM session_type_labels").fetchall()}
        df = pd.DataFrame(rows, columns=["session_id", "session_type", "entity_type", "count"])
        df["session_type_label"] = df["session_type"].map(label_map).fillna(df["session_type"]).fillna("unlabeled")
        return df

    ent_df = load_enttype_by_sessiontype(id(store.conn))
    if ent_df.empty:
        st.info("No entity data available yet.")
    else:
        chunks_per_type = agg.groupby("session_type_label")["chunks"].sum()
        ent_comp = ent_df.groupby(["session_type_label", "entity_type"])["count"].sum().reset_index()
        ent_comp["avg_per_chunk"] = ent_comp.apply(
            lambda r: r["count"] / max(1, chunks_per_type.get(r["session_type_label"], 1)), axis=1,
        )
        fig_ent_type = px.bar(
            ent_comp, x="session_type_label", y="avg_per_chunk", color="entity_type",
            barmode="stack",
            labels={"session_type_label": "Session type", "avg_per_chunk": "Avg entities / chunk"},
            height=380,
        )
        fig_ent_type.update_layout(margin=dict(t=10, b=80), xaxis_tickangle=-30)
        st.plotly_chart(fig_ent_type, use_container_width=True)

    # ── Recovery rate leaderboard ──────────────────────────────────────────────
    st.markdown("**Recovery rate leaderboard** — sessions with most struggle")
    st.caption(
        "Sorting by raw Recovery % alone is misleading for tiny sessions — a single-chunk "
        "session with 1 recovery memory out of 2 total memories shows as \"50%\", ranking "
        "above a substantial 45-chunk session with 17 recovery memories out of 179 (9.5%). "
        "The floor below excludes low-signal tiny sessions before ranking; sort by absolute "
        "count instead of rate if you want the biggest total struggle regardless of session size."
    )
    lb_col1, lb_col2 = st.columns([1, 1])
    with lb_col1:
        min_memories = st.number_input(
            "Min memories to qualify", min_value=0, value=10, step=5,
            help="Excludes sessions below this total memory count from the leaderboard, so a "
                 "session with 1 flagged memory out of 2 total can't outrank one with 17 out of 179.",
        )
    with lb_col2:
        sort_by = st.radio("Sort by", ["Recovery %", "Recovery+Ineff count"], horizontal=True)
    sort_col = "recovery_rate" if sort_by == "Recovery %" else "recovery_n"

    leader_df = (
        agg[(agg["recovery_n"] > 0) & (agg["memories"] >= min_memories)]
        .sort_values(sort_col, ascending=False)
        [["session_id", "plugin", "workspace", "model", "chunks", "memories", "recovery_n", "recovery_rate", "tool_per_turn"]]
        .rename(columns={"session_id": "Session", "plugin": "Plugin", "workspace": "Workspace", "model": "Model",
                         "chunks": "Chunks", "memories": "Memories",
                         "recovery_n": "Recovery+Ineff", "recovery_rate": "Recovery %",
                         "tool_per_turn": "Tool/turn"})
    )
    leader_df["Session"] = leader_df["Session"].str[:12]
    st.dataframe(leader_df, use_container_width=True, hide_index=True)

    # ── UMAP of structural embeddings ──────────────────────────────────────────
    st.markdown("**Session map — structural embedding UMAP**")
    st.caption(
        "Position reflects raw trace structure (tool call patterns, turn rhythm), not topic. "
        "Color = structural session type (the HDBSCAN cluster this UMAP projection is itself derived "
        "from — colors should roughly correspond to visual groupings, since both come from the same "
        "embedding space). Each dot is exactly one session, all drawn the same size. Sessions with very "
        "similar structure land at nearly the same (x, y), so same-colored dots often overlap directly "
        "— dots are semi-transparent so real overlap reads as a darker patch rather than one big session "
        "(see the community graph below for actual session-to-session groupings). Hover for "
        "workspace/model/chunk count."
    )

    @st.cache_data(ttl=300)
    def load_umap(_conn_id):
        emb_rows = store.conn.execute(
            "SELECT session_id, embedding FROM session_structural_embeddings"
        ).fetchall()
        if len(emb_rows) < 4:
            return None
        sids = [r[0] for r in emb_rows]
        vecs = np.array([list(struct.unpack(f"{len(r[1])//4}f", r[1])) for r in emb_rows], dtype=np.float32)
        import umap
        reducer = umap.UMAP(n_components=2, random_state=42, n_neighbors=min(10, len(sids)-1))
        coords = reducer.fit_transform(vecs)
        return pd.DataFrame({"session_id": sids, "x": coords[:, 0], "y": coords[:, 1]})

    umap_df = load_umap(id(store.conn))
    if umap_df is not None:
        umap_df = umap_df.merge(
            agg[["session_id", "plugin", "workspace", "model", "session_type_label", "recovery_rate",
                 "chunks", "memories", "tool_per_turn"]],
            on="session_id", how="left",
        )
        umap_df["recovery_rate"] = umap_df["recovery_rate"].fillna(0)
        fig_umap = px.scatter(
            umap_df, x="x", y="y", color="session_type_label",
            opacity=0.6,
            hover_data={"session_id": True, "plugin": True, "workspace": True, "model": True,
                        "memories": True, "chunks": True, "tool_per_turn": True, "recovery_rate": True},
            labels={"x": "UMAP-1", "y": "UMAP-2", "session_type_label": "Session type"},
            height=440,
        )
        fig_umap.update_traces(marker=dict(size=8))
        fig_umap.update_layout(margin=dict(t=10, b=20), legend=dict(orientation="h", y=-0.15))
        st.plotly_chart(fig_umap, use_container_width=True)
        st.caption(f"{len(umap_df)} sessions with structural embeddings. Run `ats embed` to add more, `ats cluster-sessions` to (re)label.")
    else:
        st.info("Need ≥4 sessions with structural embeddings. Run `ats embed` to populate.")

    # ── Group summary ──────────────────────────────────────────────────────────
    st.markdown("**Group summary**")
    summary = agg.groupby(group_col).agg(
        sessions=("session_id", "count"),
        chunks_total=("chunks", "sum"),
        memories_total=("memories", "sum"),
        mem_per_chunk_avg=("mem_per_chunk", "mean"),
        entities_total=("entities", "sum"),
        cache_hit_pct_avg=("cache_hit_pct", "mean"),
        tool_per_turn_avg=("tool_per_turn", "mean"),
        recovery_rate_avg=("recovery_rate", "mean"),
    ).round(2).reset_index()
    summary.columns = [group_col, "sessions", "chunks", "memories",
                       "mem/chunk", "entities", "cache hit %", "tools/turn", "recovery %"]
    st.dataframe(summary, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# SESSION CONNECTION GRAPH — community structure across all three edge types
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("Session connection graph")
st.caption(
    "Every session pair connected by a shared workspace, a shared file/commit/PR entity, "
    "a shared consolidated memory pattern, or similar interaction shape — with automatic "
    "community detection."
)

col_edges, col_res = st.columns([3, 1])
with col_edges:
    edge_type_choices = st.multiselect(
        "Edge types to include",
        ["workspace", "structural", "shared_memory", "structural_similarity"],
        default=["workspace", "structural", "shared_memory"],
        help="workspace = same project · structural = shared file/commit/PR · shared_memory = both sessions "
             "fed the same consolidated cluster memory (often the only cross-project connection) · "
             "structural_similarity = similar tool-call rhythm from embeddings (top-8 nearest neighbours "
             "per session, cosine ≥0.5) — off by default since it's a soft, continuous-similarity signal "
             "rather than a hard shared-thing connection, and is by far the densest edge type",
    )
with col_res:
    resolution = st.slider(
        "Community granularity", 0.5, 3.0, 1.3, 0.1,
        help="Passed to greedy-modularity community detection. Higher = more, smaller communities "
             "(splits loosely-connected sessions apart); lower = fewer, larger communities.",
    )

if not edge_type_choices:
    st.info("Select at least one edge type.")
else:
    @st.cache_data(ttl=60)
    def load_graph(_conn_id, edge_types: tuple):
        placeholders = ",".join("?" * len(edge_types))
        rows = store.conn.execute(
            f"SELECT source_session_id, target_session_id, edge_type FROM session_graph_edges "
            f"WHERE edge_type IN ({placeholders})",
            edge_types,
        ).fetchall()
        sess_rows = store.conn.execute(
            "SELECT id, source_plugin, workspace_id, session_type FROM sessions"
        ).fetchall()
        # sqlite3.Row (this connection's row_factory) isn't reliably picklable for
        # st.cache_data's storage — convert to plain tuples before returning.
        return [tuple(r) for r in rows], [tuple(r) for r in sess_rows]

    edge_rows, sess_rows = load_graph(id(store.conn), tuple(sorted(edge_type_choices)))
    sess_info = {r[0]: {"plugin": r[1], "workspace": format_workspace(r[2] or ""), "session_type": r[3]} for r in sess_rows}

    if not edge_rows:
        st.info("No edges of the selected type(s) found.")
    else:
        import networkx as nx

        G = nx.Graph()
        for sid, info in sess_info.items():
            G.add_node(sid, **info)
        edge_types_seen: dict[tuple, set] = {}
        for src, tgt, etype in edge_rows:
            pair = tuple(sorted((src, tgt)))
            if pair[0] == pair[1]:
                continue
            edge_types_seen.setdefault(pair, set()).add(etype)
        for (a, b), etypes in edge_types_seen.items():
            G.add_edge(a, b, weight=len(etypes), types=", ".join(sorted(etypes)))

        # Isolated sessions (no edges of the selected type) clutter the layout with
        # no information — drop them from this view.
        G.remove_nodes_from(list(nx.isolates(G)))

        if G.number_of_nodes() == 0:
            st.info("No connected sessions for the selected edge type(s).")
        else:
            communities = list(
                nx.algorithms.community.greedy_modularity_communities(
                    G, weight="weight", resolution=resolution,
                )
            )
            community_of = {}
            for i, comm in enumerate(communities):
                for node in comm:
                    community_of[node] = i

            pos = nx.spring_layout(G, seed=42, weight="weight", k=1.5 / max(1, G.number_of_nodes() ** 0.5))

            edge_x, edge_y = [], []
            for a, b in G.edges():
                edge_x += [pos[a][0], pos[b][0], None]
                edge_y += [pos[a][1], pos[b][1], None]
            edge_trace = go.Scatter(
                x=edge_x, y=edge_y, mode="lines",
                line=dict(width=0.5, color="#4b5563"), opacity=0.35,
                hoverinfo="none", showlegend=False,
            )

            # Community id is categorical, not ordinal — a continuous colorscale (e.g. Turbo)
            # makes adjacent community ids look like a gradient. Use a discrete qualitative
            # palette instead, one trace per community, so the legend doubles as a key.
            palette = px.colors.qualitative.Bold + px.colors.qualitative.Pastel
            traces = [edge_trace]
            for i, comm in enumerate(communities):
                comm_nodes = [n for n in comm if n in pos]
                if not comm_nodes:
                    continue
                hover = [
                    f"{n[:12]} | {sess_info[n]['plugin']} | {sess_info[n]['workspace']}<br>"
                    f"community {i} · degree {G.degree(n)}"
                    for n in comm_nodes
                ]
                traces.append(go.Scatter(
                    x=[pos[n][0] for n in comm_nodes], y=[pos[n][1] for n in comm_nodes],
                    mode="markers", hoverinfo="text", text=hover,
                    name=f"community {i} ({len(comm_nodes)})",
                    marker=dict(
                        # Uniform size — degree previously drove size here, but with 100+ nodes
                        # varying size just looked like some sessions being "clusters" of others.
                        # Degree is still in the hover text for anyone who wants it.
                        size=10,
                        color=palette[i % len(palette)],
                        line=dict(width=1, color="#111827"),
                    ),
                ))

            fig_graph = go.Figure(data=traces)
            fig_graph.update_layout(
                height=560, margin=dict(t=10, b=10, l=10, r=10),
                xaxis=dict(visible=False), yaxis=dict(visible=False),
                plot_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", y=-0.05),
            )
            st.plotly_chart(fig_graph, use_container_width=True)
            st.caption(
                f"{G.number_of_nodes()} connected sessions, {G.number_of_edges()} unique pairs, "
                f"{len(communities)} communities detected (greedy modularity, resolution={resolution}). "
                f"Every dot is one session, all drawn the same size — color/legend = community. Hover a "
                f"dot for its workspace and degree (how many other sessions it connects to). Isolated "
                f"sessions (no edge of the selected type) are hidden. Raise the granularity slider above "
                f"to split large communities further."
            )

            # Community membership table — communities are otherwise just colors
            comm_rows = []
            for i, comm in enumerate(communities):
                workspaces = {sess_info[n]["workspace"] for n in comm if sess_info[n]["workspace"]}
                plugins = {sess_info[n]["plugin"] for n in comm if sess_info[n]["plugin"]}
                comm_rows.append({
                    "Community": i,
                    "Sessions": len(comm),
                    "Workspaces": ", ".join(sorted(workspaces)[:4]) + (f" +{len(workspaces)-4} more" if len(workspaces) > 4 else ""),
                    "Harnesses": ", ".join(sorted(plugins)),
                })
            comm_df = pd.DataFrame(comm_rows).sort_values("Sessions", ascending=False)
            st.dataframe(comm_df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# COMPARE SPECIFIC SESSIONS — hand-picked side-by-side (was the "Session Compare" page)
# ══════════════════════════════════════════════════════════════════════════════
def _render_session_compare_section() -> None:
    st.divider()
    st.subheader("Compare specific sessions")
    st.caption("Pick a handful of sessions to compare side by side — summary scorecards, memory/entity breakdown, token usage, and pairwise structural embedding similarity.")

    cmp_sessions = store.conn.execute(
        "SELECT id, source_plugin, workspace_id, session_timestamp, chunk_count FROM sessions ORDER BY session_timestamp DESC"
    ).fetchall()
    if not cmp_sessions:
        st.info("No sessions available.")
        return

    sess_opts = {
        session_label(dict(zip(["id", "source_plugin", "workspace_id", "session_timestamp", "chunk_count"], r))): r[0]
        for r in cmp_sessions
    }
    selected_labels = st.multiselect(
        "Select sessions to compare", list(sess_opts.keys()),
        default=list(sess_opts.keys())[:min(4, len(sess_opts))],
        key="explore_compare_session_picker",
    )
    selected_ids = [sess_opts[lbl] for lbl in selected_labels]
    if not selected_ids:
        st.info("Select at least one session.")
        return

    def _decode(blob: bytes) -> list[float]:
        n = len(blob) // 4
        return list(struct.unpack(f"{n}f", blob))

    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0

    def get_session_stats(sid: str) -> dict:
        chunk_count = store.conn.execute("SELECT chunk_count FROM sessions WHERE id=?", (sid,)).fetchone()[0] or 0

        meta_row = store.conn.execute(
            """SELECT dominant_model, total_input_tokens, total_output_tokens,
                      total_cache_read_tokens, total_cache_creation_tokens, total_thought_tokens,
                      user_turn_count, tool_call_count, duration_ms, permission_mode
               FROM session_metadata WHERE session_id=?""",
            (sid,),
        ).fetchone()

        mem_type_rows = store.conn.execute(
            """SELECT m.memory_type, COUNT(*) FROM memories m
               JOIN memory_sources ms ON ms.memory_id = m.id
               WHERE ms.session_id = ? GROUP BY m.memory_type""",
            (sid,),
        ).fetchall()
        mem_by_type = {r[0]: r[1] for r in mem_type_rows}
        mem_total = sum(mem_by_type.values())
        pattern_total = sum(v for k, v in mem_by_type.items() if k.startswith("pattern_"))

        entity_rows = store.conn.execute(
            """SELECT e.entity_type, COUNT(DISTINCT e.id) FROM entities e
               JOIN occurrences o ON o.entity_id = e.id
               WHERE o.session_id = ? GROUP BY e.entity_type""",
            (sid,),
        ).fetchall()
        entity_by_type = {r[0]: r[1] for r in entity_rows}
        entity_total = sum(entity_by_type.values())

        has_meta = meta_row is not None
        inp = (meta_row[1] or 0) if has_meta else 0
        out = (meta_row[2] or 0) if has_meta else 0
        cache_read = (meta_row[3] or 0) if has_meta else 0
        cache_create = (meta_row[4] or 0) if has_meta else 0
        thoughts = (meta_row[5] or 0) if has_meta else 0
        turns = (meta_row[6] or 0) if has_meta else 0
        tool_calls = (meta_row[7] or 0) if has_meta else 0
        duration_ms = (meta_row[8] or 0) if has_meta else 0
        total_tok = inp + out + cache_read + cache_create + thoughts
        # cache_read / (inp + cache_read + cache_create) is the true cache hit rate;
        # inp alone is the non-cached portion so dividing by it gives a >100% ratio.
        cache_denom = inp + cache_read + cache_create
        cache_hit_pct = round(cache_read / cache_denom * 100, 1) if cache_denom > 0 else None
        mem_per_chunk = round(mem_total / chunk_count, 2) if chunk_count > 0 else 0.0
        tok_per_chunk = round(total_tok / chunk_count) if (chunk_count > 0 and total_tok > 0) else None
        tool_per_turn = round(tool_calls / turns, 2) if (has_meta and turns > 0) else None

        return {
            "chunks": chunk_count,
            "mem_total": mem_total,
            "mem_by_type": mem_by_type,
            "pattern_total": pattern_total,
            "mem_per_chunk": mem_per_chunk,
            "entity_total": entity_total,
            "entity_by_type": entity_by_type,
            "dominant_model": meta_row[0] if meta_row else None,
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_create,
            "thought_tokens": thoughts,
            "total_tokens": total_tok,
            "tok_per_chunk": tok_per_chunk,
            "cache_hit_pct": cache_hit_pct,
            "turns": turns,
            "tool_calls": tool_calls,
            "tool_per_turn": tool_per_turn,
            "duration_ms": duration_ms,
            "permission_mode": meta_row[9] if meta_row else None,
        }

    cmp_stats = {sid: get_session_stats(sid) for sid in selected_ids}
    short_labels = {sid: selected_labels[i][:40] for i, sid in enumerate(selected_ids)}

    # ── Summary scorecards ────────────────────────────────────────────────────
    st.markdown("**Summary**")
    cols = st.columns(len(selected_ids))
    for col, sid in zip(cols, selected_ids):
        s = cmp_stats[sid]
        col.markdown(f"**{short_labels[sid]}**")
        col.metric("Chunks", s["chunks"])
        col.metric("Memories", s["mem_total"])
        col.metric("Mem / chunk", s["mem_per_chunk"])
        col.metric("Pattern memories", s["pattern_total"])
        col.metric("Entities", s["entity_total"])
        col.metric("Cache hit %", f"{s['cache_hit_pct']}%" if s["cache_hit_pct"] is not None else "—")
        col.metric("Tokens / chunk", f"{s['tok_per_chunk']:,}" if s["tok_per_chunk"] is not None else "—")
        col.metric("Tool calls / turn", s["tool_per_turn"] if s["tool_per_turn"] is not None else "—")
        if s["permission_mode"]:
            col.caption(f"Mode: {s['permission_mode']} · Model: {s['dominant_model'] or '—'}")

    # ── Memory type breakdown ─────────────────────────────────────────────────
    st.markdown("**Memory type breakdown**")
    _MEM_TYPE_ORDER = ["episodic", "procedural", "preference",
                       "pattern_strategy", "pattern_recovery", "pattern_inefficiency", "cluster"]
    all_types = [t for t in _MEM_TYPE_ORDER if any(t in cmp_stats[sid]["mem_by_type"] for sid in selected_ids)]
    all_types += sorted({t for sid in selected_ids for t in cmp_stats[sid]["mem_by_type"] if t not in _MEM_TYPE_ORDER})

    if all_types:
        fig_mem = go.Figure()
        for sid in selected_ids:
            fig_mem.add_trace(go.Bar(
                name=short_labels[sid],
                x=all_types,
                y=[cmp_stats[sid]["mem_by_type"].get(t, 0) for t in all_types],
            ))
        fig_mem.update_layout(barmode="group", height=320, margin=dict(t=20, b=20), yaxis_title="Count")
        st.plotly_chart(fig_mem, use_container_width=True)
    else:
        st.info("No memories extracted for selected sessions.")

    # ── Entity type breakdown ─────────────────────────────────────────────────
    st.markdown("**Entity type breakdown**")
    all_etypes = sorted({t for sid in selected_ids for t in cmp_stats[sid]["entity_by_type"]})
    if all_etypes:
        fig_ent = go.Figure()
        for sid in selected_ids:
            fig_ent.add_trace(go.Bar(
                name=short_labels[sid],
                x=all_etypes,
                y=[cmp_stats[sid]["entity_by_type"].get(t, 0) for t in all_etypes],
            ))
        fig_ent.update_layout(barmode="group", height=280, margin=dict(t=20, b=20), yaxis_title="Distinct entities")
        st.plotly_chart(fig_ent, use_container_width=True)
    else:
        st.info("No entity data for selected sessions.")

    # ── Token usage ───────────────────────────────────────────────────────────
    st.markdown("**Token usage**")
    token_categories = ["input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "thought_tokens"]
    tok_labels = ["Input", "Output", "Cache Read", "Cache Write", "Thoughts"]
    has_token_data = any(any(cmp_stats[sid][k] > 0 for k in token_categories) for sid in selected_ids)
    if has_token_data:
        fig_tok = go.Figure()
        for sid in selected_ids:
            s = cmp_stats[sid]
            model_label = f" ({s['dominant_model']})" if s.get("dominant_model") else ""
            fig_tok.add_trace(go.Bar(
                name=short_labels[sid] + model_label,
                x=tok_labels,
                y=[s[k] for k in token_categories],
            ))
        fig_tok.update_layout(barmode="group", height=320, margin=dict(t=20, b=20), yaxis_title="Tokens")
        st.plotly_chart(fig_tok, use_container_width=True)

        # Cache efficiency comparison
        col_cache, col_density = st.columns(2)
        with col_cache:
            st.markdown("**Cache hit rate**")
            cache_sids = [sid for sid in selected_ids if cmp_stats[sid]["cache_hit_pct"] is not None]
            fig_cache = go.Figure(go.Bar(
                x=[short_labels[sid] for sid in cache_sids],
                y=[cmp_stats[sid]["cache_hit_pct"] for sid in cache_sids],
                marker_color=["#22c55e" if cmp_stats[sid]["cache_hit_pct"] > 50 else "#f59e0b" for sid in cache_sids],
            ))
            fig_cache.update_layout(height=240, yaxis_title="Cache read / input %",
                                    yaxis_range=[0, 100], margin=dict(t=10, b=20))
            st.plotly_chart(fig_cache, use_container_width=True)
        with col_density:
            st.markdown("**Tokens per chunk**")
            dens_sids = [sid for sid in selected_ids if cmp_stats[sid]["tok_per_chunk"] is not None]
            fig_dens = go.Figure(go.Bar(
                x=[short_labels[sid] for sid in dens_sids],
                y=[cmp_stats[sid]["tok_per_chunk"] for sid in dens_sids],
            ))
            fig_dens.update_layout(height=240, yaxis_title="Tokens / chunk", margin=dict(t=10, b=20))
            st.plotly_chart(fig_dens, use_container_width=True)
    else:
        st.info("No token usage data available. Run `ats backfill-metadata` to populate.")

    # ── Pairwise embedding similarity ─────────────────────────────────────────
    st.markdown("**Pairwise structural embedding similarity**")
    st.caption("Cosine similarity of session structural embeddings — higher = sessions worked on similar code/context.")

    emb_rows = store.conn.execute(
        f"SELECT session_id, embedding FROM session_structural_embeddings "
        f"WHERE session_id IN ({','.join('?'*len(selected_ids))})",
        selected_ids,
    ).fetchall()
    emb_map = {r[0]: _decode(r[1]) for r in emb_rows}

    covered = [sid for sid in selected_ids if sid in emb_map]
    if len(covered) < 2:
        st.info("Need at least 2 sessions with structural embeddings. Run `ats embed` to populate.")
    else:
        labels = [short_labels[sid] for sid in covered]
        n = len(covered)
        matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    matrix[i][j] = 1.0
                elif j > i:
                    sim = _cosine(emb_map[covered[i]], emb_map[covered[j]])
                    matrix[i][j] = round(sim, 3)
                    matrix[j][i] = round(sim, 3)

        fig_sim = go.Figure(go.Heatmap(
            z=matrix,
            x=labels,
            y=labels,
            colorscale="Blues",
            zmin=0, zmax=1,
            text=[[f"{v:.2f}" for v in row] for row in matrix],
            texttemplate="%{text}",
        ))
        fig_sim.update_layout(height=max(300, n * 80), margin=dict(t=20, b=20))
        st.plotly_chart(fig_sim, use_container_width=True)

        if len(covered) < len(selected_ids):
            missing = [short_labels[sid] for sid in selected_ids if sid not in emb_map]
            st.caption(f"No structural embedding for: {', '.join(missing)}")


_render_session_compare_section()
