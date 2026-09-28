"""Split-task variants of the v2 D-prompt: one call for entities only, one call
for memories/patterns/preferences only — both on the SAME model, to isolate
"focused single-purpose prompt" from "different model" as the variable under
test (the prior round split entities/memories across two different models).
"""

from __future__ import annotations
import logging

from agent_trace_signals.config import ModelConfig, PipelineConfig
from agent_trace_signals.models import Record, Session
from agent_trace_signals.providers.base import ModelProvider
from agent_trace_signals.pipeline.utils import _is_trivial
from v2_chunk_analyzer import DEntity, DMemory, DPattern, DPreference

logger = logging.getLogger(__name__)

CITATION_RULE = """Before extracting anything, decide whether the chunk shows the agent's own first-hand actions/
observations in THIS session, or whether it is quoting, displaying, or reviewing content that
originated elsewhere (e.g. inspecting stored memory/database records, pasting example output,
summarizing another tool's prior results). Only extract from genuine first-hand observations. If
the chunk is reviewing or quoting other content, you may extract facts ABOUT that act of review
(e.g. "agent reviewed N stored records and found them accurate") but do NOT extract the quoted
material's subject matter as if the agent newly observed or asserted it — a name or fact mentioned
only inside quoted/reviewed content should be skipped, even if it would otherwise look important.

Extract SPARINGLY on top of that. Every item below should clear a high bar — when in doubt, extract
nothing. A future reader trusts everything you return without re-checking it; a wrong or trivial
entry costs more than a missed one."""


class EntitiesOnlyAnalyzer:
    """Single-purpose call: summary + entities only."""

    def __init__(self, provider: ModelProvider, config: ModelConfig, pipeline_config: PipelineConfig | None = None) -> None:
        self.provider = provider
        self.config = config
        self.pipeline_config = pipeline_config

    def _normalize_entity_type(self, raw_type: str) -> str:
        from agent_trace_signals.pipeline.chunk_analyzer import D_TYPE_NORMALIZATION, _SHARED_ENTITY_VOCAB
        t = raw_type.lower().strip()
        if t in D_TYPE_NORMALIZATION:
            return D_TYPE_NORMALIZATION[t]
        if t in _SHARED_ENTITY_VOCAB:
            return t
        return "concept"

    def _entity_types_str(self) -> str:
        cfg = self.pipeline_config
        if cfg and cfg.d_entity_types:
            types = cfg.d_entity_types
            if isinstance(types, dict):
                return "\n   ".join(f"{t}: {desc}" for t, desc in types.items())
            return " | ".join(types)
        return "technology | concept | model | dataset | person | file"

    def analyze(self, record: Record, session: Session) -> tuple[str, list[DEntity]]:
        if len(record.chunk_text) < 200:
            return "", []
        chunk_text = record.chunk_text[:6000]

        prompt = f"""You are extracting named entities from an agent session chunk for long-term recall.
A future user will ask "what did I work on in <project>?" — your output should help answer that.

{CITATION_RULE}

Produce JSON with two fields:

1. summary — 3-5 sentences covering: what was being worked on (name the project/system),
   what problem or gap was identified, what decision or action resulted and WHY.
   Skip file paths, passing test counts, and routine commits unless something unexpected happened.

2. entities — named things worth remembering across sessions. For each, include name, type, and
   a one-phrase role explaining its significance in this chunk. Re-check each candidate against the
   citation rule above before including it.
   "Extract sparingly" is a bar on QUALITY, not a cap on COVERAGE — a chunk that mentions five
   distinct files worth remembering should return five entities, not stop after the first one or
   two. Scan the ENTIRE chunk before finalizing this list; do not stop as soon as you have a
   plausible answer. This is the only thing this call does — there is no other field competing for
   your attention, so be exhaustive.
   Types (use the most specific match):
   {self._entity_types_str()}
   SKIP: generic tool names (Bash, Read, Edit, Write), usernames, file paths, version numbers,
   hyperparameters, UI elements (buttons, menu items, labels), internal code symbols/function
   names (unless the symbol itself names a published library/framework), and anything that only
   matters within this single session. If something doesn't clearly fit a type above, omit it —
   do not default it into "technology" just because nothing else fits.

Return JSON:
{{"summary": "...", "entities": [{{"name": "...", "type": "...", "role": "..."}}]}}
Return an empty array if nothing meets the bar. Do NOT invent information not in the text.

Chunk:
{chunk_text}"""

        schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"}, "type": {"type": "string"}, "role": {"type": "string"},
                        },
                        "required": ["name", "type", "role"],
                    },
                },
            },
            "required": ["summary", "entities"],
        }

        try:
            result = self.provider.complete_json(prompt, schema=schema, model=self.config.chunk_summarizer, max_tokens=1024)
            summary = result.get("summary", "").strip()
            entities = [
                DEntity(name=e.get("name", "").strip(), type=self._normalize_entity_type(e.get("type", "")), role=e.get("role", "").strip())
                for e in result.get("entities", []) if e.get("name", "").strip()
            ]
            return summary, entities
        except Exception as e:
            logger.warning(f"Failed to analyze (entities-only) chunk {record.id}: {e}")
            return "", []


class MemoriesPatternsAnalyzer:
    """Single-purpose call: memories + patterns + preferences only (no entities)."""

    def __init__(self, provider: ModelProvider, config: ModelConfig, pipeline_config: PipelineConfig | None = None) -> None:
        self.provider = provider
        self.config = config
        self.pipeline_config = pipeline_config

    def analyze(self, record: Record, session: Session) -> tuple[list[DMemory], list[DPattern], list[DPreference]]:
        if len(record.chunk_text) < 200:
            return [], [], []
        chunk_text = record.chunk_text[:6000]

        prompt = f"""You are extracting durable memories, reasoning patterns, and stated preferences from an
agent session chunk for long-term recall. A future user will ask "what did I work on in <project>?"
and "what patterns show up in how I work?" — your output should help answer that.

{CITATION_RULE}

Produce JSON with three fields:

1. memories — durable observations a future session should know. Two types only — there is no
   "semantic"/stable-fact type: a single chunk cannot verify that something is permanently true,
   only that it was true/observed/decided at this point. If you're tempted to write "X is Y",
   rewrite it as "X was Y as of this session" or skip it.
   - episodic: a specific, CONSEQUENTIAL event, decision, or observation — not a routine step,
     not incidental detail. Include a `status`:
       "resolved" — concluded, with a clear outcome.
       "open" — still being discussed/decided, no resolution yet.
       "reverted" — was decided, then undone/changed again within this same chunk.
     Do not skip open/unresolved items.
   - procedural: a reusable how-to, but ONLY for a durable PUBLIC interface — a CLI command, a
     documented API, a config flag meant for external use. Do NOT extract internal implementation
     detail dressed up as a how-to. `status` is not applicable — leave it empty.
   Each memory MUST name a specific project/tool/concept. Skip trivial outcomes and vague content.
   Scan the ENTIRE chunk before finalizing this list — this is the only thing this call does, be
   exhaustive within the stated bar.

2. patterns — agent reasoning observations worth remembering for future sessions. Three types.
   ONLY extract if something notable is observable in THIS chunk's own content. Return [] if
   nothing stands out. The three "shape" descriptions below describe the FORM, not real content —
   never reuse their wording, subject matter, or any specific noun from them in your actual output.
   MAX 2 per chunk. Must be non-ephemeral — abstract away session-specific names, paths, model names.
   Skip ONLY if substantially identical (same steps, same action verbs) to a PROCEDURAL memory
   already in field 1.
   `recovery` is a special case of `strategy` (a choice made under a stuck state) — pick exactly
   ONE per event:
     1. If a stuck-state → fix arc is present, check the evidence bar under `recovery` first.
     2. Otherwise, if a choice was made and reasoned about, classify as `strategy`.
     3. `inefficiency` is independent — evaluate separately.
   - strategy: HOW or WHAT the agent decided, and WHY — covers both a single choice-with-reason
     and a stated repeatable method, abstracting away specific tool/model/file names.
     Shape: "when [condition/tradeoff], [agent's method or choice] — e.g. [category of action]
     over [category of action] — because [reason]".
   - recovery: ONLY if the agent demonstrably got stuck (repeated failures, errors, backtracking)
     AND the chunk shows verifying evidence that the fix actually worked — test output, a command
     result, a passing check, a file diff — not merely the agent's own stated belief that it
     worked. If the chunk ends with the agent claiming success but no verifying evidence is shown,
     use `strategy` instead, or leave it for episodic with status "open".
     Shape: "[stuck state] caused by [root cause]; agent [fix] — verified by [evidence] —
     addresses [general class of problem]".
   - inefficiency: ONLY if a shorter/faster path existed. Shape: "[what was done] could have
     used [better approach] instead — avoids [cost] for [operation]".

3. preferences — explicit instructions, corrections, or standing preferences the USER stated
   about how they want to work or be worked with — NOT facts about the project's design or code.
   ONLY extract if the user is explicitly stating a preference/instruction or correcting the
   agent's approach — never infer one from a single instance of the agent doing something a
   certain way.

Return JSON:
{{
  "memories": [{{"type": "episodic|procedural", "status": "resolved|open|reverted|", "content": "..."}}],
  "patterns": [{{"type": "strategy|recovery|inefficiency", "content": "..."}}],
  "preferences": [{{"content": "..."}}]
}}
Return empty arrays if nothing meets the bar. Do NOT invent information not in the text.

Chunk:
{chunk_text}"""

        schema = {
            "type": "object",
            "properties": {
                "memories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"type": {"type": "string"}, "status": {"type": "string"}, "content": {"type": "string"}},
                        "required": ["type", "content"],
                    },
                },
                "patterns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"type": {"type": "string"}, "content": {"type": "string"}},
                        "required": ["type", "content"],
                    },
                },
                "preferences": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]},
                },
            },
            "required": ["memories", "patterns", "preferences"],
        }

        try:
            result = self.provider.complete_json(prompt, schema=schema, model=self.config.chunk_summarizer, max_tokens=1024)
            memories = [
                DMemory(type=m.get("type", "").strip(), status=m.get("status", "").strip(), content=m.get("content", "").strip())
                for m in result.get("memories", [])
                if m.get("content", "").strip() and m.get("type", "").strip() in {"episodic", "procedural"}
            ]
            memories = [m for m in memories if not _is_trivial(m.content)]
            patterns = [
                DPattern(type=p.get("type", "").strip(), content=p.get("content", "").strip())
                for p in result.get("patterns", [])
                if p.get("content", "").strip() and p.get("type", "").strip() in {"strategy", "recovery", "inefficiency"}
            ]
            preferences = [DPreference(content=p.get("content", "").strip()) for p in result.get("preferences", []) if p.get("content", "").strip()]
            return memories, patterns, preferences
        except Exception as e:
            logger.warning(f"Failed to analyze (memories-only) chunk {record.id}: {e}")
            return [], [], []
