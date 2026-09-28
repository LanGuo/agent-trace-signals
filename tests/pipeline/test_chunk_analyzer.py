"""Tests for chunk_analyzer module."""



def test_dchunkresult_has_patterns_field():
    from agent_trace_signals.pipeline.chunk_analyzer import DChunkResult, DPattern
    result = DChunkResult(
        summary="test",
        entities=[],
        memories=[],
        patterns=[DPattern(type="strategy", content="read before write")],
    )
    assert len(result.patterns) == 1
    assert result.patterns[0].type == "strategy"
    assert result.patterns[0].content == "read before write"


def test_dchunkresult_patterns_defaults_empty():
    from agent_trace_signals.pipeline.chunk_analyzer import DChunkResult
    result = DChunkResult(summary="test", entities=[], memories=[])
    assert result.patterns == []


def test_analyze_returns_patterns_field(tmp_path):
    """DChunkAnalyzer.analyze() returns a DChunkResult with patterns list (may be empty)."""
    from unittest.mock import MagicMock
    from agent_trace_signals.pipeline.chunk_analyzer import DChunkAnalyzer
    from agent_trace_signals.models import Record, Session

    provider = MagicMock()
    provider.complete_json.return_value = {
        "summary": "Agent checked memory then read files.",
        "entities": [],
        "memories": [],
        "patterns": [
            {"type": "strategy", "content": "agent checks memory before starting any task"}
        ],
    }

    from agent_trace_signals.config import ModelConfig, PipelineConfig
    analyzer = DChunkAnalyzer(provider=provider, config=ModelConfig(), pipeline_config=PipelineConfig())
    record = Record(
        id="r1", session_id="s1", chunk_index=0,
        chunk_text="USER: resume work\nA: Let me check project state. [TOOL: Read(memory.md)]" * 5,
        embedding_text="",
    )
    session = Session(id="s1", org_id="o", workspace_id="w", app_id="a",
                      source_type="agent_trace", source_plugin="cc")
    result = analyzer.analyze(record, session)

    assert hasattr(result, "patterns")
    assert isinstance(result.patterns, list)
    assert len(result.patterns) == 1
    assert result.patterns[0].type == "strategy"
