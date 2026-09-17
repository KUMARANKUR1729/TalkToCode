"""
Which file extensions map to which parser.

Kept separate from run_graphify.py so the app can answer "is this folder a
codebase we could parse?" without importing the CLI.
"""
from . import c_parser, java_parser, js_parser, python_parser

EXT_MAP = {
    ".java": ("java", java_parser),
    ".c": ("c", c_parser),
    ".h": ("c", c_parser),
    ".py": ("python", python_parser),
    ".js": ("javascript", js_parser),
    ".jsx": ("javascript", js_parser),
    ".mjs": ("javascript", js_parser),
}

SKIP_DIRS = {"node_modules", ".git", "__pycache__", "dist", "build",
             "venv", ".venv", ".mypy_cache", ".pytest_cache", "site-packages"}


def is_source(path) -> bool:
    return (path.is_file()
            and path.suffix in EXT_MAP
            and not any(part in SKIP_DIRS for part in path.parts))


def source_files(root):
    """Every parseable file under `root`, in a stable order."""
    return sorted(f for f in root.rglob("*") if is_source(f))


def has_source(root) -> bool:
    return any(is_source(f) for f in root.rglob("*"))
