"""
Experiment 04: D per-chunk → E synthesis over D outputs.

Two-stage pipeline:
  Stage 1 — D: combined prompt (summary + entities + memories) on each raw chunk.
             Reuses D outputs already in exp02_results/results.json.
  Stage 2 — E: cross-chunk synthesis over the 5 D outputs (1 LLM call).
             Input is structured D JSON, not prose. E deduplicates entities,
             merges related memories, and surfaces cross-chunk arc memories.

Comparison:
  D per-chunk  — exp02 D outputs (fine-grained, from raw chunk text)
  E-over-B     — exp03 E output (synthesis over B prose summaries)
  E-over-D     — this experiment (synthesis over D structured output)

Total new LLM calls: 1 (just the E-over-D synthesis)

Run from project root:
    uv run python experiments/run_exp04_d_then_e.py
"""

from __future__ import annotations
import json
import sys
from functools import partial
from pathlib import Path

print = partial(print, flush=True)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_trace_signals.config import Config
from agent_trace_signals.providers.ollama import OllamaProvider

EXP02_JSON = Path(__file__).parent / "exp02_results" / "results.json"
EXP03_JSON = Path(__file__).parent / "exp03_results" / "results.json"
OUT_DIR = Path(__file__).parent / "exp04_results"
OUT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# E-over-D prompt — input is structured D outputs, not prose
# ---------------------------------------------------------------------------

PROMPT_E_OVER_D = """\
You are given {n} consecutive chunk analyses from a single work session.
Each chunk has already been processed to extract a summary, entities, and memories.

Your task is to synthesize these into a unified window-level output:

1. entities — deduplicate across chunks. If the same entity appears in multiple
   chunks, keep one entry with the most informative role description.
   Merge near-duplicates (e.g. "SLR system" and "SLR_gemma4" if they refer to the
   same project). Skip file paths and version numbers that slipped through.
   Types: technology | framework | algorithm | concept | model | dataset | person | project

2. memories — synthesize and deduplicate across chunks:
   - If the same fact appears in multiple chunks, produce one memory that captures it.
   - If related memories across chunks together tell a larger story, merge them into
     one higher-level memory that names the arc.
   - Keep memories that are genuinely unique to a single chunk and meet the quality bar.
   - Prefer memories that span multiple chunks over single-chunk facts.
   - episodic: specific event with clear outcome or resolution.
   - procedural: reusable how-to with enough detail to act on.
   - semantic: stable fact about a named system or concept.
   - Skip trivial outcomes and incomplete issues.

Return JSON:
{{
  "entities": [{{"name": "...", "type": "...", "role": "..."}}],
  "memories": [{{"type": "episodic|procedural|semantic", "content": "...", "spans_chunks": [chunk indices]}}]
}}

Chunk analyses:
{chunk_analyses}"""

PROMPT_E_OVER_D_SCHEMA = {
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
                    "spans_chunks": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["type", "content"],
            },
        },
    },
    "required": ["entities", "memories"],
}


def run_e_over_d(provider, model: str, d_chunks: list[dict]) -> dict:
    """Single synthesis call over all D per-chunk outputs."""
    blocks = []
    for i, chunk in enumerate(d_chunks):
        d = chunk["variants"].get("D_combined_prompt", {})
        if not isinstance(d, dict):
            continue
        block = {
            "chunk_index": i,
            "summary": d.get("summary", ""),
            "entities": d.get("entities", []),
            "memories": d.get("memories", []),
        }
        blocks.append(f"[Chunk {i}]\n{json.dumps(block, indent=2)}")

    chunk_analyses = "\n\n---\n\n".join(blocks)
    prompt = PROMPT_E_OVER_D.format(n=len(blocks), chunk_analyses=chunk_analyses)

    try:
        return provider.complete_json(
            prompt,
            schema=PROMPT_E_OVER_D_SCHEMA,
            model=model,
            max_tokens=1536,
        )
    except Exception as ex:
        return {"entities": [], "memories": [], "error": str(ex)}


def main():
    for path, label in [(EXP02_JSON, "exp02"), (EXP03_JSON, "exp03")]:
        if not path.exists():
            print(f"ERROR: {path} not found — run {label} first")
            sys.exit(1)

    exp02 = json.loads(EXP02_JSON.read_text())
    exp03 = json.loads(EXP03_JSON.read_text())

    config = Config()
    provider = OllamaProvider(config.models)
    model = config.models.chunk_summarizer  # gemma3:12b

    print(f"Model: {model}")
    print(f"Running E-over-D synthesis (1 call over {len(exp02['chunks'])} D outputs)…")
    e_over_d = run_e_over_d(provider, model, exp02["chunks"])
    print(f"  → {len(e_over_d.get('entities', []))} entities, "
          f"{len(e_over_d.get('memories', []))} memories")
    if "error" in e_over_d:
        print(f"  ERROR: {e_over_d['error']}")

    # Load E-over-B from exp03 for comparison
    e_over_b = exp03.get("e_cross_chunk", {})

    # Per-chunk D outputs
    d_per_chunk = [
        {
            "chunk_index": c["chunk_index"],
            "d": c["variants"].get("D_combined_prompt", {}),
        }
        for c in exp02["chunks"]
    ]

    # ------------------------------------------------------------------
    # Write results
    # ------------------------------------------------------------------
    lines: list[str] = []
    lines.append("# Experiment 04: D per-chunk → E synthesis over D outputs\n")
    lines.append(f"**Model:** `{model}`  ")
    lines.append("**Stage 1:** D combined prompt on raw chunks (from exp02)  ")
    lines.append("**Stage 2:** E synthesis over 5 D outputs — 1 LLM call  ")
    lines.append("**Total new calls:** 1\n")
    lines.append("---\n")

    # D per-chunk
    lines.append("## Stage 1 — D outputs per chunk\n")
    for item in d_per_chunk:
        idx = item["chunk_index"]
        d = item["d"]
        if not isinstance(d, dict):
            lines.append(f"### Chunk {idx}: _(no D output)_\n")
            continue
        lines.append(f"### Chunk {idx}\n")
        lines.append(f"**Summary:** {d.get('summary', '')}\n")
        ents = d.get("entities", [])
        if ents:
            lines.append("**Entities:** " + ", ".join(
                f"`{e['name']}` ({e['type']})" for e in ents
            ))
        mems = d.get("memories", [])
        if mems:
            lines.append("\n**Memories:**")
            for m in mems:
                lines.append(f"- **[{m['type']}]** {m['content']}")
        lines.append("")

    lines.append("---\n")

    # E-over-D
    lines.append("## Stage 2 — E synthesis over D outputs\n")
    lines.append("### Entities\n")
    eod_entities = e_over_d.get("entities", [])
    if eod_entities:
        for e in eod_entities:
            lines.append(f"- `{e['name']}` ({e['type']}) — {e.get('role', '')}")
    else:
        lines.append("_(none)_")

    lines.append("\n### Memories\n")
    eod_memories = e_over_d.get("memories", [])
    if eod_memories:
        for m in eod_memories:
            spans = m.get("spans_chunks", [])
            span_str = f" _(chunks {spans})_" if spans else ""
            lines.append(f"- **[{m['type']}]** {m['content']}{span_str}")
    else:
        lines.append("_(none)_")

    lines.append("\n---\n")

    # E-over-B (from exp03) for comparison
    lines.append("## E-over-B (exp03, synthesis over B prose summaries) — for comparison\n")
    lines.append("### Entities\n")
    eob_entities = e_over_b.get("entities", [])
    if eob_entities:
        for e in eob_entities:
            lines.append(f"- `{e['name']}` ({e['type']}) — {e.get('role', '')}")
    else:
        lines.append("_(none)_")

    lines.append("\n### Memories\n")
    eob_memories = e_over_b.get("memories", [])
    if eob_memories:
        for m in eob_memories:
            lines.append(f"- **[{m['type']}]** {m['content']}")
    else:
        lines.append("_(none)_")

    lines.append("\n---\n")

    # Comparison table
    lines.append("## Comparison\n")
    lines.append("| | Entities | Memories | Input to E |")
    lines.append("|---|---|---|---|")
    d_total_ents = sum(
        len(c["variants"].get("D_combined_prompt", {}).get("entities", []))
        for c in exp02["chunks"]
        if isinstance(c["variants"].get("D_combined_prompt"), dict)
    )
    d_total_mems = sum(
        len(c["variants"].get("D_combined_prompt", {}).get("memories", []))
        for c in exp02["chunks"]
        if isinstance(c["variants"].get("D_combined_prompt"), dict)
    )
    lines.append(f"| D per-chunk (total) | {d_total_ents} | {d_total_mems} | raw chunk text |")
    lines.append(f"| E-over-B | {len(eob_entities)} | {len(eob_memories)} | B prose summaries |")
    lines.append(f"| E-over-D | {len(eod_entities)} | {len(eod_memories)} | D structured output |")

    lines.append("\n---\n")
    lines.append("## Key questions\n")
    lines.append(
        "- Does E-over-D produce better deduplicated entities than E-over-B?\n"
        "  (E-over-D has richer input: typed entities + memories, not just prose)\n"
        "- Does `spans_chunks` correctly identify multi-chunk memories?\n"
        "- Are E-over-D memories higher quality than D per-chunk memories?\n"
        "- Does E-over-D miss anything that D per-chunk caught?\n"
        "- Is the D→E pipeline worth 6 calls (5 D + 1 E) vs E-over-B's 2 calls (5 B + 1 E)?\n"
    )

    md_path = OUT_DIR / "results.md"
    json_path = OUT_DIR / "results.json"
    md_path.write_text("\n".join(lines))
    json_path.write_text(json.dumps(
        {"e_over_d": e_over_d, "e_over_b": e_over_b, "d_per_chunk": d_per_chunk},
        indent=2, default=str,
    ))

    print(f"\nDone. Results written to:\n  {md_path}\n  {json_path}")


if __name__ == "__main__":
    main()
