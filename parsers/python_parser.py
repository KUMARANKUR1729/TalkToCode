"""
Python parser (stdlib `ast` — exact line numbers, no third-party grammar).

Additions over the first version:
  * calls are attributed to the INNERMOST enclosing function, not smeared
    onto every ancestor
  * `self.method()` resolves against the enclosing class, so intra-class
    calls are internal edges instead of name guesses
  * async functions, decorators, and module-level constants are captured
  * docstrings are stored on the node, which materially improves retrieval
"""
import ast
from pathlib import Path

from .common import read_file

LANGUAGE = "python"

_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _unparse(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return getattr(node, "id", "?")


def _arity(fn) -> int:
    a = fn.args
    n = len(a.posonlyargs) + len(a.args) + len(a.kwonlyargs)
    if a.vararg:
        n += 1
    if a.kwarg:
        n += 1
    return n


def _call_argc(call, _has_receiver) -> int:
    """
    Argument count as the callee would see it. A bound method call passes an
    implicit `self`, so `obj.score(a, b)` lines up with `def score(self, a, b)`.
    An imprecise count is harmless: resolution falls back to all candidates
    when nothing matches exactly.
    """
    count = len(call.args) + len(call.keywords)
    if isinstance(call.func, ast.Attribute):
        count += 1
    return count


def _signature(fn) -> str:
    prefix = "async def" if isinstance(fn, ast.AsyncFunctionDef) else "def"
    try:
        args = ast.unparse(fn.args)
    except Exception:
        args = "..."
    ret = f" -> {_unparse(fn.returns)}" if fn.returns is not None else ""
    return f"{prefix} {fn.name}({args}){ret}"


def _direct_calls(scope_node):
    """
    Every Call in this scope's body, stopping at nested function/class/lambda
    boundaries so a nested def's calls are not credited to its parent.
    """
    found = []

    def visit(node, is_root=False):
        if not is_root and isinstance(node, _SCOPE_TYPES):
            return
        if isinstance(node, ast.Call):
            found.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(scope_node, is_root=True)
    return found


def parse_file(path: Path, rel_path: str, acc, _legacy=None):
    src_text = read_file(path)
    file_id = acc.add_file_node(rel_path, src_text, LANGUAGE)

    try:
        tree = ast.parse(src_text, filename=str(path))
    except SyntaxError as e:
        acc._by_id[file_id]["parse_error"] = f"{e.msg} (line {e.lineno})"
        return

    module_classes = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
    }

    def end_of(node):
        return getattr(node, "end_lineno", node.lineno) or node.lineno

    def callee_of(call, enclosing_class):
        """Returns (name, owner_type, has_receiver) for a Call node."""
        fn = call.func
        if isinstance(fn, ast.Name):
            return fn.id, None, False
        if isinstance(fn, ast.Attribute):
            value = fn.value
            if isinstance(value, ast.Name):
                if value.id == "self" and enclosing_class:
                    return fn.attr, enclosing_class, False
                if value.id in module_classes:
                    return fn.attr, value.id, True   # SimpleTokenizer.encode(...)
            # `self._model.score()` — receiver is an attribute, so this is a call
            # on another object, never a call to the enclosing method.
            return fn.attr, None, True
        return None, None, False

    def register_decorators(node, node_id):
        for dec in node.decorator_list:
            name = _unparse(dec).split("(")[0].lstrip("@")
            if name:
                acc.add_edge(node_id, name, "decorated_by")

    def walk(node, parent_id, enclosing_class):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                node_id = acc.new_id(parent_id, child.name)
                bases = ", ".join(_unparse(b) for b in child.bases)
                acc.add_node(node_id, child.name, "class", rel_path,
                             child.lineno, end_of(child), parent_id,
                             language=LANGUAGE,
                             signature=f"class {child.name}({bases})" if bases else f"class {child.name}",
                             doc=ast.get_docstring(child))
                for base in child.bases:
                    acc.queue_ref(node_id, _unparse(base), "extends")
                register_decorators(child, node_id)
                walk(child, node_id, child.name)

            elif isinstance(child, _FUNC_TYPES):
                is_method = acc._by_id.get(parent_id, {}).get("type") == "class"
                ntype = "method" if is_method else "function"
                arity = _arity(child)
                node_id = acc.new_id(parent_id, child.name, arity)
                acc.add_node(node_id, child.name, ntype, rel_path,
                             child.lineno, end_of(child), parent_id,
                             language=LANGUAGE, signature=_signature(child),
                             doc=ast.get_docstring(child), arity=arity,
                             is_async=isinstance(child, ast.AsyncFunctionDef))
                register_decorators(child, node_id)
                for call in _direct_calls(child):
                    name, owner, has_receiver = callee_of(call, enclosing_class)
                    if name:
                        acc.queue_call(node_id, name, owner_type=owner,
                                       has_receiver=has_receiver,
                                       argc=_call_argc(call, has_receiver))
                walk(child, node_id, enclosing_class)

            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                parent_type = acc._by_id.get(parent_id, {}).get("type")
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for t in targets:
                    if not isinstance(t, ast.Name):
                        continue
                    if parent_type == "class":
                        # Class-level declarations, which is how a @dataclass
                        # declares its fields. Without this a Python model class
                        # showed zero fields while its Java twin showed all of
                        # them — same program, different graph.
                        node_id = acc.new_id(parent_id, t.id)
                        acc.add_node(node_id, t.id, "field", rel_path,
                                     child.lineno, end_of(child), parent_id,
                                     language=LANGUAGE,
                                     signature=_unparse(child)[:160],
                                     value_type=(_unparse(child.annotation)
                                                 if isinstance(child, ast.AnnAssign)
                                                 else None))
                    elif parent_id == file_id and t.id.isupper():
                        node_id = acc.new_id(parent_id, t.id)
                        acc.add_node(node_id, t.id, "constant", rel_path,
                                     child.lineno, end_of(child), parent_id,
                                     language=LANGUAGE,
                                     signature=_unparse(child)[:160])
            else:
                walk(child, parent_id, enclosing_class)

    walk(tree, file_id, None)

    # module-level calls that belong to no function (script bodies)
    for call in _direct_calls(tree):
        name, owner, has_receiver = callee_of(call, None)
        if name:
            acc.queue_call(file_id, name, owner_type=owner, has_receiver=has_receiver,
                           argc=_call_argc(call, has_receiver))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                acc.queue_ref(file_id, alias.name, "imports", prefer_file=True)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module:
                acc.queue_ref(file_id, module, "imports", prefer_file=True)
            elif node.level:  # `from . import x`
                for alias in node.names:
                    acc.queue_ref(file_id, alias.name, "imports", prefer_file=True)
