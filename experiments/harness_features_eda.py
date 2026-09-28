"""
Analyze whether cluster membership is driven by topic or agent harness structure
(tool call count, human message count, tool diversity, etc.)
"""

import json
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd
import plotly.express as px
from scipy import stats
from sklearn.ensemble import RandomForestClassifier

ARCHIVE = Path("data/archive")
OUT = Path("experiments")

# ── 1. extract structural features from raw files ─────────────────────────────

def features_claude(path: Path) -> dict:
    n_human = 0          # user turns (real prompts, not tool results)
    n_assistant = 0
    n_tool_calls = 0
    tool_names = []
    n_tool_results = 0
    assistant_text_len = 0
    human_text_len = 0
    n_attachments = 0
    n_sidechain = 0      # isSidechain turns

    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            t = r.get("type", "")

            if t == "user":
                n_human += 1
                content = r.get("message", {}).get("content", "")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict):
                            if c.get("type") == "tool_result":
                                n_tool_results += 1
                            elif c.get("type") == "text":
                                human_text_len += len(c.get("text", ""))
                        else:
                            human_text_len += len(str(c))
                else:
                    human_text_len += len(str(content))

            elif t == "assistant":
                n_assistant += 1
                content = r.get("message", {}).get("content", "")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict):
                            if c.get("type") == "tool_use":
                                n_tool_calls += 1
                                tool_names.append(c.get("name", "unknown"))
                            elif c.get("type") == "text":
                                assistant_text_len += len(c.get("text", ""))
                else:
                    assistant_text_len += len(str(content))

                if r.get("isSidechain"):
                    n_sidechain += 1

            elif t == "attachment":
                n_attachments += 1

    tool_counter = Counter(tool_names)
    n_unique_tools = len(tool_counter)
    top_tool = tool_counter.most_common(1)[0][0] if tool_counter else "none"

    # real human turns = user turns that are NOT just tool results
    # approximate: n_human - n_tool_results (each tool result comes in a user message)
    # but tool results can be batched, so use n_human as upper bound
    real_human_turns = max(0, n_human - n_tool_results)

    return dict(
        n_human_turns=n_human,
        real_human_turns=real_human_turns,
        n_assistant_turns=n_assistant,
        n_tool_calls=n_tool_calls,
        n_tool_results=n_tool_results,
        n_unique_tools=n_unique_tools,
        n_attachments=n_attachments,
        n_sidechain=n_sidechain,
        tool_call_rate=n_tool_calls / max(n_assistant, 1),
        human_tool_ratio=real_human_turns / max(n_tool_calls, 1),
        assistant_text_len=assistant_text_len,
        human_text_len=human_text_len,
        top_tool=top_tool,
        tool_names_csv=",".join(sorted(set(tool_names))),
    )


def features_gemini(path: Path) -> dict:
    with open(path) as f:
        data = json.load(f)

    n_human = 0
    n_assistant = 0
    n_tool_calls = 0
    tool_names = []
    assistant_text_len = 0
    human_text_len = 0
    n_thoughts = 0

    for m in data.get("messages", []):
        t = m.get("type", "")
        if t == "user":
            n_human += 1
            content = m.get("content", "")
            if isinstance(content, list):
                human_text_len += sum(len(c.get("text","")) for c in content if isinstance(c,dict))
            else:
                human_text_len += len(str(content))
        elif t == "gemini":
            n_assistant += 1
            content = m.get("content", "")
            assistant_text_len += len(str(content))
            for tc in m.get("toolCalls", []):
                n_tool_calls += 1
                tool_names.append(tc.get("name", "unknown"))
            if m.get("thoughts"):
                n_thoughts += len(m["thoughts"])

    tool_counter = Counter(tool_names)
    n_unique_tools = len(tool_counter)
    top_tool = tool_counter.most_common(1)[0][0] if tool_counter else "none"

    return dict(
        n_human_turns=n_human,
        real_human_turns=n_human,
        n_assistant_turns=n_assistant,
        n_tool_calls=n_tool_calls,
        n_tool_results=n_tool_calls,  # 1:1 in gemini format
        n_unique_tools=n_unique_tools,
        n_attachments=0,
        n_sidechain=n_thoughts,       # repurpose: thinking turns
        tool_call_rate=n_tool_calls / max(n_assistant, 1),
        human_tool_ratio=n_human / max(n_tool_calls, 1),
        assistant_text_len=assistant_text_len,
        human_text_len=human_text_len,
        top_tool=top_tool,
        tool_names_csv=",".join(sorted(set(tool_names))),
    )


rows = []

for f in sorted(ARCHIVE.glob("**/*.jsonl")):
    raw = features_claude(f)
    if raw["n_human_turns"] == 0 and raw["n_assistant_turns"] == 0:
        continue
    parent = f.parent.name
    project = parent if parent != "claude_code" else f.stem
    rows.append({"id": f.stem, "source": "claude_code", "project": project, "file": str(f), **raw})

for f in sorted(ARCHIVE.glob("**/*.json")):
    try:
        raw = features_gemini(f)
    except Exception:
        continue
    if raw["n_human_turns"] == 0:
        continue
    parent = f.parent.name
    project = parent if parent not in ("gemini_cli",) else f.stem
    rows.append({"id": f.stem, "source": "gemini_cli", "project": project, "file": str(f), **raw})

feat_df = pd.DataFrame(rows)
print(f"Sessions with features: {len(feat_df)}")

# ── 2. load cluster labels from prior UMAP run ────────────────────────────────
# Re-run UMAP/HDBSCAN inline so cluster labels align
cache = np.load(OUT / "raw_session_embeddings.npz", allow_pickle=True)
cached_ids = list(cache["ids"])
cached_embs = cache["embeddings"]

from sklearn.preprocessing import normalize
import umap
import hdbscan

emb_norm = normalize(cached_embs.astype(np.float32))
reducer = umap.UMAP(n_components=2, n_neighbors=8, min_dist=0.1, metric="cosine", random_state=42)
coords = reducer.fit_transform(emb_norm)
clusterer = hdbscan.HDBSCAN(min_cluster_size=3, min_samples=2, metric="euclidean",
                              cluster_selection_method="eom")
cluster_labels = clusterer.fit_predict(coords)

cluster_df = pd.DataFrame({
    "id": cached_ids,
    "umap_x": coords[:, 0],
    "umap_y": coords[:, 1],
    "cluster": cluster_labels,
})

# merge
df = cluster_df.merge(feat_df, on="id", how="inner")
print(f"Merged: {len(df)} sessions  clusters: {df['cluster'].nunique()}")

# ── 3. per-cluster structural stats ───────────────────────────────────────────
STRUCT_COLS = ["real_human_turns", "n_tool_calls", "n_unique_tools",
               "tool_call_rate", "human_tool_ratio", "n_assistant_turns",
               "n_sidechain", "assistant_text_len"]

print("\n── Mean structural features per cluster ──────────────────────────────")
summary = df.groupby("cluster")[STRUCT_COLS].mean().round(1)
summary["n"] = df.groupby("cluster").size()
print(summary.sort_values("cluster").to_string())

# ── 4. kruskal-wallis: does cluster explain variance in each feature? ──────────
print("\n── Kruskal-Wallis: does cluster membership predict each feature? ──────")
for col in STRUCT_COLS:
    groups = [g[col].dropna().values for _, g in df.groupby("cluster")]
    groups = [g for g in groups if len(g) > 1]
    stat, p = stats.kruskal(*groups)
    sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
    print(f"  {col:<25s}  H={stat:.1f}  p={p:.4f}  {sig}")

# ── 5. feature importance via random forest ───────────────────────────────────
print("\n── Random Forest feature importance (predicting cluster) ─────────────")
X = df[STRUCT_COLS].fillna(0).values
y = df["cluster"].values
rf = RandomForestClassifier(n_estimators=200, random_state=42)
rf.fit(X, y)
importances = pd.Series(rf.feature_importances_, index=STRUCT_COLS).sort_values(ascending=False)
print(importances.round(4).to_string())

# ── 6. correlation: umap coords vs features ───────────────────────────────────
print("\n── Spearman correlation: UMAP coords vs structural features ──────────")
for col in STRUCT_COLS:
    rx, pxv = stats.spearmanr(df["umap_x"], df[col])
    ry, pyv = stats.spearmanr(df["umap_y"], df[col])
    print(f"  {col:<25s}  umap_x r={rx:+.3f}(p={pxv:.3f})  umap_y r={ry:+.3f}(p={pyv:.3f})")

# ── 7. visualizations ─────────────────────────────────────────────────────────

# 7a. UMAP coloured by n_tool_calls
df["hover"] = df["project"] + " | tools=" + df["n_tool_calls"].astype(str) + " | human=" + df["real_human_turns"].astype(str)
fig = px.scatter(
    df, x="umap_x", y="umap_y",
    color="n_tool_calls",
    text="project",
    hover_data={"hover": True, "umap_x": False, "umap_y": False, "project": False},
    color_continuous_scale="Viridis",
    title="UMAP coloured by total tool calls per session",
    width=1000, height=700,
)
fig.update_traces(marker=dict(size=10, opacity=0.85), textposition="top center", textfont=dict(size=7))
fig.write_html(OUT / "harness_umap_tool_calls.html")

fig2 = px.scatter(
    df, x="umap_x", y="umap_y",
    color="real_human_turns",
    text="project",
    hover_data={"hover": True, "umap_x": False, "umap_y": False, "project": False},
    color_continuous_scale="Plasma",
    title="UMAP coloured by real human turns per session",
    width=1000, height=700,
)
fig2.update_traces(marker=dict(size=10, opacity=0.85), textposition="top center", textfont=dict(size=7))
fig2.write_html(OUT / "harness_umap_human_turns.html")

fig3 = px.scatter(
    df, x="umap_x", y="umap_y",
    color="tool_call_rate",
    text="project",
    hover_data={"hover": True, "umap_x": False, "umap_y": False, "project": False},
    color_continuous_scale="RdYlGn",
    title="UMAP coloured by tool_call_rate (tool calls / assistant turns)",
    width=1000, height=700,
)
fig3.update_traces(marker=dict(size=10, opacity=0.85), textposition="top center", textfont=dict(size=7))
fig3.write_html(OUT / "harness_umap_tool_rate.html")

# 7b. boxplots per cluster for key features
for col, title in [
    ("n_tool_calls", "Tool calls per session by cluster"),
    ("real_human_turns", "Real human turns by cluster"),
    ("tool_call_rate", "Tool call rate (calls/assistant turn) by cluster"),
    ("human_tool_ratio", "Human/tool ratio by cluster"),
    ("n_unique_tools", "Unique tool types by cluster"),
]:
    fig_b = px.box(
        df, x="cluster", y=col, points="all",
        hover_data=["project", "source"],
        title=title, width=950, height=480,
    )
    fig_b.write_html(OUT / f"harness_box_{col}.html")

print("\nWrote harness_*.html files")

# 7c. scatter: tool calls vs human turns coloured by cluster
fig4 = px.scatter(
    df, x="real_human_turns", y="n_tool_calls",
    color=df["cluster"].astype(str),
    text="project",
    hover_data={"hover": True},
    title="Human turns vs Tool calls — coloured by cluster",
    labels={"x": "real human turns", "y": "n tool calls"},
    width=1000, height=650,
)
fig4.update_traces(marker=dict(size=9, opacity=0.85), textposition="top center", textfont=dict(size=7))
fig4.write_html(OUT / "harness_human_vs_tools_scatter.html")
print("Wrote harness_human_vs_tools_scatter.html")

# 7d. feature importance bar
imp_df = importances.reset_index(); imp_df.columns = ["feature", "importance"]
fig5 = px.bar(imp_df, x="feature", y="importance",
              title="RF feature importance: which structural features predict cluster?",
              labels={"feature": "feature", "importance": "importance"},
              width=800, height=400)
fig5.write_html(OUT / "harness_rf_importance.html")
print("Wrote harness_rf_importance.html")

print("\nDone.")
