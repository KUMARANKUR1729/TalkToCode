"""
JavaScript / JSX parser (tree-sitter).

The three things that made the old React graph misleading, now fixed:

1. `const login = useCallback(async () => {...})` produced NO node, because
   the declarator's value is a call_expression, not an arrow_function. So
   `LoginForm.handleSubmit -> login` came out as `external: true`. Known HOC
   wrappers (useCallback/useMemo/memo/forwardRef/...) are now unwrapped and
   the inner function is registered under the variable name — which makes
   that edge resolve internally, no type inference needed.
2. `<LoginForm />` in App.jsx is the actual structure of a React app and was
   completely absent. JSX elements with capitalised names now emit `renders`
   edges.
3. `setEmail`, `setUser` etc. were marked `external: true` as if they were
   library functions. Locally-bound names (params, destructured values,
   consts) are now tracked and marked `resolution: "local"` instead.

Calls are attributed to the innermost enclosing function, and nesting is
recorded via `contains`, so a nested closure has a real parent.
"""
import tree_sitter_javascript as tsjs
from tree_sitter import Language, Parser

from pathlib import Path
from .common import read_file, node_lines, first_line_signature

_LANG = Language(tsjs.language())
LANGUAGE = "javascript"

# Wrappers whose first function argument IS the thing being named.
HOC_WRAPPERS = {
    "useCallback", "useMemo", "memo", "forwardRef", "useEvent",
    "React.memo", "React.forwardRef", "React.useCallback", "React.useMemo",
    "observer", "withRouter", "connect",
}

_FN_VALUE_TYPES = {"arrow_function", "function_expression", "generator_function"}
_FN_DECL_TYPES = {"function_declaration", "generator_function_declaration"}
_SCOPE_BOUNDARIES = _FN_VALUE_TYPES | _FN_DECL_TYPES | {
    "class_declaration", "class", "method_definition",
}


def _classify(name: str) -> str:
    if name.startswith("use") and len(name) > 3 and name[3].isupper():
        return "hook"
    if name[:1].isupper():
        return "component"
    return "function"


def parse_file(path: Path, rel_path: str, acc, _legacy=None):
    src_text = read_file(path)
    src_bytes = src_text.encode("utf-8")
    tree = Parser(_LANG).parse(src_bytes)
    root = tree.root_node

    file_id = acc.add_file_node(rel_path, src_text, LANGUAGE)
    file_stem = Path(rel_path).stem

    def text(n):
        return src_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="replace")

    def leading_doc(n):
        parts = []
        sib = n.prev_sibling
        while sib is not None and sib.type == "comment":
            parts.append(text(sib))
            sib = sib.prev_sibling
        if not parts:
            return None
        raw = "\n".join(reversed(parts))
        cleaned = " ".join(
            line.strip().lstrip("/*").lstrip("*").rstrip("*/").strip()
            for line in raw.splitlines()
        )
        return " ".join(cleaned.split())[:400] or None

    def callee_text(fn_ref):
        """Dotted name for a call target: `useCallback`, `React.memo`, `res.json`."""
        if fn_ref is None:
            return None
        if fn_ref.type == "identifier":
            return text(fn_ref)
        if fn_ref.type == "member_expression":
            return text(fn_ref).strip()
        return None

    def pattern_names(node, out):
        """Every identifier bound by a parameter or destructuring pattern."""
        if node is None:
            return out
        t = node.type
        if t == "identifier":
            out.add(text(node))
        elif t in ("shorthand_property_identifier_pattern", "property_identifier"):
            out.add(text(node))
        elif t == "pair_pattern":
            pattern_names(node.child_by_field_name("value"), out)
        elif t == "assignment_pattern":
            pattern_names(node.child_by_field_name("left"), out)
        elif t in ("object_pattern", "array_pattern", "rest_pattern"):
            for c in node.children:
                pattern_names(c, out)
        return out

    def param_names(fn_node):
        names = set()
        params = fn_node.child_by_field_name("parameters")
        if params is not None:
            for c in params.children:
                if c.type not in ("(", ")", ","):
                    pattern_names(c, names)
        else:
            # single-parameter arrow: `e => ...`
            p = fn_node.child_by_field_name("parameter")
            if p is not None:
                pattern_names(p, names)
        return names

    def arity_of(fn_node):
        params = fn_node.child_by_field_name("parameters")
        if params is not None:
            return sum(1 for c in params.children if c.type not in ("(", ")", ","))
        return 1 if fn_node.child_by_field_name("parameter") is not None else 0

    def collect_bindings(scope_node):
        """Names declared directly in this scope, not descending into nested functions."""
        names = set()

        def rec(n, is_root=False):
            if not is_root and n.type in _SCOPE_BOUNDARIES:
                return
            if n.type == "variable_declarator":
                pattern_names(n.child_by_field_name("name"), names)
            elif n.type in _FN_DECL_TYPES:
                nm = n.child_by_field_name("name")
                if nm is not None:
                    names.add(text(nm))
                return
            for c in n.children:
                rec(c)

        rec(scope_node, is_root=True)
        return names

    def span_for(node):
        """
        Prefer the whole statement so line ranges cover `export default
        function X()` and `const x = () => {}` including the keyword.
        """
        parent = node.parent
        if parent is None:
            return node
        if parent.type == "export_statement":
            return parent
        if parent.type in ("lexical_declaration", "variable_declaration"):
            declarators = [c for c in parent.children if c.type == "variable_declarator"]
            if len(declarators) <= 1:
                return parent
        return node

    def signature_of(span_node, fn_node):
        raw = text(span_node)
        body = fn_node.child_by_field_name("body")
        if body is not None:
            cut = body.start_byte - span_node.start_byte
            if 0 < cut <= len(raw):
                raw = raw[:cut]
        return first_line_signature(raw)

    def register_function(fn_node, name, ntype, parent_id, span_node, scopes):
        ls, le = node_lines(span_node)
        arity = arity_of(fn_node)
        node_id = acc.new_id(parent_id, name, arity)
        acc.add_node(node_id, name, ntype, rel_path, ls, le, parent_id,
                     language=LANGUAGE, signature=signature_of(span_node, fn_node),
                     doc=leading_doc(span_node), arity=arity,
                     is_async=b"async" in text(span_node)[:32].encode("utf-8"))
        body = fn_node.child_by_field_name("body")
        inner_scope = param_names(fn_node)
        if body is not None:
            inner_scope |= collect_bindings(body)
        walk(fn_node, node_id, node_id, scopes + [inner_scope])
        return node_id

    def unwrap_hoc(value_node):
        """`useCallback(fn, deps)` -> fn. Returns None if not a known wrapper."""
        if value_node is None or value_node.type != "call_expression":
            return None
        callee = callee_text(value_node.child_by_field_name("function"))
        if callee not in HOC_WRAPPERS:
            return None
        args = value_node.child_by_field_name("arguments")
        if args is None:
            return None
        for a in args.children:
            if a.type in _FN_VALUE_TYPES:
                return a
        return None

    def argc_of_call(node):
        args = node.child_by_field_name("arguments")
        if args is None:
            return None
        return sum(1 for c in args.children if c.type not in ("(", ")", ","))

    def handle_call(node, current_fn, scopes):
        owner = current_fn or file_id
        fn_ref = node.child_by_field_name("function")
        if fn_ref is None:
            return
        argc = argc_of_call(node)
        if fn_ref.type == "identifier":
            name = text(fn_ref)
            bound_locally = any(name in s for s in scopes)
            acc.queue_call(owner, name, local_binding=bound_locally, argc=argc)
        elif fn_ref.type == "member_expression":
            prop = fn_ref.child_by_field_name("property")
            obj = fn_ref.child_by_field_name("object")
            if prop is None:
                return
            owner_type = text(obj) if (obj is not None and obj.type == "identifier"
                                       and text(obj)[:1].isupper()) else None
            is_this = obj is not None and obj.type == "this"
            acc.queue_call(owner, text(prop), owner_type=owner_type,
                           has_receiver=not is_this, argc=argc)

    def handle_jsx(node, current_fn):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        raw = text(name_node)
        leaf = raw.rsplit(".", 1)[-1]
        if not leaf[:1].isupper():
            return  # <div>, <form>, ... are DOM elements, not components
        acc.queue_ref(current_fn or file_id, leaf, "renders")

    def walk(node, parent_id, current_fn, scopes):
        for child in node.children:
            t = child.type

            if t in _FN_DECL_TYPES:
                name_node = child.child_by_field_name("name")
                name = text(name_node) if name_node is not None else file_stem
                register_function(child, name, _classify(name), parent_id,
                                  span_for(child), scopes)
                continue

            if t in ("class_declaration", "class"):
                name_node = child.child_by_field_name("name")
                name = text(name_node) if name_node is not None else file_stem
                heritage = child.child_by_field_name("superclass")
                ls, le = node_lines(span_for(child))
                ntype = "component" if name[:1].isupper() and heritage is not None else (
                    "component" if name[:1].isupper() else "class")
                node_id = acc.new_id(parent_id, name)
                acc.add_node(node_id, name, ntype, rel_path, ls, le, parent_id,
                             language=LANGUAGE,
                             signature=first_line_signature(text(child)),
                             doc=leading_doc(span_for(child)))
                if heritage is not None:
                    acc.queue_ref(node_id, text(heritage).rsplit(".", 1)[-1], "extends")
                walk(child, node_id, None, scopes)
                continue

            if t == "method_definition":
                name_node = child.child_by_field_name("name")
                name = text(name_node) if name_node is not None else "<anonymous>"
                ls, le = node_lines(child)
                arity = arity_of(child)
                node_id = acc.new_id(parent_id, name, arity)
                acc.add_node(node_id, name, "method", rel_path, ls, le, parent_id,
                             language=LANGUAGE, signature=signature_of(child, child),
                             doc=leading_doc(child), arity=arity)
                body = child.child_by_field_name("body")
                inner = param_names(child)
                if body is not None:
                    inner |= collect_bindings(body)
                walk(child, node_id, node_id, scopes + [inner])
                continue

            if t == "variable_declarator":
                name_node = child.child_by_field_name("name")
                value_node = child.child_by_field_name("value")
                if name_node is not None and name_node.type == "identifier":
                    name = text(name_node)
                    fn_node = None
                    if value_node is not None and value_node.type in _FN_VALUE_TYPES:
                        fn_node = value_node
                    else:
                        fn_node = unwrap_hoc(value_node)
                    if fn_node is not None:
                        # `const login = useCallback(fn, [])` genuinely calls
                        # useCallback, so keep that edge even though the node we
                        # register is the inner function.
                        if value_node is not None and fn_node is not value_node:
                            handle_call(value_node, current_fn, scopes)
                        register_function(fn_node, name, _classify(name), parent_id,
                                          span_for(child), scopes)
                        continue
                    # Module-level UPPER_CASE constant (`const BASE_URL = "/api"`),
                    # for parity with the Python parser's constant capture.
                    if (parent_id == file_id and current_fn is None
                            and name.isupper() and len(name) > 1):
                        span = span_for(child)
                        ls, le = node_lines(span)
                        const_id = acc.new_id(parent_id, name)
                        acc.add_node(const_id, name, "constant", rel_path, ls, le,
                                     parent_id, language=LANGUAGE,
                                     signature=first_line_signature(text(span)))
                        continue
                walk(child, parent_id, current_fn, scopes)
                continue

            if t == "export_statement":
                # `export default () => {}` / `export default function () {}`
                anon = next((c for c in child.children if c.type in _FN_VALUE_TYPES), None)
                if anon is not None:
                    register_function(anon, file_stem, _classify(file_stem), parent_id,
                                      child, scopes)
                    continue
                walk(child, parent_id, current_fn, scopes)
                continue

            if t == "call_expression":
                handle_call(child, current_fn, scopes)
                walk(child, parent_id, current_fn, scopes)
                continue

            if t in ("jsx_opening_element", "jsx_self_closing_element"):
                handle_jsx(child, current_fn)
                walk(child, parent_id, current_fn, scopes)
                continue

            walk(child, parent_id, current_fn, scopes)

    top_scope = collect_bindings(root)
    walk(root, file_id, None, [top_scope])

    for imp in _find_all(root, {"import_statement"}):
        src_node = imp.child_by_field_name("source")
        if src_node is None:
            continue
        module = text(src_node).strip("\"'")
        if module.startswith("."):
            acc.queue_ref(file_id, module, "imports", prefer_file=True)
        else:
            acc.add_edge(file_id, module, "imports", external=True, resolution="external")


def _find_all(n, types):
    out = []

    def rec(x):
        if x.type in types:
            out.append(x)
        for c in x.children:
            rec(c)

    rec(n)
    return out
