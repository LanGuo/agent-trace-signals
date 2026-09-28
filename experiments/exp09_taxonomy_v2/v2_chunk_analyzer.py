"""Experimental D-prompt v2: taxonomy redesign.

Changes from production chunk_analyzer.py:
- `semantic` memory type removed entirely — folds into `episodic` with mandatory framing
  as a point-in-time observation, never a timeless claim. "Stable fact" status is now
  something only cross-session consolidation can earn, not something extraction can claim.
- `episodic` gains a `status` field (resolved|open|reverted) and drops the old
  "skip if no resolution" filter — open questions/decisions-in-flux are now capturable.
- `procedural` tightened to durable PUBLIC interfaces only (CLI/API), not internal
  implementation details wearing a how-to's clothing.
- New `preferences` field: user-stated instructions/corrections, durable because they're
  about the user, not the system.
- `patterns` unchanged — already the strongest-performing category in both prior audits.
- Overall bar raised: explicit "extract sparingly, when in doubt omit" framing up front.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field

from agent_trace_signals.config import ModelConfig, PipelineConfig
from agent_trace_signals.models import Record, Session
from agent_trace_signals.providers.base import ModelProvider
from agent_trace_signals.pipeline.chunk_analyzer import D_TYPE_NORMALIZATION, _SHARED_ENTITY_VOCAB

logger = logging.getLogger(__name__)


@dataclass
class DEntity:
    name: str
    type: str
    role: str


@dataclass
class DMemory:
    type: str          # "episodic" | "procedural"
    status: str         # "resolved" | "open" | "reverted" | "" (procedural has no status)
    content: str


@dataclass
class DPattern:
    type: str
    content: str


@dataclass
class DPreference:
    content: str


@dataclass
class DChunkResultV2:
    summary: str
    entities: list[DEntity]
    memories: list[DMemory]
    patterns: list[DPattern] = field(default_factory=list)
    preferences: list[DPreference] = field(default_factory=list)


class DChunkAnalyzerV2:
    """Analyze chunks using the redesigned taxonomy D-prompt."""

    def __init__(self, provider: ModelProvider, config: ModelConfig, pipeline_config: PipelineConfig | None = None) -> None:
        self.provider = provider
        self.config = config
        self.pipeline_config = pipeline_config

    def _normalize_entity_type(self, raw_type: str) -> str:
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

    def analyze(self, record: Record, session: Session) -> DChunkResultV2:
        if len(record.chunk_text) < 200:
            return DChunkResultV2(summary="", entities=[], memories=[])

        chunk_text = record.chunk_text[:6000]

        prompt = f"""You are extracting structured knowledge from an agent session chunk for long-term recall.
A future user will ask "what did I work on in <project>?" — your output should help answer that.

Before extracting anything, decide whether the chunk shows the agent's own first-hand actions/
observations in THIS session, or whether it is quoting, displaying, or reviewing content that
originated elsewhere (e.g. inspecting stored memory/database records, pasting example output,
summarizing another tool's prior results). Only extract entities/memories/patterns from genuine
first-hand observations. If the chunk is reviewing or quoting other content, you may extract facts
ABOUT that act of review (e.g. "agent reviewed N stored records and found them accurate") but do
NOT extract the quoted material's subject matter as if the agent newly observed or asserted it —
this applies to EVERY field below, including entities: a name mentioned only inside quoted/reviewed
content is not a first-hand entity, skip it, even if that name would otherwise look important.

Extract SPARINGLY on top of that. Every field below should clear a high bar — when in doubt,
extract nothing. A future reader trusts everything you return without re-checking it; a wrong or
trivial entry costs more than a missed one. Most chunks should produce few or zero items in most
fields.

Produce JSON with five fields:

1. summary — 3-5 sentences covering: what was being worked on (name the project/system),
   what problem or gap was identified, what decision or action resulted and WHY.
   Skip file paths, passing test counts, and routine commits unless something unexpected happened.

2. entities — named things worth remembering across sessions. For each, include name, type, and
   a one-phrase role explaining its significance in this chunk. Re-check each candidate against the
   citation rule above before including it — if it only appears inside quoted/reviewed material, skip it.
   "Extract sparingly" (above) is a bar on QUALITY, not a cap on COVERAGE — a chunk that mentions
   five distinct files worth remembering should return five entities, not stop after the first one
   or two. Scan the entire chunk before finalizing this list; do not stop as soon as you have a
   plausible answer.
   Types (use the most specific match):
   {self._entity_types_str()}
   SKIP: generic tool names (Bash, Read, Edit, Write), usernames, file paths, version numbers,
   hyperparameters, UI elements (buttons, menu items, labels), internal code symbols/function
   names (unless the symbol itself names a published library/framework), and anything that only
   matters within this single session. If something doesn't clearly fit a type above, omit it —
   do not default it into "technology" just because nothing else fits.

3. memories — durable observations a future session should know. Two types only — there is no
   "semantic"/stable-fact type: a single chunk cannot verify that something is permanently true,
   only that it was true/observed/decided at this point. If you're tempted to write "X is Y",
   rewrite it as "X was Y as of this session" or skip it — claiming permanence is not this
   extraction's job, only a separate cross-session process that can see multiple sessions over
   time is allowed to decide something is actually stable.
   - episodic: a specific, CONSEQUENTIAL event, decision, or observation — not a routine step,
     not incidental detail. Include a `status`:
       "resolved" — concluded, with a clear outcome.
       "open" — still being discussed/decided, no resolution yet. Capture what's being weighed,
                 not just settled outcomes — e.g. "the team is deciding between X and Y for Z,
                 no decision yet" is a valid, useful episodic memory.
       "reverted" — was decided, then undone/changed again within this same chunk.
     Do not skip open/unresolved items — they were wrongly excluded before and that's a real gap.
   - procedural: a reusable how-to, but ONLY for a durable PUBLIC interface — a CLI command, a
     documented API, a config flag meant for external use. Do NOT extract internal implementation
     detail dressed up as a how-to (e.g. "internally, function X does Y" is a fact about internals,
     not a procedure — omit it, or reframe as episodic if genuinely noteworthy). `status` is not
     applicable to procedural entries — leave it empty.
   Each memory MUST name a specific project/tool/concept. Skip trivial outcomes and vague content.

4. patterns — agent reasoning observations worth remembering for future sessions. Three types —
   `decision` and `strategy` are merged into one (`strategy`); the old boundary between "a one-off
   choice" and "a stated repeatable rule" was too soft to classify consistently, causing the same
   underlying event to land in different buckets depending on incidental phrasing.
   ONLY extract if something notable is observable in THIS chunk's own content. Return [] if
   nothing stands out — an empty array is a normal, expected result, not a failure to avoid.
   The three "shape" descriptions below describe the FORM each pattern type takes, not real
   content — never reuse their wording, subject matter, or any specific noun from them (no model
   names, no function names, no scenario details from these descriptions) in your actual output.
   A pattern copied from these shapes instead of derived from the chunk is a fabrication, not an
   extraction.
   MAX 2 per chunk. Must be non-ephemeral — abstract away session-specific names, paths, model names.
   Skip ONLY if the pattern is substantially identical (same steps, same action verbs) to a
   PROCEDURAL memory already in field 3. Overlap with episodic memories does NOT trigger suppression.
   Patterns capture HOW the agent reasoned (meta-level mindset, not steps).

   `recovery` is still a special case of `strategy` (a choice made under a stuck state) — pick
   exactly ONE per event, do not emit the same underlying event under two types:
     1. If a stuck-state → fix arc is present (repeated failures, errors, or backtracking), check
        the evidence bar under `recovery` below before classifying it there.
     2. Otherwise, if a choice was made and reasoned about — whether a one-off decision or a
        stated repeatable rule — classify as `strategy`.
     3. `inefficiency` is independent of the other two — evaluate it separately (see below).
   - strategy: HOW or WHAT the agent decided, and WHY — covers both a single choice-with-reason
     and a stated repeatable method, abstracting away specific tool/model/file names.
     Shape: "when [condition/tradeoff], [agent's method or choice] — e.g. [category of action]
     over [category of action] — because [reason]". Not a shape: literally doing one specific
     read then one specific edit with no stated reasoning — that's a step, not a strategy.
   - recovery: ONLY if the agent demonstrably got stuck (repeated failures, errors, backtracking)
     AND the chunk shows verifying evidence that the fix actually worked — test output, a command
     result, a passing check, a file diff confirming the change — not merely the agent's own
     stated belief that it worked. If the chunk ends with the agent claiming success but no
     verifying evidence is shown, do NOT classify as recovery — use `strategy` instead (the
     choice-under-stress is still capturable there) or, if genuinely unresolved, leave it for
     episodic with status "open". This bar exists because agents are known to narrate an
     unverified fix as if it succeeded — extracting that narration as a confirmed "recovery"
     launders the false confidence into a memory a future session would trust.
     Shape: "[stuck state] caused by [root cause]; agent [fix] — verified by [evidence] —
     addresses [general class of problem]", grounded in this chunk's specific failure and fix.
     Root cause, if stated or clearly inferable, fits one of: wrong assumption about behavior,
     missed spec/requirement, misread output, ignored an available signal, acted before enough
     information, knowledge gap, or environment/tooling limitation — use whichever fits, don't
     force one if none clearly applies.
   - inefficiency: ONLY if a shorter/faster path existed. Shape: "[what was done] could have
     used [better approach] instead — avoids [cost] for [operation]", grounded in this chunk's
     actual operation, not a hypothetical one.

5. preferences — explicit instructions, corrections, or standing preferences the USER stated
   about how they want to work or be worked with — NOT facts about the project's design or code.
   Examples: "always run tests before committing", "don't use emoji", "prefers terse answers",
   "wants to review before any destructive action". ONLY extract if the user is explicitly stating
   a preference/instruction or correcting the agent's approach — never infer one from context or
   from a single instance of the agent happening to do something a certain way.

Return JSON:
{{
  "summary": "...",
  "entities": [{{"name": "...", "type": "...", "role": "..."}}],
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
                            "status": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["type", "content"],
                    },
                },
                "patterns": {
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
                "preferences": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"content": {"type": "string"}},
                        "required": ["content"],
                    },
                },
            },
            "required": ["summary", "entities", "memories", "patterns", "preferences"],
        }

        try:
            result = self.provider.complete_json(
                prompt, schema=schema, model=self.config.chunk_summarizer, max_tokens=1024,
            )

            summary = result.get("summary", "").strip()
            entities = [
                DEntity(
                    name=e.get("name", "").strip(),
                    type=self._normalize_entity_type(e.get("type", "")),
                    role=e.get("role", "").strip(),
                )
                for e in result.get("entities", [])
                if e.get("name", "").strip()
            ]
            memories = [
                DMemory(
                    type=m.get("type", "").strip(),
                    status=m.get("status", "").strip(),
                    content=m.get("content", "").strip(),
                )
                for m in result.get("memories", [])
                if m.get("content", "").strip() and m.get("type", "").strip() in {"episodic", "procedural"}
            ]
            from agent_trace_signals.pipeline.utils import _is_trivial
            memories = [m for m in memories if not _is_trivial(m.content)]

            patterns = [
                DPattern(type=p.get("type", "").strip(), content=p.get("content", "").strip())
                for p in result.get("patterns", [])
                if p.get("content", "").strip()
                and p.get("type", "").strip() in {"strategy", "recovery", "inefficiency"}
            ]
            preferences = [
                DPreference(content=p.get("content", "").strip())
                for p in result.get("preferences", [])
                if p.get("content", "").strip()
            ]

            return DChunkResultV2(summary=summary, entities=entities, memories=memories, patterns=patterns, preferences=preferences)

        except Exception as e:
            logger.warning(f"Failed to analyze chunk {record.id}: {e}")
            return DChunkResultV2(summary="", entities=[], memories=[], patterns=[], preferences=[])
