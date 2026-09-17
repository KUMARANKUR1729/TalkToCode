"""Prompt construction, answer normalisation and model fallback."""
import pytest

from graphify import config, prompting
from graphify.llm import LLMError, chat_with_fallback, model_chain
from graphify.retrieval import gather_context, retrieve


# ---------- answer normalisation ----------

def test_thinking_preamble_is_stripped():
    raw = ("Here's a thinking process:\n1. The user asks...\n2. Facts say...\n\n"
           "ANSWER: `checkout_process` places an order.\n"
           "FILE: c-shop-sample/src/checkout.c\nLINE: 6-23")
    cleaned = prompting.normalize_answer(raw)
    assert cleaned.startswith("ANSWER:")
    assert "thinking process" not in cleaned


def test_think_tags_are_stripped():
    raw = "<think>let me plan this out</think>\nANSWER: it returns a token."
    assert prompting.normalize_answer(raw) == "ANSWER: it returns a token."


def test_last_answer_marker_wins():
    """A scratchpad often quotes the word ANSWER while planning."""
    raw = ("Plan: I will start my reply with ANSWER: and then explain.\n\n"
           "ANSWER: the real answer.")
    assert prompting.normalize_answer(raw) == "ANSWER: the real answer."


def test_clean_answer_is_untouched():
    raw = "ANSWER: already clean.\nFILE: a/b.c\nLINE: 1-2"
    assert prompting.normalize_answer(raw) == raw


def test_empty_input_is_safe():
    assert prompting.normalize_answer("") == ""
    assert prompting.normalize_answer(None) == ""


# ---------- prompt content ----------

def test_prompt_carries_exact_names_ranges_and_resolutions(graphs):
    graph = graphs["react-shop-sample"]
    question = "What does useCart.reload do?"
    result = retrieve(graph, question, semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    prompt = prompting.build_prompt(question, "react-shop-sample", blocks)

    assert "`useCart.reload`" in prompt
    assert "react-shop-sample/src/useCart.js#L13-22" in prompt
    assert "setItems [local]" in prompt
    assert "fetchCart [internal]" in prompt
    # the misleading "always PASS" confidence field must be gone
    assert "CONFIDENCE" not in prompt


def test_not_found_prompt_forbids_guessing(graphs):
    graph = graphs["java-shop-sample"]
    question = "What does CheckoutService.refund do?"
    result = retrieve(graph, question, semantic_mode="off")
    messages = prompting.build_messages(question, "java-shop-sample", [], result)
    user = messages[-1]["content"]
    assert "Not found in the knowledge graph" in user
    assert "refund" in user
    assert "do not invent" in user.lower()


def test_repeated_call_count_is_shown(graphs):
    graph = graphs["react-shop-sample"]
    result = retrieve(graph, "What does the useCart hook do?", semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    prompt = prompting.build_prompt("What does the useCart hook do?",
                                    "react-shop-sample", blocks)
    assert "useState [external] x3" in prompt


def test_prompt_includes_render_relationships(graphs):
    graph = graphs["react-shop-sample"]
    result = retrieve(graph, "What does CartView do?", semantic_mode="off")
    blocks = gather_context(graph, result.matches)
    prompt = prompting.build_prompt("What does CartView do?", "react-shop-sample", blocks)
    assert "RENDERS (JSX): CheckoutButton" in prompt


# ---------- model fallback ----------

def test_model_chain_puts_preferred_first():
    chain = model_chain(config.KNOWN_MODELS[-1])
    assert chain[0] == config.KNOWN_MODELS[-1]
    assert set(chain) == set(config.KNOWN_MODELS)
    assert len(chain) == len(set(chain))


def test_fallback_moves_on_when_a_model_fails(monkeypatch):
    calls = []

    def fake_chat(api_key, model, messages, **kwargs):
        calls.append(model)
        if len(calls) < 3:
            raise LLMError("HTTP 503 overloaded")
        return "ANSWER: fine"

    monkeypatch.setattr("graphify.llm.chat", fake_chat)
    text, used, notes = chat_with_fallback("key", config.KNOWN_MODELS[0], [])
    assert text == "ANSWER: fine"
    assert used == calls[-1] != calls[0]
    assert len(notes) == 2


def test_fallback_raises_when_everything_fails(monkeypatch):
    def always_fail(api_key, model, messages, **kwargs):
        raise LLMError("HTTP 410 Gone")

    monkeypatch.setattr("graphify.llm.chat", always_fail)
    with pytest.raises(LLMError, match="Every known model failed"):
        chat_with_fallback("key", config.KNOWN_MODELS[0], [])
