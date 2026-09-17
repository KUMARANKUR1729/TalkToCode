"""
Retrieval: question -> the exact set of graph nodes the answer may use.

Layered, cheapest first, and every layer reports which one fired so the UI can
show it and the confidence can be computed from evidence rather than asserted
by the model:

  1. qualified   `AuthService.login`  — exact class+member, strictest
  2. exact       a question word IS a symbol name
  3. lexical     BM25 over identifier subwords, docs and signatures (offline)
  4. semantic    embeddings, or the chat model as a symbol selector
  5. suggestions nothing matched — offer the closest real names

The not-found guarantee from the original design is preserved and tightened:
if the question names a real class and a member that does not belong to it,
retrieval deliberately returns nothing rather than answering about a
same-named member of some other class.
"""
import difflib
import re
from dataclasses import dataclass, field

from . import config, semantic
from .graph import read_snippet
from .lexical import Bm25Index, tokenize

QUALIFIED_RE = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")
IDENT_RE = re.compile(r"[A-Za-z_]\w*")
#: `[a-z][A-Z]` transition or an underscore — how a code identifier looks when
#: it appears in an English sentence. Plain words like "authentication" and
#: acronyms like "JSON" deliberately do not qualify.
SYMBOL_LIKE_RE = re.compile(r"[a-z][A-Z]")
#: `AuthService.java` is a filename, not a class member access.
FILE_EXTENSIONS = {"java", "py", "js", "jsx", "mjs", "c", "h", "ts", "tsx",
                   "json", "md", "txt", "html", "css", "yml", "yaml"}

#: Words that name a kind of symbol. "which component ..." should not answer
#: with a function just because a question word happens to be a function name.
ROLE_WORDS = {
    "hook": {"hook"},
    "component": {"component"},
    "class": {"class"},
    "interface": {"interface"},
    "method": {"method"},
    "function": {"function", "func", "function_declaration"},
    "constructor": {"constructor"},
    "macro": {"macro"},
    "struct": {"struct"},
    "union": {"union"},
    "enum": {"enum"},
    "typedef": {"typedef"},
    "field": {"field", "attribute", "property", "member"},
    "constant": {"constant"},
    "global": {"global"},
}
_ROLE_LOOKUP = {word: ntype for ntype, words in ROLE_WORDS.items() for word in words}
#: A `function` hint should also accept a C prototype node.
_ROLE_EXPAND = {"function": {"function", "function_declaration"},
                "method": {"method", "function"},
                "component": {"component"},
                "field": {"field", "global", "constant"}}
#: Two-character names ("id", "fc") mentioned in prose are almost always a
#: coincidence rather than a reference to that symbol.
MIN_EXACT_NAME_LEN = 3
#: Data-holding symbols. "reserves stock for an item" is prose that happens to
#: contain the names of a global and a struct member; treating that as an exact
#: HIGH-confidence hit stops the search before it reaches the function the
#: question is actually about. These only match exactly when the question spells
#: them identifier-style (`stock_count`, `totalCents`) or names their kind.
DATA_TYPES = {"field", "global", "constant", "enum_constant"}

#: BM25 score below which a lexical hit is treated as a weak guess.
LEXICAL_STRONG = 3.0
LEXICAL_MIN = 0.8
#: Keep lexical hits within this fraction of the best score.
LEXICAL_RELATIVE = 0.35

CONFIDENCE_ORDER = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}


@dataclass
class Match:
    node: dict
    score: float
    strategy: str


@dataclass
class Retrieval:
    question: str
    matches: list = field(default_factory=list)
    strategy: str = "none"
    confidence: str = "NONE"
    suggestions: list = field(default_factory=list)
    reason: str | None = None
    debug: dict = field(default_factory=dict)

    @property
    def nodes(self) -> list:
        return [m.node for m in self.matches]

    def __bool__(self) -> bool:
        return bool(self.matches)


# ---------- helpers ----------

def _prefer_definitions(graph, nodes: list) -> list:
    """
    A C header prototype and its definition are the same symbol. Always answer
    from the definition, and never show both.
    """
    out, seen = [], set()
    for node in nodes:
        chosen = node
        if node["type"] == "function_declaration":
            definition = next(
                (n for n in graph.by_name.get(node["name"], []) if n["type"] == "function"),
                None,
            )
            if definition is not None:
                chosen = definition
        if chosen["id"] in seen:
            continue
        seen.add(chosen["id"])
        out.append(chosen)
    return out


def _dedupe(nodes: list, limit: int) -> list:
    out, seen = [], set()
    for node in nodes:
        if node["id"] in seen:
            continue
        seen.add(node["id"])
        out.append(node)
    return out[:limit]


def _snippets_for_index(graph, codebase_dir, max_files: int = 400) -> dict:
    if len(graph.symbols) > max_files * 8:
        return {}
    snippets = {}
    for node in graph.symbols:
        try:
            snippets[node["id"]] = read_snippet(node, codebase_dir)
        except OSError:
            continue
    return snippets


# ---------- layer 1: qualified ----------

def _type_hints(question: str) -> set:
    """Node types implied by role words in the question ('which component ...')."""
    hinted = set()
    for word in re.findall(r"[a-z_]+", question.lower()):
        ntype = _ROLE_LOOKUP.get(word)
        if ntype:
            hinted |= _ROLE_EXPAND.get(ntype, {ntype})
    return hinted


def _apply_type_hints(nodes: list, hints: set) -> list:
    """Narrow to hinted types, but never to nothing."""
    if not hints:
        return nodes
    filtered = [n for n in nodes if n["type"] in hints]
    return filtered or []


def _qualified(graph, question: str):
    """
    Returns (nodes, reason). `reason` set means "deliberately not found".
    """
    qualified_map = graph.all_qualified_names()
    symbol_names = {n["name"] for n in graph.symbols}
    hits = []

    for owner, member in QUALIFIED_RE.findall(question):
        if member.lower() in FILE_EXTENSIONS and owner in symbol_names:
            continue  # `AuthService.java` is a path, not a member access
        if owner not in symbol_names:
            continue  # `res.json`, `e.preventDefault` — not a codebase type
        direct = qualified_map.get(f"{owner}.{member}")
        if direct is not None:
            hits.append(direct)
            continue
        if member not in symbol_names:
            return [], (f"`{owner}` exists in this codebase but it has no member "
                        f"named `{member}`.")
        owner_ids = {n["id"] for n in graph.by_name.get(owner, [])}
        owned = [n for n in graph.by_name.get(member, []) if n.get("parent") in owner_ids]
        if owned:
            hits.extend(owned)
        else:
            return [], (f"`{member}` exists in this codebase but not as a member of "
                        f"`{owner}`.")
    return hits, None


# ---------- layer 2: exact ----------

def _named_symbol_miss(graph, question: str):
    """
    The not-found guarantee, extended past `Class.member`.

    If the question spells out something that clearly *is* an identifier
    (`useSession`, `auth_refresh_token`) and no such symbol exists, refuse.
    Without this, the lexical layer happily answers a question about
    `useSession` using `useAuth`, which is exactly the improvising the design
    set out to prevent.
    """
    known = {n["name"].lower() for n in graph.symbols}
    known |= {q.lower() for q in graph.all_qualified_names()}
    missing = []
    for word in IDENT_RE.findall(question):
        lowered = word.lower()
        if lowered in known:
            continue
        is_symbol_like = SYMBOL_LIKE_RE.search(word) or ("_" in word and len(word) > 3)
        if not is_symbol_like:
            continue
        if lowered in _ROLE_LOOKUP:
            continue
        # "the CHECKOUT_ERR macros" names a family by its shared prefix rather
        # than a symbol that does not exist. Refusing there is over-strict; a
        # genuine miss like `useWishlist` is a prefix of nothing.
        if len(lowered) >= 4 and any(name.startswith(lowered) for name in known):
            continue
        if word not in missing:
            missing.append(word)
    return missing


def _exact(graph, question: str, hints: set = frozenset()):
    """
    Case-SENSITIVE only, never for very short names, and data-holding symbols
    only when the question really looks like it means them.

    A case-insensitive pass used to run here, which made the prose word "user"
    short-circuit onto the class `User` with HIGH confidence and stop the search
    before it could find what the question actually asked about. Case-mismatched
    guesses still work — BM25 lowercases everything — they just arrive with
    honest MEDIUM confidence instead.
    """
    words = {w for w in IDENT_RE.findall(question) if len(w) >= MIN_EXACT_NAME_LEN}
    identifier_shaped = {w for w in words
                         if "_" in w or SYMBOL_LIKE_RE.search(w) or w[:1].isupper()}
    matches = []
    for node in graph.symbols:
        name = node["name"]
        if len(name) < MIN_EXACT_NAME_LEN or name not in words:
            continue
        if node["type"] in DATA_TYPES and not (name in identifier_shaped
                                               or node["type"] in hints):
            continue
        matches.append(node)
    return matches


# ---------- layer 3: lexical ----------

#: Behavioural questions ("where do we ...", "what handles ...") are about code
#: that runs. A one-line field declaration should not outrank the method that
#: uses it just because BM25 rewards short documents.
TYPE_WEIGHTS = {
    "method": 1.0, "function": 1.0, "constructor": 0.95, "hook": 1.0,
    "component": 0.95, "class": 0.9, "interface": 0.9, "struct": 0.8,
    "union": 0.8, "enum": 0.7, "record": 0.9, "macro": 0.6,
    "function_declaration": 0.55, "typedef": 0.5, "field": 0.45,
    "global": 0.45, "constant": 0.45, "enum_constant": 0.4,
}
DEFAULT_TYPE_WEIGHT = 0.7
#: Multiplier when the question's role word matches the node's kind.
HINT_BOOST = 2.0
#: Lower than BM25's usual 0.75: these documents vary wildly in length (a field
#: declaration vs a whole class), and strong length normalisation over-rewards
#: the tiny ones.
LENGTH_NORM_B = 0.4

_INDEX_CACHE = {}


def _lexical_index(graph, codebase_dir):
    """Build (and memoise) the BM25 index over name + signature + doc + code."""
    key = (graph.meta.get("hash", ""), graph.target, str(codebase_dir))
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached

    snippets = _snippets_for_index(graph, codebase_dir)
    nodes, docs = [], []
    for node in graph.symbols:
        text = semantic.document_for(graph, node)
        snippet = snippets.get(node["id"])
        if snippet:
            # Including the body is what lets "turn a user id into a token"
            # reach TokenUtil.generate, whose name alone says nothing about ids.
            text = f"{text}\n{snippet[:2000]}"
        nodes.append(node)
        docs.append(tokenize(text))

    index = (nodes, Bm25Index(docs, b=LENGTH_NORM_B)) if docs else (nodes, None)
    _INDEX_CACHE[key] = index
    return index


def _lexical(graph, question: str, codebase_dir, limit: int, hints: set = frozenset()):
    nodes, index = _lexical_index(graph, codebase_dir)
    if index is None:
        return [], {}
    query = tokenize(question)
    if not query:
        return [], {"query_tokens": []}

    expanded = index.expand(query)
    scored = []
    for position, node in enumerate(nodes):
        raw = index.score(expanded, position)
        if raw <= 0:
            continue
        weight = TYPE_WEIGHTS.get(node["type"], DEFAULT_TYPE_WEIGHT)
        if hints and node["type"] in hints:
            weight *= HINT_BOOST
        scored.append((node, raw * weight))
    if not scored:
        return [], {"query_tokens": query, "expanded_tokens": expanded}

    scored.sort(key=lambda pair: -pair[1])
    best = scored[0][1]
    if best < LEXICAL_MIN:
        return [], {"query_tokens": query, "best_score": best}
    kept = [(node, score) for node, score in scored
            if score >= max(LEXICAL_MIN, best * LEXICAL_RELATIVE)]
    return kept[:limit], {"query_tokens": query, "expanded_tokens": expanded,
                          "best_score": best}


# ---------- layer 5: suggestions ----------

def _suggestions(graph, question: str, limit: int = 5) -> list:
    words = [w for w in IDENT_RE.findall(question) if len(w) > 2]
    names = sorted({graph.qualified_name(n) for n in graph.symbols})
    bare = sorted({n["name"] for n in graph.symbols})
    found = []
    for word in words:
        for pool in (names, bare):
            for hit in difflib.get_close_matches(word, pool, n=3, cutoff=0.6):
                if hit not in found:
                    found.append(hit)
    if not found:
        found = names[:limit]
    return found[:limit]


# ---------- orchestrator ----------

def retrieve(graph, question: str, *, api_key: str = "", model: str | None = None,
             codebase_dir=None, semantic_mode: str | None = None,
             limit: int | None = None) -> Retrieval:
    limit = limit or config.MAX_CONTEXT_NODES
    model = model or config.DEFAULT_MODEL
    semantic_mode = (semantic_mode or config.SEMANTIC_MODE).lower()
    result = Retrieval(question=question)
    result.debug["attempted"] = []
    hints = _type_hints(question)
    if hints:
        result.debug["type_hints"] = sorted(hints)

    def refuse(reason: str, strategy: str):
        result.reason = reason
        result.confidence = "NONE"
        result.strategy = strategy
        result.suggestions = _suggestions(graph, question)
        lexical_hits, _ = _lexical(graph, question, codebase_dir, 3, hints)
        for node, _score in lexical_hits:
            name = graph.qualified_name(node)
            if name not in result.suggestions:
                result.suggestions.append(name)
        result.suggestions = result.suggestions[:6]
        return result

    # 1. qualified
    hits, reason = _qualified(graph, question)
    result.debug["attempted"].append("qualified")
    if reason:
        return refuse(reason, "qualified-miss")
    if hits:
        nodes = _dedupe(_prefer_definitions(graph, hits), limit)
        result.matches = [Match(n, 1.0, "qualified") for n in nodes]
        result.strategy = "qualified"
        result.confidence = "HIGH"
        return result

    # 2. named but non-existent symbol -> refuse instead of fuzzy matching
    result.debug["attempted"].append("named-symbol")
    missing = _named_symbol_miss(graph, question)
    if missing:
        listed = ", ".join(f"`{m}`" for m in missing)
        result.debug["missing_symbols"] = missing
        return refuse(
            f"The question names {listed}, and no symbol with that name exists in "
            f"this codebase.", "named-symbol-miss")

    # 3. exact
    result.debug["attempted"].append("exact")
    hits = _apply_type_hints(_exact(graph, question, hints), hints)
    if hits:
        nodes = _dedupe(_prefer_definitions(graph, hits), limit)
        result.matches = [Match(n, 1.0, "exact") for n in nodes]
        result.strategy = "exact"
        result.confidence = "HIGH"
        return result

    # 4. lexical (offline)
    result.debug["attempted"].append("lexical")
    scored, lex_debug = _lexical(graph, question, codebase_dir, limit, hints)
    result.debug.update(lex_debug)
    if scored:
        best = scored[0][1]
        nodes = _dedupe(_prefer_definitions(graph, [n for n, _ in scored]), limit)
        score_by_id = {n["id"]: s for n, s in scored}
        result.matches = [Match(n, score_by_id.get(n["id"], best), "lexical") for n in nodes]
        result.strategy = "lexical"
        result.confidence = "MEDIUM" if best >= LEXICAL_STRONG else "LOW"
        return result

    # 5. semantic
    if semantic_mode != "off" and api_key:
        result.debug["attempted"].append(f"semantic:{semantic_mode}")
        nodes = []
        if semantic_mode in ("auto", "embeddings"):
            index = semantic.load_or_build_index(
                graph, api_key, snippets=_snippets_for_index(graph, codebase_dir))
            if index:
                pairs = semantic.embedding_search(graph, question, api_key, index, limit=limit)
                nodes = [graph.by_id[nid] for nid, _ in pairs if nid in graph.by_id]
                result.debug["embedding_scores"] = [round(s, 3) for _, s in pairs]
        if not nodes and semantic_mode in ("auto", "llm"):
            nodes = semantic.llm_select_symbols(graph, question, api_key, model, limit=limit)
            if nodes:
                result.debug["semantic_backend"] = "llm-selector"
        if nodes:
            nodes = _apply_type_hints(nodes, hints) or nodes
            nodes = _dedupe(_prefer_definitions(graph, nodes), limit)
            result.matches = [Match(n, 0.5, "semantic") for n in nodes]
            result.strategy = "semantic"
            result.confidence = "LOW"
            return result

    # 6. nothing
    return refuse("No symbol in the knowledge graph matched this question.", "none")


# ---------- context assembly ----------

def _endpoint(graph, edge, key):
    node = graph.by_id.get(edge[key])
    if node is not None:
        return {"name": graph.qualified_name(node), "node": node,
                "resolution": edge.get("resolution", "internal"),
                "count": edge.get("count", 1)}
    return {"name": edge[key], "node": None,
            "resolution": edge.get("resolution", "external"),
            "count": edge.get("count", 1)}


def gather_context(graph, matches: list, codebase_dir=None) -> list:
    """
    For each matched node: its verbatim snippet plus the graph facts around it.
    Nothing else is ever sent to the model.
    """
    blocks = []
    for match in matches:
        node = match.node if isinstance(match, Match) else match
        callers = [_endpoint(graph, e, "from") for e in graph.callers(node["id"])]
        callees = [_endpoint(graph, e, "to") for e in graph.callees(node["id"])]
        parent = graph.by_id.get(node.get("parent") or "")
        blocks.append({
            "node": node,
            "qualified_name": graph.qualified_name(node),
            "parent": parent,
            "snippet": read_snippet(node, codebase_dir),
            "callers": callers,
            "callees": callees,
            "children": graph.children(node["id"]),
            "renders": [_endpoint(graph, e, "to") for e in graph.outgoing(node["id"], "renders")],
            "rendered_by": [_endpoint(graph, e, "from")
                            for e in graph.incoming(node["id"], "renders")],
            "strategy": match.strategy if isinstance(match, Match) else "direct",
        })
    return blocks
