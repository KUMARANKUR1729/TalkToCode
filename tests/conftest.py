import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graphify.graph import CodeGraph  # noqa: E402
from run_graphify import build_graph  # noqa: E402

from run_graphify import EXT_MAP, SKIP_DIRS  # noqa: E402

VOLATILE_META = {"generated_at", "source_path", "codebase_root"}


def discover_targets():
    """
    Every codebase under codebase/ is a test target automatically.

    Drop a new sample in and the structural invariants (unique ids, ranges
    inside the file, POSIX paths, hashes, determinism, declaration-at-range,
    golden snapshot) cover it with no edits here.
    """
    root = ROOT / "codebase"
    if not root.exists():
        return []
    found = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name in SKIP_DIRS:
            continue
        has_source = any(
            f.is_file() and f.suffix in EXT_MAP
            and not any(part in SKIP_DIRS for part in f.parts)
            for f in path.rglob("*")
        )
        if has_source:
            found.append(path.name)
    return found


TARGETS = discover_targets()


def pytest_report_header(config):
    return f"graphify targets discovered: {', '.join(TARGETS) or '(none)'}"


@pytest.fixture(scope="session")
def raw_graphs():
    """Freshly parsed graphs for every sample codebase, keyed by target."""
    return {t: build_graph(t, (ROOT / "codebase" / t).resolve()) for t in TARGETS}


@pytest.fixture(scope="session")
def graphs(raw_graphs):
    """The same graphs wrapped in CodeGraph, with outputs/ paths attached."""
    out = {}
    for target, raw in raw_graphs.items():
        out[target] = CodeGraph(
            target=target, meta=raw["meta"], nodes=raw["nodes"], edges=raw["edges"],
            path=ROOT / "outputs" / target / "knowledge_graph.json",
        )
    return out


@pytest.fixture
def build_from_source(tmp_path):
    """
    Build a graph from inline source files:
        graph = build_from_source({"src/Dup.java": "class A { ... }"})
    """
    def _build(files: dict, target: str = "probe"):
        base = tmp_path / target
        for rel, content in files.items():
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        raw = build_graph(target, base.resolve())
        return CodeGraph(target=target, meta=raw["meta"],
                         nodes=raw["nodes"], edges=raw["edges"], path=None)

    return _build


def node_named(graph, name, ntype=None):
    matches = [n for n in graph.nodes
               if n["name"] == name and (ntype is None or n["type"] == ntype)]
    assert matches, f"no node named {name!r} (type={ntype}) in {graph.target}"
    return matches[0]


def edge_between(graph, from_name, to_name, etype="calls"):
    for edge in graph.edges:
        if edge["type"] != etype:
            continue
        src = graph.by_id.get(edge["from"])
        dst = graph.by_id.get(edge["to"])
        src_name = src["name"] if src else edge["from"]
        dst_name = dst["name"] if dst else edge["to"]
        if src_name == from_name and dst_name == to_name:
            return edge
    return None
