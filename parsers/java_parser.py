"""
Java parser (tree-sitter).

Beyond classes/methods it now does *receiver-type resolution*: it builds a
scope table of declared types (fields, parameters, locals) so a call like
`userRepo.findByEmail(email)` resolves to `UserRepo.findByEmail` instead of
being guessed by name alone. That's what turns the call graph from
"probably right" into "actually right" when a method name is reused across
classes.
"""
import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

from pathlib import Path
from .common import (read_file, node_lines, count_lines, first_line_signature)

_LANG = Language(tsjava.language())
LANGUAGE = "java"

_CALLABLE = {"method_declaration", "constructor_declaration"}
_TYPE_DECL = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
}


def _base_type_name(text: str) -> str:
    """`Map<String, User>` -> `Map`; `com.example.model.User` -> `User`; `int[]` -> `int`."""
    t = text.split("<")[0].replace("[]", "").strip()
    t = t.replace("final ", "").strip()
    return t.rsplit(".", 1)[-1]


def parse_file(path: Path, rel_path: str, acc, _legacy=None):
    src_text = read_file(path)
    src_bytes = src_text.encode("utf-8")
    tree = Parser(_LANG).parse(src_bytes)
    root = tree.root_node

    file_id = acc.add_file_node(rel_path, src_text, LANGUAGE)

    def text(n):
        return src_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="replace")

    def leading_doc(n):
        parts = []
        sib = n.prev_sibling
        while sib is not None and sib.type in ("comment", "line_comment", "block_comment"):
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

    def declared_vars(decl_node):
        """(name, base_type) pairs from a field/local/param declaration."""
        type_node = decl_node.child_by_field_name("type")
        if type_node is None:
            return []
        base = _base_type_name(text(type_node))
        pairs = []
        if decl_node.type == "formal_parameter":
            name_node = decl_node.child_by_field_name("name")
            if name_node is not None:
                pairs.append((text(name_node), base))
            return pairs
        for d in decl_node.children:
            if d.type == "variable_declarator":
                nm = d.child_by_field_name("name")
                if nm is not None:
                    pairs.append((text(nm), base))
        return pairs

    def lookup_type(scopes, var_name):
        for scope in reversed(scopes):
            if var_name in scope:
                return scope[var_name]
        return None

    def signature_of(n):
        body = n.child_by_field_name("body")
        raw = text(n)[: body.start_byte - n.start_byte] if body is not None else text(n)
        return first_line_signature(raw)

    def arity_of(n):
        params = n.child_by_field_name("parameters")
        if params is None:
            return 0
        return sum(1 for c in params.children
                   if c.type in ("formal_parameter", "spread_parameter", "receiver_parameter"))

    def argc_of(node):
        args = node.child_by_field_name("arguments")
        if args is None:
            return None
        return sum(1 for c in args.children if c.type not in ("(", ")", ","))

    def record_call(node, current_callable, scopes, enclosing_type):
        """Attribute a call site to the innermost enclosing method/constructor."""
        if current_callable is None:
            return
        if node.type == "method_invocation":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                return
            callee = text(name_node)
            obj = node.child_by_field_name("object")
            has_receiver = obj is not None and obj.type != "this"
            owner = None
            if obj is not None:
                if obj.type == "this":
                    owner = enclosing_type
                elif obj.type == "identifier":
                    var = text(obj)
                    owner = lookup_type(scopes, var)
                    if owner is None and var[:1].isupper():
                        owner = var  # static call: TokenUtil.generate(...)
                else:
                    # chained / field access receiver (System.out, foo().bar())
                    owner = None
            else:
                owner = enclosing_type  # unqualified call = this class (or inherited)
            acc.queue_call(current_callable, callee, owner_type=owner,
                           has_receiver=has_receiver, argc=argc_of(node))
        elif node.type == "object_creation_expression":
            type_node = node.child_by_field_name("type")
            if type_node is None:
                return
            cls = _base_type_name(text(type_node))
            # constructors are named after their class
            acc.queue_call(current_callable, cls, owner_type=cls, argc=argc_of(node))

    def walk(node, parent_id, current_callable, scopes, enclosing_type):
        for child in node.children:
            ctype = child.type

            if ctype in _TYPE_DECL:
                name_node = child.child_by_field_name("name")
                name = text(name_node) if name_node else "<anonymous>"
                ls, le = node_lines(child)
                node_id = acc.new_id(parent_id, name)
                acc.add_node(node_id, name, _TYPE_DECL[ctype], rel_path, ls, le, parent_id,
                             language=LANGUAGE, signature=signature_of(child),
                             doc=leading_doc(child))

                superclass = child.child_by_field_name("superclass")
                if superclass is not None:
                    acc.queue_ref(node_id, _base_type_name(
                        text(superclass).replace("extends", "").strip()), "extends")
                interfaces = child.child_by_field_name("interfaces")
                if interfaces is not None:
                    for id_node in find(interfaces, {"type_identifier"}):
                        acc.queue_ref(node_id, text(id_node), "implements")

                # Pre-scan fields so methods can resolve receivers regardless of
                # declaration order inside the class.
                class_scope = {}
                body = child.child_by_field_name("body")
                if body is not None:
                    for fd in body.children:
                        if fd.type == "field_declaration":
                            class_scope.update(dict(declared_vars(fd)))

                walk(child, node_id, None, scopes + [class_scope], name)
                continue

            if ctype == "field_declaration":
                type_node = child.child_by_field_name("type")
                base = _base_type_name(text(type_node)) if type_node is not None else "var"
                ls, le = node_lines(child)
                for var_name, _ in declared_vars(child):
                    fid = acc.new_id(parent_id, var_name)
                    acc.add_node(fid, var_name, "field", rel_path, ls, le, parent_id,
                                 language=LANGUAGE, signature=first_line_signature(text(child)),
                                 doc=leading_doc(child), value_type=base)
                continue

            if ctype in _CALLABLE:
                name_node = child.child_by_field_name("name")
                name = text(name_node) if name_node else "<unknown>"
                ntype = "constructor" if ctype == "constructor_declaration" else "method"
                ls, le = node_lines(child)
                arity = arity_of(child)
                node_id = acc.new_id(parent_id, name, arity)
                acc.add_node(node_id, name, ntype, rel_path, ls, le, parent_id,
                             language=LANGUAGE, signature=signature_of(child),
                             doc=leading_doc(child), arity=arity)

                method_scope = {}
                params = child.child_by_field_name("parameters")
                if params is not None:
                    for p in params.children:
                        if p.type in ("formal_parameter", "spread_parameter"):
                            method_scope.update(dict(declared_vars(p)))
                body = child.child_by_field_name("body")
                if body is not None:
                    for lv in find(body, {"local_variable_declaration"}):
                        method_scope.update(dict(declared_vars(lv)))

                walk(child, node_id, node_id, scopes + [method_scope], enclosing_type)
                continue

            if ctype in ("method_invocation", "object_creation_expression"):
                record_call(child, current_callable, scopes, enclosing_type)
                # keep walking: arguments can contain further calls
                walk(child, parent_id, current_callable, scopes, enclosing_type)
                continue

            walk(child, parent_id, current_callable, scopes, enclosing_type)

    walk(root, file_id, None, [{}], None)

    for imp in find(root, {"import_declaration"}):
        spec = text(imp).replace("import", "").replace("static", "").strip().rstrip(";").strip()
        if spec:
            acc.queue_ref(file_id, spec, "imports")
