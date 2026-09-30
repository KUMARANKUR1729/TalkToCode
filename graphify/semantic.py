"""
Semantic retrieval — the layer that answers "what handles authentication?"
when no symbol is literally named in the question.

Two independent backends, because model availability on hosted catalogs
changes and this should never be a hard dependency:

  * embeddings — cosine similarity over cached vectors (fast, cheap, exact
    same result every run for a given graph)
  * llm_select — hand the chat model the symbol inventory and ask which
    symbols are relevant; works with nothing but the chat endpoint

Both are optional. With GRAPHIFY_SEMANTIC=off retrieval stays fully offline
and falls back to lexical BM25.
"""
import json
import math
import re

from . import config
from .llm import LLMError, chat, embed

CACHE_NAME = "embeddings.json"


def document_for(graph, node) -> str:
    """The text that represents a node for semantic/lexical matching."""
    bits = [
        graph.qualified_name(node),
        node["name"],
        node["type"].replace("_", " "),
        node.get("signature") or "",
        node.get("doc") or "",
        node["file"],
    ]
    return "\n".join(b for b in bits if b)


# ---------- embeddings backend ----------

def _cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def _cache_path(graph):
    if graph.path is None:
        return None
    return graph.path.parent / CACHE_NAME


def load_or_build_index(graph, api_key: str, model: str | None = None,
                        snippets: dict | None = None):
    """
    Returns {"ids": [...], "vectors": [[...]], ...} or None if embeddings are
    unavailable. Cached on disk and invalidated by the graph hash, so editing
    the codebase and rebuilding the graph rebuilds the vectors too.
    """
    model = model or config.EMBED_MODEL
    graph_hash = graph.meta.get("artifact_id") or graph.meta.get("hash", "")
    path = _cache_path(graph)

    if path is not None and path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("graph_hash") == graph_hash and cached.get("model") == model:
                return cached
        except (json.JSONDecodeError, OSError):
            pass

    nodes = graph.symbols
    if not nodes:
        return None
    docs = []
    for n in nodes:
        doc = document_for(graph, n)
        snippet = (snippets or {}).get(n["id"])
        if snippet:
            doc = f"{doc}\n{snippet[:1200]}"
        docs.append(doc)

    try:
        vectors = embed(api_key, docs, model=model, input_type="passage")
    except LLMError:
        return None

    index = {"graph_hash": graph_hash, "model": model,
             "ids": [n["id"] for n in nodes], "vectors": vectors}
    if path is not None:
        try:
            path.write_text(json.dumps(index), encoding="utf-8")
        except OSError:
            pass
    return index


def embedding_search(graph, question: str, api_key: str, index, limit: int = 5) -> list:
    """Returns [(node_id, similarity)] sorted best-first, or [] on failure."""
    if not index:
        return []
    try:
        query_vec = embed(api_key, [question], model=index["model"], input_type="query")[0]
    except LLMError:
        return []
    scored = [(nid, _cosine(query_vec, vec))
              for nid, vec in zip(index["ids"], index["vectors"])]
    scored.sort(key=lambda pair: -pair[1])
    return [(nid, score) for nid, score in scored[:limit] if score > 0]


# ---------- chat-model selector backend ----------

_SELECTOR_SYSTEM = (
    "You map a natural-language question about a codebase onto symbol names. "
    "You are given the complete inventory of symbols. Reply with ONLY a JSON "
    "array of the exact names that are most relevant, best first, at most 5 "
    "entries. Copy names character-for-character from the inventory. If none "
    "are relevant, reply with []."
)


def llm_select_symbols(graph, question: str, api_key: str, model: str,
                       limit: int = 5) -> list:
    """
    Ask the chat model which symbols matter. Only the inventory is sent (names,
    types, files) — never source code — so this stays cheap.
    """
    inventory = [
        f"{graph.qualified_name(n)} ({n['type']}, {n['file']})"
        for n in graph.symbols
    ]
    if not inventory:
        return []
    prompt = (
        f"QUESTION: {question}\n\n"
        f"SYMBOL INVENTORY ({len(inventory)} symbols):\n" + "\n".join(inventory) +
        "\n\nJSON array of the most relevant symbol names:"
    )
    try:
        raw = chat(api_key, model,
                   [{"role": "system", "content": _SELECTOR_SYSTEM},
                    {"role": "user", "content": prompt}],
                   temperature=0.0, max_tokens=200)
    except LLMError:
        return []

    match = re.search(r"\[.*?\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        names = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []

    qualified = graph.all_qualified_names()
    resolved = []
    for name in names:
        if not isinstance(name, str):
            continue
        node = qualified.get(name)
        if node is None:
            bare = name.rsplit(".", 1)[-1]
            candidates = [n for n in graph.symbols if n["name"] == bare]
            node = candidates[0] if candidates else None
        if node is not None and node["id"] not in {n["id"] for n in resolved}:
            resolved.append(node)
    return resolved[:limit]
