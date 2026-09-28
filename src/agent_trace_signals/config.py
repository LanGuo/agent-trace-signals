"""Runtime configuration — all defaults are single-user laptop values."""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ScannerConfig:
    claude_code_paths: list[str] = field(
        default_factory=lambda: [str(Path.home() / ".claude" / "projects")]
    )
    gemini_paths: list[str] = field(
        default_factory=lambda: [str(Path.home() / ".gemini" / "tmp")]
    )
    opencode_paths: list[str] = field(
        default_factory=lambda: [str(Path.home() / ".local" / "share" / "opencode")]
    )
    include_subagents: bool = False
    zoom_paths: list[str] = field(default_factory=list)
    otter_paths: list[str] = field(default_factory=list)
    archive_dir: str = "data/archive"  # local copy of source files; relative to CWD or absolute


@dataclass
class ModelConfig:
    ollama_base_url: str = "http://localhost:11434"
    # gemma4:e4b (taxonomy v2 prompt) beats gemma3:12b and a 2-model entity/memory split on
    # memories/patterns/preferences at half the split's inference cost — see design_decisions.md
    # 2026-07-22. d_prompt_max_tokens below must move with this: gemma4:e4b is a thinking model
    # and the old chunk_summarizer default (gemma3:12b) didn't need the headroom.
    chunk_summarizer: str = "gemma4:e4b"
    entity_extractor_llm: str = "gemma4:e4b"  # fast for short per-chunk prompts; gemma3:12b for complex/long prompts
    explicit_memory: str = "gemma3:12b"
    session_summarizer: str = "gemma3:12b"
    light_analytics_llm: str = "gemma3:12b"
    cluster_labeler: str = "claude-haiku-4-5-20251001"
    conflict_verifier: str = "claude-haiku-4-5-20251001"
    # Observer LLM for per-step trajectory scoring (Stage 2).
    # Default: local Ollama (reliable JSON). For Anthropic Haiku, set to
    # e.g. "claude-haiku-4-5-20251001" — model name routes to AnthropicProvider.
    observer_llm: str = "gemma4:e4b"
    anthropic_api_key: str | None = None    # falls back to ANTHROPIC_API_KEY env var
    embedding_model: str = "nomic-embed-text"
    # KV cache context window for Ollama calls. Model default is 131072 which allocates
    # ~32 GB of GPU memory per call even for 500-token prompts. 8192 covers our longest
    # prompts (fact-compression context ~4K tokens) and drops cache to ~2 GB.
    ollama_num_ctx: int = 8192
    # gemma4:e4b is a thinking model: num_predict caps *total* generated tokens including
    # chain-of-thought reasoning. Multi-chunk verification prompts trigger longer reasoning,
    # so the verifier needs a higher budget than the default 512 used for short extraction
    # prompts. num_predict does NOT affect KV cache size (that's controlled by ollama_num_ctx).
    entity_verifier_max_tokens: int = 2048
    # Stub chunk filter for step-scoring (Stage 2).
    # Exchanges where combined evidence_text + user_text is below this threshold
    # are skipped before calling the observer LLM. Chunk-level embedding EDA
    # (2026-06-13) identified near-empty chunks (mean=21 chars) as a distinct
    # cluster that generates near-zero progress/cost vectors and fires
    # spurious failure_critical detections. 50 chars is above that noise floor
    # while well below any meaningful tool output or user directive.
    min_evidence_chars: int = 50
    # Max tokens for observer LLM per exchange. 512 is enough for gemma3:12b
    # (non-thinking, JSON-only output). Thinking models (qwen3, gemma4:e4b)
    # emit <think>...</think> blocks before JSON — 2048 gives ~1500 tokens of
    # reasoning headroom + the JSON output. Increase if you see truncated responses.
    observer_max_tokens: int = 2048
    # Answer-generation model for the Query page's "Generate Answer" RAG synthesis.
    # Deliberately separate from chunk_summarizer (which drives ingestion's combined
    # per-chunk extraction calls) so bumping quality here doesn't also slow down/change
    # ingestion. gemma4:31b was the original choice but measured ~7x slower than
    # gemma4:e4b on equivalent answer-generation prompts (~79s vs ~11s) with no
    # noticeable quality drop — same finding as the cluster-memories consolidation
    # swap (see design_decisions.md 2026-07-13). num_predict=2048 confirmed empirically
    # sufficient for gemma4:e4b's <think> reasoning + full, non-truncated answer text.
    query_answer_model: str = "gemma4:e4b"
    query_answer_max_tokens: int = 2048
    # num_predict budget for the D-prompt combined call (chunk_analyzer.py). gemma4:e4b is a
    # thinking model — reasoning tokens count against this before any JSON output. Measured
    # directly (num_predict swept 4096-16384 on the full v2 combined prompt): natural
    # done_reason="stop" at eval_count=1473 on the tested chunk, not truncation — 12288 (the
    # untuned ceiling used during taxonomy-v2 experimentation) was far more than needed. 4096
    # gives comfortable headroom over the highest observed per-chunk output in that test set
    # (7 entities/3 memories/1 pattern) without inheriting the unjustified larger figure.
    d_prompt_max_tokens: int = 4096


@dataclass
class PipelineConfig:
    max_chunk_tokens: int = 800
    chunk_overlap_pct: float = 0.10
    occurrence_context_sentences: int = 2   # sentences either side of mention
    session_promotion_min_chunks: int = 2   # chunks within a session before entity is 'session'-promoted
    structural_entity_types: tuple[str, ...] = ("file", "commit", "pr")
    # GLiNER open-vocabulary NER labels → mapped to canonical entity types.
    # Extend this list to teach the pipeline new entity categories without code changes.
    ner_entity_labels: tuple[str, ...] = (
        "software library or framework",
        "ML or AI model",
        "cloud service or API",
        "database or storage system",
        "programming language",
        "research methodology or concept",
        "dataset or benchmark",
        "person name",
        "organization or company",
    )
    # GLiNER model to use (downloaded automatically on first use, ~400 MB)
    gliner_model: str = "urchade/gliner_multi-v2.1"
    # Entity types for the D-prompt combined analyzer: {type_label: description}.
    # Both the label and description are sent to the LLM so it classifies
    # consistently. Types are normalized to the shared entity vocabulary
    # before storage (see chunk_analyzer.D_TYPE_NORMALIZATION).
    d_entity_types: dict = field(default_factory=lambda: {
        "technology": "libraries, tools, CLIs, APIs, languages, platforms, services — NOT "
                      "hyperparameters, UI elements, or internal code symbols/function names; "
                      "if a mention doesn't clearly name a real published technology, omit it "
                      "rather than defaulting it here",
        "framework":  "orchestration and workflow libraries (→ stored as technology)",
        "algorithm":  "specific named algorithms or methods (→ stored as concept)",
        "concept":    "abstractions, patterns, architectural ideas, methodologies",
        "model":      "ML/AI models and model families",
        "dataset":    "named benchmarks, corpora, or datasets",
        "person":     "named individuals",
        "project":    "named repos, products, or systems being built (→ stored as technology)",
        "file":       "a specific source file, config file, or doc mentioned by name/path "
                      "(e.g. foo.py, README.md, src/utils.ts) — classify here even if only "
                      "mentioned in prose, not just when edited via a tool call",
    })


@dataclass
class LightAnalyticsConfig:
    entity_promotion_threshold: int = 3      # degree_count threshold
    max_occurrences_per_entity: int = 20     # top-N occurrences by recency for fact compression
    min_atomic_facts: int = 1               # skip memory extraction if fewer facts than this
    min_memory_words: int = 10              # drop memories shorter than this word count
    occurrence_promotion_threshold: int = 20  # total mentions in any sessions
    max_entity_session_coverage: float = 0.7  # exclude entities present in > 70% of sessions (cross-session IDF)
    max_entity_chunk_coverage: float = 0.7    # exclude entities present in > 70% of chunks (within-session IDF)


@dataclass
class CriticalStepConfig:
    """Thresholds for Stage 3 critical-step detection.

    Defaults are calibrated for the DEFAULT observer (gemma3:12b via Ollama),
    which scores conservatively (typical max ~0.25 on healthy work). When
    using a more capable observer (e.g. Anthropic Haiku via
    `ModelConfig.observer_llm = "claude-haiku-4-5-..."`), the score
    distribution is wider; raise delta_high to ~0.6-0.8 and delta_low
    to ~0.35 for tighter filtering.

    Empirical defaults (gemma3:12b on SLR-gemma4 first-10-scorable test,
    after the user_text fix): scores 0.0-0.249. With these defaults the
    detector flags ~40% of scored exchanges as critical — a reasonable
    starting density before threshold tuning on a labeled corpus.
    """
    delta_low: float = 0.10
    delta_high: float = 0.20
    delta_change: float = 0.10
    k: int = 3   # window for recovery: prior k steps must be in stuck state

    # FP-suppression rules informed by 2026-06-12 cohort-1 labeling (Path C):
    skip_first_exchange: bool = True
    """Skip detection at exchange_idx==0 (resume-after-break artifacts —
    first exchange in a resumed session looks token-bloated vs. empty
    session median and fires trajectory_inflation FPs).
    """
    absolute_low_requires_corroboration: bool = True
    """Tighten the pure absolute_low failure_critical rule.

    When score ≤ delta_low AND delta ≈ 0 (the step was already at the bottom,
    not a fresh drop), only fire if there is corroborating evidence of real effort
    that failed: either substantial token cost (cost_vector.tokens > 0.3) or the
    features JSON records tool errors.

    Without this gate, every neutral exchange — research discussion, doc update,
    git commit — that happens to produce score=0 fires failure_critical because
    the observer correctly notes zero SWE-progress but there's no actual failure.
    The negative_jump rule (score dropped from something positive) is already
    well-targeted; it's the plain absolute_low with delta=0 that over-fires.

    Disabled automatically when delta < -epsilon (negative jump already supplies
    the corroboration), so the jump rule is unaffected.
    """

    use_session_percentile_thresholds: bool = True
    """Within-session threshold calibration. When True, effective delta_low and
    delta_high are computed as max(config_absolute, session_p20/p80) so that the
    detector fires on the relatively worst/best exchanges within a session rather
    than against a fixed absolute bar.

    Fixes two observed saturation modes:
    - Gemini exploratory sessions: observer scores compressed to [0.0, 0.25] due
      to redundancy=1.0 (repeated tool calls) → every exchange scores 0 → every
      exchange fires failure_critical under absolute delta_low=0.10.
    - CC discussion sessions: most exchanges score 0.0 (no SWE progress dims) →
      same saturation.

    With percentile calibration, delta_low rises to match the session's p20, so
    only the genuinely bottom-20% exchanges fire as failure_critical. Falls back
    to absolute thresholds for sessions with <5 scored exchanges.
    """

    max_human_tool_ratio: float | None = 8.0
    """Session-level activity gate based on human_tool_ratio (genuine_human_turns /
    n_tool_calls). Replaces the old min_tool_using_exchanges count-based gate.

    Embedding EDA (2026-06-13) showed human_tool_ratio is the strongest single
    predictor of cluster membership (RF importance 0.20) and correlates strongly
    with the UMAP autonomy axis (r=0.75). Pure conversation sessions have
    n_tool_calls=0 (→ skipped unconditionally); discussion-heavy sessions where
    the human says far more than the agent does (ratio > 8) are also skipped.

    Calibrated from observed cluster extremes:
      - Cluster 2 (pure conversation): ratio = ∞  (0 tool calls)
      - Cluster 5 (Gemini thinking-only): ratio = ∞
      - ATS design sessions: ~15-20 (many human turns, very few tool calls)
      - ATS implementation sessions: ~0.5-3.0
      - Long-horizon coding: ~0.15-0.5

    Set to None to disable the gate entirely.
    """


@dataclass
class Config:
    db_path: str = "traces.db"
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    models: ModelConfig = field(default_factory=ModelConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    light_analytics: LightAnalyticsConfig = field(default_factory=LightAnalyticsConfig)

    @classmethod
    def default(cls) -> "Config":
        return cls()
