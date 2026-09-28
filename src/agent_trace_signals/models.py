"""Pydantic data models — map 1:1 to DB schema tables."""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import hashlib
import uuid
from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Ingestion units
# ---------------------------------------------------------------------------

class Session(BaseModel):
    id: str                         # sha256(plugin+path+content_hash)
    org_id: str = "default"
    workspace_id: str = "default"   # encoded project-dir for claude_code
    app_id: str = "default"
    user_id: str = "default"
    source_type: str                # "agent_trace" | "meeting_transcript"
    source_plugin: str              # "claude_code" | "zoom" | "otter" | "otel"
    session_timestamp: str | None = None
    ingested_at: str = Field(default_factory=_now)
    session_summary: str = ""
    embedding_text: str = ""
    raw_facets: str = "{}"          # JSON
    tags: str | None = None         # JSON array
    metadata: str | None = None     # JSON escape hatch
    turn_descriptors: str | None = None  # JSON array of TurnDescriptor objects
    chunk_count: int = 0
    cluster_id: str | None = None
    analytics_run_id: str | None = None


class Record(BaseModel):
    id: str                         # sha256(session_id+chunk_index)
    session_id: str
    chunk_index: int
    chunk_text: str
    chunk_summary: str | None = None
    evidence_text: str = ""
    embedding_text: str = ""
    token_count: int | None = None
    span_start: str | None = None
    span_end: str | None = None

    @staticmethod
    def make_id(session_id: str, chunk_index: int) -> str:
        return hashlib.sha256(f"{session_id}:{chunk_index}".encode()).hexdigest()


class Entity(BaseModel):
    id: str
    org_id: str = "default"
    workspace_id: str = "default"
    canonical_name: str
    entity_type: str                # "person"|"tool"|"technology"|"file"|"commit"|"pr"|"concept"
    degree_count: int = 0
    confidence: str = "raw"   # "raw" | "llm_verified" | "session" | "corpus"
    first_seen: str = Field(default_factory=_now)
    last_seen: str = Field(default_factory=_now)
    source_diversity: str | None = None   # JSON
    entity_profile: str | None = None
    memory_promoted: str | None = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    @staticmethod
    def make_structured_id(org_id: str, entity_type: str, canonical_name: str) -> str:
        key = f"{org_id}:{entity_type}:{canonical_name}".lower()
        return hashlib.sha256(key.encode()).hexdigest()


class Occurrence(BaseModel):
    id: str
    record_id: str
    session_id: str
    entity_id: str
    role: str   # "subject"|"tool_used"|"person_mentioned"|"file_modified"|"commit_ref"|"topic"
    mention_text: str
    context_text: str
    span_start: str | None = None
    span_end: str | None = None
    created_at: str = Field(default_factory=_now)

    @staticmethod
    def make_id(record_id: str, entity_id: str, span_start: str) -> str:
        key = f"{record_id}:{entity_id}:{span_start}"
        return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Memory layer
# ---------------------------------------------------------------------------

class Memory(BaseModel):
    id: str = Field(default_factory=_uuid)
    org_id: str = "default"
    workspace_id: str = "default"
    app_id: str = "default"
    memory_type: str               # "episodic" | "procedural" | "preference" | "pattern_*" | "cluster"
    extraction_method: str         # "explicit" | "frequency" | "cluster"
    content: str
    status: str | None = None      # episodic only: "resolved" | "open" | "reverted"
    evidence_count: int = 1
    first_observed: str | None = None
    last_observed: str | None = None
    tags: str | None = None
    conflict_status: str = "none"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    created_by_pipeline: str = "ingestion"
    analytics_run_id: str | None = None
    entity_id: str | None = None
    access_count: int = 0
    last_accessed_at: str | None = None
    metadata: str | None = None


PATTERN_TYPES: frozenset[str] = frozenset({
    "pattern_strategy",
    "pattern_recovery",
    "pattern_inefficiency",
})

MEMORY_TYPES: frozenset[str] = frozenset({
    "episodic", "procedural", "preference",
}) | PATTERN_TYPES


class MemorySource(BaseModel):
    memory_id: str
    session_id: str
    record_id: str | None = None
    occurrence_id: str | None = None
    relevance_score: float = 1.0
    span_hint: str | None = None


class SessionGraphEdge(BaseModel):
    source_session_id: str
    target_session_id: str
    edge_type: str          # "structural" | "workspace" | "shared_memory"
    via_entity_id: str | None = None
    weight: float = 1.0
    direction: str = "undirected"
    created_by: str = "ingestion"
    created_at: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class IngestionState(BaseModel):
    id: str                         # sha256(abs_path)
    abs_path: str
    file_type: str                  # "claude_code" | "zoom" | "otter" | "otel"
    content_hash: str
    session_id: str | None = None
    last_seen_at: str = Field(default_factory=_now)
    last_ingested_at: str | None = None
    status: str = "pending"         # "pending"|"ingested"|"failed"|"skipped"
    error: str | None = None
    metadata: str | None = None     # JSON


# ---------------------------------------------------------------------------
# Plugin output — what a source plugin returns
# ---------------------------------------------------------------------------

class RawChunk(BaseModel):
    """Raw chunk from a source plugin before summarization/embedding."""
    chunk_index: int
    chunk_text: str
    evidence_text: str = ""
    span_start: str | None = None
    span_end: str | None = None
    token_count: int | None = None


class RawEntityMention(BaseModel):
    """Entity mention extracted before resolution."""
    canonical_name: str
    entity_type: str
    mention_text: str
    context_text: str
    role: str
    span_start: int | None = None   # char offset in chunk_text
    span_end: int | None = None     # char offset in chunk_text
    source_layer: str = "regex"     # "tool_call" | "regex" | "ner" | "llm"


class ParsedSession(BaseModel):
    """Full output of a source plugin's parse() call."""
    source_plugin: str
    source_type: str
    session_timestamp: str | None = None
    raw_facets: dict[str, Any] = {}
    chunks: list[RawChunk] = []
    workspace_id: str = "default"
    metadata: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Inflection detection (Track 2)
# ---------------------------------------------------------------------------

class TurnDescriptor(BaseModel):
    """Compact per-exchange summary for inflection detection. Extracted by source plugin."""
    exchange_idx: int
    user_word_count: int
    tool_calls: list[dict] = Field(default_factory=list)  # [{"name": str, "target": str}]
    tool_errors: list[bool] = Field(default_factory=list)  # is_error per tool result
    model_turn_count: int = 0  # model response turns in this exchange (>1 = fragmented execution)
    exchange_type: str = "genuine_human"  # classifier output; see GENUINE_TYPES / SCORABLE_TYPES
    avg_think_tok_ratio: float = 0.0      # Gemini only; 0.0 for CC/Opencode
    user_text: str = ""                   # raw user message text (capped); needed by Stage 2 observer


# Exchange classifier — taxonomy filter sets (see design/exchange_classifier.md)
GENUINE_TYPES = frozenset({"genuine_human", "genuine_text"})
SCORABLE_TYPES = frozenset({
    "genuine_human", "genuine_text", "agent_continuation", "summary_diff",
})
NON_SCORABLE_TYPES = frozenset({
    "skill_invocation", "task_notification", "context_continuation", "wakeup_injection",
})


class SessionInflection(BaseModel):
    """Detected inflection episode in a session."""
    id: str
    session_id: str
    inflection_type: str          # loop_entry|loop_exit|user_correction|escalation|strategy_pivot
    precipitating_turn: int       # estimated root-cause exchange index
    entry_turn: int               # detectable threshold crossing
    exit_turn: int | None = None  # resolution; None if unresolved
    signal_strength: float        # z-score magnitude of dominant feature at entry_turn
    dominant_feature: str         # action_entropy|action_target_recurrence|error_rate|user_turn_length_ratio
    signal_detail: str | None = None  # JSON: feature values at detection time
    detected_at: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Session metadata (extracted by source plugins)
# ---------------------------------------------------------------------------

class SessionMetadata(BaseModel):
    """Per-session aggregate metadata extracted from raw trace messages."""
    session_id: str
    dominant_model: str | None = None
    models_used: list[str] = Field(default_factory=list)
    entrypoint: str | None = None        # "sdk-cli" | "ide" | "cli"
    permission_mode: str | None = None   # "bypassPermissions" | "default" | "plan"
    user_type: str | None = None
    is_sidechain: bool = False
    cc_version: str | None = None
    user_turn_count: int = 0
    assistant_turn_count: int = 0
    tool_call_count: int = 0
    start_time: str | None = None
    end_time: str | None = None
    duration_ms: int | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_thought_tokens: int = 0        # Gemini only
    cwd: str | None = None
    git_branch: str | None = None


# ---------------------------------------------------------------------------
# Memory retrieval log (Stage 6)
# ---------------------------------------------------------------------------

class MemoryRetrieval(BaseModel):
    """Logged whenever a memory is surfaced to a caller. Input to future
    retrieval-conditioned posterior computation (see design/memory_evolution.md)."""
    id: str                              # sha256(memory_id+retrieved_at+session_id) for idempotency
    session_id: str | None = None        # nullable for ad-hoc / MCP retrievals not tied to a session
    segment_id: str | None = None        # reserved for future use
    exchange_idx: int | None = None      # nullable when retrieval isn't anchored to a session step
    memory_id: str
    query_text: str | None = None        # the query string that surfaced this memory, if available
    query_features: str = "{}"           # JSON: bucketed features at retrieval time, empty dict for v1
    relevance_rank: int                  # 1-based; 1 = top-ranked in the result set
    relevance_score: float | None = None # raw score (BM25, cosine, RRF) for downstream calibration
    surfaced: bool = True                # did this memory get returned to caller (vs. considered but cut by top-k)
    retrieved_at: str = Field(default_factory=_now)

    @staticmethod
    def make_id(memory_id: str, retrieved_at: str, session_id: str | None) -> str:
        key = f"{memory_id}:{retrieved_at}:{session_id or ''}"
        return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Per-step trajectory scoring (Stage 2)
# ---------------------------------------------------------------------------

class StepScore(BaseModel):
    """One observer-judged scoring row per scorable exchange.

    See design/trajectory_signals.md for field semantics.
    """
    session_id: str
    exchange_idx: int
    evidence_supports: float                   # [0, 1] — clipped scalar
    progress_vector: str                       # JSON: {delta_test, delta_scope, delta_patch, delta_info}
    cost_vector: str                           # JSON: {tokens, redundancy}
    agent_action_summary: str | None = None
    features: str = "{}"                       # JSON: bucketed evidence features
    observer_model: str | None = None
    scored_at: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Critical-step detection (Stage 3)
# ---------------------------------------------------------------------------

class CriticalStep(BaseModel):
    """One critical step (failure/success/recovery) detected from step_scores.

    See design/trajectory_signals.md "Critical-step detection".
    """
    id: str
    session_id: str
    exchange_idx: int
    tag: str                  # 'failure_critical' | 'success_critical' | 'recovery_critical'
    failure_mode: str | None = None
    delta: float
    score: float
    features: str = "{}"
    detected_at: str = Field(default_factory=_now)

    @staticmethod
    def make_id(session_id: str, exchange_idx: int, tag: str) -> str:
        key = f"{session_id}:{exchange_idx}:{tag}"
        return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

class AnalyticsRun(BaseModel):
    id: str
    pipeline: str                      # "light" | "full"
    started_at: str
    completed_at: str | None = None
    session_count: int | None = None
    memory_count: int | None = None
    cluster_count: int | None = None
    conflict_count: int | None = None
    k_value: int | None = None
    embedding_model: str
    labeler_model: str | None = None
