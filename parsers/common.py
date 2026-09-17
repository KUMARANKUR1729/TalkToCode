"""
Shared helpers used by every language parser.

Design guarantees this module enforces:

1. Line numbers come DIRECTLY from the syntax tree, never from an LLM
   skimming text. Same input -> same output, always.
2. Node IDs are fully qualified by their parent chain plus arity, so
   overloads and same-named methods in different classes never collide
   (the old `file::name` scheme silently merged them).
3. Cross-file references (calls, inheritance, imports) are queued during
   parsing and resolved only AFTER every file is walked, so resolution
   never depends on file processing order.
4. Every file node carries a content hash, so a consumer can detect that
   the graph is stale relative to the code on disk.
5. Paths are stored POSIX-style so a graph built on Windows is readable
   on Linux/macOS and vice versa.
"""
import hashlib
from pathlib import Path

SCHEMA_VERSION = 2

# Node types that can be the target of a `calls` edge, in preference order.
CALLABLE_TYPES = ("method", "function", "constructor", "hook", "component",
                  "function_declaration", "macro")
# Node types that can be the target of an `extends`/`implements` edge.
TYPE_LIKE_TYPES = ("class", "interface", "struct", "component", "enum", "record")


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def ts_point_to_line(point_row: int) -> int:
    """tree-sitter rows are 0-indexed; our schema is 1-indexed."""
    return point_row + 1


def node_lines(ts_node):
    """
    (line_start, line_end), both 1-indexed and inclusive.

    Some tree-sitter nodes (notably C preprocessor directives) include the
    trailing newline, which puts end_point at column 0 of the FOLLOWING
    line. Without this correction a one-line `#define` reported as two
    lines, and the snippet shown to the model contained a stray extra line.
    """
    start_row = ts_node.start_point[0]
    end_row, end_col = ts_node.end_point
    if end_col == 0 and end_row > start_row:
        end_row -= 1
    return ts_point_to_line(start_row), ts_point_to_line(end_row)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def file_hash(path: Path) -> str:
    return content_hash(read_file(path))


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def posix(path_str: str) -> str:
    return str(path_str).replace("\\", "/")


def first_line_signature(text: str, limit: int = 160) -> str:
    """
    A short human-readable signature: the declaration up to the opening
    brace/colon, whitespace-collapsed. Used for display and for retrieval
    scoring, never for line numbers.
    """
    sig = text.split("{")[0].split("\n")[0].strip()
    sig = " ".join(sig.split())
    return sig[:limit]


class PendingCall:
    """A call site recorded during parsing, resolved after the full walk."""

    __slots__ = ("frm", "name", "owner_type", "resolved", "local_binding",
                 "has_receiver", "argc")

    def __init__(self, frm, name, owner_type=None, resolved=None, local_binding=False,
                 has_receiver=False, argc=None):
        self.frm = frm
        self.name = name
        self.owner_type = owner_type      # e.g. "UserRepo" from `userRepo.findByEmail()`
        self.resolved = resolved          # a node id, if the parser already knows it
        self.local_binding = local_binding  # name was bound in an enclosing scope
        # True when the call had an explicit receiver that is not `self`/`this`.
        # `self._model.score()` cannot be a call to the enclosing `score`, so the
        # caller must be excluded from name resolution or it resolves to itself.
        self.has_receiver = has_receiver
        # Number of arguments at the call site. Lets `charge(a, b)` calling
        # `charge(a, b, "CARD")` pick the 3-parameter overload instead of
        # resolving back onto itself.
        self.argc = argc


class PendingRef:
    """A non-call reference (extends / implements / imports / includes)."""

    __slots__ = ("frm", "name", "etype", "prefer_file")

    def __init__(self, frm, name, etype, prefer_file=False):
        self.frm = frm
        self.name = name
        self.etype = etype
        self.prefer_file = prefer_file    # imports/includes point at files, not symbols


class GraphAccumulator:
    """Collects nodes/edges across every file in a codebase walk."""

    def __init__(self, target: str, source_path: str):
        self.target = target
        self.source_path = posix(source_path)
        self.nodes = []
        self.edges = []
        self.pending_calls = []
        self.pending_refs = []
        self._by_id = {}
        self._edge_index = {}     # (frm, to, type) -> edge dict, for dedupe + count
        self._names = {}          # bare name -> [node ids]
        self._languages = {}      # language -> file count
        self._file_hashes = {}    # rel_path -> hash
        self.failures = []        # files a parser could not handle

    # ---------- node construction ----------

    def new_id(self, parent_id: str, name: str, arity: int | None = None) -> str:
        """
        Fully qualified, collision-free node id.

        `parent_id` is the enclosing node's id (a file id for top-level
        symbols). Arity is appended for callables so Java/C++ style
        overloads stay distinct. A numeric suffix is added as a last-resort
        tiebreaker so an id is never reused.
        """
        base = f"{parent_id}::{name}"
        if arity is not None:
            base = f"{base}/{arity}"
        if base not in self._by_id:
            return base
        n = 2
        while f"{base}#{n}" in self._by_id:
            n += 1
        return f"{base}#{n}"

    def add_file_node(self, rel_path: str, text: str, language: str) -> str:
        rel_path = posix(rel_path)
        digest = content_hash(text)
        node = {
            "id": rel_path,
            "name": Path(rel_path).name,
            "type": "file",
            "language": language,
            "file": rel_path,
            "line_start": 1,
            "line_end": count_lines(text),
            "parent": None,
            "hash": digest,
        }
        self.nodes.append(node)
        self._by_id[rel_path] = node
        self._file_hashes[rel_path] = digest
        self._languages[language] = self._languages.get(language, 0) + 1
        return rel_path

    def add_node(self, node_id: str, name: str, ntype: str, rel_path: str,
                 line_start: int, line_end: int, parent: str | None,
                 language: str | None = None, signature: str | None = None,
                 doc: str | None = None, arity: int | None = None, **extra):
        node = {
            "id": node_id,
            "name": name,
            "type": ntype,
            "language": language,
            "file": posix(rel_path),
            "line_start": line_start,
            "line_end": line_end,
            "parent": parent,
        }
        if arity is not None:
            node["arity"] = arity
        if signature:
            node["signature"] = signature
        if doc:
            node["doc"] = doc
        node.update(extra)
        self.nodes.append(node)
        self._by_id[node_id] = node
        self._names.setdefault(name, []).append(node_id)
        # every symbol is contained by its parent (file or enclosing symbol)
        if parent:
            self.add_edge(parent, node_id, "contains", external=False, resolution="internal")
        else:
            self.add_edge(posix(rel_path), node_id, "contains", external=False, resolution="internal")
        return node_id

    # ---------- edge construction ----------

    def add_edge(self, frm: str, to: str, etype: str,
                 external: bool | None = None, resolution: str | None = None, **extra):
        """
        Deduplicated edge insert. A repeated identical edge bumps `count`
        instead of appending a second copy — the old version emitted
        `useAuth -> useState` twice with no way to tell duplication from
        two genuine call sites.
        """
        if external is None:
            external = to not in self._by_id
        if resolution is None:
            resolution = "external" if external else "internal"
        key = (frm, to, etype)
        existing = self._edge_index.get(key)
        if existing is not None:
            existing["count"] = existing.get("count", 1) + 1
            return existing
        edge = {
            "from": frm,
            "to": to,
            "type": etype,
            "external": external,
            "resolution": resolution,
            "count": 1,
        }
        edge.update(extra)
        self.edges.append(edge)
        self._edge_index[key] = edge
        return edge

    def queue_call(self, frm, name, owner_type=None, resolved=None,
                   local_binding=False, has_receiver=False, argc=None):
        self.pending_calls.append(
            PendingCall(frm, name, owner_type, resolved, local_binding,
                        has_receiver, argc))

    def queue_ref(self, frm, name, etype, prefer_file=False):
        self.pending_refs.append(PendingRef(frm, name, etype, prefer_file))

    # ---------- resolution (after every file is parsed) ----------

    def _by_arity(self, candidates: list, argc: int | None) -> list:
        """Prefer the overload whose parameter count matches the call site."""
        if argc is None:
            return candidates
        exact = [c for c in candidates if self._by_id[c].get("arity") == argc]
        return exact or candidates

    def _lookup(self, name: str, prefer_types=(), from_id: str | None = None,
                exclude: set | None = None, argc: int | None = None):
        """
        Resolve a bare name to a node id. Returns (node_id, ambiguous_bool)
        or (None, False).
        """
        candidates = list(self._names.get(name, ()))
        if not candidates:
            return None, False
        if exclude:
            narrowed = [c for c in candidates if c not in exclude]
            if narrowed:
                candidates = narrowed
        candidates = self._by_arity(candidates, argc)

        # A C header prototype and its definition are the same logical symbol,
        # so their coexistence is not real ambiguity worth flagging.
        if any(self._by_id[c]["type"] == "function" for c in candidates):
            candidates = [c for c in candidates
                          if self._by_id[c]["type"] != "function_declaration"]
        if len(candidates) == 1:
            return candidates[0], False

        ranked = candidates
        if prefer_types:
            typed = [c for c in ranked if self._by_id[c]["type"] in prefer_types]
            if typed:
                # prefer_types is ordered by preference, so a real `function`
                # definition wins over a header `function_declaration`
                ranked = sorted(typed, key=lambda c: prefer_types.index(self._by_id[c]["type"]))
        if from_id:
            same_file = [c for c in ranked if self._by_id[c]["file"] == self._file_of(from_id)]
            if same_file:
                ranked = same_file
        return ranked[0], len(candidates) > 1

    def _file_of(self, node_id: str) -> str | None:
        node = self._by_id.get(node_id)
        return node["file"] if node else None

    def _member_of_type(self, type_name: str, member: str, argc: int | None = None,
                        exclude: set | None = None):
        """`userRepo.findByEmail()` where userRepo is a UserRepo -> that method's id."""
        for type_id in self._names.get(type_name, ()):
            if self._by_id[type_id]["type"] not in TYPE_LIKE_TYPES:
                continue
            owned = [m for m in self._names.get(member, ())
                     if self._by_id[m].get("parent") == type_id]
            if exclude:
                owned = [m for m in owned if m not in exclude] or owned
            owned = self._by_arity(owned, argc)
            if owned:
                return owned[0]
        return None

    def resolve(self):
        """Resolve every queued call and reference. Idempotent per accumulator."""
        for pc in self.pending_calls:
            if pc.resolved and pc.resolved in self._by_id:
                self.add_edge(pc.frm, pc.resolved, "calls", external=False, resolution="internal")
                continue

            # 1. receiver type known (e.g. a Java field/local with a declared type)
            if pc.owner_type:
                target = self._member_of_type(
                    pc.owner_type, pc.name, argc=pc.argc,
                    exclude={pc.frm} if pc.has_receiver else None)
                if target:
                    self.add_edge(pc.frm, target, "calls", external=False,
                                  resolution="internal", via=pc.owner_type)
                    continue
                # receiver is a type we know nothing about (Map, System, ...)
                if pc.owner_type not in self._names:
                    self.add_edge(pc.frm, f"{pc.owner_type}.{pc.name}", "calls",
                                  external=True, resolution="external")
                    continue

            # 2. plain name lookup across the whole graph. A call through an
            #    explicit receiver cannot be a call to the enclosing method, so
            #    the caller is excluded — otherwise `self._model.score()` inside
            #    `Evaluator.score` resolves to itself and reports fake recursion.
            target, ambiguous = self._lookup(
                pc.name, CALLABLE_TYPES, pc.frm,
                exclude={pc.frm} if pc.has_receiver else None, argc=pc.argc)
            if target:
                self.add_edge(pc.frm, target, "calls", external=False,
                              resolution="internal", **({"ambiguous": True} if ambiguous else {}))
                continue

            # 3. bound in an enclosing scope but not its own graph node
            #    (a useState setter, a destructured prop) — NOT an external library
            if pc.local_binding:
                self.add_edge(pc.frm, pc.name, "calls", external=False, resolution="local")
                continue

            # 4. genuinely external / stdlib
            self.add_edge(pc.frm, pc.name, "calls", external=True, resolution="external")

        for pr in self.pending_refs:
            if pr.prefer_file:
                target = self._resolve_file_ref(pr.name)
                if target:
                    self.add_edge(pr.frm, target, pr.etype, external=False, resolution="internal")
                    continue
                self.add_edge(pr.frm, pr.name, pr.etype, external=True, resolution="external")
                continue

            # symbol reference: try the full name, then its last dotted segment
            # (`com.example.model.User` -> `User`)
            for candidate in (pr.name, pr.name.rsplit(".", 1)[-1]):
                target, _ = self._lookup(candidate, TYPE_LIKE_TYPES, pr.frm)
                if target:
                    self.add_edge(pr.frm, target, pr.etype, external=False, resolution="internal")
                    break
            else:
                self.add_edge(pr.frm, pr.name, pr.etype, external=True, resolution="external")

        self._link_declarations_to_definitions()

    def _link_declarations_to_definitions(self):
        """
        A C header prototype and its .c definition are two separate nodes.
        Link them so a consumer can jump from the declared public API to the
        implementation instead of treating them as unrelated symbols.
        """
        for node in list(self.nodes):
            if node["type"] != "function_declaration":
                continue
            for cand in self._names.get(node["name"], ()):
                if self._by_id[cand]["type"] == "function":
                    self.add_edge(node["id"], cand, "defined_by",
                                  external=False, resolution="internal")
                    break

    def _resolve_file_ref(self, spec: str):
        """
        Map an import/include spec onto a file node.
        Handles `./useAuth` -> `react-sample/src/useAuth.js`, `auth.h`,
        and dotted Python modules like `.model` / `model`.
        """
        spec = posix(spec).lstrip("./")
        if not spec:
            return None
        stem = spec.rsplit("/", 1)[-1]
        dotted = stem.replace(".", "/") if "." not in Path(stem).suffix else stem

        file_ids = [n["id"] for n in self.nodes if n["type"] == "file"]
        # exact suffix match first (handles extensionless JS imports)
        for fid in file_ids:
            if fid == spec or fid.endswith("/" + spec):
                return fid
        for fid in file_ids:
            if Path(fid).stem == Path(stem).stem or Path(fid).name == stem:
                return fid
        for fid in file_ids:
            if Path(fid).stem == Path(dotted).stem:
                return fid
        return None

    # ---------- output ----------

    def to_json(self):
        combined = content_hash("".join(f"{k}:{v}" for k, v in sorted(self._file_hashes.items())))
        languages = dict(sorted(self._languages.items(), key=lambda kv: (-kv[1], kv[0])))
        return {
            "meta": {
                "schema_version": SCHEMA_VERSION,
                "target": self.target,
                # `language` kept for backward compatibility = dominant language
                "language": next(iter(languages), None),
                "languages": languages,
                "source_path": self.source_path,
                "hash": combined,
                "generated_at": None,  # filled in by run_graphify.py
                "node_count": len(self.nodes),
                "edge_count": len(self.edges),
                # Recorded in the graph, not just printed: a partially parsed
                # codebase silently produces a graph with missing edges, and the
                # app needs to be able to say so.
                "parse_failures": list(self.failures),
            },
            "nodes": self.nodes,
            "edges": self.edges,
        }
