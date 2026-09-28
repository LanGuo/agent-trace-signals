"""Head-to-head retrieval comparison: raw session grep vs. design-log grep vs. ats (MCP surface).

Rerun after two production fixes landed (2026-07-27/28): RRF guaranteed-slot fix and
workspace-scoped filtering. Uses the "full potential" ats configuration: both recall and
chunk_search (an agent has both via MCP), workspace filter restricted to this project, and
graph expansion enabled on both.

Ground truth (which chunk/memory ids actually contain each fact) was hand-verified earlier
in the investigation by joining memory_sources.record_id back to the exact source chunk and
reading every memory minted from it in full — not by keyword regex, which produced false
negatives when memories paraphrased away from a literal code symbol or number (see
FINDINGS.md). manifest.json in this directory is that verified ground truth.

Run: ATS_DB=traces.db uv run python experiments/exp10_retrieval_h2h/run_h2h.py
"""
from __future__ import annotations
import json
import subprocess
import time
from pathlib import Path

import tiktoken

from agent_trace_signals.config import ModelConfig
from agent_trace_signals.db.schema import open_db
from agent_trace_signals.db.store import SQLiteStore
from agent_trace_signals.embedder import Embedder
from agent_trace_signals.pipeline.retrieval import RetrievalEngine

HERE = Path(__file__).parent
REPO_ROOT = Path(__file__).resolve().parents[2]
SESSDIR = Path.home() / ".claude/projects/-Users-languo-src-agent-trace-signals"
DESIGN_LOG = REPO_ROOT / "design/design_decisions.md"
WORKSPACE_FILTER = "agent-trace-signals"

enc = tiktoken.get_encoding("cl100k_base")
manifest = json.loads((HERE / "manifest.json").read_text())

# fact -> (precise query, fuzzy query, naive grep keyword for precise, naive grep keyword for fuzzy)
CASES = {
    "genuine_types": (
        "exchange classifier genuine vs scorable types",
        "how did we decide which turns count as real activity in a session",
        "GENUINE_TYPES", "real activity turns",
    ),
    "per_session_mint": (
        "why was the cross-session recurrence threshold removed for minting memories",
        "why did we stop requiring multiple sessions before saving a memory",
        "cross-session threshold", "stop requiring multiple sessions",
    ),
    "min_entity_name_length": (
        "minimum entity name length filter for extraction noise",
        "how did we clean up junk short entity names",
        "MIN_ENTITY_NAME_LENGTH", "junk short entity names",
    ),
    "gemini_jsonl_streaming": (
        "gemini cli sessions written as jsonl streaming format",
        "why did some gemini session files look different from others",
        "jsonl streaming format", "gemini session files different",
    ),
    "thinking_blocks": (
        "include model thinking blocks in exchange serialization",
        "why do we capture the model's internal reasoning in the transcript",
        "[thinking] block", "internal reasoning transcript",
    ),
    "qwen3_embedding": (
        "embedding model used for clustering raw traces experiment",
        "what did the raw trace clustering experiment find",
        "qwen3-embedding", "raw trace clustering experiment",
    ),
    "related_sessions_tab": (
        "why was the related sessions page folded into session detail",
        "what happened to the standalone related sessions page",
        "Related Sessions page", "standalone related sessions",
    ),
    "memory_map_umap": (
        "memory page embedding UMAP corpus wide view",
        "how can I see all memories laid out visually in one place",
        "memory UMAP", "all memories laid out visually",
    ),
    "query_answer_model": (
        "query answer model attribute error config",
        "why did generate answer break on the query page",
        "query_answer_model AttributeError", "generate answer break query page",
    ),
    "taxonomy_v2": (
        "what changed in the taxonomy v2 redesign",
        "how did we rethink what's worth extracting from sessions",
        "taxonomy v2 redesign", "rethink what's worth extracting",
    ),
    "dup_33_cleanup": (
        "how were the 33 duplicate session pairs cleaned up",
        "how did we get rid of sessions that were counted twice",
        "33 duplicate session pairs", "sessions counted twice",
    ),
    "eom_vs_leaf": (
        "why did session clustering switch from eom to leaf",
        "why did the clustering feature get changed",
        "cluster_selection_method", "clustering feature changed",
    ),
    "ollama_read_timeout": (
        "ollama timeout bug connect vs read",
        "why were requests to the model server failing",
        "ollama read timeout", "requests to model server failing",
    ),
    "d_prompt_audit": (
        "what bugs were found when auditing the combined extraction prompt",
        "what's wrong with how we pull facts out of conversations",
        "extraction audit bugs", "pull facts out of conversations",
    ),
}


def toks(s: str) -> int:
    return len(enc.encode(s))


def recall_wire_format(results: list[dict]) -> str:
    """Exact shape mcp/server.py's recall() tool returns to the calling agent."""
    output = [
        {
            "id": m["id"], "content": m["content"], "memory_type": m["memory_type"],
            "extraction_method": m.get("extraction_method"), "action_orientation": m.get("action_orientation"),
            "evidence_count": m.get("evidence_count"), "rrf_score": round(m["rrf_score"], 6),
        }
        for m in results
    ]
    return json.dumps(output, indent=2)


def chunk_wire_format(results: list[dict]) -> str:
    """Exact shape mcp/server.py's chunk_search() tool returns — chunk_text truncated to 500 chars."""
    output = [
        {
            "id": r["id"], "session_id": r["session_id"], "chunk_index": r["chunk_index"],
            "chunk_text": r["chunk_text"][:500], "rrf_score": round(r["rrf_score"], 6),
        }
        for r in results
    ]
    return json.dumps(output, indent=2)


def grep_case(keyword: str, path_glob: str):
    cmd = f'grep -rni "{keyword}" {path_glob}'
    t0 = time.time()
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    dt = time.time() - t0
    out = p.stdout + p.stderr
    return dt, toks(out), bool(p.stdout.strip())


def main():
    conn = open_db(str(REPO_ROOT / "traces.db"))
    store = SQLiteStore(conn)
    engine = RetrievalEngine(store, Embedder(ModelConfig()))

    results = []
    for fact, (precise_q, fuzzy_q, precise_kw, fuzzy_kw) in CASES.items():
        gt = manifest[fact]
        gt_mem = set(gt["memory_ids"])
        gt_rec = set(gt["record_ids"])

        for variant, query, kw in [("precise", precise_q, precise_kw), ("fuzzy", fuzzy_q, fuzzy_kw)]:
            row = {"fact": fact, "variant": variant, "query": query, "keyword": kw}

            dt, tok, hit = grep_case(kw, f'{SESSDIR}/*.jsonl')
            row["raw_grep_latency_s"] = round(dt, 3)
            row["raw_grep_tokens"] = tok
            row["raw_grep_hit"] = hit

            dt, tok, hit = grep_case(kw, str(DESIGN_LOG))
            row["design_log_latency_s"] = round(dt, 3)
            row["design_log_tokens"] = tok
            row["design_log_hit"] = hit

            # Primary comparison: workspace filter on, top-k as requested, NO graph
            # expansion — include_graph appends a whole extra hop of graph-connected
            # results with no size cap, so "rank" inside that expanded tail is not
            # a meaningful hit (an agent isn't reading item #2052). Graph expansion's
            # actual effect is measured separately below.
            t0 = time.time()
            recall_res = engine.recall(query, top_k=5, method="hybrid",
                                        workspace_id=WORKSPACE_FILTER, include_graph=False)
            dt = time.time() - t0
            recall_out = recall_wire_format(recall_res)
            rank = next((i + 1 for i, r in enumerate(recall_res) if r["id"] in gt_mem), None)
            row["ats_recall_latency_s"] = round(dt, 3)
            row["ats_recall_tokens"] = toks(recall_out)
            row["ats_recall_rank"] = rank

            t0 = time.time()
            chunk_res = engine.chunk_search(query, top_k=10, method="hybrid",
                                             workspace_id=WORKSPACE_FILTER, include_graph=False)
            dt = time.time() - t0
            chunk_out = chunk_wire_format(chunk_res)
            rank = next((i + 1 for i, r in enumerate(chunk_res) if r["id"] in gt_rec), None)
            row["ats_chunk_latency_s"] = round(dt, 3)
            row["ats_chunk_tokens"] = toks(chunk_out)
            row["ats_chunk_rank"] = rank

            row["ats_hit"] = bool(row["ats_recall_rank"] or row["ats_chunk_rank"])
            row["ats_tokens_total"] = row["ats_recall_tokens"] + row["ats_chunk_tokens"]
            row["ats_latency_total_s"] = round(row["ats_recall_latency_s"] + row["ats_chunk_latency_s"], 3)

            # Graph-expansion side experiment: same query, include_graph=True, same
            # top_k request. Measures whether the extra hop (a) rescues a miss into
            # the *unexpanded* rank window, and (b) how much it inflates result-list
            # size / token cost, since expansion has no cap of its own.
            recall_g = engine.recall(query, top_k=5, method="hybrid",
                                      workspace_id=WORKSPACE_FILTER, include_graph=True)
            chunk_g = engine.chunk_search(query, top_k=10, method="hybrid",
                                           workspace_id=WORKSPACE_FILTER, include_graph=True)
            recall_g_out = recall_wire_format(recall_g)
            chunk_g_out = chunk_wire_format(chunk_g)
            row["graph_recall_extra_items"] = len(recall_g) - len(recall_res)
            row["graph_chunk_extra_items"] = len(chunk_g) - len(chunk_res)
            row["graph_recall_tokens"] = toks(recall_g_out)
            row["graph_chunk_tokens"] = toks(chunk_g_out)
            # did expansion surface the fact within a *reasonable* read window (first
            # 10 recall / first 15 chunk items) when the unexpanded call missed?
            rescued_recall = (row["ats_recall_rank"] is None and
                               any(r["id"] in gt_mem for r in recall_g[:10]))
            rescued_chunk = (row["ats_chunk_rank"] is None and
                              any(r["id"] in gt_rec for r in chunk_g[:15]))
            row["graph_rescued"] = bool(rescued_recall or rescued_chunk)

            results.append(row)
            print(f"{fact:<24}{variant:<9} raw_grep={row['raw_grep_hit']!s:<6} "
                  f"design_log={row['design_log_hit']!s:<6} "
                  f"ats_recall_rank={str(row['ats_recall_rank']):<5} "
                  f"ats_chunk_rank={str(row['ats_chunk_rank']):<5} ats_hit={row['ats_hit']!s:<6} "
                  f"graph_rescued={row['graph_rescued']}")

    out_path = HERE / "h2h_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {len(results)} rows to {out_path}")


if __name__ == "__main__":
    main()
