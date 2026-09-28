"""
Experiment 05: D-prompt output vs stored pipeline output on session 1bae6787.

Runs DChunkAnalyzer on the stored chunk texts and compares:
  - D summary vs stored chunk_summary
  - D entities vs stored entities (from occurrences)
  - D memories vs stored explicit memories

Uses first 5 chunks for a focused comparison. No DB writes.

Run from project root:
    uv run python experiments/run_exp05_d_vs_stored.py
"""

from __future__ import annotations
import json
import sqlite3
import sys
from functools import partial
from pathlib import Path

print = partial(print, flush=True)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_trace_signals.config import Config
from agent_trace_signals.pipeline.chunk_analyzer import DChunkAnalyzer
from agent_trace_signals.providers.ollama import OllamaProvider
from agent_trace_signals.models import Record, Session

SESSION_ID = "1bae6787dbdbb5cac87010ec58a7905227e5ed6901e5492af9789444f46d5471"
DB_PATH = Path("traces.db")

# Two windows to compare: early technical (entity fixes, gemma4 token issue)
# and later architectural (per-session thresholds, UI, RAM config)
WINDOWS = [
    {"label": "Window A — chunks 5-9 (entity extraction fixes + gemma4 token issue)", "start": 5, "count": 5},
    {"label": "Window B — chunks 19-23 (per-session thresholds + UI planning)", "start": 19, "count": 5},
]

OUT_DIR = Path(__file__).parent / "exp05_results"
OUT_DIR.mkdir(exist_ok=True)


def load_stored(conn, session_id: str, record_ids: list[str]) -> dict:
    """Load stored summaries, entities, memories for the given records."""
    ph = ",".join("?" * len(record_ids))

    summaries = {
        r["id"]: r["chunk_summary"]
        for r in conn.execute(
            f"SELECT id, chunk_summary FROM records WHERE id IN ({ph})", record_ids
        ).fetchall()
    }

    # Entities via occurrences — dedup by entity id per record
    entities: dict[str, list[dict]] = {}
    for row in conn.execute(
        f"""SELECT o.record_id, e.canonical_name, e.entity_type, e.confidence,
               o.context_text
            FROM occurrences o JOIN entities e ON e.id = o.entity_id
            WHERE o.record_id IN ({ph})""",
        record_ids,
    ).fetchall():
        entities.setdefault(row["record_id"], []).append(dict(row))

    # Explicit memories
    memories: dict[str, list[dict]] = {}
    for row in conn.execute(
        f"""SELECT ms.record_id, m.memory_type, m.extraction_method, m.content
            FROM memories m JOIN memory_sources ms ON ms.memory_id = m.id
            WHERE ms.record_id IN ({ph}) AND m.extraction_method = 'explicit'""",
        record_ids,
    ).fetchall():
        memories.setdefault(row["record_id"], []).append(dict(row))

    return {"summaries": summaries, "entities": entities, "memories": memories}


def main():
    config = Config()
    provider = OllamaProvider(config.models)
    model = config.models.chunk_summarizer

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    sess_row = conn.execute(
        "SELECT workspace_id, session_timestamp, source_plugin FROM sessions WHERE id=?",
        (SESSION_ID,),
    ).fetchone()

    print(f"Session: {SESSION_ID[:8]} ({sess_row['workspace_id']}), model: {model}\n")

    session = Session(
        id=SESSION_ID, org_id="exp05",
        workspace_id=sess_row["workspace_id"],
        source_type="agent_trace",
        source_plugin=sess_row["source_plugin"],
        session_timestamp=sess_row["session_timestamp"],
    )
    analyzer = DChunkAnalyzer(provider, config.models)

    lines: list[str] = []
    lines.append("# Experiment 05: D-prompt vs stored pipeline — session 1bae6787\n")
    lines.append(f"**Session:** `{SESSION_ID[:8]}` ({sess_row['workspace_id']})  ")
    lines.append(f"**Model:** `{model}`\n")
    lines.append("---\n")

    all_comparison = {}

    for window in WINDOWS:
        label = window["label"]
        start = window["start"]
        count = window["count"]

        print(f"{'='*60}")
        print(f"{label}")
        print(f"{'='*60}")

        rows = conn.execute(
            "SELECT id, chunk_index, chunk_text FROM records "
            "WHERE session_id=? AND chunk_index>=? AND chunk_index<? ORDER BY chunk_index",
            (SESSION_ID, start, start + count),
        ).fetchall()

        records = [
            Record(id=r["id"], session_id=SESSION_ID,
                   chunk_index=r["chunk_index"], chunk_text=r["chunk_text"])
            for r in rows
        ]
        record_ids = [r.id for r in records]
        stored = load_stored(conn, SESSION_ID, record_ids)

        d_results = []
        for record in records:
            print(f"  Chunk {record.chunk_index} ({len(record.chunk_text)} chars)…")
            d_results.append(analyzer.analyze(record, session))
        print()

        lines.append(f"## {label}\n")
        comparison = []

        for record, d in zip(records, d_results):
            rid = record.id
            idx = record.chunk_index
            lines.append(f"### Chunk {idx}  ({len(record.chunk_text)} chars)\n")

            lines.append("**Chunk preview:**\n```")
            lines.append(record.chunk_text[:250].replace("```", "'''"))
            lines.append("```\n")

            # Summary
            lines.append("**Summary — D:**")
            lines.append(f"> {d.summary}\n")
            stored_s = stored["summaries"].get(rid) or "_(empty)_"
            lines.append("**Summary — stored:**")
            lines.append(f"> {stored_s}\n")

            # Entities
            stored_ents_raw = stored["entities"].get(rid, [])
            seen: set[str] = set()
            stored_ents: list[dict] = []
            for e in stored_ents_raw:
                k = e["canonical_name"].lower()
                if k not in seen:
                    seen.add(k)
                    stored_ents.append(e)

            lines.append(f"**Entities — D ({len(d.entities)}):**")
            if d.entities:
                for e in d.entities:
                    lines.append(f"- `{e.name}` ({e.type}) — {e.role}")
            else:
                lines.append("_(none)_")

            lines.append(f"\n**Entities — stored ({len(stored_ents)} unique):**")
            if stored_ents:
                for e in stored_ents[:20]:
                    lines.append(f"- `{e['canonical_name']}` ({e['entity_type']}) [{e['confidence']}]")
                if len(stored_ents) > 20:
                    lines.append(f"  _(+{len(stored_ents)-20} more)_")
            else:
                lines.append("_(none)_")

            # Memories
            stored_mems = stored["memories"].get(rid, [])
            lines.append(f"\n**Memories — D ({len(d.memories)}):**")
            if d.memories:
                for m in d.memories:
                    lines.append(f"- **[{m.type}]** {m.content}")
            else:
                lines.append("_(none)_")

            lines.append(f"\n**Memories — stored explicit ({len(stored_mems)}):**")
            if stored_mems:
                for m in stored_mems:
                    lines.append(f"- **[{m['memory_type']}]** {m['content']}")
            else:
                lines.append("_(none)_")

            lines.append("")
            comparison.append({
                "chunk_index": idx,
                "len": len(record.chunk_text),
                "d_summary": d.summary,
                "stored_summary": stored["summaries"].get(rid),
                "d_entities": [{"name": e.name, "type": e.type, "role": e.role} for e in d.entities],
                "stored_entities": stored_ents,
                "d_memories": [{"type": m.type, "content": m.content} for m in d.memories],
                "stored_memories": stored_mems,
            })

        # Density table per window
        lines.append(f"**Density — {label.split('—')[0].strip()}:**\n")
        lines.append("| Chunk | chars | D ents | Stored ents | D mems | Stored mems |")
        lines.append("|-------|-------|--------|-------------|--------|-------------|")
        for c in comparison:
            lines.append(
                f"| {c['chunk_index']} | {c['len']} "
                f"| {len(c['d_entities'])} | {len(c['stored_entities'])} "
                f"| {len(c['d_memories'])} | {len(c['stored_memories'])} |"
            )
        lines.append("\n---\n")
        all_comparison[label] = comparison

    conn.close()

    md_path = OUT_DIR / "results.md"
    json_path = OUT_DIR / "results.json"
    md_path.write_text("\n".join(lines))
    json_path.write_text(json.dumps(all_comparison, indent=2, default=str))
    print(f"Done.\n  {md_path}\n  {json_path}")


if __name__ == "__main__":
    main()
