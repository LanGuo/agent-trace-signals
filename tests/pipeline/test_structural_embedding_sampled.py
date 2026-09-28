from agent_trace_signals.pipeline.utils import build_structural_text


def test_short_session_uses_all_chunks():
    chunks = ["chunk_a", "chunk_b"]
    result = build_structural_text(chunks)
    assert "chunk_a" in result
    assert "chunk_b" in result


def test_single_chunk_session():
    result = build_structural_text(["only chunk"])
    assert result == "only chunk"


def test_long_session_samples_first_middle_last():
    # 12 chunks: first, 10 middle, last
    chunks = [f"chunk_{i}" for i in range(12)]
    result = build_structural_text(chunks)
    assert "chunk_0" in result   # first always included
    assert "chunk_11" in result  # last always included
    # total sampled chunks: 1 + min(8,10) + 1 = 10 — not all 12
    # can't assert exact middle chunks (random), but length should be reasonable
    assert len(result) < sum(len(c) for c in chunks) + 20


def test_truncates_at_32000_chars():
    # each chunk is 5000 chars; 10 chunks would be ~50k chars
    chunks = ["x" * 5000 for _ in range(10)]
    result = build_structural_text(chunks)
    assert len(result) <= 32000


def test_preserves_chunk_order():
    chunks = [f"chunk_{i:02d}" for i in range(15)]
    result = build_structural_text(chunks)
    # chunk_00 (first) must appear before chunk_14 (last)
    assert result.index("chunk_00") < result.index("chunk_14")
