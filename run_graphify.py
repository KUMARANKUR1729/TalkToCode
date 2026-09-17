#!/usr/bin/env python3
"""
Graphify graph builder: walks a codebase, dispatches every file to a real
parser (tree-sitter for Java/C/JS, stdlib `ast` for Python) and writes
outputs/{target}/knowledge_graph.json.

Nothing here guesses. Line numbers come from syntax trees, so the same input
always produces the same graph.

Usage:
    python run_graphify.py <target> <codebase_path> [--out PATH]
    python run_graphify.py --all                    # rebuild every codebase/*
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from parsers.common import GraphAccumulator
from parsers.registry import EXT_MAP, SKIP_DIRS, source_files

ROOT = Path(__file__).parent


def build_graph(target: str, codebase_path: Path) -> dict:
    """
    Polyglot by design: every file goes to the parser for its own extension
    and carries its own `language`, so a Java backend + React frontend in one
    target is a single coherent graph rather than one language winning.
    """
    files = source_files(codebase_path)
    if not files:
        raise SystemExit(f"No recognized source files under {codebase_path}")

    acc = GraphAccumulator(target=target, source_path=str(codebase_path))

    for f in files:
        rel_path = f.relative_to(codebase_path.parent).as_posix()
        _, parser_module = EXT_MAP[f.suffix]
        try:
            parser_module.parse_file(f, rel_path, acc)
        except Exception as e:  # one bad file must not kill the whole build
            detail = f"{type(e).__name__}: {e}"
            acc.failures.append({"file": rel_path, "error": detail})
            print(f"  ! {rel_path}: {detail}", file=sys.stderr)

    # Resolve calls AND inheritance/imports only now that every file's symbols
    # are known, so resolution never depends on file order.
    acc.resolve()

    out = acc.to_json()
    out["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    out["meta"]["codebase_root"] = codebase_path.parent.as_posix()
    return out


def write_graph(target: str, codebase_path: Path, out_path: Path) -> dict:
    graph = build_graph(target, codebase_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    langs = ", ".join(f"{k}:{v}" for k, v in graph["meta"]["languages"].items())
    failures = graph["meta"].get("parse_failures") or []
    suffix = f"  ** {len(failures)} FILE(S) FAILED TO PARSE **" if failures else ""
    print(f"Wrote {out_path}  ({graph['meta']['node_count']} nodes, "
          f"{graph['meta']['edge_count']} edges, {langs}){suffix}")
    return graph


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?")
    ap.add_argument("codebase_path", nargs="?")
    ap.add_argument("--out", default=None)
    ap.add_argument("--all", action="store_true",
                    help="rebuild a graph for every directory under codebase/")
    args = ap.parse_args()

    if args.all:
        codebase_dir = ROOT / "codebase"
        targets = sorted(p for p in codebase_dir.iterdir() if p.is_dir())
        if not targets:
            raise SystemExit(f"No codebases found under {codebase_dir}")
        for path in targets:
            write_graph(path.name, path.resolve(),
                        ROOT / "outputs" / path.name / "knowledge_graph.json")
        return

    if not args.target or not args.codebase_path:
        ap.error("provide <target> and <codebase_path>, or use --all")

    out_path = Path(args.out) if args.out else ROOT / "outputs" / args.target / "knowledge_graph.json"
    write_graph(args.target, Path(args.codebase_path).resolve(), out_path)


if __name__ == "__main__":
    main()
