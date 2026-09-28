"""
Experiment: embed raw trace chunks with qwen3-embedding:0.6b via ollama,
cluster with HDBSCAN, reduce dims with UMAP, visualize + EDA.
"""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import requests
from sklearn.preprocessing import normalize

# ── config ────────────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent.parent / "traces.db"
MODEL = "qwen3-embedding:0.6b"
OLLAMA_URL = "http://localhost:11434/api/embed"
OUT_DIR = Path(__file__).parent
CACHE_PATH = OUT_DIR / "embed_cache_qwen3.npz"

# ── 1. load chunks ─────────────────────────────────────────────────────────────
con = sqlite3.connect(DB_PATH)
df = pd.read_sql_query(
    """
    SELECT r.id, r.session_id, r.chunk_index, r.chunk_text,
           s.source_plugin, s.session_timestamp
    FROM records r
    JOIN sessions s ON r.session_id = s.id
    ORDER BY r.session_id, r.chunk_index
    """,
    con,
)
con.close()

print(f"Loaded {len(df)} chunks from {df['session_id'].nunique()} sessions")
print(f"Chunk text length — mean:{df['chunk_text'].str.len().mean():.0f}  "
      f"min:{df['chunk_text'].str.len().min()}  max:{df['chunk_text'].str.len().max()}")

# ── 2. embed (with cache) ──────────────────────────────────────────────────────
if CACHE_PATH.exists():
    print(f"Loading embeddings from cache: {CACHE_PATH}")
    data = np.load(CACHE_PATH, allow_pickle=True)
    embeddings = data["embeddings"]
    cached_ids = list(data["ids"])
    # align to current df order
    id_to_idx = {v: i for i, v in enumerate(cached_ids)}
    order = [id_to_idx[i] for i in df["id"]]
    embeddings = embeddings[order]
else:
    print(f"Embedding {len(df)} chunks with {MODEL} …")
    embeddings = []
    for i, (_, row) in enumerate(df.iterrows()):
        text = row["chunk_text"][:4096]  # truncate to avoid OOM
        resp = requests.post(OLLAMA_URL, json={"model": MODEL, "input": text}, timeout=60)
        resp.raise_for_status()
        vec = resp.json()["embeddings"][0]
        embeddings.append(vec)
        if (i + 1) % 50 == 0 or i == 0:
            print(f"  {i+1}/{len(df)}")
    embeddings = np.array(embeddings, dtype=np.float32)
    np.savez(CACHE_PATH, embeddings=embeddings, ids=np.array(df["id"].tolist()))
    print(f"Saved embeddings to {CACHE_PATH}")

embeddings_norm = normalize(embeddings)
print(f"Embedding matrix: {embeddings_norm.shape}  dtype:{embeddings_norm.dtype}")

# ── 3. UMAP dim reduction ──────────────────────────────────────────────────────
print("Running UMAP …")
import umap

reducer = umap.UMAP(n_components=2, n_neighbors=10, min_dist=0.05,
                    metric="cosine", random_state=42)
coords = reducer.fit_transform(embeddings_norm)
df["umap_x"] = coords[:, 0]
df["umap_y"] = coords[:, 1]

# ── 4. HDBSCAN clustering ──────────────────────────────────────────────────────
print("Running HDBSCAN …")
import hdbscan

clusterer = hdbscan.HDBSCAN(min_cluster_size=5, min_samples=3,
                              metric="euclidean", cluster_selection_method="eom")
labels = clusterer.fit_predict(coords)
df["cluster"] = labels
n_clusters = (labels >= 0).sum() and len(set(labels[labels >= 0]))
n_noise = (labels == -1).sum()
print(f"Clusters: {n_clusters}  noise points: {n_noise}/{len(df)}")

# ── 5. EDA stats ───────────────────────────────────────────────────────────────
print("\n── Cluster sizes ──────────────────────────────────────────────────────")
cluster_counts = df.groupby("cluster").size().sort_values(ascending=False)
print(cluster_counts.to_string())

print("\n── Chunks per session ────────────────────────────────────────────────")
print(df.groupby("session_id")["cluster"].apply(
    lambda x: x.value_counts().to_dict()
).head(10).to_string())

print("\n── Chunk length stats by cluster ─────────────────────────────────────")
df["text_len"] = df["chunk_text"].str.len()
cstats = df.groupby("cluster")["text_len"].agg(["mean", "min", "max", "count"])
print(cstats.sort_values("count", ascending=False).to_string())

# ── 6. per-cluster keyword peek ────────────────────────────────────────────────
from collections import Counter
import re

STOPWORDS = set("""the a an and or is are was were be been being have has had do does did
    will would shall should may might can could not no nor so yet but if then than that
    this these those with for of to in on at by from as it its i you we they he she
    USER ASSISTANT tool result TOOL""".split())

def top_words(texts, n=8):
    words = re.findall(r"[a-z]{4,}", " ".join(texts).lower())
    freq = Counter(w for w in words if w not in STOPWORDS)
    return [w for w, _ in freq.most_common(n)]

print("\n── Top keywords per cluster ───────────────────────────────────────────")
for c, grp in df[df["cluster"] >= 0].groupby("cluster"):
    kw = top_words(grp["chunk_text"].tolist())
    print(f"  cluster {c:3d} (n={len(grp):3d}): {kw}")

# ── 7. visualizations ─────────────────────────────────────────────────────────
df["hover"] = (
    "session: " + df["session_id"].str[:12] + "<br>"
    + "chunk: " + df["chunk_index"].astype(str) + "<br>"
    + "cluster: " + df["cluster"].astype(str) + "<br>"
    + df["chunk_text"].str[:200].str.replace("\n", " ")
)

# 7a. scatter coloured by cluster
fig_cluster = px.scatter(
    df, x="umap_x", y="umap_y",
    color=df["cluster"].astype(str),
    hover_data={"hover": True, "umap_x": False, "umap_y": False},
    title=f"UMAP of {len(df)} trace chunks — coloured by HDBSCAN cluster",
    labels={"color": "cluster"},
    width=1000, height=700,
)
fig_cluster.update_traces(marker=dict(size=6, opacity=0.8))
out_cluster = OUT_DIR / "embed_clusters.html"
fig_cluster.write_html(out_cluster)
print(f"\nWrote {out_cluster}")

# 7b. scatter coloured by session
fig_session = px.scatter(
    df, x="umap_x", y="umap_y",
    color=df["session_id"].str[:8],
    hover_data={"hover": True, "umap_x": False, "umap_y": False},
    title="UMAP — coloured by session",
    labels={"color": "session"},
    width=1000, height=700,
)
fig_session.update_traces(marker=dict(size=6, opacity=0.8))
out_session = OUT_DIR / "embed_sessions.html"
fig_session.write_html(out_session)
print(f"Wrote {out_session}")

# 7c. cluster size bar chart
fig_bar = px.bar(
    cluster_counts.reset_index().rename(columns={"cluster": "cluster", 0: "count"}),
    x="cluster", y="count",
    title="Chunks per cluster",
    width=800, height=400,
)
out_bar = OUT_DIR / "embed_cluster_sizes.html"
fig_bar.write_html(out_bar)
print(f"Wrote {out_bar}")

# 7d. text-length distribution per cluster (box)
fig_len = px.box(
    df[df["cluster"] >= 0], x="cluster", y="text_len",
    title="Chunk text length by cluster",
    width=900, height=450,
)
out_len = OUT_DIR / "embed_length_by_cluster.html"
fig_len.write_html(out_len)
print(f"Wrote {out_len}")

# 7e. session-cluster heatmap
pivot = df.pivot_table(index="session_id", columns="cluster",
                        aggfunc="size", fill_value=0)
pivot.index = pivot.index.str[:12]
fig_heat = px.imshow(
    pivot,
    title="Session × Cluster chunk counts",
    aspect="auto",
    color_continuous_scale="Blues",
    width=1100, height=600,
)
out_heat = OUT_DIR / "embed_session_cluster_heatmap.html"
fig_heat.write_html(out_heat)
print(f"Wrote {out_heat}")

print("\nDone.")
