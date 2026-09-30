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
    """Validated codebase names that have a current graph artifact."""
    from .artifacts import ArtifactError, validate_target

    outputs_dir = outputs_dir or config.OUTPUTS_DIR
    if not outputs_dir.exists():
        return []
    targets = []
    for path in outputs_dir.glob("*/knowledge_graph.json"):
        try:
            targets.append(validate_target(path.parent.name))
        except ArtifactError:
            continue
    return sorted(set(targets))


def list_codebases(codebase_dir: Path | None = None) -> list:
    """Parseable, safely named source folders on disk."""
    from parsers.registry import SKIP_DIRS, has_source
    from .artifacts import ArtifactError, validate_target

    codebase_dir = codebase_dir or config.CODEBASE_DIR
    if not codebase_dir.exists():
        return []
    found = []
    for path in codebase_dir.iterdir():
        if not path.is_dir() or path.name in SKIP_DIRS:
            continue
        try:
            validate_target(path.name)
        except ArtifactError:
            continue
        if has_source(path):
            found.append(path.name)
    return sorted(found)


def list_all(outputs_dir: Path | None = None, codebase_dir: Path | None = None):
    """Return every selectable target and the subset without a graph."""
    built = list_targets(outputs_dir)
    on_disk = list_codebases(codebase_dir)
    unbuilt = [target for target in on_disk if target not in built]
    return sorted(set(built) | set(on_disk)), unbuilt


def load_graph(target: str, outputs_dir: Path | None = None) -> CodeGraph:
    """Load a size-bounded, checksummed, schema-validated graph artifact."""
    from .artifacts import (ArtifactError, safe_child, validate_graph_data,
                            validate_target, verify_checksum)

    outputs_dir = outputs_dir or config.OUTPUTS_DIR
    validate_target(target)
    path = safe_child(outputs_dir, f"{target}/knowledge_graph.json", must_exist=True)
    size = path.stat().st_size
    if size > config.MAX_GRAPH_BYTES:
        raise ArtifactError(f"Graph artifact exceeds {config.MAX_GRAPH_BYTES} bytes.")
    data = path.read_bytes()
    verify_checksum(path, data)
    try:
        raw = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"Malformed graph artifact for {target}.") from exc
    validate_graph_data(raw, expected_target=target)
    return CodeGraph(target=target, meta=raw["meta"], nodes=raw["nodes"],
                     edges=raw["edges"], path=path)


# ---------- snippets & staleness ----------

def source_path(node_file: str, codebase_dir: Path | None = None) -> Path:
    """Resolve an artifact path while enforcing the configured repository root."""
    from .artifacts import safe_child

    return safe_child(codebase_dir or config.CODEBASE_DIR, node_file)


def _source_lines(node_file: str, codebase_dir: Path | None = None) -> list[str]:
    path = source_path(node_file, codebase_dir)
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise OSError(f"Source path is not a file: {node_file}")
    if path.stat().st_size > config.MAX_SOURCE_FILE_BYTES:
        raise OSError(f"Source file exceeds {config.MAX_SOURCE_FILE_BYTES} bytes: {node_file}")
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _slice_lines(node: dict, lines: list[str]) -> str:
    start = max(1, int(node["line_start"]))
    end = min(len(lines), int(node["line_end"]))
    if start > len(lines):
        return "(line range is outside the current file — graph is stale)"
    snippet = "\n".join(lines[start - 1:end])
    if len(snippet) > config.MAX_SNIPPET_CHARS:
        return snippet[:config.MAX_SNIPPET_CHARS] + "\n… (snippet truncated by policy)"
    return snippet


def read_snippet(node: dict, codebase_dir: Path | None = None) -> str:
    """Read one validated source range without searching or following unsafe paths."""
    try:
        lines = _source_lines(node["file"], codebase_dir)
    except FileNotFoundError:
        return "(source file not found on disk)"
    return _slice_lines(node, lines)


def read_snippets(nodes: list[dict], codebase_dir: Path | None = None) -> dict[str, str]:
    """Read each containing file once and return snippets keyed by node id."""
    grouped: dict[str, list[dict]] = {}
    for node in nodes:
        grouped.setdefault(node["file"], []).append(node)
    snippets = {}
    for node_file, members in grouped.items():
        try:
            lines = _source_lines(node_file, codebase_dir)
        except (FileNotFoundError, OSError):
            continue
        for node in members:
            snippets[node["id"]] = _slice_lines(node, lines)
    return snippets


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
        elif path.stat().st_size > config.MAX_SOURCE_FILE_BYTES:
            changed.append(node["file"])
        elif not stored:
            unverifiable.append(node["file"])
        elif _hash_text(path.read_text(encoding="utf-8", errors="replace")) != stored:
            changed.append(node["file"])
    return Staleness(changed=sorted(changed), missing=sorted(missing),
                     unverifiable=sorted(unverifiable))
