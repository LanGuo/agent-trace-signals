"""
Experiment: embed raw traces from data/archive with qwen3-embedding:0.6b.
Unit of analysis: session (all turns concatenated, smartly truncated).
Also does turn-level sampling for fine-grained cluster EDA.
"""

import json
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import requests
from sklearn.preprocessing import normalize

# ── config ────────────────────────────────────────────────────────────────────
ARCHIVE = Path("data/archive")
MODEL = "qwen3-embedding:0.6b"
OLLAMA_URL = "http://localhost:11434/api/embed"
OUT = Path("experiments")
SESSION_CACHE = OUT / "raw_session_embeddings.npz"
MAX_CHARS = 6000   # chars sent to embedder per session (head + tail)
MAX_CHARS_HEAD = 4000
MAX_CHARS_TAIL = 2000


# ── 1. load raw sessions ───────────────────────────────────────────────────────
def extract_text_claude(path: Path) -> str:
    """Flatten a claude_code .jsonl into human-readable turn text."""
    turns = []
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("type") not in ("user", "assistant"):
                continue
            role = r["type"].upper()
            content = r.get("message", {}).get("content", "")
            if isinstance(content, list):
                parts = []
                for c in content:
                    if isinstance(c, dict):
                        if c.get("type") == "text":
                            parts.append(c.get("text", ""))
                        elif c.get("type") == "tool_use":
                            parts.append(f"[TOOL:{c.get('name','')}]")
                        elif c.get("type") == "tool_result":
                            inner = c.get("content", "")
                            if isinstance(inner, list):
                                inner = " ".join(x.get("text","") for x in inner if isinstance(x,dict))
                            parts.append(f"[RESULT:{str(inner)[:200]}]")
                    else:
                        parts.append(str(c))
                content = " ".join(parts)
            turns.append(f"{role}: {content}")
    return "\n".join(turns)


def extract_text_gemini(path: Path) -> str:
    """Flatten a gemini_cli .json session."""
    with open(path) as f:
        data = json.load(f)
    turns = []
    for m in data.get("messages", []):
        role = m.get("type", "?").upper()
        content = m.get("content", "")
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    parts.append(c.get("text", str(c)))
                else:
                    parts.append(str(c))
            content = " ".join(parts)
        turns.append(f"{role}: {content}")
    return "\n".join(turns)


def truncate(text: str, head: int = MAX_CHARS_HEAD, tail: int = MAX_CHARS_TAIL) -> str:
    if len(text) <= head + tail:
        return text
    return text[:head] + "\n…\n" + text[-tail:]


sessions = []

for f in sorted(ARCHIVE.glob("**/*.jsonl")):
    raw = extract_text_claude(f)
    if len(raw) < 50:
        continue
    # derive project name from parent dir
    parent = f.parent.name
    project = parent if parent != "claude_code" else f.stem
    sessions.append({
        "id": f.stem,
        "source": "claude_code",
        "project": project,
        "file": str(f),
        "full_text": raw,
        "embed_text": truncate(raw),
        "n_chars": len(raw),
        "n_turns": raw.count("\nUSER:") + raw.count("\nASSISTANT:") + 1,
    })

for f in sorted(ARCHIVE.glob("**/*.json")):
    try:
        raw = extract_text_gemini(f)
    except Exception:
        continue
    if len(raw) < 50:
        continue
    parent = f.parent.name
    project = parent if parent not in ("gemini_cli",) else f.stem
    sessions.append({
        "id": f.stem,
        "source": "gemini_cli",
        "project": project,
        "file": str(f),
        "full_text": raw,
        "embed_text": truncate(raw),
        "n_chars": len(raw),
        "n_turns": raw.count("\nUSER:") + 1,
    })

df = pd.DataFrame(sessions)
print(f"Sessions loaded: {len(df)}")
print(df.groupby("source")[["n_chars","n_turns"]].mean().round(0))
print(f"\nProjects ({df['project'].nunique()}):")
print(df["project"].value_counts().to_string())

# ── 2. embed (with cache) ──────────────────────────────────────────────────────
def embed_text(text: str) -> list[float]:
    resp = requests.post(OLLAMA_URL, json={"model": MODEL, "input": text[:8000]}, timeout=120)
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


if SESSION_CACHE.exists():
    print("\nLoading session embeddings from cache …")
    cache = np.load(SESSION_CACHE, allow_pickle=True)
    cached_ids = list(cache["ids"])
    cached_embs = cache["embeddings"]
    id_to_emb = {i: e for i, e in zip(cached_ids, cached_embs)}
    # embed any new sessions not in cache
    new_ids = [s for s in df["id"] if s not in id_to_emb]
    if new_ids:
        print(f"  Embedding {len(new_ids)} new sessions …")
        for sid in new_ids:
            text = df.loc[df["id"] == sid, "embed_text"].iloc[0]
            id_to_emb[sid] = embed_text(text)
        cached_ids = list(id_to_emb.keys())
        cached_embs = np.array([id_to_emb[i] for i in cached_ids], dtype=np.float32)
        np.savez(SESSION_CACHE, embeddings=cached_embs, ids=np.array(cached_ids))
    embeddings = np.array([id_to_emb[sid] for sid in df["id"]], dtype=np.float32)
else:
    print(f"\nEmbedding {len(df)} sessions with {MODEL} …")
    vecs = []
    for i, (_, row) in enumerate(df.iterrows()):
        vecs.append(embed_text(row["embed_text"]))
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  {i+1}/{len(df)}")
    embeddings = np.array(vecs, dtype=np.float32)
    np.savez(SESSION_CACHE, embeddings=embeddings, ids=np.array(df["id"].tolist()))
    print(f"Saved to {SESSION_CACHE}")

embeddings_norm = normalize(embeddings)
print(f"\nEmbedding matrix: {embeddings_norm.shape}")

# ── 3. UMAP ────────────────────────────────────────────────────────────────────
print("Running UMAP …")
import umap

reducer2d = umap.UMAP(n_components=2, n_neighbors=8, min_dist=0.1,
                      metric="cosine", random_state=42)
coords2d = reducer2d.fit_transform(embeddings_norm)
df["umap_x"] = coords2d[:, 0]
df["umap_y"] = coords2d[:, 1]

# ── 4. HDBSCAN ─────────────────────────────────────────────────────────────────
print("Running HDBSCAN …")
import hdbscan

clusterer = hdbscan.HDBSCAN(min_cluster_size=3, min_samples=2,
                              metric="euclidean", cluster_selection_method="eom")
df["cluster"] = clusterer.fit_predict(coords2d)
n_clusters = len(set(df["cluster"][df["cluster"] >= 0]))
n_noise = (df["cluster"] == -1).sum()
print(f"Clusters: {n_clusters}  noise: {n_noise}/{len(df)}")

# ── 5. EDA ─────────────────────────────────────────────────────────────────────
print("\n── Cluster contents ──────────────────────────────────────────────────")
for c, grp in df.groupby("cluster"):
    label = "NOISE" if c == -1 else f"cluster {c:2d}"
    projs = grp["project"].tolist()
    sources = grp["source"].tolist()
    print(f"  {label} (n={len(grp)}): {projs}  [{set(sources)}]")

# pairwise cosine similarity matrix
from sklearn.metrics.pairwise import cosine_similarity
sim = cosine_similarity(embeddings_norm)
np.fill_diagonal(sim, np.nan)
print(f"\nInter-session cosine sim — mean:{np.nanmean(sim):.3f}  "
      f"max:{np.nanmax(sim):.3f}  min:{np.nanmin(sim):.3f}")

# within-cluster vs between-cluster similarity
within, between = [], []
for i in range(len(df)):
    for j in range(i+1, len(df)):
        s = cosine_similarity(embeddings_norm[i:i+1], embeddings_norm[j:j+1])[0,0]
        if df["cluster"].iloc[i] == df["cluster"].iloc[j] and df["cluster"].iloc[i] >= 0:
            within.append(s)
        else:
            between.append(s)
print(f"Within-cluster sim: mean={np.mean(within):.3f}  n={len(within)}")
print(f"Between-cluster sim: mean={np.mean(between):.3f}  n={len(between)}")

# ── 6. keyword top words per cluster ──────────────────────────────────────────
STOPWORDS = set("""the a an and or is are was were be been being have has had do does did
will would shall should may might can could not no nor so yet but if then than that
this these those with for of to in on at by from as it its i you we they he she
user assistant tool result bash read write edit file path command claude languo users
type text content true false null list let result error output import print return
string none what your""".split())

def top_words(texts, n=10):
    words = re.findall(r"[a-z]{4,}", " ".join(texts).lower())
    freq = Counter(w for w in words if w not in STOPWORDS)
    return [f"{w}({c})" for w, c in freq.most_common(n)]

print("\n── Top keywords per cluster ───────────────────────────────────────────")
for c, grp in df[df["cluster"] >= 0].groupby("cluster"):
    kw = top_words(grp["embed_text"].tolist())
    projs = ", ".join(grp["project"].tolist())
    print(f"  cluster {c:2d} (n={len(grp)}): {kw}")
    print(f"           projects: {projs}")

# ── 7. session length distribution ────────────────────────────────────────────
print("\n── Session stats ─────────────────────────────────────────────────────")
print(df[["source","n_chars","n_turns"]].groupby("source").describe().round(0).to_string())

# ── 8. visualizations ─────────────────────────────────────────────────────────
df["hover"] = (
    "<b>" + df["project"] + "</b><br>"
    + "source: " + df["source"] + "<br>"
    + "cluster: " + df["cluster"].astype(str) + "<br>"
    + "turns: " + df["n_turns"].astype(str) + "<br>"
    + "chars: " + df["n_chars"].astype(str) + "<br>"
    + df["embed_text"].str[:300].str.replace("\n", " ")
)

# 8a. by cluster
fig = px.scatter(
    df, x="umap_x", y="umap_y",
    color=df["cluster"].astype(str),
    text="project",
    hover_data={"hover": True, "umap_x": False, "umap_y": False, "project": False},
    title=f"UMAP of {len(df)} raw sessions — coloured by cluster (qwen3-embedding:0.6b)",
    width=1100, height=750,
)
fig.update_traces(marker=dict(size=10, opacity=0.85), textposition="top center",
                  textfont=dict(size=8))
fig.write_html(OUT / "raw_session_clusters.html")
print("\nWrote raw_session_clusters.html")

# 8b. by source
fig2 = px.scatter(
    df, x="umap_x", y="umap_y",
    color="source",
    text="project",
    hover_data={"hover": True, "umap_x": False, "umap_y": False, "project": False},
    title="UMAP — coloured by source (claude_code vs gemini_cli)",
    width=1100, height=750,
)
fig2.update_traces(marker=dict(size=10, opacity=0.85), textposition="top center",
                   textfont=dict(size=8))
fig2.write_html(OUT / "raw_session_sources.html")
print("Wrote raw_session_sources.html")

# 8c. session length vs cluster
fig3 = px.scatter(
    df, x="n_turns", y="n_chars",
    color=df["cluster"].astype(str),
    text="project",
    hover_data={"hover": True},
    title="Session length (chars vs turns) coloured by cluster",
    width=900, height=550,
)
fig3.update_traces(marker=dict(size=8, opacity=0.8), textposition="top center",
                   textfont=dict(size=7))
fig3.write_html(OUT / "raw_session_length_scatter.html")
print("Wrote raw_session_length_scatter.html")

# 8d. pairwise similarity heatmap
import plotly.graph_objects as go
labels = df["project"].tolist()
fig4 = go.Figure(go.Heatmap(
    z=sim, x=labels, y=labels,
    colorscale="RdBu", zmid=0,
    text=np.round(sim, 2),
))
fig4.update_layout(title="Pairwise cosine similarity — raw sessions",
                   width=1100, height=1000,
                   xaxis=dict(tickfont=dict(size=7)),
                   yaxis=dict(tickfont=dict(size=7)))
fig4.write_html(OUT / "raw_session_similarity_heatmap.html")
print("Wrote raw_session_similarity_heatmap.html")

print("\nDone.")
