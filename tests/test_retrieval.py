"""
Retrieval and verification tests — no network access required.

The behaviours locked down here are the ones that make the system trustworthy:
the not-found guarantee, class-scoped disambiguation, and the verifier catching
renamed symbols and fabricated citations.
"""
from graphify.lexical import Bm25Index, split_identifier, tokenize
from graphify.retrieval import gather_context, retrieve
from graphify.verification import verify


def names_of(result):
    return {m.node["name"] for m in result.matches}


def qualified_of(result, graph):
    return {graph.qualified_name(m.node) for m in result.matches}


# ---------- tokenisation ----------

def test_identifier_splitting():
    assert set(split_identifier("findByEmail")) >= {"find", "by", "email"}
    assert set(split_identifier("order_repo_status")) >= {"order", "repo", "status"}
    assert set(split_identifier("MAX_ITEMS")) >= {"max", "items"}


def test_tokenize_drops_question_scaffolding():
    tokens = tokenize("What does checkout_process do?")
    assert "checkout_process" in tokens and "checkout" in tokens
    assert "what" not in tokens and "does" not in tokens


def test_prefix_expansion_bridges_morphology():
    index = Bm25Index([tokenize("inventory_reserve reserve stock for an item")])
    expanded = index.expand(tokenize("reservation"))
    assert "reserve" in expanded


# ---------- layer 1: qualified matching ----------

def test_qualified_match_is_exact(graphs):
    graph = graphs["java-shop-sample"]
    result = retrieve(graph, "What does CheckoutService.checkout do?", semantic_mode="off")
    assert result.strategy == "qualified"
    assert result.confidence == "HIGH"
    assert qualified_of(result, graph) == {"CheckoutService.checkout"}


def test_missing_member_of_real_class_is_refused(graphs):
    """The not-found guarantee: do not answer using some other `refund`."""
    graph = graphs["java-shop-sample"]
    result = retrieve(graph, "What does CheckoutService.refund do?", semantic_mode="off")
    assert not result.matches
    assert result.confidence == "NONE"
    assert "CheckoutService" in result.reason and "refund" in result.reason
    assert result.suggestions


def test_method_of_the_wrong_class_is_refused(graphs):
    """`charge` is real and `OrderRepository` is real, but not together."""
    graph = graphs["java-shop-sample"]
    result = retrieve(graph, "What does OrderRepository.charge do?", semantic_mode="off")
    assert not result.matches
    assert "not as a member of" in result.reason


def test_same_method_name_in_two_classes_is_disambiguated(graphs):
    """`status` exists on OrderRepository and PaymentGateway."""
    graph = graphs["java-shop-sample"]
    result = retrieve(graph, "What does PaymentGateway.status do?", semantic_mode="off")
    assert len(result.matches) == 1
    node = result.matches[0].node
    assert graph.qualified_name(node) == "PaymentGateway.status"
    assert node["file"].endswith("PaymentGateway.java")

    other = retrieve(graph, "What does OrderRepository.status do?", semantic_mode="off")
    assert other.matches[0].node["file"].endswith("OrderRepository.java")


def test_cross_language_same_names_stay_in_their_own_graph(graphs):
    """java-shop and python-shop both define CheckoutService.checkout."""
    for target, suffix in (("java-shop-sample", ".java"), ("python-shop-sample", ".py")):
        graph = graphs[target]
        result = retrieve(graph, "What does CheckoutService.checkout do?",
                          semantic_mode="off")
        assert result.matches[0].node["file"].endswith(suffix)


# ---------- layer 2: exact ----------

def test_exact_name_match_prefers_definition_over_prototype(graphs):
    graph = graphs["c-shop-sample"]
    result = retrieve(graph, "What does checkout_process do?", semantic_mode="off")
    assert result.strategy == "exact"
    assert len(result.matches) == 1
    node = result.matches[0].node
    assert node["type"] == "function"
    assert node["file"].endswith("checkout.c")


def test_macro_question_matches_the_macro(graphs):
    result = retrieve(graphs["c-shop-sample"], "What does the MAX_ITEMS macro do?",
                      semantic_mode="off")
    assert result.matches[0].node["type"] == "macro"
    assert result.matches[0].node["name"] == "MAX_ITEMS"


def test_hook_question_matches_the_hook(graphs):
    result = retrieve(graphs["react-shop-sample"], "What does the useCart hook do?",
                      semantic_mode="off")
    assert result.matches[0].node["name"] == "useCart"
    assert result.matches[0].node["type"] == "hook"


# ---------- layer 3: lexical, offline ----------

def test_description_without_a_symbol_name_still_matches(graphs):
    """
    "which part reserves stock" names no symbol. Identifier-aware BM25 with
    prefix expansion reaches inventory_reserve.
    """
    result = retrieve(graphs["c-shop-sample"], "which part reserves stock for an item?",
                      semantic_mode="off")
    assert result.strategy == "lexical"
    assert names_of(result) & {"inventory_reserve", "stock_find", "inventory_restock"}


def test_payment_question_finds_the_charge_path(graphs):
    result = retrieve(graphs["java-shop-sample"],
                      "where is the customer actually charged money?",
                      semantic_mode="off")
    assert "charge" in names_of(result)


def test_lexical_confidence_is_never_high(graphs):
    result = retrieve(graphs["java-shop-sample"],
                      "where is the customer actually charged money?",
                      semantic_mode="off")
    assert result.confidence in ("MEDIUM", "LOW")


def test_offline_mode_never_calls_the_network(graphs, monkeypatch):
    import graphify.llm as llm

    def explode(*args, **kwargs):
        raise AssertionError("network call attempted in offline mode")

    monkeypatch.setattr(llm.requests, "post", explode)
    result = retrieve(graphs["ml-shop-sample"], "totally unrelated gibberish zzzz",
                      semantic_mode="off")
    assert result.confidence in ("NONE", "LOW", "MEDIUM")


# ---------- the not-found guarantee beyond Class.member ----------

def test_nonexistent_hook_is_refused_not_fuzzy_matched(graphs):
    """
    Regression: the lexical layer made `useWishlist` match `useCart` and produce
    a confident answer about the wrong hook.
    """
    result = retrieve(graphs["react-shop-sample"], "What does the useWishlist hook do?",
                      semantic_mode="off")
    assert not result.matches
    assert result.strategy == "named-symbol-miss"
    assert "useWishlist" in result.reason
    assert any("useC" in s for s in result.suggestions)


def test_nonexistent_snake_case_function_is_refused(graphs):
    result = retrieve(graphs["c-shop-sample"], "What does order_repo_delete do?",
                      semantic_mode="off")
    assert not result.matches
    assert result.strategy == "named-symbol-miss"
    assert "order_repo_delete" in result.reason
    assert any("order_repo" in s for s in result.suggestions)


def test_shared_prefix_naming_a_family_is_not_a_miss(graphs):
    """
    "the CHECKOUT_ERR macros" refers to CHECKOUT_ERR_NOT_FOUND and friends by
    their common prefix. That is a real reference, not a nonexistent symbol.
    """
    result = retrieve(graphs["c-shop-sample"], "What are the CHECKOUT_ERR macros for?",
                      semantic_mode="off")
    assert result.strategy != "named-symbol-miss"
    assert {"CHECKOUT_ERR_NOT_FOUND", "CHECKOUT_ERR_STOCK",
            "CHECKOUT_ERR_PAYMENT"} <= names_of(result)


def test_prefix_leniency_does_not_weaken_the_guarantee(graphs):
    """A genuine miss is a prefix of nothing, so it is still refused."""
    for target, bogus in (("react-shop-sample", "useWishlist"),
                          ("c-shop-sample", "order_repo_delete")):
        result = retrieve(graphs[target], f"What does {bogus} do?", semantic_mode="off")
        assert result.strategy == "named-symbol-miss", bogus


def test_plain_english_is_not_treated_as_a_missing_symbol(graphs):
    """"inventory" is a word, not an identifier — it must reach BM25."""
    result = retrieve(graphs["c-shop-sample"], "how is inventory tracked?",
                      semantic_mode="off")
    assert result.strategy == "lexical"
    assert result.matches


def test_filename_is_not_read_as_member_access(graphs):
    """`CheckoutService.java` must not parse as class member `java`."""
    graph = graphs["java-shop-sample"]
    result = retrieve(graph, "explain CheckoutService.java", semantic_mode="off")
    assert result.strategy != "qualified-miss"
    assert "CheckoutService" in names_of(result)


# ---------- type hints from role words ----------

def test_role_word_selects_the_right_kind_of_symbol(graphs):
    """
    "which component ..." must not match the `submit` function just because the
    word appears in the question.
    """
    result = retrieve(graphs["react-shop-sample"],
                      "which component renders the cart contents?",
                      semantic_mode="off")
    assert result.matches
    assert result.matches[0].node["type"] == "component"
    assert result.matches[0].node["name"] in ("CartView", "App", "CheckoutButton")


def test_class_role_word_narrows_to_classes(graphs):
    result = retrieve(graphs["python-shop-sample"], "which class stores orders?",
                      semantic_mode="off")
    assert result.matches
    assert result.matches[0].node["type"] == "class"


def test_macro_role_word_keeps_the_macro(graphs):
    result = retrieve(graphs["c-shop-sample"], "What does the MAX_ORDERS macro do?",
                      semantic_mode="off")
    assert result.matches[0].node["type"] == "macro"


def test_bare_name_question_still_matches_exactly(graphs):
    """Guard against over-reach: a bare real name must still win outright."""
    result = retrieve(graphs["java-shop-sample"],
                      "What calls checkout and what does it call in turn?",
                      semantic_mode="off")
    assert result.strategy == "exact"
    assert result.confidence == "HIGH"
    assert names_of(result) == {"checkout"}


def test_prose_word_does_not_case_insensitively_hijack_a_class(graphs):
    """
    "an order" must not exact-match the class `Order` with HIGH confidence and
    stop the search before reaching what the question is really about.
    """
    graph = graphs["python-shop-sample"]
    result = retrieve(graph, "how do we look up an order by its id?", semantic_mode="off")
    assert result.confidence != "HIGH"
    assert "find_by_id" in names_of(result)


def test_case_mismatched_name_still_found_with_lower_confidence(graphs):
    graph = graphs["java-shop-sample"]
    result = retrieve(graph, "what does paymentgateway do?", semantic_mode="off")
    assert "PaymentGateway" in names_of(result)
    assert result.confidence in ("MEDIUM", "LOW")


# ---------- context assembly ----------

def test_context_includes_snippet_and_edges(graphs):
    graph = graphs["java-shop-sample"]
    result = retrieve(graph, "What does CheckoutService.checkout do?", semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    assert len(blocks) == 1
    block = blocks[0]
    assert block["qualified_name"] == "CheckoutService.checkout"
    assert "public String checkout" in block["snippet"]
    assert "Main.main" in {c["name"] for c in block["callers"]}
    callee_names = {c["name"] for c in block["callees"]}
    assert {"OrderRepository.findById", "InventoryService.reserve",
            "PaymentGateway.charge"} <= callee_names
    assert all(c["resolution"] in ("internal", "local", "external")
               for c in block["callees"])


def test_context_labels_local_bindings(graphs):
    """
    `reload` lives inside `useCart`. Its `setItems`/`setError` calls are locally
    bound values, not library functions, and the context must say so.
    """
    graph = graphs["react-shop-sample"]
    result = retrieve(graph, "What does useCart.reload do?", semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    assert blocks[0]["qualified_name"] == "useCart.reload"
    resolutions = {c["name"]: c["resolution"] for c in blocks[0]["callees"]}
    assert resolutions.get("setItems") == "local"
    assert resolutions.get("setError") == "local"
    assert resolutions.get("fetchCart") == "internal"


def test_hook_context_keeps_the_wrapper_call(graphs):
    graph = graphs["react-shop-sample"]
    result = retrieve(graph, "What does the useCart hook do?", semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    resolutions = {c["name"]: c["resolution"] for c in blocks[0]["callees"]}
    assert resolutions.get("useState") == "external"
    assert resolutions.get("useCallback") == "external"
    assert {c["name"] for c in blocks[0]["children"]} == {"reload"}


def test_context_includes_render_relationships(graphs):
    graph = graphs["react-shop-sample"]
    result = retrieve(graph, "What does CartView do?", semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    block = next(b for b in blocks if b["node"]["name"] == "CartView")
    assert "CheckoutButton" in {r["name"] for r in block["renders"]}
    assert "App" in {r["name"] for r in block["rendered_by"]}


# ---------- verification ----------

def _block(file="java-shop-sample/src/CheckoutService.java", name="checkout",
           qualified="CheckoutService.checkout", start=17, end=34):
    return {
        "node": {"id": f"{file}::{name}", "name": name, "type": "method",
                 "file": file, "line_start": start, "line_end": end},
        "qualified_name": qualified,
        "snippet": "...", "callers": [], "callees": [],
        "children": [], "renders": [], "rendered_by": [],
    }


def test_windows_style_citation_is_accepted():
    """
    Regression: the verifier normalised the answer's path but not the graph's,
    so every correct Windows-path citation was flagged as fabricated.
    """
    answer = ("ANSWER: `checkout` places an order.\n"
              "LOCATIONS:\n- java-shop-sample\\src\\CheckoutService.java#L17-34 (definition)")
    assert verify(answer, [_block()]) == []


def test_renamed_symbol_is_caught():
    answer = "ANSWER: The CheckoutService.placeOrder method does the work."
    findings = verify(answer, [_block(name="checkout_process",
                                     qualified="checkout_process")])
    assert any(f.kind == "renamed_symbol" for f in findings)


def test_fabricated_line_range_is_caught():
    answer = ("ANSWER: `checkout` works.\n"
              "LOCATIONS:\n- java-shop-sample/src/CheckoutService.java#L20-21 (definition)")
    findings = verify(answer, [_block()])
    assert any(f.kind == "fabricated_range" for f in findings)


def test_unknown_file_citation_is_caught():
    answer = ("ANSWER: `checkout` works.\nLOCATIONS:\n"
              "- java-shop-sample/src/Ghost.java#L1-5 (definition)")
    findings = verify(answer, [_block()])
    assert any(f.kind == "fabricated_range" for f in findings)


def test_invented_dotted_symbol_is_caught(graphs):
    """The classic drift: reporting a C function as a Java-style class method."""
    graph = graphs["c-shop-sample"]
    block = _block(file="c-shop-sample/src/checkout.c", name="checkout_process",
                   qualified="checkout_process", start=6, end=23)
    answer = ("ANSWER: `checkout_process` delegates to "
              "`CheckoutService.placeOrder` internally.")
    findings = verify(answer, [block], graph)
    assert any(f.kind == "invented_symbol" for f in findings)


def test_filename_in_answer_is_not_an_invented_symbol(graphs):
    """
    Regression: `order_repo.py` contains a dot and is not a symbol name, so the
    invented-symbol check flagged answers that correctly named their own file.
    """
    graph = graphs["python-shop-sample"]
    result = retrieve(graph, "What does OrderRepository.find_by_id do?",
                      semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    answer = ("ANSWER: `OrderRepository.find_by_id` looks an order up by id. "
              "It lives in `order_repo.py`.")
    assert verify(answer, blocks, graph) == []


def test_verifier_allows_focusing_on_one_of_several_candidates(graphs):
    """
    Retrieval hands over candidates, not requirements. An answer that discusses
    the relevant one and ignores the rest is correct, not a renamed symbol.
    """
    graph = graphs["c-shop-sample"]
    result = retrieve(graph, "which part reserves stock for an item?", semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    assert len(blocks) > 1
    answer = ("ANSWER: `inventory_reserve` reserves stock.\n"
              "FILE: c-shop-sample/src/inventory.c\nLINE: 34-41")
    assert verify(answer, blocks, graph) == []


def test_verifier_still_catches_using_none_of_the_real_names(graphs):
    graph = graphs["c-shop-sample"]
    result = retrieve(graph, "which part reserves stock for an item?", semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    answer = "ANSWER: The `StockManager.acquire` helper does this."
    findings = verify(answer, blocks, graph)
    assert any(f.kind == "renamed_symbol" for f in findings)


def test_clean_answer_passes_verification(graphs):
    graph = graphs["java-shop-sample"]
    result = retrieve(graph, "What does CheckoutService.checkout do?", semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    answer = ("ANSWER: `CheckoutService.checkout` loads the order with "
              "`OrderRepository.findById`, reserves stock through "
              "`InventoryService.reserve`, then charges via `PaymentGateway.charge`.\n"
              "FILE: java-shop-sample/src/CheckoutService.java\nLINE: 17-34")
    assert verify(answer, blocks, graph) == []
