"""Security boundary for graph artifacts and repository-relative paths."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from . import config

TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REQUIRED_NODE_FIELDS = {"id", "name", "type", "file", "line_start", "line_end"}
REQUIRED_EDGE_FIELDS = {"from", "to", "type"}


class ArtifactError(ValueError):
    """Raised when an artifact or path violates the trusted contract."""


def validate_target(target: str) -> str:
    if not isinstance(target, str) or not TARGET_RE.fullmatch(target):
        raise ArtifactError("Invalid target name; use letters, numbers, '.', '_' or '-'.")
    if target in {".", ".."}:
        raise ArtifactError("Invalid target name.")
    return target


def validate_relative_path(value: str, *, label: str = "path") -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ArtifactError(f"Invalid {label}.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactError(f"Unsafe {label}: {value!r}.")
    if Path(value).drive:
        raise ArtifactError(f"Unsafe {label}: {value!r}.")
    return path


def safe_child(base: Path, relative: str, *, must_exist: bool = False) -> Path:
    rel = validate_relative_path(relative)
    root = Path(base).resolve()
    candidate = root.joinpath(*rel.parts).resolve(strict=must_exist)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ArtifactError(f"Path escapes approved root: {relative!r}.") from exc
    return candidate
def validate_graph_data(raw: object, *, expected_target: str | None = None) -> dict:
    """Validate the complete graph contract before consumers index or read it."""
    if not isinstance(raw, dict):
        raise ArtifactError("Graph root must be an object.")
    meta, nodes, edges = raw.get("meta"), raw.get("nodes"), raw.get("edges")
    if not isinstance(meta, dict) or not isinstance(nodes, list) or not isinstance(edges, list):
        raise ArtifactError("Graph requires object 'meta' and array 'nodes'/'edges'.")
    required_meta = {"schema_version", "target", "hash", "node_count", "edge_count",
                     "parse_failures", "artifact_id"}
    if not required_meta <= meta.keys():
        raise ArtifactError("Graph metadata is missing required fields.")
    if meta.get("schema_version") not in config.SUPPORTED_GRAPH_SCHEMAS:
        raise ArtifactError(f"Unsupported graph schema version: {meta.get('schema_version')!r}.")
    target = validate_target(meta.get("target"))
    if expected_target is not None and target != validate_target(expected_target):
        raise ArtifactError("Graph target does not match its artifact location.")
    if len(nodes) > config.MAX_GRAPH_NODES or len(edges) > config.MAX_GRAPH_EDGES:
        raise ArtifactError("Graph exceeds configured node or edge limits.")
    if meta.get("node_count") != len(nodes) or meta.get("edge_count") != len(edges):
        raise ArtifactError("Graph metadata counts do not match its contents.")
    failures = meta.get("parse_failures", [])
    if not isinstance(failures, list):
        raise ArtifactError("Graph parse_failures must be an array.")

    ids: set[str] = set()
    file_ids: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not REQUIRED_NODE_FIELDS <= node.keys():
            raise ArtifactError(f"Node {index} is missing required fields.")
        if not all(isinstance(node[key], str) and node[key] for key in ("id", "name", "type")):
            raise ArtifactError(f"Node {index} has invalid identity fields.")
        validate_relative_path(node["file"], label="node file")
        if node["id"] in ids:
            raise ArtifactError(f"Duplicate node id: {node['id']!r}.")
        ids.add(node["id"])
        if node["type"] == "file":
            if node["id"] != node["file"]:
                raise ArtifactError(f"File node {node['id']!r} must identify its own path.")
            file_ids.add(node["id"])
        start, end = node["line_start"], node["line_end"]
        if (isinstance(start, bool) or isinstance(end, bool) or
                not isinstance(start, int) or not isinstance(end, int) or
                start < 1 or end < start):
            raise ArtifactError(f"Node {node['id']!r} has an invalid line range.")

    for node in nodes:
        if node["file"] not in file_ids:
            raise ArtifactError(f"Node {node['id']!r} refers to an unknown file node.")
        parent = node.get("parent")
        if parent is not None and parent not in ids:
            raise ArtifactError(f"Node {node['id']!r} refers to an unknown parent.")

    for index, edge in enumerate(edges):
        if not isinstance(edge, dict) or not REQUIRED_EDGE_FIELDS <= edge.keys():
            raise ArtifactError(f"Edge {index} is missing required fields.")
        if not all(isinstance(edge[key], str) and edge[key]
                   for key in ("from", "to", "type")):
            raise ArtifactError(f"Edge {index} has invalid fields.")
        if edge["from"] not in ids:
            raise ArtifactError(f"Edge {index} has an unknown source.")
        if edge.get("resolution") == "internal" and edge["to"] not in ids:
            raise ArtifactError(f"Internal edge {index} has an unknown destination.")
        count = edge.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ArtifactError(f"Edge {index} has an invalid count.")
    return raw


def serialize_graph(graph: dict) -> bytes:
    validate_graph_data(graph)
    return (json.dumps(graph, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
def _checksum_path(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def verify_checksum(path: Path, data: bytes) -> None:
    sidecar = _checksum_path(path)
    if not sidecar.exists():
        if config.REQUIRE_ARTIFACT_CHECKSUM:
            raise ArtifactError(f"Missing checksum for graph artifact: {path.name}.")
        return
    tokens = sidecar.read_text(encoding="ascii").strip().split()
    if not tokens:
        raise ArtifactError(f"Invalid checksum for graph artifact: {path.name}.")
    expected = tokens[0]
    actual = checksum(data)
    if not expected or not hmac.compare_digest(expected, actual):
        raise ArtifactError(f"Checksum mismatch for graph artifact: {path.name}.")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


@contextmanager
def artifact_lock(path: Path):
    """Cross-process publication lock with bounded waiting and stale recovery."""
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + config.ARTIFACT_LOCK_TIMEOUT
    descriptor = None
    while descriptor is None:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > config.ARTIFACT_LOCK_STALE_AFTER
                if stale:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise ArtifactError(f"Timed out waiting to publish {path.name}.")
            time.sleep(0.1)
    try:
        yield
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)


def publish_graph(graph: dict, out_path: Path) -> str:
    """Validate and atomically publish current, previous, and immutable artifacts."""
    data = serialize_graph(graph)
    digest = checksum(data)
    out_path = Path(out_path)
    sidecar_data = f"{digest}  {out_path.name}\n".encode("ascii")
    artifact_id = graph.get("meta", {}).get("artifact_id") or digest[:24]
    validate_target(str(artifact_id))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with artifact_lock(out_path):
        if out_path.exists():
            previous = out_path.with_name(out_path.stem + ".previous" + out_path.suffix)
            old_data = out_path.read_bytes()
            _atomic_write(previous, old_data)
            _atomic_write(_checksum_path(previous),
                          f"{checksum(old_data)}  {previous.name}\n".encode("ascii"))
        version = out_path.parent / "versions" / f"{artifact_id}.json"
        if not version.exists():
            _atomic_write(version, data)
            _atomic_write(_checksum_path(version),
                          f"{digest}  {version.name}\n".encode("ascii"))
        _atomic_write(out_path, data)
        _atomic_write(_checksum_path(out_path), sidecar_data)
    return digest
