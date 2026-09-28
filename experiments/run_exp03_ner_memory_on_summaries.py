"""
Experiment 03: NER + memory extraction on B-generated summaries vs D (combined prompt)
vs E (cross-chunk unified window synthesis).

Reuses exp02 summaries — no LLM calls for summarization.

Variants compared:
  B+NER+memory  — per-chunk: NER on B summary + batched memory extraction (2 LLM calls total)
  D             — per-chunk: combined prompt on raw chunk from exp02 (5 LLM calls)
  E             — cross-chunk: all 5 B summaries in one unified call → deduplicated
                  entities + synthesized memories for the whole window (1 LLM call)

Run from project root:
    uv run python experiments/run_exp03_ner_memory_on_summaries.py
"""

from __future__ import annotations
import json
import sys
import uuid
from functools import partial
from pathlib import Path

print = partial(print, flush=True)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_trace_signals.config import Config
from agent_trace_signals.pipeline.entity_extractor import EntityExtractor
from agent_trace_signals.pipeline.memory_extractor import ExplicitMemoryExtractor
from agent_trace_signals.providers.ollama import OllamaProvider
from agent_trace_signals.models import Record, Session

EXP02_JSON = Path(__file__).parent / "exp02_results" / "results.json"
OUT_DIR = Path(__file__).parent / "exp03_results"
OUT_DIR.mkdir(exist_ok=True)

SESSION_ID = "e65084a8c4874643e744c8d19ab0e34c567f039847a6734357b02833e3231b86"

# ---------------------------------------------------------------------------
# Variant E — cross-chunk unified prompt
# ---------------------------------------------------------------------------

PROMPT_E = """\
You are given {n} consecutive segment summaries from a single work session.
Extract a unified set of entities and memories for the whole window.

Rules for entities:
- Name each specific project, tool, framework, algorithm, or concept that appears.
- Include a one-phrase role explaining its significance across the session window.
- Deduplicate: if the same entity appears in multiple segments, list it once.
- Types: technology | framework | algorithm | concept | model | dataset | person | project
- SKIP: generic tool names (Bash, Read, Edit), usernames, file paths, version numbers.

Rules for memories:
- Extract durable facts worth recalling in a future session.
- Deduplicate and synthesize: if the same fact appears in multiple segments,
  produce one memory that captures it — don't repeat it per segment.
- Prefer memories that span multiple segments over single-segment facts.
- episodic: specific event with a clear outcome or resolution across this window.
- procedural: reusable how-to with enough detail to act on.
- semantic: stable fact about a named system or concept.
- Each memory MUST name a specific project/tool/concept. Skip trivial outcomes.
- If nothing meets the bar, return an empty list.

Return JSON:
{{
  "entities": [{{"name": "...", "type": "...", "role": "..."}}],
  "memories": [{{"type": "episodic|procedural|semantic", "content": "..."}}]
}}

Segment summaries:
{summaries}"""

PROMPT_E_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["name", "type", "role"],
            },
        },
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["type", "content"],
            },
        },
    },
    "required": ["entities", "memories"],
}


def run_cross_chunk_unified(provider, model: str, summaries: list[str]) -> dict:
    """Single LLM call over all summaries → unified entities + memories."""
    numbered = "\n\n".join(f"[{i+1}] {s}" for i, s in enumerate(summaries))
    prompt = PROMPT_E.format(n=len(summaries), summaries=numbered)
    try:
        return provider.complete_json(
            prompt,
            schema=PROMPT_E_SCHEMA,
            model=model,
            max_tokens=1024,
        )
    except Exception as e:
        return {"entities": [], "memories": [], "error": str(e)}


def main():
    if not EXP02_JSON.exists():
        print(f"ERROR: {EXP02_JSON} not found — run exp02 first")
        sys.exit(1)

    exp02 = json.loads(EXP02_JSON.read_text())
    config = Config()
    provider = OllamaProvider(config.models)

    print("Loading GLiNER model…")
    extractor = EntityExtractor(provider, config.pipeline, config.models)
    mem_extractor = ExplicitMemoryExtractor(provider, config.models)

    dummy_session = Session(
        id=SESSION_ID,
        org_id="exp03",
        workspace_id="SLR-gemma4",
        source_type="agent_trace",
        source_plugin="claude_code",
        session_timestamp="2026-04-03T07:08:24.894Z",
    )

    # Collect valid chunks first
    chunk_entries = []
    for chunk_data in exp02["chunks"]:
        chunk_idx = chunk_data["chunk_index"]
        b_summary = chunk_data["variants"].get("B_decision_focused", "")
        d_output = chunk_data["variants"].get("D_combined_prompt", {})
        chunk_entries.append((chunk_idx, b_summary, d_output))

    valid = [(idx, s, d) for idx, s, d in chunk_entries if s and not s.startswith("ERROR")]
    print(f"{len(valid)} valid B summaries (avg {sum(len(s) for _,s,_ in valid)//len(valid)} chars)\n")

    # Build records for all valid summaries
    records = [
        Record(id=uuid.uuid4().hex, session_id=SESSION_ID, chunk_index=idx, chunk_text=summary)
        for idx, summary, _ in valid
    ]

    # --- NER: pre-LLM per chunk (GLiNER is fast on short text) ---
    print("Running pre-LLM NER on all B summaries…")
    pre_llm_per_chunk = [
        extractor.extract_pre_llm(r.chunk_text, "claude_code", []) for r in records
    ]

    # --- Verifier: 1 batch call for all 5 summaries ---
    print(f"Running LLM verifier (1 batch call for {len(records)} summaries)…")
    chunk_pairs = [(r.chunk_text, pre_llm_per_chunk[i]) for i, r in enumerate(records)]
    verified_per_chunk = extractor.verify_batch(chunk_pairs)

    # --- Memory: 1 batch call for all 5 summaries ---
    print(f"Running memory extraction (1 batch call for {len(records)} summaries)…")
    batch_memory_results = mem_extractor.extract_batch(records, dummy_session)

    # --- E: cross-chunk unified call over all B summaries ---
    b_summaries = [s for _, s, _ in valid]
    print(f"Running cross-chunk unified call (E) over all {len(b_summaries)} summaries…")
    e_result = run_cross_chunk_unified(
        provider, config.models.chunk_summarizer, b_summaries
    )
    print(f"  → {len(e_result.get('entities', []))} entities, "
          f"{len(e_result.get('memories', []))} memories")

    # Assemble per-chunk results
    results = []
    for i, (chunk_idx, b_summary, d_output) in enumerate(valid):
        b_mems, _ = batch_memory_results[i]
        d_entities = d_output.get("entities", []) if isinstance(d_output, dict) else []
        d_memories = d_output.get("memories", []) if isinstance(d_output, dict) else []
        results.append({
            "chunk_index": chunk_idx,
            "b_summary": b_summary,
            "b_pre_llm": [m.model_dump() for m in pre_llm_per_chunk[i]],
            "b_verified": [m.model_dump() for m in verified_per_chunk[i]],
            "b_memories": [m.model_dump() for m in b_mems],
            "d_entities": d_entities,
            "d_memories": d_memories,
        })

    print()

    # ------------------------------------------------------------------
    # Write results
    # ------------------------------------------------------------------
    lines: list[str] = []
    lines.append("# Experiment 03: NER + Memory on B Summaries vs D (combined prompt)\n")
    lines.append("**Input for NER/memory:** B summary text (3-5 sentences)  ")
    lines.append("**Comparison:** D entities + memories from the same exp02 run  ")
    lines.append(f"**NER model:** GLiNER `{config.pipeline.gliner_model}` + verifier `{config.models.entity_extractor_llm}`  ")
    lines.append(f"**Memory model:** `{config.models.explicit_memory}`  ")
    lines.append(f"**LLM calls:** B+NER+memory=2, D={len(results)} (per-chunk), E=1 (cross-chunk unified)\n")
    lines.append("---\n")

    for r in results:
        idx = r["chunk_index"]
        lines.append(f"## Chunk {idx}\n")

        lines.append("### B summary (input to NER + memory)\n")
        lines.append(f"> {r['b_summary']}\n")

        # NER comparison
        lines.append("### Entities: NER on B summary\n")
        lines.append("**Pre-LLM (regex + GLiNER):**")
        if r["b_pre_llm"]:
            for e in r["b_pre_llm"]:
                lines.append(f"- `{e['canonical_name']}` ({e['entity_type']}) [{e['source_layer']}]")
        else:
            lines.append("_(none)_")

        lines.append("\n**After LLM verifier:**")
        if r["b_verified"]:
            pre_names = {e["canonical_name"] for e in r["b_pre_llm"]}
            ver_names = {e["canonical_name"] for e in r["b_verified"]}
            for e in r["b_verified"]:
                lines.append(f"- `{e['canonical_name']}` ({e['entity_type']}) [{e['source_layer']}]")
            rejected = pre_names - ver_names
            added = ver_names - pre_names
            if rejected:
                lines.append(f"\n_Rejected: {', '.join(f'`{n}`' for n in sorted(rejected))}_")
            if added:
                lines.append(f"\n_Added: {', '.join(f'`{n}`' for n in sorted(added))}_")
        else:
            lines.append("_(none)_")

        lines.append("\n**D entities (combined prompt on raw chunk):**")
        if r["d_entities"]:
            for e in r["d_entities"]:
                lines.append(f"- `{e['name']}` ({e['type']}) — {e.get('role', '')}")
        else:
            lines.append("_(none)_")

        # Memory comparison
        lines.append("\n### Memories: extracted from B summary\n")
        if r["b_memories"]:
            for m in r["b_memories"]:
                lines.append(f"- **[{m['memory_type']}]** {m['content']}")
        else:
            lines.append("_(none)_")

        lines.append("\n### Memories: from D (combined prompt on raw chunk)\n")
        if r["d_memories"]:
            for m in r["d_memories"]:
                lines.append(f"- **[{m['type']}]** {m['content']}")
        else:
            lines.append("_(none)_")

        lines.append("\n---\n")

    # E — window-level section
    lines.append("## E — Cross-chunk unified (1 call over all 5 B summaries)\n")
    lines.append("### Entities\n")
    e_entities = e_result.get("entities", [])
    if e_entities:
        for e in e_entities:
            lines.append(f"- `{e['name']}` ({e['type']}) — {e.get('role', '')}")
    else:
        lines.append("_(none)_")

    lines.append("\n### Memories\n")
    e_memories = e_result.get("memories", [])
    if e_memories:
        for m in e_memories:
            lines.append(f"- **[{m['type']}]** {m['content']}")
    else:
        lines.append("_(none)_")

    if "error" in e_result:
        lines.append(f"\n_Error: {e_result['error']}_")

    lines.append("\n---\n")

    # Summary table
    lines.append("## Signal density comparison\n")
    lines.append("| Chunk | B pre-LLM | B verified | B memories | D entities | D memories |")
    lines.append("|-------|-----------|------------|------------|------------|------------|")
    for r in results:
        lines.append(
            f"| {r['chunk_index']} "
            f"| {len(r['b_pre_llm'])} "
            f"| {len(r['b_verified'])} "
            f"| {len(r['b_memories'])} "
            f"| {len(r['d_entities'])} "
            f"| {len(r['d_memories'])} |"
        )
    lines.append(f"| **E (window)** | — | — | — | {len(e_entities)} | {len(e_memories)} |")

    lines.append("\n---\n")
    lines.append("## Key questions to answer\n")
    lines.append(
        "- Does NER on B summaries reduce noise vs NER on raw chunks (exp01)?\n"
        "- Are B+batched memories better/worse/different from D per-chunk memories?\n"
        "- Does E (cross-chunk) deduplicate effectively — are its memories "
        "higher-level syntheses that span multiple chunks?\n"
        "- Does E miss anything important that per-chunk B or D caught?\n"
        "- Which approach gives the best recall signal for "
        "'what did I work on in SLR-gemma4?'\n"
    )

    md_path = OUT_DIR / "results.md"
    json_path = OUT_DIR / "results.json"
    md_path.write_text("\n".join(lines))
    json_path.write_text(json.dumps(
        {"per_chunk": results, "e_cross_chunk": e_result},
        indent=2, default=str,
    ))

    print(f"Done. Results written to:\n  {md_path}\n  {json_path}")


if __name__ == "__main__":
    main()
