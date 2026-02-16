"""Unit tests for advanced network metrics helpers."""
import networkx as nx

from database.analytics import (
    compute_betweenness_approx,
    compute_bridge_ratio,
    compute_communities,
)


def test_compute_betweenness_approx_is_seed_deterministic():
    graph = nx.path_graph(["a", "b", "c", "d", "e"])
    result_one = compute_betweenness_approx(graph, k=3, seed=42, weight_mode="unweighted")
    result_two = compute_betweenness_approx(graph, k=3, seed=42, weight_mode="unweighted")

    assert set(result_one.keys()) == {"a", "b", "c", "d", "e"}
    assert result_one == result_two
    assert all(value is not None for value in result_one.values())


def test_compute_communities_returns_stable_assignments():
    graph = nx.Graph()
    graph.add_edges_from(
        [
            ("a1", "a2"),
            ("a2", "a3"),
            ("a1", "a3"),
            ("b1", "b2"),
            ("b2", "b3"),
            ("b1", "b3"),
            ("a3", "b1"),
        ]
    )

    assignment_one = compute_communities(graph)
    assignment_two = compute_communities(graph)

    assert assignment_one == assignment_two
    assert set(assignment_one.keys()) == {"a1", "a2", "a3", "b1", "b2", "b3"}
    assert all(isinstance(value, int) for value in assignment_one.values())


def test_compute_bridge_ratio_uses_system_labels():
    nodes = [
        {"id": "a"},
        {"id": "b"},
        {"id": "c"},
        {"id": "d"},
    ]
    edges = [
        {"source": "a", "target": "b", "weight": 1.0},
        {"source": "a", "target": "c", "weight": 1.0},
        {"source": "c", "target": "d", "weight": 1.0},
    ]
    labels = {
        "a": "state",
        "b": "state",
        "c": "federal",
        "d": None,
    }

    ratios = compute_bridge_ratio(nodes, edges, labels)

    assert ratios["a"] == 0.5
    assert ratios["b"] == 0.0
    assert ratios["c"] == 1.0
    assert ratios["d"] is None
