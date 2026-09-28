"""Tests for pipeline/utils.py — build_structural_similarity_edges."""
from agent_trace_signals.pipeline.utils import build_structural_similarity_edges


def test_returns_edges_above_threshold():
    vecs = [
        ("s1", [1.0, 0.0]),
        ("s2", [0.99, 0.01]),   # near-identical to s1
        ("s3", [0.0, 1.0]),     # orthogonal to s1/s2
    ]
    edges = build_structural_similarity_edges(vecs, top_k=8, min_similarity=0.5)
    pairs = {(e.source_session_id, e.target_session_id) for e in edges}
    assert ("s1", "s2") in pairs
    assert ("s2", "s1") in pairs
    assert ("s1", "s3") not in pairs
    assert ("s3", "s1") not in pairs


def test_edges_are_bidirectional_with_matching_weight():
    vecs = [("s1", [1.0, 0.0]), ("s2", [1.0, 0.0])]
    edges = build_structural_similarity_edges(vecs, min_similarity=0.5)
    by_pair = {(e.source_session_id, e.target_session_id): e.weight for e in edges}
    assert by_pair[("s1", "s2")] == by_pair[("s2", "s1")]


def test_top_k_caps_neighbours_per_session():
    vecs = [(f"s{i}", [1.0, 0.0]) for i in range(10)]  # all identical, all above threshold
    edges = build_structural_similarity_edges(vecs, top_k=3, min_similarity=0.5)
    from collections import Counter
    counts = Counter(e.source_session_id for e in edges)
    assert all(c <= 3 for c in counts.values())


def test_no_edges_below_threshold():
    vecs = [("s1", [1.0, 0.0]), ("s2", [0.0, 1.0])]
    edges = build_structural_similarity_edges(vecs, min_similarity=0.9)
    assert edges == []


def test_single_session_no_edges():
    edges = build_structural_similarity_edges([("s1", [1.0, 0.0])])
    assert edges == []


def test_edge_type_and_via_entity_id():
    vecs = [("s1", [1.0, 0.0]), ("s2", [1.0, 0.0])]
    edges = build_structural_similarity_edges(vecs, min_similarity=0.5)
    assert all(e.edge_type == "structural_similarity" for e in edges)
    assert all(e.via_entity_id == "" for e in edges)
