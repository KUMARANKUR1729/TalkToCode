"""
Parser tests.

Two layers:

* Structural invariants, parametrised over every codebase discovered under
  codebase/. Drop in a new sample and it is covered automatically — unique ids,
  ranges inside the file, POSIX paths, hashes, determinism, and the check that
  reading a node's own line range off disk really shows its declaration.
* Behaviour specific to the shop samples, where expected line numbers were read
  off the real sources.
"""
from collections import Counter

import pytest
from conftest import ROOT, TARGETS, VOLATILE_META, edge_between, node_named

from run_graphify import build_graph

DECLARING_TYPES = {"function", "method", "constructor", "class", "interface",
                   "struct", "component", "hook", "macro", "enum", "typedef"}


def call_edge(graph, from_id_suffix, to_id_suffix):
    for edge in graph.edges:
        if (edge["type"] == "calls"
                and edge["from"].endswith(from_id_suffix)
                and edge["to"].endswith(to_id_suffix)):
            return edge
    return None


# ---------- structural invariants, every codebase ----------

def test_targets_were_discovered():
    assert TARGETS, "no codebases found under codebase/"


@pytest.mark.parametrize("target", TARGETS)
def test_no_parse_failures(graphs, target):
    failures = graphs[target].meta.get("parse_failures") or []
    assert failures == [], f"{target}: parser errors {failures}"


@pytest.mark.parametrize("target", TARGETS)
def test_node_ids_are_unique(graphs, target):
    """
    Regression: `file::name` ids merged overloads and same-named methods of
    different classes into one node, so edge lookups hit the wrong symbol.
    """
    counts = Counter(n["id"] for n in graphs[target].nodes)
    assert [i for i, c in counts.items() if c > 1] == []


@pytest.mark.parametrize("target", TARGETS)
def test_line_ranges_are_inside_the_file(graphs, target):
    graph = graphs[target]
    file_end = {n["id"]: n["line_end"] for n in graph.nodes if n["type"] == "file"}
    for node in graph.nodes:
        assert node["line_start"] >= 1, node
        assert node["line_start"] <= node["line_end"], node
        limit = file_end.get(node["file"])
        assert limit is not None and node["line_end"] <= limit, node


@pytest.mark.parametrize("target", TARGETS)
def test_paths_are_posix(graphs, target):
    for node in graphs[target].nodes:
        assert "\\" not in node["file"]
        assert "\\" not in node["id"]


@pytest.mark.parametrize("target", TARGETS)
def test_every_file_node_has_a_hash(graphs, target):
    files = [n for n in graphs[target].nodes if n["type"] == "file"]
    assert files
    assert all(n.get("hash") for n in files)


@pytest.mark.parametrize("target", TARGETS)
def test_every_symbol_has_a_language_and_parent(graphs, target):
    graph = graphs[target]
    for node in graph.symbols:
        assert node.get("language"), node
        assert node.get("parent") in graph.by_id, node


@pytest.mark.parametrize("target", TARGETS)
def test_declaration_line_range_starts_at_the_declaration(graphs, target):
    """
    The strongest single check that line numbers are real: reading the node's
    own range from disk must show its name near the top.
    """
    from graphify.graph import read_snippet

    graph = graphs[target]
    for node in graph.nodes:
        if node["type"] not in DECLARING_TYPES:
            continue
        snippet = read_snippet(node)
        head = "\n".join(snippet.splitlines()[:3])
        assert node["name"] in head, f"{node['id']} -> {head!r}"


@pytest.mark.parametrize("target", TARGETS)
def test_no_edge_points_at_a_missing_internal_node(graphs, target):
    graph = graphs[target]
    for edge in graph.edges:
        if edge.get("resolution") == "internal":
            assert edge["to"] in graph.by_id, edge
        assert edge["from"] in graph.by_id, edge


@pytest.mark.parametrize("target", TARGETS)
def test_no_symbol_reports_calling_itself(graphs, target):
    """
    Regression: `self._model.score()` inside `Evaluator.score` resolved onto the
    caller, inventing recursion that is not in the source.
    """
    graph = graphs[target]
    for edge in graph.edges:
        if edge["type"] != "calls" or edge["from"] != edge["to"]:
            continue
        node = graph.by_id[edge["from"]]
        from graphify.graph import read_snippet
        snippet = read_snippet(node)
        # a real self-call must actually name itself in its own body
        assert snippet.count(node["name"]) > 1, f"fake recursion on {edge['from']}"


@pytest.mark.parametrize("target", TARGETS)
def test_parsing_is_deterministic(target):
    a = build_graph(target, (ROOT / "codebase" / target).resolve())
    b = build_graph(target, (ROOT / "codebase" / target).resolve())
    assert a["nodes"] == b["nodes"]
    assert a["edges"] == b["edges"]
    assert ({k: v for k, v in a["meta"].items() if k not in VOLATILE_META}
            == {k: v for k, v in b["meta"].items() if k not in VOLATILE_META})


@pytest.mark.parametrize("target", TARGETS)
def test_committed_graph_matches_a_fresh_parse(raw_graphs, target):
    """
    outputs/*/knowledge_graph.json is the golden snapshot. If a parser changes,
    this fails until the graphs are rebuilt — a committed graph that no longer
    matches the parser is worse than no graph.

    Fix by running: python run_graphify.py --all
    """
    import json

    path = ROOT / "outputs" / target / "knowledge_graph.json"
    if not path.exists():
        pytest.fail(f"no committed graph for {target} — run `python run_graphify.py --all`")
    committed = json.loads(path.read_text(encoding="utf-8"))
    fresh = raw_graphs[target]

    assert committed["nodes"] == fresh["nodes"], (
        f"{target}: committed nodes differ from a fresh parse — "
        "run `python run_graphify.py --all`")
    assert committed["edges"] == fresh["edges"], (
        f"{target}: committed edges differ from a fresh parse — "
        "run `python run_graphify.py --all`")


# ---------- Java ----------

def test_java_known_line_ranges(graphs):
    graph = graphs["java-shop-sample"]
    checkout = node_named(graph, "checkout", "method")
    assert (checkout["line_start"], checkout["line_end"]) == (17, 34)
    reserve = node_named(graph, "reserve", "method")
    assert (reserve["line_start"], reserve["line_end"]) == (14, 20)


def test_java_receiver_type_resolution(graphs):
    """Calls resolve through declared field types, not by bare-name guessing."""
    graph = graphs["java-shop-sample"]
    edge = edge_between(graph, "checkout", "findById")
    assert edge is not None and edge["resolution"] == "internal"
    assert edge.get("via") == "OrderRepository"
    assert graph.by_id[edge["to"]]["file"].endswith("OrderRepository.java")

    assert edge_between(graph, "checkout", "reserve")["via"] == "InventoryService"
    assert edge_between(graph, "checkout", "getItem")["via"] == "Order"
    assert edge_between(graph, "checkout", "charge")["via"] == "PaymentGateway"


def test_java_overloads_are_distinct_nodes(graphs):
    graph = graphs["java-shop-sample"]
    charges = [n for n in graph.symbols if n["name"] == "charge"]
    assert len(charges) == 2
    assert {n["arity"] for n in charges} == {2, 3}
    assert len({n["id"] for n in charges}) == 2


def test_java_call_site_arity_picks_the_right_overload(graphs):
    """
    `charge(userId, amountCents)` calls `charge(userId, amountCents, "CARD")`.
    Without call-site arity this resolved onto itself and reported recursion.
    """
    graph = graphs["java-shop-sample"]
    assert call_edge(graph, "charge/2", "charge/3") is not None
    assert call_edge(graph, "charge/2", "charge/2") is None
    # the external caller passes two arguments, so it must reach the 2-arg one
    assert call_edge(graph, "checkout/1", "charge/2") is not None


def test_java_same_method_name_in_two_classes_stays_separate(graphs):
    graph = graphs["java-shop-sample"]
    statuses = {graph.qualified_name(n) for n in graph.symbols
                if n["name"] == "status" and n["type"] == "method"}
    assert statuses == {"OrderRepository.status", "PaymentGateway.status"}
    # `Order.status` is a field with the same bare name — kinds stay separate
    assert node_named(graph, "status", "field")["parent"].endswith("::Order")
    # Main calls orders.status(...) -> the repository one, via the field type
    assert edge_between(graph, "main", "status")["via"] == "OrderRepository"


def test_java_unqualified_self_call_resolves_in_class(graphs):
    graph = graphs["java-shop-sample"]
    edge = edge_between(graph, "release", "restock")
    assert edge is not None and edge["resolution"] == "internal"
    assert edge["via"] == "InventoryService"


def test_java_constructor_and_implicit_constructor(graphs):
    graph = graphs["java-shop-sample"]
    explicit = edge_between(graph, "main", "CheckoutService")
    assert graph.by_id[explicit["to"]]["type"] == "constructor"
    # OrderRepository declares none, so the class itself is the only honest target
    implicit = edge_between(graph, "main", "OrderRepository")
    assert graph.by_id[implicit["to"]]["type"] == "class"


def test_java_exception_inheritance_is_external(graphs):
    graph = graphs["java-shop-sample"]
    edges = [e for e in graph.edges if e["type"] == "extends"]
    assert {e["to"] for e in edges} == {"RuntimeException"}
    assert all(e["resolution"] == "external" for e in edges)


def test_java_fields_capture_declared_type(graphs):
    graph = graphs["java-shop-sample"]
    assert node_named(graph, "orders", "field")["value_type"] == "OrderRepository"
    assert node_named(graph, "totalCents", "field")["value_type"] == "int"


# ---------- C ----------

def test_c_header_prototypes_link_to_definitions(graphs):
    graph = graphs["c-shop-sample"]
    proto = next(n for n in graph.symbols
                 if n["name"] == "inventory_reserve" and n["type"] == "function_declaration")
    assert proto["file"].endswith("inventory.h")
    linked = graph.outgoing(proto["id"], "defined_by")
    assert linked
    assert graph.by_id[linked[0]["to"]]["file"].endswith("inventory.c")


def test_c_calls_prefer_the_definition_over_the_prototype(graphs):
    graph = graphs["c-shop-sample"]
    edge = edge_between(graph, "checkout_process", "inventory_reserve")
    assert edge is not None
    assert graph.by_id[edge["to"]]["type"] == "function"


def test_c_include_guards_are_not_macros(graphs):
    names = {n["name"] for n in graphs["c-shop-sample"].symbols if n["type"] == "macro"}
    assert names == {
        "CHECKOUT_ERR_NOT_FOUND", "CHECKOUT_ERR_STOCK", "CHECKOUT_ERR_PAYMENT",
        "MAX_ITEMS", "MAX_ORDERS", "MAX_PAYMENTS",
        "PAYMENT_ERR_AMOUNT", "PAYMENT_ERR_FULL",
    }


def test_c_single_line_macros_span_one_line(graphs):
    for macro in (n for n in graphs["c-shop-sample"].symbols if n["type"] == "macro"):
        assert macro["line_start"] == macro["line_end"], macro


def test_c_struct_members_include_arrays(graphs):
    """
    Regression: struct members are `field_identifier` nodes, so array members
    like `char item[32];` were dropped while `int quantity;` came through.
    """
    graph = graphs["c-shop-sample"]
    order = node_named(graph, "order", "struct")
    members = {c["name"] for c in graph.children(order["id"])}
    assert members == {"order_id", "user_id", "item", "quantity",
                       "total_cents", "status"}


def test_c_static_helpers_and_globals(graphs):
    graph = graphs["c-shop-sample"]
    assert node_named(graph, "stock_find", "function")
    globals_ = {n["name"] for n in graph.symbols if n["type"] == "global"}
    assert {"stock", "stock_count", "orders", "order_count",
            "payment_states", "payment_count"} <= globals_


def test_c_includes_resolve_to_files(graphs):
    graph = graphs["c-shop-sample"]
    internal = [e for e in graph.edges
                if e["type"] == "includes" and e["resolution"] == "internal"]
    assert internal
    assert all(graph.by_id[e["to"]]["type"] == "file" for e in internal)
    system = {e["to"] for e in graph.edges
              if e["type"] == "includes" and e["resolution"] == "external"}
    assert {"stdio.h", "string.h"} <= system


# ---------- Python ----------

def test_python_known_line_ranges(graphs):
    graph = graphs["python-shop-sample"]
    checkout = node_named(graph, "checkout", "method")
    assert (checkout["line_start"], checkout["line_end"]) == (17, 30)
    ml = graphs["ml-shop-sample"]
    train = node_named(ml, "train_epoch", "function")
    assert (train["line_start"], train["line_end"]) == (7, 11)


def test_python_qualified_names(graphs):
    qualified = set(graphs["python-shop-sample"].all_qualified_names())
    assert {"CheckoutService.checkout", "OrderRepository.find_by_id",
            "PaymentGateway.charge", "InventoryService.reserve"} <= qualified


def test_python_self_call_resolves_within_the_class(graphs):
    graph = graphs["python-shop-sample"]
    edge = edge_between(graph, "mark_paid", "find_by_id")
    assert edge is not None and edge["resolution"] == "internal"
    assert edge["via"] == "OrderRepository"


def test_python_cross_object_call_does_not_resolve_to_itself(graphs):
    """
    `Evaluator.score` calls `self._model.score(...)`. Both classes define
    `score`, and the receiver is an attribute — so the target is the model's,
    never the caller's own method.
    """
    graph = graphs["ml-shop-sample"]
    evaluator_score = next(n for n in graph.symbols
                           if graph.qualified_name(n) == "Evaluator.score")
    callees = [graph.by_id[e["to"]] for e in graph.callees(evaluator_score["id"])
               if e["to"] in graph.by_id]
    names = {graph.qualified_name(n) for n in callees}
    assert "ReturnRiskModel.score" in names
    assert "Evaluator.score" not in names


def test_python_dataclass_fields_are_captured(graphs):
    """A Python model class should not show zero fields while its Java twin shows six."""
    graph = graphs["python-shop-sample"]
    order = node_named(graph, "Order", "class")
    fields = {c["name"] for c in graph.children(order["id"]) if c["type"] == "field"}
    assert fields == {"order_id", "user_id", "item", "quantity",
                      "total_cents", "status"}
    assert node_named(graph, "quantity", "field")["value_type"] == "int"


def test_python_decorator_edge(graphs):
    graph = graphs["python-shop-sample"]
    assert any(e["type"] == "decorated_by" and e["to"] == "dataclass"
               for e in graph.edges)


def test_python_module_constant_and_script_entry(graphs):
    graph = graphs["ml-shop-sample"]
    assert node_named(graph, "SAMPLE_ORDERS", "constant")
    # `if __name__ == "__main__": main()` belongs to the file, not to a function
    edge = next(e for e in graph.edges
                if e["type"] == "calls" and e["from"].endswith("train.py"))
    assert graph.by_id[edge["to"]]["name"] == "main"


def test_python_exception_inheritance(graphs):
    graph = graphs["python-shop-sample"]
    extends = {graph.by_id[e["from"]]["name"]: e["to"]
               for e in graph.edges if e["type"] == "extends"}
    assert extends == {"OutOfStockError": "Exception", "PaymentError": "Exception"}


def test_python_calls_attributed_to_innermost_function(graphs):
    graph = graphs["ml-shop-sample"]
    main = node_named(graph, "main", "function")
    callees = {graph.by_id.get(e["to"], {}).get("name", e["to"])
               for e in graph.callees(main["id"])}
    assert {"train_model", "Evaluator", "report"} & callees
    assert "update" not in callees  # that happens inside train_epoch


# ---------- JavaScript / React ----------

def test_react_hoc_wrapped_functions_become_nodes(graphs):
    """
    `const reload = useCallback(async () => {...})` produced no node before, so
    calls to it looked like external library calls.
    """
    graph = graphs["react-shop-sample"]
    reload = node_named(graph, "reload")
    assert reload["file"].endswith("useCart.js")
    assert (reload["line_start"], reload["line_end"]) == (13, 22)
    assert graph.by_id[reload["parent"]]["name"] == "useCart"


def test_react_destructured_hook_value_resolves(graphs):
    """`const { submit } = useCheckout()` then `onClick={() => submit(items)}`."""
    graph = graphs["react-shop-sample"]
    edge = edge_between(graph, "CheckoutButton", "submit")
    assert edge is not None and edge["resolution"] == "internal"
    assert graph.by_id[edge["to"]]["file"].endswith("useCheckout.js")


def test_react_same_helper_name_in_two_files_prefers_own_file(graphs):
    """`formatError` exists in useCart.js and useCheckout.js — no crossing over."""
    graph = graphs["react-shop-sample"]
    helpers = [n for n in graph.symbols if n["name"] == "formatError"]
    assert len(helpers) == 2
    for edge in graph.edges:
        if edge["type"] != "calls" or not edge["to"].endswith("formatError/1"):
            continue
        assert graph.by_id[edge["from"]]["file"] == graph.by_id[edge["to"]]["file"]


def test_react_state_setters_are_local_not_external(graphs):
    graph = graphs["react-shop-sample"]
    for setter in ("setItems", "setTotal", "setError", "setStatus"):
        edge = next((e for e in graph.edges
                     if e["type"] == "calls" and e["to"] == setter), None)
        assert edge is not None, setter
        assert edge["resolution"] == "local", setter
        assert edge["external"] is False, setter


def test_react_jsx_renders_chain(graphs):
    graph = graphs["react-shop-sample"]
    assert edge_between(graph, "App", "CartView", etype="renders")["resolution"] == "internal"
    assert edge_between(graph, "CartView", "CheckoutButton", etype="renders") is not None
    dom = [e for e in graph.edges if e["type"] == "renders"
           and str(e["to"]).lower() in ("div", "main", "section", "ul", "li",
                                        "p", "span", "button", "h1")]
    assert dom == [], "DOM elements must not be treated as components"


def test_react_classification(graphs):
    graph = graphs["react-shop-sample"]
    assert node_named(graph, "useCart")["type"] == "hook"
    assert node_named(graph, "CartView")["type"] == "component"
    assert node_named(graph, "fetchCart")["type"] == "function"


def test_js_module_constant_is_captured(graphs):
    """Parity with the Python parser, which already captured module constants."""
    graph = graphs["react-shop-sample"]
    const = node_named(graph, "BASE_URL", "constant")
    assert const["file"].endswith("shopApi.js")


def test_repeated_call_sites_are_deduped_with_a_count(graphs):
    """`useState` is called three times in useCart: one edge, count 3."""
    graph = graphs["react-shop-sample"]
    edges = [e for e in graph.edges
             if e["type"] == "calls" and e["to"] == "useState"
             and graph.by_id.get(e["from"], {}).get("name") == "useCart"]
    assert len(edges) == 1
    assert edges[0]["count"] == 3


# ---------- synthetic edge cases ----------

def test_overload_ids_stay_distinct(build_from_source):
    graph = build_from_source({"src/Dup.java": (
        "class A {\n"
        "    int calc(int x) { return x; }\n"
        "    int calc(int x, int y) { return x + y; }\n"
        "}\n"
        "class B {\n"
        "    int calc(int x) { return -x; }\n"
        "}\n"
    )})
    calcs = [n for n in graph.nodes if n["name"] == "calc"]
    assert len({n["id"] for n in calcs}) == 3
    assert {graph.qualified_name(n) for n in calcs} == {"A.calc", "B.calc"}


def test_inheritance_resolves_regardless_of_file_order(build_from_source):
    """Zebra.java is parsed first alphabetically, yet still finds Apple."""
    graph = build_from_source({
        "src/Zebra.java": "class Zebra extends Apple {}\n",
        "src/Apple.java": "class Apple {}\n",
    })
    edge = next(e for e in graph.edges if e["type"] == "extends")
    assert edge["resolution"] == "internal"
    assert graph.by_id[edge["to"]]["name"] == "Apple"


def test_python_async_and_docstrings(build_from_source):
    graph = build_from_source({"mod.py": (
        'CONST_LIMIT = 10\n'
        '\n'
        '@staticmethod\n'
        'async def fetch_all(url):\n'
        '    """Fetch everything."""\n'
        '    return url\n'
    )})
    fn = node_named(graph, "fetch_all")
    assert fn["is_async"] is True
    assert fn["doc"] == "Fetch everything."
    assert node_named(graph, "CONST_LIMIT", "constant")


def test_polyglot_target_keeps_every_language(build_from_source):
    graph = build_from_source({
        "Api.java": "class Api { void ping() {} }\n",
        "ui.jsx": "export default function Ui() { return null; }\n",
        "util.py": "def helper():\n    return 1\n",
    })
    assert graph.meta["languages"] == {"java": 1, "javascript": 1, "python": 1}
    langs = {n["language"] for n in graph.nodes if n["type"] != "file"}
    assert langs == {"java", "javascript", "python"}


def test_broken_file_is_recorded_not_swallowed(build_from_source):
    """A syntax error must be visible in the graph, not just on stderr."""
    graph = build_from_source({
        "ok.py": "def fine():\n    return 1\n",
        "broken.py": "def oops(:\n",
    })
    broken = next(n for n in graph.nodes if n["file"].endswith("broken.py"))
    assert broken.get("parse_error")
    assert node_named(graph, "fine")
