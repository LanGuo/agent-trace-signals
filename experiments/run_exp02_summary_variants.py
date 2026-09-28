"""
Experiment 02: Chunk summarizer prompt variants + combined prompt.

Tests four prompts on the same 5 chunks (SLR-gemma4, chunks 25-29):
  A — entity-anchored summarizer
  B — decision-focused summarizer  (recommended baseline)
  C — combined summarizer (structured instructions)
  D — combined prompt: summary + entities + memories in one JSON call

No GLiNER, no entity extraction pipeline — pure LLM comparison.

Run from project root:
    uv run python experiments/run_exp02_summary_variants.py
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
from agent_trace_signals.providers.ollama import OllamaProvider

SESSION_ID = "e65084a8c4874643e744c8d19ab0e34c567f039847a6734357b02833e3231b86"
DB_PATH = Path("traces.db")
CHUNK_START = 25
CHUNK_COUNT = 5

OUT_DIR = Path(__file__).parent / "exp02_results"
OUT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

PROMPT_CURRENT = """\
In 2-3 sentences, summarize what happened in this segment: key actions, decisions, or topics covered.

{chunk_text}"""

PROMPT_A = """\
Summarize this segment in 3-4 sentences. Name every specific tool, framework, library, \
algorithm, or project mentioned. For each key action or decision, state what was done \
and why. Omit file paths, test counts, and boilerplate tool output.

{chunk_text}"""

PROMPT_B = """\
In 3-5 sentences, extract the durable knowledge from this segment: what problem was \
identified, what decision was made and why, what changed as a result. Be specific about \
named systems and concepts. Skip routine actions (file reads, test runs, commits) unless \
something unexpected happened.

{chunk_text}"""

PROMPT_C = """\
Summarize this segment in 3-5 sentences covering:
1. What was being worked on (name the specific project/system/component)
2. What problem or question was raised
3. What decision or finding resulted, and the reason behind it

Name specific tools, frameworks, or algorithms. Skip file paths, passing test counts, \
and routine commits.

{chunk_text}"""

PROMPT_D = """\
You are extracting structured knowledge from an agent session chunk for long-term recall.
A future user will ask "what did I work on in <project>?" — your output should help answer that.

Produce JSON with three fields:

1. summary — 3-5 sentences covering: what was being worked on (name the project/system),
   what problem or gap was identified, what decision or action resulted and WHY.
   Skip file paths, passing test counts, and routine commits unless something unexpected happened.

2. entities — named things worth remembering across sessions. For each, include name, type, and
   a one-phrase role explaining its significance in this chunk.
   Types: technology | framework | algorithm | concept | model | dataset | person | project
   SKIP: generic tool names (Bash, Read, Edit, Write), usernames, file paths, version numbers,
   and anything that only matters within this single session.

3. memories — durable facts a future session should know. Apply these rules:
   - episodic: a specific event with a clear outcome or resolution. Skip if no resolution.
   - procedural: a reusable how-to with enough detail to act on.
   - semantic: a stable fact about a named system or concept — not a one-session observation.
   Each memory MUST name a specific project/tool/concept. Skip trivial outcomes ("it worked",
   "tests passed") and incomplete issues (no root cause = no memory).

Return JSON:
{{
  "summary": "...",
  "entities": [{{"name": "...", "type": "...", "role": "..."}}],
  "memories": [{{"type": "episodic|procedural|semantic", "content": "..."}}]
}}
Return empty arrays if nothing meets the bar. Do NOT invent information not in the text.

Chunk:
{chunk_text}"""

PROMPT_D_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
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
    "required": ["summary", "entities", "memories"],
}

TEXT_VARIANTS = {
    "current": PROMPT_CURRENT,
    "A_entity_anchored": PROMPT_A,
    "B_decision_focused": PROMPT_B,
    "C_combined_summarizer": PROMPT_C,
}


def run_text_variant(provider, model: str, prompt_template: str, chunk_text: str) -> str:
    prompt = prompt_template.replace("{chunk_text}", chunk_text[:2000])
    try:
        return provider.complete_text(prompt, model=model, max_tokens=512)
    except Exception as e:
        return f"ERROR: {e}"


def run_combined_prompt(provider, model: str, chunk_text: str) -> dict:
    prompt = PROMPT_D.replace("{chunk_text}", chunk_text[:2000])
    try:
        return provider.complete_json(prompt, schema=PROMPT_D_SCHEMA, model=model, max_tokens=1024)
    except Exception as e:
        return {"summary": f"ERROR: {e}", "entities": [], "memories": []}


def main():
    config = Config()
    provider = OllamaProvider(config.models)
    model = config.models.chunk_summarizer  # gemma3:12b

    print(f"Model: {model}")

    # ------------------------------------------------------------------
    # Load chunks
    # ------------------------------------------------------------------
    print(f"Loading chunks {CHUNK_START}–{CHUNK_START + CHUNK_COUNT - 1} from traces.db…")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, chunk_index, chunk_text FROM records "
        "WHERE session_id = ? AND chunk_index >= ? AND chunk_index < ? "
        "ORDER BY chunk_index",
        (SESSION_ID, CHUNK_START, CHUNK_START + CHUNK_COUNT),
    ).fetchall()
    conn.close()

    if not rows:
        print("ERROR: no chunks found")
        sys.exit(1)
    print(f"  Loaded {len(rows)} chunks\n")

    # ------------------------------------------------------------------
    # Run variants
    # ------------------------------------------------------------------
    results = []
    for row in rows:
        chunk_idx = row["chunk_index"]
        chunk_text = row["chunk_text"]
        print(f"Chunk {chunk_idx} ({len(chunk_text)} chars)")

        chunk_result = {
            "chunk_index": chunk_idx,
            "chunk_text": chunk_text,
            "variants": {},
        }

        for variant_name, prompt_template in TEXT_VARIANTS.items():
            print(f"  Running {variant_name}…")
            out = run_text_variant(provider, model, prompt_template, chunk_text)
            chunk_result["variants"][variant_name] = out

        print("  Running D_combined_prompt…")
        chunk_result["variants"]["D_combined_prompt"] = run_combined_prompt(
            provider, model, chunk_text
        )

        results.append(chunk_result)
        print()

    # ------------------------------------------------------------------
    # Write markdown results
    # ------------------------------------------------------------------
    lines: list[str] = []
    lines.append("# Experiment 02: Chunk Summarizer Prompt Variants\n")
    lines.append(f"**Session:** `{SESSION_ID[:8]}` — SLR-gemma4  ")
    lines.append(f"**Chunks:** {CHUNK_START}–{CHUNK_START + CHUNK_COUNT - 1}  ")
    lines.append(f"**Model:** `{model}`\n")
    lines.append("---\n")

    # Prompts reference section
    lines.append("## Prompts\n")
    for name, prompt in {**TEXT_VARIANTS, "D_combined_prompt": PROMPT_D}.items():
        lines.append(f"### {name}\n```")
        lines.append(prompt.replace("{chunk_text}", "<chunk_text>"))
        lines.append("```\n")
    lines.append("---\n")

    # Per-chunk results
    for r in results:
        idx = r["chunk_index"]
        lines.append(f"## Chunk {idx}\n")

        lines.append("### Raw chunk text\n```")
        lines.append(r["chunk_text"][:1500])
        if len(r["chunk_text"]) > 1500:
            lines.append(f"... [{len(r['chunk_text']) - 1500} chars truncated]")
        lines.append("```\n")

        for variant_name, output in r["variants"].items():
            lines.append(f"### {variant_name}\n")
            if isinstance(output, dict):
                # Combined prompt — format each field
                lines.append(f"**Summary:**  \n{output.get('summary', '')}\n")
                entities = output.get("entities", [])
                if entities:
                    lines.append("**Entities:**")
                    for e in entities:
                        lines.append(f"- `{e.get('name')}` ({e.get('type')}) — {e.get('role')}")
                else:
                    lines.append("**Entities:** _(none)_")
                memories = output.get("memories", [])
                if memories:
                    lines.append("\n**Memories:**")
                    for m in memories:
                        lines.append(f"- **[{m.get('type')}]** {m.get('content')}")
                else:
                    lines.append("\n**Memories:** _(none)_")
            else:
                lines.append(output)
            lines.append("")

        lines.append("---\n")

    # Cross-chunk comparison table — summary length as a proxy for density
    lines.append("## Summary length comparison (words)\n")
    header = "| Chunk | current | A | B | C | D (summary only) |"
    sep    = "|-------|---------|---|---|---|-----------------|"
    lines.append(header)
    lines.append(sep)
    for r in results:
        v = r["variants"]
        d_summary = v["D_combined_prompt"].get("summary", "") if isinstance(v["D_combined_prompt"], dict) else ""
        lines.append(
            f"| {r['chunk_index']} "
            f"| {len(v['current'].split())} "
            f"| {len(v['A_entity_anchored'].split())} "
            f"| {len(v['B_decision_focused'].split())} "
            f"| {len(v['C_combined_summarizer'].split())} "
            f"| {len(d_summary.split())} |"
        )

    md_path = OUT_DIR / "results.md"
    json_path = OUT_DIR / "results.json"
    md_path.write_text("\n".join(lines))
    json_path.write_text(json.dumps(
        {"model": model, "chunks": results},
        indent=2, default=str,
    ))

    print(f"Done. Results written to:\n  {md_path}\n  {json_path}")


if __name__ == "__main__":
    main()
