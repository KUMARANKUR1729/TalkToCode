"""
Graphify Talk-to-Code — UI only.

All retrieval, prompting, verification and rendering logic lives in the
`graphify` package so it can be tested without Streamlit. This file just wires
it to widgets.

Run:
    streamlit run app.py
"""
import json
from datetime import datetime, timezone

import streamlit as st
from dotenv import load_dotenv

from graphify import config, prompting, verification, viz
from graphify.graph import list_all, load_graph, staleness_report
from graphify.llm import LLMError, chat_stream, chat_with_fallback
from graphify.retrieval import gather_context, retrieve

load_dotenv()

CONFIDENCE_BADGE = {
    "HIGH": ("🟢", "exact symbol match from the graph"),
    "MEDIUM": ("🟡", "lexical match — verify the symbol is the one you meant"),
    "LOW": ("🟠", "semantic guess — check the cited code before trusting it"),
    "NONE": ("⚪", "nothing matched"),
}

STRATEGY_HELP = {
    "qualified": "matched an exact Class.member pair",
    "exact": "a word in your question is a symbol name",
    "lexical": "BM25 over identifier subwords, signatures and doc comments",
    "semantic": "embeddings / model-assisted symbol selection",
    "qualified-miss": "the class exists but the member does not — answering was refused",
    "none": "no layer matched",
}

st.set_page_config(page_title="Talk to Code", layout="wide")


# ---------- sidebar ----------

def rebuild_graph(target: str):
    from run_graphify import build_graph

    codebase_path = config.CODEBASE_DIR / target
    if not codebase_path.exists():
        st.sidebar.error(f"No source folder at {codebase_path}")
        return
    graph = build_graph(target, codebase_path.resolve())
    graph["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    out = config.OUTPUTS_DIR / target / "knowledge_graph.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    st.sidebar.success(f"Rebuilt: {graph['meta']['node_count']} nodes, "
                       f"{graph['meta']['edge_count']} edges")


targets, unbuilt = list_all()
if not targets:
    st.error("No codebases in codebase/ and no knowledge graphs in outputs/. "
             "Add a project under codebase/ or run `python run_graphify.py --all` first.")
    st.stop()

with st.sidebar:
    st.header("Settings")
    target = st.selectbox(
        "Codebase", targets,
        format_func=lambda t: f"{t} (not built)" if t in unbuilt else t,
    )

    model = st.selectbox(
        "Model", config.KNOWN_MODELS,
        index=(config.KNOWN_MODELS.index(config.DEFAULT_MODEL)
               if config.DEFAULT_MODEL in config.KNOWN_MODELS else 0),
        help="If the chosen model is overloaded or retired, the next one in the "
             "list is used automatically.",
    )
    st.caption(config.MODEL_NOTES.get(model, ""))
    semantic_mode = st.selectbox(
        "Semantic retrieval", ["auto", "embeddings", "llm", "off"],
        index=["auto", "embeddings", "llm", "off"].index(
            config.SEMANTIC_MODE if config.SEMANTIC_MODE in
            ("auto", "embeddings", "llm", "off") else "auto"),
        help="Used only when name and lexical matching find nothing. "
             "'off' keeps retrieval fully offline.",
    )
    show_internals = st.toggle("Show retrieval details", value=True)

    st.divider()
    if st.button("Rebuild this graph", use_container_width=True):
        rebuild_graph(target)
        st.cache_data.clear()

api_key = config.api_key()

if target in unbuilt:
    st.info(f"**{target}** has no knowledge graph yet. Build it to start asking questions.")
    if st.button("Build graph", type="primary"):
        rebuild_graph(target)
        st.cache_data.clear()
        st.rerun()
    st.stop()

graph = load_graph(target)

st.title("Talk to Code")
langs = ", ".join(f"{k} ({v} files)" for k, v in graph.languages.items())
st.caption(f"**{target}** — {len(graph.symbols)} symbols, {len(graph.edges)} edges — {langs}")

# ---------- staleness: the graph's line numbers must still match disk ----------

failures = graph.meta.get("parse_failures") or []
if failures:
    st.error(
        f"**{len(failures)} file(s) failed to parse when this graph was built.** "
        "Their symbols and edges are missing, so answers may be incomplete.\n\n"
        + "".join(f"\n- `{f['file']}` — {f['error']}" for f in failures)
    )

stale = staleness_report(graph)
if stale.is_stale:
    st.error(
        f"**Graph is stale — {stale.summary()}.** Line numbers in the graph no longer "
        "match the files on disk, so snippets shown to the model may be the wrong "
        "lines. Rebuild before trusting any answer.\n\n"
        + "".join(f"\n- changed: `{f}`" for f in stale.changed)
        + "".join(f"\n- missing: `{f}`" for f in stale.missing)
    )
elif stale.unverifiable:
    st.warning(f"{len(stale.unverifiable)} file(s) have no stored hash "
               "(graph built by an older version) — staleness can't be verified.")

if not api_key:
    api_key = st.text_input("NVIDIA API key (put NVIDIA_API_KEY in .env to skip this)",
                            type="password")
    if not api_key:
        st.info("An API key is needed to generate answers. Retrieval details still work.")

# ---------- chat state ----------

if st.session_state.get("chat_target") != target:
    st.session_state.chat_target = target
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


def render_details(result, blocks):
    icon, note = CONFIDENCE_BADGE[result.confidence]
    st.caption(f"{icon} **{result.confidence}** confidence — {note} · "
               f"strategy `{result.strategy}` ({STRATEGY_HELP.get(result.strategy, '')})")
    if not show_internals:
        return
    with st.expander(f"Retrieval details — {len(blocks)} node(s)", expanded=False):
        st.write("**Layers attempted:**", ", ".join(result.debug.get("attempted", [])))
        if result.debug.get("query_tokens"):
            st.write("**Query tokens:**", ", ".join(result.debug["query_tokens"]))
        if result.debug.get("best_score") is not None:
            st.write("**Top BM25 score:**", round(result.debug["best_score"], 2))
        if result.debug.get("embedding_scores"):
            st.write("**Embedding similarities:**", result.debug["embedding_scores"])
        for block in blocks:
            node = block["node"]
            st.markdown(f"**`{block['qualified_name']}`** · {node['type']} · "
                        f"`{node['file']}` L{node['line_start']}-{node['line_end']}")
            if node.get("signature"):
                st.code(node["signature"], language=node.get("language") or "text")
            st.code(block["snippet"], language=node.get("language") or "text")
    if blocks:
        dot = viz.neighbourhood_dot(graph, blocks)
        if dot:
            with st.expander("Graph neighbourhood", expanded=False):
                st.graphviz_chart(dot, use_container_width=True)
                st.caption("Solid blue = internal · dashed green = locally bound · "
                           "dotted grey = external/library")


question = st.chat_input(f"Ask about {target}...")
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Retrieving from the graph..."):
            result = retrieve(graph, question, api_key=api_key, model=model,
                              semantic_mode=semantic_mode)
            blocks = gather_context(graph, result.matches)

        render_details(result, blocks)

        if not blocks and result.suggestions:
            st.info("Closest symbols that do exist: "
                    + ", ".join(f"`{s}`" for s in result.suggestions))

        answer = ""
        if not api_key:
            answer = ("_No API key configured, so no explanation was generated._ "
                      + (f"Retrieval found: {', '.join(b['qualified_name'] for b in blocks)}"
                         if blocks else f"Retrieval found nothing. {result.reason or ''}"))
            st.markdown(answer)
        else:
            messages = prompting.build_messages(question, target, blocks, result)
            placeholder = st.empty()
            try:
                used_model = model
                raw = ""
                try:
                    buffer = []
                    for piece in chat_stream(api_key, model, messages):
                        buffer.append(piece)
                        placeholder.markdown("".join(buffer))
                    raw = "".join(buffer)
                except LLMError as stream_error:
                    placeholder.info(f"{model} unavailable, trying a fallback model...")
                    raw, used_model, _ = chat_with_fallback(api_key, model, messages)
                    st.caption(f"Streaming failed ({stream_error}); answered by "
                               f"`{used_model}`.")

                if not raw.strip():
                    raw, used_model, _ = chat_with_fallback(api_key, model, messages)

                # Reasoning models prepend their scratchpad; keep only the answer.
                answer = prompting.normalize_answer(raw)
                placeholder.markdown(answer)
                if used_model != model:
                    st.caption(f"Answered by `{used_model}` (fallback).")

                findings = verification.verify(answer, blocks, graph)
                if any(f.severity == "error" for f in findings):
                    with st.status("Verifier caught a grounding violation — retrying once",
                                   expanded=True):
                        st.markdown(verification.summarize(findings))
                    retry_messages = prompting.build_correction_messages(
                        question, target, blocks, answer, findings)
                    retry_raw, used_model, _ = chat_with_fallback(
                        api_key, used_model, retry_messages)
                    answer = prompting.normalize_answer(retry_raw)
                    placeholder.markdown(answer)
                    findings = verification.verify(answer, blocks, graph)

                if findings:
                    st.warning("Verifier findings — check the cited code before "
                               "trusting this answer:\n\n"
                               + verification.summarize(findings))
                elif blocks:
                    st.success("Verified: every symbol name and line range in the "
                               "answer matches the graph.")
            except LLMError as e:
                answer = f"**LLM error:** {e}"
                placeholder.error(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
