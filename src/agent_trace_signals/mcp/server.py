"""MCP server exposing ATS retrieval tools to Claude Code."""

from __future__ import annotations
import json
import os

from mcp.server.fastmcp import FastMCP

from agent_trace_signals.config import ModelConfig
from agent_trace_signals.db.schema import open_db
from agent_trace_signals.db.store import SQLiteStore
from agent_trace_signals.embedder import Embedder
from agent_trace_signals.pipeline.retrieval import RetrievalEngine

mcp = FastMCP("ats-memory")

_engine: RetrievalEngine | None = None


def _get_engine() -> RetrievalEngine:
    global _engine
    if _engine is None:
        db_path = os.environ.get("ATS_DB", "ats.db")
        conn = open_db(db_path)
        store = SQLiteStore(conn)
        _engine = RetrievalEngine(store, Embedder(ModelConfig()))
    return _engine


@mcp.tool()
def recall(
    query: str,
    memory_type: str | None = None,
    top_k: int = 10,
    include_graph: bool = False,
    workspace: str | None = None,
) -> str:
    """Retrieve relevant memories from agent session history using hybrid BM25 + semantic search.

    Args:
        query: Natural language query (e.g. "PDF rendering error", "auth bug pattern")
        memory_type: Optional filter — 'episodic', 'procedural', or 'preference'
        top_k: Number of memories to return (default 10)
        include_graph: Whether to expand results with graph-connected memories
        workspace: Optional — restrict to one project/workspace (substring match)

    Returns:
        JSON list of memories with content, type, extraction_method, and rrf_score
    """
    engine = _get_engine()
    results = engine.recall(query, memory_type=memory_type, top_k=top_k, include_graph=include_graph, workspace_id=workspace)
    output = [
        {
            "id": m["id"],
            "content": m["content"],
            "memory_type": m["memory_type"],
            "extraction_method": m.get("extraction_method"),
            "action_orientation": m.get("action_orientation"),
            "evidence_count": m.get("evidence_count"),
            "rrf_score": round(m["rrf_score"], 6),
        }
        for m in results
    ]
    return json.dumps(output, indent=2)


@mcp.tool()
def chunk_search(
    query: str,
    session_id: str | None = None,
    top_k: int = 20,
    include_graph: bool = False,
    workspace: str | None = None,
) -> str:
    """Search raw conversation chunks from agent session history using hybrid BM25 + semantic search.

    Args:
        query: Natural language query
        session_id: Optional — restrict to a specific session (full ID)
        top_k: Number of chunks to return (default 20)
        include_graph: Expand top hits with chunks (from any session) that mention
            the same entity — a cross-session hop via shared file/commit/PR/etc.
        workspace: Optional — restrict to one project/workspace (substring match)

    Returns:
        JSON list of chunks with chunk_text, session_id, chunk_index, and rrf_score
    """
    engine = _get_engine()
    results = engine.chunk_search(query, session_id=session_id, top_k=top_k, include_graph=include_graph, workspace_id=workspace)
    output = [
        {
            "id": r["id"],
            "session_id": r["session_id"],
            "chunk_index": r["chunk_index"],
            "chunk_text": r["chunk_text"][:500],
            "rrf_score": round(r["rrf_score"], 6),
        }
        for r in results
    ]
    return json.dumps(output, indent=2)


@mcp.tool()
def graph_walk(seed_memory_id: str, depth: int = 2) -> str:
    """Walk the session graph from a seed memory to find related memories.

    Args:
        seed_memory_id: Full memory ID to start from
        depth: BFS depth (1 = direct neighbours, 2 = neighbours of neighbours)

    Returns:
        JSON list of related memories
    """
    engine = _get_engine()
    results = engine.graph_walk(seed_memory_id, depth=depth)
    return json.dumps(
        [{"id": m["id"], "content": m["content"], "memory_type": m["memory_type"]}
         for m in results],
        indent=2,
    )


def run() -> None:
    mcp.run()


if __name__ == "__main__":
    run()
