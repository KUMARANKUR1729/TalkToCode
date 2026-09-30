#!/usr/bin/env python3
"""Deterministic, resource-bounded and atomic knowledge-graph builder."""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from graphify import config
from graphify.artifacts import publish_graph, validate_graph_data, validate_target
from parsers.common import GraphAccumulator
from parsers.registry import EXT_MAP, SKIP_DIRS, source_files

ROOT = Path(__file__).parent
PARSER_VERSION = "1"


class GraphBuildError(RuntimeError):
    """Raised when a complete, publishable graph cannot be produced."""

    def __init__(self, message: str, failures: list[dict] | None = None):
        super().__init__(message)
        self.failures = failures or []


def _artifact_id(graph: dict) -> str:
    stable = {
        "schema_version": graph["meta"]["schema_version"],
        "parser_version": PARSER_VERSION,
        "source_hash": graph["meta"]["hash"],
        "nodes": graph["nodes"],
        "edges": graph["edges"],
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def build_graph(target: str, codebase_path: Path, *, allow_partial: bool = False) -> dict:
    """Build a complete graph; parser failures are fatal unless explicitly overridden."""
    validate_target(target)
    codebase_path = Path(codebase_path).resolve(strict=True)
    if not codebase_path.is_dir():
        raise GraphBuildError(f"Codebase path is not a directory: {codebase_path}")

    files = source_files(codebase_path)
    if not files:
        raise GraphBuildError(f"No recognized source files under {codebase_path}")
    if len(files) > config.MAX_SOURCE_FILES:
        raise GraphBuildError(
            f"Repository has {len(files)} source files; limit is {config.MAX_SOURCE_FILES}.")

    total_bytes = 0
    unsafe = []
    for path in files:
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(codebase_path)
            size = resolved.stat().st_size
        except (OSError, ValueError) as exc:
            unsafe.append({"file": str(path), "error": f"Unsafe source path: {type(exc).__name__}"})
            continue
        if path.is_symlink():
            unsafe.append({"file": str(path), "error": "Symbolic links are not parsed"})
        elif size > config.MAX_SOURCE_FILE_BYTES:
            unsafe.append({"file": str(path), "error": "Source file exceeds configured size limit"})
        total_bytes += size
    if total_bytes > config.MAX_REPOSITORY_BYTES:
        raise GraphBuildError(
            f"Repository source size is {total_bytes} bytes; limit is {config.MAX_REPOSITORY_BYTES}.")
    if unsafe and not allow_partial:
        raise GraphBuildError(f"Refusing to build: {len(unsafe)} unsafe source file(s).", unsafe)

    acc = GraphAccumulator(target=target, source_path=target)
    acc.failures.extend(unsafe)
    rejected = {item["file"] for item in unsafe}
    for path in files:
        if str(path) in rejected:
            continue
        rel_path = path.relative_to(codebase_path.parent).as_posix()
        _, parser_module = EXT_MAP[path.suffix]
        try:
            parser_module.parse_file(path, rel_path, acc)
        except Exception as exc:
            detail = f"{type(exc).__name__}: parser failed"
            acc.failures.append({"file": rel_path, "error": detail})
            print(f"  ! {rel_path}: {detail}", file=sys.stderr)

    acc.resolve()
    graph = acc.to_json()
    graph["meta"].update({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "codebase_root": ".",
        "parser_version": PARSER_VERSION,
        "build_status": "partial" if acc.failures else "complete",
    })
    graph["meta"]["artifact_id"] = _artifact_id(graph)
    validate_graph_data(graph, expected_target=target)
    if acc.failures and not allow_partial:
        raise GraphBuildError(
            f"Refusing to publish partial graph: {len(acc.failures)} file(s) failed.",
            list(acc.failures),
        )
    return graph


def write_graph(target: str, codebase_path: Path, out_path: Path, *,
                allow_partial: bool = False) -> dict:
    """Build, validate, checksum, version and atomically publish a graph."""
    graph = build_graph(target, codebase_path, allow_partial=allow_partial)
    digest = publish_graph(graph, out_path)
    langs = ", ".join(f"{key}:{value}" for key, value in graph["meta"]["languages"].items())
    print(f"Published {out_path} ({graph['meta']['node_count']} nodes, "
          f"{graph['meta']['edge_count']} edges, {langs}, sha256:{digest[:12]})")
    return graph


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?")
    parser.add_argument("codebase_path", nargs="?")
    parser.add_argument("--out", default=None)
    parser.add_argument("--all", action="store_true",
                        help="rebuild every codebase under codebase/")
    parser.add_argument("--allow-partial", action="store_true",
                        help="publish despite parser failures (unsafe; explicit override)")
    args = parser.parse_args()

    try:
        if args.all:
            targets = sorted(path for path in (ROOT / "codebase").iterdir() if path.is_dir())
            if not targets:
                raise GraphBuildError("No codebases found under codebase/")
            for path in targets:
                write_graph(path.name, path,
                            ROOT / "outputs" / path.name / "knowledge_graph.json",
                            allow_partial=args.allow_partial)
            return

        if not args.target or not args.codebase_path:
            parser.error("provide <target> and <codebase_path>, or use --all")
        out = Path(args.out) if args.out else ROOT / "outputs" / args.target / "knowledge_graph.json"
        write_graph(args.target, Path(args.codebase_path), out,
                    allow_partial=args.allow_partial)
    except (GraphBuildError, ValueError, OSError) as exc:
        raise SystemExit(f"Graph build failed: {exc}") from exc


if __name__ == "__main__":
    main()
