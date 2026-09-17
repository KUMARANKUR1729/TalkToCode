"""Graph visualisation. Checks the DOT is well-formed and label-safe."""
import pytest
from conftest import TARGETS

from graphify import viz
from graphify.retrieval import gather_context, retrieve


def test_empty_blocks_produce_no_chart(graphs):
    assert viz.neighbourhood_dot(graphs[TARGETS[0]], []) == ""


@pytest.mark.parametrize("target", TARGETS)
def test_dot_is_balanced_and_quoted(graphs, target):
    graph = graphs[target]
    node = next(n for n in graph.symbols
                if n["type"] in ("method", "function", "hook", "component"))
    blocks = gather_context(graph, [node])
    dot = viz.neighbourhood_dot(graph, blocks)

    assert dot.startswith("digraph G {") and dot.rstrip().endswith("}")
    assert dot.count("{") == dot.count("}")
    for line in dot.splitlines()[1:-1]:
        assert line.strip().endswith(";"), line
    # ids contain "::" and "/" — they must always be inside quotes
    for line in dot.splitlines():
        assert line.count('"') % 2 == 0, line


def test_matched_node_is_highlighted(graphs):
    graph = graphs["java-shop-sample"]
    result = retrieve(graph, "What does CheckoutService.checkout do?", semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    dot = viz.neighbourhood_dot(graph, blocks)
    assert "CheckoutService.checkout" in dot
    assert "penwidth=2.4" in dot


def test_edges_carry_resolution_styling(graphs):
    """A locally bound call must be visually distinct from a library call."""
    graph = graphs["react-shop-sample"]
    result = retrieve(graph, "What does useCart.reload do?", semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    dot = viz.neighbourhood_dot(graph, blocks)
    assert "style=dashed" in dot     # local binding (setItems)
    assert "style=solid" in dot      # internal (fetchCart)


def test_jsx_renders_edge_is_drawn(graphs):
    graph = graphs["react-shop-sample"]
    app_node = next(n for n in graph.symbols if n["name"] == "App")
    blocks = gather_context(graph, [app_node])
    dot = viz.neighbourhood_dot(graph, blocks)
    assert 'label="renders"' in dot
    assert "CartView" in dot


def test_overloads_appear_as_separate_boxes(graphs):
    """Both `charge` overloads must be distinguishable in the picture."""
    graph = graphs["java-shop-sample"]
    charges = [n for n in graph.symbols if n["name"] == "charge"]
    blocks = gather_context(graph, charges)
    dot = viz.neighbourhood_dot(graph, blocks)
    for node in charges:
        assert node["id"] in dot


def test_quotes_in_labels_are_escaped():
    assert viz._escape('a"b\\c') == 'a\\"b\\\\c'
