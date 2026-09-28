"""
Experiment 01: Session summary vs entity extraction + LLM verifier
vs explicit memory extraction on 5 consecutive chunks.

Run from project root:
    uv run python experiments/run_exp01.py
"""

from __future__ import annotations
import json
import sys
import textwrap
from functools import partial
from pathlib import Path

print = partial(print, flush=True)

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import sqlite3
from agent_trace_signals.config import Config
from agent_trace_signals.pipeline.entity_extractor import EntityExtractor
from agent_trace_signals.pipeline.summarizer import Summarizer
from agent_trace_signals.pipeline.memory_extractor import ExplicitMemoryExtractor
from agent_trace_signals.providers.ollama import OllamaProvider
from agent_trace_signals.models import Record, Session

# Claude Code session: SLR-gemma4, chunks 25-29
# Rich content: actual SLR run output, HITL gates, Ollama/LangGraph/PRISMA discussion
SESSION_ID = "e65084a8c4874643e744c8d19ab0e34c567f039847a6734357b02833e3231b86"
DB_PATH = Path("traces.db")

CHUNK_START = 25
CHUNK_COUNT = 5

OUT_DIR = Path(__file__).parent / "exp01_results"
OUT_DIR.mkdir(exist_ok=True)


def wrap(text: str, width: int = 100, indent: str = "  ") -> str:
    return textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent)


def section(title: str) -> str:
    bar = "=" * (len(title) + 4)
    return f"\n{bar}\n| {title} |\n{bar}\n"


def subsection(title: str) -> str:
    return f"\n--- {title} ---\n"


def main():
    config = Config()
    provider = OllamaProvider(config.models)

    # ------------------------------------------------------------------
    # 1. Load chunks directly from the already-ingested CC traces DB
    # ------------------------------------------------------------------
    print(f"Loading chunks {CHUNK_START}–{CHUNK_START + CHUNK_COUNT - 1} from traces.db…")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    chunk_rows = conn.execute(
        "SELECT id, chunk_index, chunk_text FROM records "
        "WHERE session_id = ? AND chunk_index >= ? AND chunk_index < ? "
        "ORDER BY chunk_index",
        (SESSION_ID, CHUNK_START, CHUNK_START + CHUNK_COUNT),
    ).fetchall()
    conn.close()
    if not chunk_rows:
        print("ERROR: no chunks found — check SESSION_ID and CHUNK_START")
        sys.exit(1)
    print(f"  Loaded {len(chunk_rows)} chunks")

    sess_row = sqlite3.connect(str(DB_PATH)).execute(
        "SELECT workspace_id, session_timestamp, source_plugin FROM sessions WHERE id = ?",
        (SESSION_ID,),
    ).fetchone()

    # ------------------------------------------------------------------
    # 2. Build record objects
    # ------------------------------------------------------------------
    dummy_session = Session(
        id=SESSION_ID,
        org_id="exp01",
        workspace_id=sess_row[0] or "unknown",
        source_type="agent_trace",
        source_plugin=sess_row[2] or "claude_code",
        session_timestamp=sess_row[1],
    )
    records = [
        Record(
            id=row["id"],
            session_id=SESSION_ID,
            chunk_index=row["chunk_index"],
            chunk_text=row["chunk_text"],
        )
        for row in chunk_rows
    ]

    # ------------------------------------------------------------------
    # 3. Pre-LLM entity extraction
    # ------------------------------------------------------------------
    print("Loading GLiNER model (urchade/gliner_multi-v2.1, ~400MB — may take 1-2 min)…")
    extractor = EntityExtractor(provider, config.pipeline, config.models)
    print("Running pre-LLM entity extraction (regex + GLiNER)…")
    pre_llm_per_chunk = [
        extractor.extract_pre_llm(r.chunk_text, "claude_code", []) for r in records
    ]

    # ------------------------------------------------------------------
    # 4. LLM verifier
    # ------------------------------------------------------------------
    print("Running LLM verifier…")
    chunk_pairs = [(r.chunk_text, pre_llm_per_chunk[i]) for i, r in enumerate(records)]
    verified_per_chunk = extractor.verify_batch(chunk_pairs)

    # ------------------------------------------------------------------
    # 5. Summarizer — per chunk + session-level across the 5 chunks
    # ------------------------------------------------------------------
    print("Running chunk summarizer…")
    summarizer = Summarizer(provider, config.models)

    CHUNK_SUMMARIZER_PROMPT_TEMPLATE = (
        "In 2-3 sentences, summarize what happened in this segment: "
        "key actions, decisions, or topics covered.\n\n{chunk_text}"
    )
    SESSION_SUMMARIZER_PROMPT_TEMPLATE = (
        "Given these consecutive segment summaries from a {source_type}, write a 4-6 sentence "
        "summary covering: overall task or discussion, key decisions or outcomes, "
        "what went wrong (if anything), and the final state.\n\nSegments:\n{joined_summaries}"
    )

    summaries = [summarizer.summarize_chunk(r.chunk_text) for r in records]

    print("Running session-level summary over the 5 chunks…")
    session_summary = summarizer.summarize_session(summaries, source_type="agent_trace")

    # ------------------------------------------------------------------
    # 6. Explicit memory extraction
    # ------------------------------------------------------------------
    print("Running explicit memory extraction…")
    mem_extractor = ExplicitMemoryExtractor(provider, config.models)
    memories_per_chunk = [mem_extractor.extract(r, dummy_session) for r in records]

    # ------------------------------------------------------------------
    # 6b. Load already-stored pipeline outputs for these chunks (ground truth)
    # ------------------------------------------------------------------
    print("Loading existing pipeline outputs from DB for comparison…")
    conn2 = sqlite3.connect(str(DB_PATH))
    conn2.row_factory = sqlite3.Row
    record_ids = [r.id for r in records]
    placeholders = ",".join("?" * len(record_ids))

    stored_summaries = {
        row["id"]: row["chunk_summary"]
        for row in conn2.execute(
            f"SELECT id, chunk_summary FROM records WHERE id IN ({placeholders})", record_ids
        ).fetchall()
    }
    stored_memories = {}
    for row in conn2.execute(
        f"SELECT ms.record_id, m.memory_type, m.extraction_method, m.content "
        f"FROM memories m JOIN memory_sources ms ON ms.memory_id = m.id "
        f"WHERE ms.record_id IN ({placeholders}) AND m.extraction_method = 'explicit'",
        record_ids,
    ).fetchall():
        stored_memories.setdefault(row["record_id"], []).append(dict(row))

    stored_entities = {}
    for row in conn2.execute(
        f"SELECT o.record_id, e.canonical_name, e.entity_type, e.confidence "
        f"FROM occurrences o JOIN entities e ON e.id = o.entity_id "
        f"WHERE o.record_id IN ({placeholders})",
        record_ids,
    ).fetchall():
        stored_entities.setdefault(row["record_id"], []).append(dict(row))
    conn2.close()

    # ------------------------------------------------------------------
    # 7. Write results
    # ------------------------------------------------------------------
    doc_lines: list[str] = []
    doc_lines.append("# Experiment 01: Session Summary vs Entity + Memory Extraction")
    doc_lines.append(f"\n**Session:** `{SESSION_ID[:8]}` — SLR-gemma4, claude_code")
    doc_lines.append(f"**Chunks examined:** {CHUNK_START}–{CHUNK_START + CHUNK_COUNT - 1}")
    doc_lines.append("\n---\n")
    doc_lines.append("## Prompts used\n")
    doc_lines.append("### Chunk summarizer prompt\n```")
    doc_lines.append(CHUNK_SUMMARIZER_PROMPT_TEMPLATE.replace("{chunk_text}", "<chunk_text>"))
    doc_lines.append("```\n")
    doc_lines.append("### Session summarizer prompt\n```")
    doc_lines.append(SESSION_SUMMARIZER_PROMPT_TEMPLATE.replace("{source_type}", "agent_trace").replace("{joined_summaries}", "<chunk_summaries joined by newline>"))
    doc_lines.append("```\n")
    doc_lines.append("\n---\n")

    for i, (record, pre_llm, verified, summary, (mems, _)) in enumerate(
        zip(records, pre_llm_per_chunk, verified_per_chunk, summaries, memories_per_chunk)
    ):
        chunk_num = CHUNK_START + i
        doc_lines.append(f"## Chunk {chunk_num}  (record {record.id[:8]})\n")

        doc_lines.append("### Raw chunk text\n```")
        doc_lines.append(record.chunk_text[:2000])
        if len(record.chunk_text) > 2000:
            doc_lines.append(f"... [{len(record.chunk_text) - 2000} chars truncated]")
        doc_lines.append("```\n")

        doc_lines.append("### Pre-LLM entities (regex + GLiNER)\n")
        if pre_llm:
            for m in pre_llm:
                doc_lines.append(f"- `{m.canonical_name}` ({m.entity_type}) [{m.source_layer}]")
        else:
            doc_lines.append("_(none)_")

        doc_lines.append("\n### After LLM verifier\n")
        if verified:
            for m in verified:
                doc_lines.append(f"- `{m.canonical_name}` ({m.entity_type}) [{m.source_layer}]")
        else:
            doc_lines.append("_(none — all candidates rejected or nothing to verify)_")

        # delta
        pre_names = {m.canonical_name for m in pre_llm}
        ver_names = {m.canonical_name for m in verified}
        rejected = pre_names - ver_names
        added = ver_names - pre_names
        if rejected:
            doc_lines.append(f"\n**Rejected by verifier:** {', '.join(f'`{n}`' for n in sorted(rejected))}")
        if added:
            doc_lines.append(f"\n**Added by verifier:** {', '.join(f'`{n}`' for n in sorted(added))}")

        doc_lines.append("\n### Chunk summary — fresh run\n")
        doc_lines.append(f"> {summary}\n")

        doc_lines.append("### Chunk summary — stored (original ingestion)\n")
        stored_s = stored_summaries.get(record.id, "_(not stored)_")
        doc_lines.append(f"> {stored_s}\n")

        doc_lines.append("### Explicit memories — fresh run\n")
        if mems:
            for m in mems:
                doc_lines.append(f"- **[{m.memory_type}]** {m.content}")
        else:
            doc_lines.append("_(none)_")

        doc_lines.append("\n### Explicit memories — stored (original ingestion)\n")
        s_mems = stored_memories.get(record.id, [])
        if s_mems:
            for m in s_mems:
                doc_lines.append(f"- **[{m['memory_type']}]** {m['content']}")
        else:
            doc_lines.append("_(none)_")

        doc_lines.append("\n### Entities — stored (original ingestion)\n")
        s_ents = stored_entities.get(record.id, [])
        if s_ents:
            for e in s_ents:
                doc_lines.append(f"- `{e['canonical_name']}` ({e['entity_type']}) [confidence={e['confidence']}]")
        else:
            doc_lines.append("_(none)_")

        doc_lines.append("\n---\n")

    # ------------------------------------------------------------------
    # 8. Cross-chunk summary: signal density comparison
    # ------------------------------------------------------------------
    doc_lines.append("## Signal density comparison\n")
    doc_lines.append("| Chunk | Pre-LLM entities | After verifier | Memories | Summary words |")
    doc_lines.append("|-------|-----------------|----------------|----------|---------------|")
    for i, (pre_llm, verified, summary, (mems, _)) in enumerate(
        zip(pre_llm_per_chunk, verified_per_chunk, summaries, memories_per_chunk)
    ):
        chunk_num = CHUNK_START + i
        doc_lines.append(
            f"| {chunk_num} | {len(pre_llm)} | {len(verified)} | {len(mems)} | {len(summary.split())} |"
        )

    doc_lines.append("\n## Session-level summary (over all 5 chunks)\n")
    doc_lines.append(f"> {session_summary}\n")

    doc_lines.append("\n---")
    doc_lines.append("\n## Observations / Questions for redesign\n")
    doc_lines.append(
        "_(Fill in after reviewing results above)_\n\n"
        "- Does the summary contain the same entities as the verifier found?\n"
        "- Are there entities in the summary that the verifier missed (and vice versa)?\n"
        "- Do the explicit memories add information beyond what the summary already captures?\n"
        "- Could a single combined prompt (summary + entities + memories) replace steps 3 + 4b + 8?\n"
        "- What is the token cost of each step?\n"
    )

    # save raw JSON for further analysis
    raw = {
        "session_id": SESSION_ID,
        "chunk_start": CHUNK_START,
        "session_summary_fresh": session_summary,
        "chunks": [
            {
                "chunk_index": CHUNK_START + i,
                "record_id": records[i].id,
                "chunk_text": records[i].chunk_text,
                "pre_llm_entities": [m.model_dump() for m in pre_llm_per_chunk[i]],
                "verified_entities": [m.model_dump() for m in verified_per_chunk[i]],
                "summary_fresh": summaries[i],
                "summary_stored": stored_summaries.get(records[i].id),
                "memories_fresh": [m.model_dump() for m in memories_per_chunk[i][0]],
                "memories_stored": stored_memories.get(records[i].id, []),
                "entities_stored": stored_entities.get(records[i].id, []),
            }
            for i in range(len(records))
        ],
    }

    md_path = OUT_DIR / "results.md"
    json_path = OUT_DIR / "results.json"
    md_path.write_text("\n".join(doc_lines))
    json_path.write_text(json.dumps(raw, indent=2, default=str))

    print(f"\nDone. Results written to:\n  {md_path}\n  {json_path}")


if __name__ == "__main__":
    main()
