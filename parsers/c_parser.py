"""
C parser (tree-sitter).

Additions over the first version:
  * header function *declarations* (prototypes) become nodes, so the public
    API declared in auth.h is actually in the graph
  * typedefs, enums (and their constants), struct members and file-scope
    globals are captured
  * include guards (`#define AUTH_H` with no value) are filtered out — they
    were showing up as meaningful "macro" nodes and polluting retrieval
"""
import tree_sitter_c as tsc
from tree_sitter import Language, Parser

from pathlib import Path
from .common import read_file, node_lines, first_line_signature

_LANG = Language(tsc.language())
LANGUAGE = "c"


#: Containers we descend through when looking for file-scope declarations.
#: Deliberately excludes function bodies (`compound_statement`).
_TRANSPARENT = {
    "translation_unit", "preproc_ifdef", "preproc_if", "preproc_else",
    "preproc_elif", "preproc_ifndef", "linkage_specification",
    "declaration_list",
}


def file_scope_declarations(root):
    """Every `declaration` at file scope, including inside include guards."""
    out = []

    def rec(node):
        for child in node.children:
            if child.type == "declaration":
                out.append(child)
            elif child.type in _TRANSPARENT:
                rec(child)

    rec(root)
    return out


def _unwrap_to(node, target_type):
    """Walk through pointer/array declarator wrappers to the real declarator."""
    cur = node
    seen = 0
    while cur is not None and cur.type != target_type and seen < 12:
        cur = cur.child_by_field_name("declarator")
        seen += 1
    return cur if (cur is not None and cur.type == target_type) else None


def parse_file(path: Path, rel_path: str, acc, _legacy=None):
    src_text = read_file(path)
    src_bytes = src_text.encode("utf-8")
    tree = Parser(_LANG).parse(src_bytes)
    root = tree.root_node

    file_id = acc.add_file_node(rel_path, src_text, LANGUAGE)
    stem_upper = Path(rel_path).stem.upper().replace("-", "_")

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

    def find(n, types):
        out = []

        def rec(x):
            if x.type in types:
                out.append(x)
            for c in x.children:
                rec(c)

        rec(n)
        return out

    def is_include_guard(name: str, value_node) -> bool:
        if value_node is not None and text(value_node).strip():
            return False
        if not name.isupper():
            return False
        flat = name.strip("_").replace("_", "")
        return (name.rstrip("_").endswith("_H")
                or name.endswith("_H")
                or flat == stem_upper.replace("_", "") + "H"
                or flat == stem_upper.replace("_", ""))

    def declarator_name(decl):
        """Innermost identifier of a declarator chain."""
        cur = decl
        seen = 0
        while cur is not None and seen < 12:
            # Struct members are `field_identifier`, not `identifier`. Missing
            # this meant array members (`char item[32];`) were silently dropped
            # while scalar ones (`int quantity;`) came through.
            if cur.type in ("identifier", "field_identifier"):
                return text(cur)
            if cur.type == "function_declarator":
                inner = cur.child_by_field_name("declarator")
                cur = inner
            else:
                nxt = cur.child_by_field_name("declarator")
                if nxt is None:
                    ident = next((c for c in cur.children
                                  if c.type in ("identifier", "field_identifier")), None)
                    return text(ident) if ident is not None else None
                cur = nxt
            seen += 1
        return None

    def arity_of(fdecl):
        params = fdecl.child_by_field_name("parameters")
        if params is None:
            return 0
        decls = [c for c in params.children if c.type == "parameter_declaration"]
        # `void` in `f(void)` is zero params, not one
        if len(decls) == 1 and text(decls[0]).strip() == "void":
            return 0
        return len(decls) + sum(1 for c in params.children if c.type == "variadic_parameter")

    def argc_of_call(call):
        args = call.child_by_field_name("arguments")
        if args is None:
            return None
        return sum(1 for c in args.children if c.type not in ("(", ")", ","))

    def record_calls(container, owner_id):
        for call in find(container, {"call_expression"}):
            fn_ref = call.child_by_field_name("function")
            if fn_ref is None:
                continue
            argc = argc_of_call(call)
            if fn_ref.type == "identifier":
                acc.queue_call(owner_id, text(fn_ref), argc=argc)
            elif fn_ref.type == "field_expression":
                field = fn_ref.child_by_field_name("field")
                if field is not None:
                    acc.queue_call(owner_id, text(field), has_receiver=True, argc=argc)

    # ---- function definitions ----
    for fn in find(root, {"function_definition"}):
        fdecl = _unwrap_to(fn.child_by_field_name("declarator"), "function_declarator")
        name = declarator_name(fdecl) if fdecl is not None else None
        if not name:
            continue
        ls, le = node_lines(fn)
        body = fn.child_by_field_name("body")
        raw_sig = text(fn)[: body.start_byte - fn.start_byte] if body is not None else text(fn)
        arity = arity_of(fdecl)
        node_id = acc.new_id(file_id, name, arity)
        acc.add_node(node_id, name, "function", rel_path, ls, le, file_id,
                     language=LANGUAGE, signature=first_line_signature(raw_sig),
                     doc=leading_doc(fn), arity=arity)
        record_calls(fn, node_id)

    # ---- top-level declarations: prototypes and globals ----
    # Header contents live INSIDE a `#ifndef FOO_H` guard, i.e. inside a
    # preproc_ifdef node — not as direct children of the root. Descending
    # through preprocessor blocks (but never into function bodies, where
    # `declaration` means "local variable") is what makes header-declared
    # APIs show up in the graph at all.
    for decl in file_scope_declarations(root):
        inner = decl.child_by_field_name("declarator")
        fdecl = _unwrap_to(inner, "function_declarator")
        ls, le = node_lines(decl)
        if fdecl is not None:
            name = declarator_name(fdecl)
            if not name:
                continue
            arity = arity_of(fdecl)
            node_id = acc.new_id(file_id, name, arity)
            acc.add_node(node_id, name, "function_declaration", rel_path, ls, le, file_id,
                         language=LANGUAGE, signature=first_line_signature(text(decl)),
                         doc=leading_doc(decl), arity=arity)
        else:
            for d in decl.children:
                if d.type not in ("init_declarator", "identifier", "pointer_declarator",
                                  "array_declarator"):
                    continue
                name = declarator_name(d) if d.type != "identifier" else text(d)
                if not name:
                    continue
                node_id = acc.new_id(file_id, name)
                acc.add_node(node_id, name, "global", rel_path, ls, le, file_id,
                             language=LANGUAGE, signature=first_line_signature(text(decl)),
                             doc=leading_doc(decl))

    # ---- structs / unions (definitions only) + their members ----
    for st in find(root, {"struct_specifier", "union_specifier"}):
        body = st.child_by_field_name("body")
        if body is None:
            continue
        name_node = st.child_by_field_name("name")
        name = text(name_node) if name_node else "<anonymous>"
        ls, le = node_lines(st)
        ntype = "struct" if st.type == "struct_specifier" else "union"
        node_id = acc.new_id(file_id, name)
        acc.add_node(node_id, name, ntype, rel_path, ls, le, file_id,
                     language=LANGUAGE, signature=first_line_signature(text(st)),
                     doc=leading_doc(st))
        for member in body.children:
            if member.type != "field_declaration":
                continue
            type_node = member.child_by_field_name("type")
            base = text(type_node).strip() if type_node is not None else "?"
            mls, mle = node_lines(member)
            for d in member.children:
                if d.type in ("field_identifier", "pointer_declarator", "array_declarator"):
                    mname = text(d) if d.type == "field_identifier" else declarator_name(d)
                    if not mname:
                        continue
                    mid = acc.new_id(node_id, mname)
                    acc.add_node(mid, mname, "field", rel_path, mls, mle, node_id,
                                 language=LANGUAGE,
                                 signature=first_line_signature(text(member)),
                                 value_type=base)

    # ---- enums + constants ----
    for en in find(root, {"enum_specifier"}):
        body = en.child_by_field_name("body")
        if body is None:
            continue
        name_node = en.child_by_field_name("name")
        name = text(name_node) if name_node else "<anonymous enum>"
        ls, le = node_lines(en)
        node_id = acc.new_id(file_id, name)
        acc.add_node(node_id, name, "enum", rel_path, ls, le, file_id,
                     language=LANGUAGE, signature=first_line_signature(text(en)),
                     doc=leading_doc(en))
        for ec in find(body, {"enumerator"}):
            ec_name_node = ec.child_by_field_name("name")
            if ec_name_node is None:
                continue
            els, ele = node_lines(ec)
            cid = acc.new_id(node_id, text(ec_name_node))
            acc.add_node(cid, text(ec_name_node), "enum_constant", rel_path, els, ele, node_id,
                         language=LANGUAGE, signature=first_line_signature(text(ec)))

    # ---- typedefs ----
    for td in find(root, {"type_definition"}):
        decl = td.child_by_field_name("declarator")
        name = declarator_name(decl) if decl is not None else None
        if not name:
            continue
        ls, le = node_lines(td)
        node_id = acc.new_id(file_id, name)
        acc.add_node(node_id, name, "typedef", rel_path, ls, le, file_id,
                     language=LANGUAGE, signature=first_line_signature(text(td)),
                     doc=leading_doc(td))

    # ---- macros (real ones — include guards filtered out) ----
    for mac in find(root, {"preproc_def", "preproc_function_def"}):
        name_node = mac.child_by_field_name("name")
        if name_node is None:
            continue
        name = text(name_node)
        value_node = mac.child_by_field_name("value")
        if mac.type == "preproc_def" and is_include_guard(name, value_node):
            continue
        ls, le = node_lines(mac)
        node_id = acc.new_id(file_id, name)
        acc.add_node(node_id, name, "macro", rel_path, ls, le, file_id,
                     language=LANGUAGE, signature=first_line_signature(text(mac)),
                     doc=leading_doc(mac))

    # ---- includes ----
    for inc in find(root, {"preproc_include"}):
        path_node = inc.child_by_field_name("path")
        if path_node is None:
            path_node = inc.children[1] if len(inc.children) > 1 else None
        if path_node is None:
            continue
        raw = text(path_node).strip('"<>')
        if path_node.type == "system_lib_string":
            acc.add_edge(file_id, raw, "includes", external=True, resolution="external")
        else:
            acc.queue_ref(file_id, raw, "includes", prefer_file=True)
