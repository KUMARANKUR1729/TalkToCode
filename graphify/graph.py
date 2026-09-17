"""
Knowledge-graph loading, indexing, staleness detection and snippet reading.

Staleness matters more than it sounds: the graph stores line numbers, and the
app reads snippets fresh from disk using them. If the code changed after the
graph was built, those line numbers point at the wrong lines and the model is
fed wrong code while sounding just as confident. Every file node carries a
content hash so that situation is detected and surfaced instead of silently
producing a plausible, wrong answer.
"""
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class CodeGraph:
    target: str
    meta: dict
    nodes: list
    edges: list
    path: Path | None = None

    by_id: dict = field(default_factory=dict, repr=False)
    by_name: dict = field(default_factory=dict, repr=False)
    symbols: list = field(default_factory=list, repr=False)
    _out: dict = field(default_factory=dict, repr=False)
    _in: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self.by_id = {n["id"]: n for n in self.nodes}
        self.by_name = {}
        for n in self.nodes:
            self.by_name.setdefault(n["name"], []).append(n)
        self.symbols = [n for n in self.nodes if n["type"] != "file"]
        for e in self.edges:
            self._out.setdefault(e["from"], []).append(e)
            self._in.setdefault(e["to"], []).append(e)

    # ---------- edge accessors ----------

    def outgoing(self, node_id: str, etype: str | None = None) -> list:
        edges = self._out.get(node_id, [])
        return [e for e in edges if etype is None or e["type"] == etype]

    def incoming(self, node_id: str, etype: str | None = None) -> list:
        edges = self._in.get(node_id, [])
        return [e for e in edges if etype is None or e["type"] == etype]

    def callers(self, node_id: str) -> list:
        return self.incoming(node_id, "calls")

    def callees(self, node_id: str) -> list:
        return self.outgoing(node_id, "calls")

    def children(self, node_id: str) -> list:
        return [self.by_id[e["to"]] for e in self.outgoing(node_id, "contains")
                if e["to"] in self.by_id]

    # ---------- naming ----------

    def qualified_name(self, node: dict) -> str:
        """`AuthService.login`, `SentimentClassifier.forward`, or just `auth_login`."""
        parts = [node["name"]]
        parent_id = node.get("parent")
        guard = 0
        while parent_id and guard < 12:
            parent = self.by_id.get(parent_id)
            if parent is None or parent["type"] == "file":
                break
            parts.append(parent["name"])
            parent_id = parent.get("parent")
            guard += 1
        return ".".join(reversed(parts))

    def all_qualified_names(self) -> dict:
        return {self.qualified_name(n): n for n in self.symbols}

    def label(self, node_id: str) -> str:
        """Human label for an edge endpoint, which may be an unresolved name."""
        node = self.by_id.get(node_id)
        if node is None:
            return node_id
        return self.qualified_name(node)

    @property
    def languages(self) -> dict:
        langs = self.meta.get("languages")
        if langs:
            return langs
        one = self.meta.get("language")
        return {one: len([n for n in self.nodes if n["type"] == "file"])} if one else {}


def list_targets(outputs_dir: Path | None = None) -> list:
    """Codebases that already have a built graph."""
    outputs_dir = outputs_dir or config.OUTPUTS_DIR
    if not outputs_dir.exists():
        return []
    return sorted(p.parent.name for p in outputs_dir.glob("*/knowledge_graph.json"))


def list_codebases(codebase_dir: Path | None = None) -> list:
    """
    Parseable source folders on disk, whether or not a graph exists yet.

    Without this the app could only ever show codebases that had already been
    built from the CLI, so dropping a new project into codebase/ left it
    invisible — the one flow the app most obviously should support.
    """
    from parsers.registry import SKIP_DIRS, has_source

    codebase_dir = codebase_dir or config.CODEBASE_DIR
    if not codebase_dir.exists():
        return []
    return sorted(p.name for p in codebase_dir.iterdir()
                  if p.is_dir() and p.name not in SKIP_DIRS and has_source(p))


def list_all(outputs_dir: Path | None = None, codebase_dir: Path | None = None):
    """(every selectable target, the subset that has no graph yet)."""
    built = list_targets(outputs_dir)
    on_disk = list_codebases(codebase_dir)
    unbuilt = [t for t in on_disk if t not in built]
    return sorted(set(built) | set(on_disk)), unbuilt


def load_graph(target: str, outputs_dir: Path | None = None) -> CodeGraph:
    outputs_dir = outputs_dir or config.OUTPUTS_DIR
    path = outputs_dir / target / "knowledge_graph.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return CodeGraph(target=target, meta=raw.get("meta", {}),
                     nodes=raw.get("nodes", []), edges=raw.get("edges", []), path=path)


# ---------- snippets & staleness ----------

def source_path(node_file: str, codebase_dir: Path | None = None) -> Path:
    codebase_dir = codebase_dir or config.CODEBASE_DIR
    return codebase_dir / node_file


def read_snippet(node: dict, codebase_dir: Path | None = None) -> str:
    """
    Reads exactly the node's line range from disk. Line numbers come from the
    parser, so this is a slice, never a search.
    """
    path = source_path(node["file"], codebase_dir)
    if not path.exists():
        return "(source file not found on disk)"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(node["line_start"]))
    end = min(len(lines), int(node["line_end"]))
    if start > len(lines):
        return "(line range is outside the current file — graph is stale)"
    return "\n".join(lines[start - 1:end])


@dataclass
class Staleness:
    changed: list
    missing: list
    unverifiable: list

    @property
    def is_stale(self) -> bool:
        return bool(self.changed or self.missing)

    def summary(self) -> str:
        bits = []
        if self.changed:
            bits.append(f"{len(self.changed)} file(s) changed since the graph was built")
        if self.missing:
            bits.append(f"{len(self.missing)} file(s) no longer exist")
        if self.unverifiable:
            bits.append(f"{len(self.unverifiable)} file(s) have no stored hash")
        return "; ".join(bits)


def staleness_report(graph: CodeGraph, codebase_dir: Path | None = None) -> Staleness:
    changed, missing, unverifiable = [], [], []
    for node in graph.nodes:
        if node["type"] != "file":
            continue
        path = source_path(node["file"], codebase_dir)
        stored = node.get("hash")
        if not path.exists():
            missing.append(node["file"])
        elif not stored:
            unverifiable.append(node["file"])
        elif _hash_text(path.read_text(encoding="utf-8", errors="replace")) != stored:
            changed.append(node["file"])
    return Staleness(changed=sorted(changed), missing=sorted(missing),
                     unverifiable=sorted(unverifiable))
