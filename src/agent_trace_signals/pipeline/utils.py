"""Shared pipeline utilities."""

from __future__ import annotations

import random
import re
from datetime import datetime, timezone


def format_workspace(workspace_id: str) -> str:
    """Extract a human-readable / normalized project name from a workspace_id.

    Claude Code workspace_ids are encoded paths with / replaced by -,
    e.g. '-Users-languo-src-agent-trace-signals'. Other harnesses (opencode,
    archived legacy files) often store the bare project name instead, e.g.
    'agent-trace-signals'. Normalizing both to the same form is what lets
    same-project sessions link up across harnesses (see same_workspace
    edges in ingestion.py) — this is not just a display helper.
    """
    if not workspace_id:
        return ""
    # Already a simple name (Gemini sessions, etc.)
    if "/" in workspace_id:
        return workspace_id.split("/")[-1]
    # Strip leading -Users-<name>- or -home-<name>- prefix
    cleaned = re.sub(r'^-(?:Users|home)-[^-]+-', '', workspace_id)
    # Strip leading -src- or -Documents- etc.
    cleaned = re.sub(r'^-?(?:src|Documents|projects|home|desktop)-', '', cleaned, flags=re.IGNORECASE)
    return cleaned.lstrip("-") or workspace_id


_TRIVIAL_PATTERNS = (
    "works correctly",
    "works.",
    "works!",
    "was created successfully",
    "is correct",
    "completed successfully",
    "succeeded",
    "the file",
    "the directory",
    "the working directory",
)

_MIN_CONTENT_WORDS = 6


def build_structural_text(raw_texts: list[str]) -> str:
    """Build concatenated text for structural embedding from a list of chunk texts.

    Samples: first chunk + up to 8 order-preserving randomly sampled middle chunks
    + last chunk. Truncates to 2000 chars if over limit.
    """
    if not raw_texts:
        return ""
    if len(raw_texts) <= 2:
        sampled = raw_texts
    else:
        middle = raw_texts[1:-1]
        k = min(8, len(middle))
        indices = sorted(random.sample(range(len(middle)), k))
        sampled = [raw_texts[0]] + [middle[i] for i in indices] + [raw_texts[-1]]
    concat = "\n\n".join(sampled)
    if len(concat) > 2000:
        concat = concat[:2000]
    return concat


def _is_trivial(content: str) -> bool:
    """Return True if a memory is too generic to be worth storing."""
    lower = content.lower().strip()
    if len(lower.split()) < _MIN_CONTENT_WORDS:
        return True
    return any(p in lower for p in _TRIVIAL_PATTERNS)


def build_structural_similarity_edges(
    session_vecs: list[tuple[str, list[float]]],
    top_k: int = 8,
    min_similarity: float = 0.5,
) -> list:
    """Build session_graph_edges[structural_similarity] from structural embeddings.

    Full pairwise recompute every call (no incremental state) — cheap at this
    corpus's scale (O(n^2) cosine over a few hundred sessions) and side-steps
    the staleness problem of a persisted-but-versioned graph: there is nothing
    to go stale, since the caller replaces the entire edge_type each run rather
    than accumulating across runs. Each session keeps only its top_k neighbours
    above min_similarity, both directions inserted (edges are stored directed
    but conceptually undirected, matching the other session_graph_edges types).
    """
    from agent_trace_signals.models import SessionGraphEdge
    import math

    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0

    now = datetime.now(timezone.utc).isoformat()
    edges = []
    for i, (sid_a, vec_a) in enumerate(session_vecs):
        sims = []
        for j, (sid_b, vec_b) in enumerate(session_vecs):
            if i == j:
                continue
            sim = _cosine(vec_a, vec_b)
            if sim >= min_similarity:
                sims.append((sim, sid_b))
        sims.sort(key=lambda x: x[0], reverse=True)
        for sim, sid_b in sims[:top_k]:
            edges.append(SessionGraphEdge(
                source_session_id=sid_a,
                target_session_id=sid_b,
                edge_type="structural_similarity",
                via_entity_id="",
                weight=sim,
                direction="undirected",
                created_by="embed",
                created_at=now,
            ))
    return edges
