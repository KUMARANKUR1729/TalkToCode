"""
Graphify Talk-to-Code — UI only.

All retrieval, prompting, verification and rendering logic lives in the
`graphify` package so it can be tested without Streamlit. This file just wires
it to widgets.

Run:
    streamlit run app.py
"""
import html

import streamlit as st
from dotenv import load_dotenv

# Environment-backed policy must be loaded before graphify.config is imported.
load_dotenv()

from graphify import config, prompting, verification, viz  # noqa: E402
from graphify.artifacts import ArtifactError  # noqa: E402
from graphify.graph import list_all, load_graph, staleness_report  # noqa: E402
from graphify.llm import LLMError, chat_stream, chat_with_fallback  # noqa: E402
from graphify.retrieval import gather_context, retrieve  # noqa: E402
from run_graphify import GraphBuildError, write_graph  # noqa: E402

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

st.set_page_config(
    page_title="Graphify · Code Intelligence",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
:root {
  --g-bg: #07111f;
  --g-panel: rgba(13, 27, 46, 0.72);
  --g-border: rgba(148, 163, 184, 0.16);
  --g-text: #e8f0fb;
  --g-muted: #8fa4bd;
  --g-cyan: #35d4c7;
  --g-blue: #5b8cff;
  --g-violet: #9b7bff;
}
html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(circle at 78% 0%, rgba(91,140,255,.16), transparent 32rem),
    radial-gradient(circle at 15% 35%, rgba(53,212,199,.09), transparent 28rem),
    linear-gradient(145deg, #07111f 0%, #091726 48%, #07101c 100%);
  color: var(--g-text);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { max-width: 1240px; padding-top: 2rem; padding-bottom: 5rem; }
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, rgba(8,19,34,.98), rgba(10,25,42,.98));
  border-right: 1px solid var(--g-border);
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color: var(--g-muted); }
[data-testid="stSidebar"] hr { border-color: var(--g-border); }
.graphify-brand { display:flex; gap:.8rem; align-items:center; margin:.2rem 0 1.5rem; }
.graphify-logo {
  width:2.6rem; height:2.6rem; display:grid; place-items:center; border-radius:.85rem;
  color:#07111f; font-size:1.25rem; font-weight:900;
  background:linear-gradient(135deg,var(--g-cyan),#8ee8ff 52%,var(--g-violet));
  box-shadow:0 0 28px rgba(53,212,199,.25);
}
.graphify-brand strong { display:block; color:#f7fbff; font-size:1.12rem; letter-spacing:-.02em; }
.graphify-brand span { color:var(--g-muted); font-size:.76rem; }
.graphify-hero {
  position:relative; overflow:hidden; padding:2.2rem 2.35rem; margin:.2rem 0 1.2rem;
  border:1px solid var(--g-border); border-radius:1.5rem;
  background:linear-gradient(125deg,rgba(16,35,59,.9),rgba(12,28,48,.68));
  box-shadow:0 22px 70px rgba(0,0,0,.23), inset 0 1px rgba(255,255,255,.04);
}
.graphify-hero:after {
  content:""; position:absolute; width:20rem; height:20rem; right:-6rem; top:-9rem;
  border-radius:50%; background:radial-gradient(circle,rgba(91,140,255,.24),transparent 68%);
}
.graphify-kicker { color:var(--g-cyan); text-transform:uppercase; letter-spacing:.14em; font-size:.72rem; font-weight:800; }
.graphify-hero h1 { margin:.45rem 0 .45rem; color:#f7fbff; font-size:clamp(2rem,4vw,3.35rem); letter-spacing:-.055em; line-height:1.02; }
.graphify-hero p { margin:0; max-width:45rem; color:#a8bad0; font-size:1rem; line-height:1.65; }
.graphify-badge {
  display:inline-flex; align-items:center; gap:.42rem; margin-top:1.1rem; padding:.38rem .7rem;
  border:1px solid rgba(53,212,199,.24); border-radius:999px; color:#b8fff7;
  background:rgba(53,212,199,.08); font-size:.76rem; font-weight:700;
}
.graphify-dot { width:.48rem; height:.48rem; border-radius:50%; background:var(--g-cyan); box-shadow:0 0 12px var(--g-cyan); }
[data-testid="stMetric"] {
  padding:1rem 1.1rem; border:1px solid var(--g-border); border-radius:1rem;
  background:var(--g-panel); box-shadow:0 12px 32px rgba(0,0,0,.12);
}
[data-testid="stMetricLabel"] { color:var(--g-muted); }
[data-testid="stMetricValue"] { color:#f3f8ff; letter-spacing:-.04em; }
.stButton > button, [data-testid="stFormSubmitButton"] > button {
  border:1px solid rgba(91,140,255,.28); border-radius:.8rem; color:#dce8ff;
  background:linear-gradient(135deg,rgba(91,140,255,.17),rgba(155,123,255,.12));
  transition:transform .16s ease,border-color .16s ease,box-shadow .16s ease;
}
.stButton > button:hover { transform:translateY(-1px); border-color:rgba(53,212,199,.55); box-shadow:0 8px 24px rgba(24,80,120,.18); color:#fff; }
[data-testid="stChatMessage"] {
  border:1px solid var(--g-border); border-radius:1rem; padding:.7rem .85rem;
  background:rgba(13,27,46,.58); box-shadow:0 10px 28px rgba(0,0,0,.1);
}
[data-testid="stChatInput"] { border-radius:1rem; border-color:rgba(91,140,255,.32); background:rgba(10,24,42,.95); }
[data-testid="stExpander"] { border:1px solid var(--g-border); border-radius:1rem; background:rgba(12,27,46,.55); }
.graphify-section { color:#dfeaff; font-size:.86rem; font-weight:800; letter-spacing:.02em; margin:1.4rem 0 .65rem; }
.graphify-empty {
  text-align:center; padding:2rem 1rem 1rem; color:var(--g-muted);
}
.graphify-empty-icon {
  width:3.3rem; height:3.3rem; margin:0 auto .9rem; display:grid; place-items:center;
  border-radius:1rem; color:#c8fff9; font-size:1.35rem;
  background:linear-gradient(135deg,rgba(53,212,199,.16),rgba(91,140,255,.18));
  border:1px solid rgba(53,212,199,.2);
}
.graphify-confidence {
  display:flex; align-items:center; gap:.75rem; padding:.8rem 1rem; margin:.35rem 0 .75rem;
  border-radius:.85rem; border:1px solid var(--g-border); background:rgba(10,24,42,.7);
}
.graphify-confidence strong { color:#f4f8ff; }
.graphify-confidence span { color:var(--g-muted); font-size:.84rem; }
.graphify-footer { text-align:center; color:#60758d; font-size:.74rem; margin-top:3rem; }
pre { border:1px solid rgba(148,163,184,.12)!important; border-radius:.85rem!important; }
@media (max-width: 700px) {
  [data-testid="stMainBlockContainer"] { padding-left:1rem; padding-right:1rem; }
  .graphify-hero { padding:1.55rem; border-radius:1.15rem; }
}
</style>
""", unsafe_allow_html=True)

if config.IS_PRODUCTION and not config.TRUSTED_AUTH_BOUNDARY:
    st.error(
        "Production mode requires an authenticated upstream boundary. Configure "
        "OIDC/repository authorization, then set GRAPHIFY_TRUSTED_AUTH_BOUNDARY=true."
    )
    st.stop()


# ---------- sidebar ----------

def rebuild_graph(target: str) -> bool:
    if not config.ALLOW_UI_REBUILD:
        st.sidebar.error("UI graph rebuilds are disabled by policy. Use the ingestion CLI/service.")
        return False
    codebase_path = config.CODEBASE_DIR / target
    try:
        graph = write_graph(
            target,
            codebase_path,
            config.OUTPUTS_DIR / target / "knowledge_graph.json",
        )
    except (GraphBuildError, ArtifactError, OSError, ValueError) as exc:
        st.sidebar.error(f"Graph rebuild refused: {exc}")
        return False
    st.sidebar.success(
        f"Published {graph['meta']['artifact_id']}: {graph['meta']['node_count']} nodes, "
        f"{graph['meta']['edge_count']} edges"
    )
    return True


targets, unbuilt = list_all()
if not targets:
    st.error("No codebases in codebase/ and no knowledge graphs in outputs/. "
             "Add a project under codebase/ or run `python run_graphify.py --all` first.")
    st.stop()

with st.sidebar:
    st.markdown(
        '<div class="graphify-brand"><div class="graphify-logo">✦</div>'
        '<div><strong>Graphify</strong><span>Code Intelligence Studio</span></div></div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="graphify-section">WORKSPACE</div>', unsafe_allow_html=True)
    target = st.selectbox(
        "Repository", targets,
        format_func=lambda name: f"{name} · not built" if name in unbuilt else name,
        help="Select a parsed repository knowledge graph.",
    )

    st.markdown('<div class="graphify-section">INTELLIGENCE</div>', unsafe_allow_html=True)
    model = st.selectbox(
        "Explanation model", config.KNOWN_MODELS,
        index=(config.KNOWN_MODELS.index(config.DEFAULT_MODEL)
               if config.DEFAULT_MODEL in config.KNOWN_MODELS else 0),
        help="Fallback models are tried automatically when policy allows.",
        disabled=not config.ALLOW_EXTERNAL_LLM,
    )
    st.caption(config.MODEL_NOTES.get(model, ""))
    semantic_mode = st.selectbox(
        "Retrieval mode", ["auto", "embeddings", "llm", "off"],
        index=["auto", "embeddings", "llm", "off"].index(
            config.SEMANTIC_MODE if config.SEMANTIC_MODE in
            ("auto", "embeddings", "llm", "off") else "off"),
        help="Exact and lexical search always run first. Off keeps retrieval local.",
        disabled=not config.ALLOW_EXTERNAL_LLM,
    )
    show_internals = st.toggle(
        "Show source evidence", value=True,
        help="Display parser evidence, retrieval path and graph relationships.",
    )

    st.markdown('<div class="graphify-section">ACTIONS</div>', unsafe_allow_html=True)
    if st.button("↻  Rebuild knowledge graph", use_container_width=True,
                 disabled=not config.ALLOW_UI_REBUILD):
        if rebuild_graph(target):
            st.cache_data.clear()
            st.rerun()
    if st.button("⌫  Clear conversation", use_container_width=True):
        st.session_state.messages = []
        st.rerun()
    if not config.ALLOW_UI_REBUILD:
        st.caption("Rebuilds are disabled by workspace policy.")

    st.divider()
    policy_label = "Online explanations" if config.ALLOW_EXTERNAL_LLM else "Local retrieval only"
    st.caption(f"◉ {policy_label}  ·  {config.ENVIRONMENT.title()} environment")

api_key = config.api_key()

if target in unbuilt:
    st.info(f"**{target}** has no knowledge graph yet.")
    if config.ALLOW_UI_REBUILD:
        if st.button("Build graph", type="primary") and rebuild_graph(target):
            st.cache_data.clear()
            st.rerun()
    else:
        st.warning("Ask an authorized operator to publish this graph through ingestion.")
    st.stop()

try:
    graph = load_graph(target)
except (ArtifactError, OSError) as exc:
    st.error(f"Knowledge graph failed integrity validation: {exc}")
    st.stop()

langs = ", ".join(f"{name} ({count} files)" for name, count in graph.languages.items())
mode_label = "Private · local retrieval" if not config.ALLOW_EXTERNAL_LLM else "Approved model egress"
st.markdown(
    f'''<section class="graphify-hero">
      <div class="graphify-kicker">Parser-grounded intelligence</div>
      <h1>Understand <span style="color:#77e4d9">{html.escape(target)}</span></h1>
      <p>Explore symbols, trace relationships, and ask precise questions with every
      answer grounded in deterministic parser evidence.</p>
      <div class="graphify-badge"><i class="graphify-dot"></i>{mode_label}</div>
    </section>''',
    unsafe_allow_html=True,
)

metric_symbols, metric_edges, metric_files, metric_build = st.columns(4)
metric_symbols.metric("Symbols", f"{len(graph.symbols):,}")
metric_edges.metric("Relationships", f"{len(graph.edges):,}")
metric_files.metric("Source files", f"{sum(graph.languages.values()):,}")
metric_build.metric("Artifact", str(graph.meta.get("artifact_id", "unknown"))[:8])
st.caption(f"Languages: {langs}  ·  Graph status: validated and checksummed")

# ---------- staleness: the graph's line numbers must still match disk ----------

failures = graph.meta.get("parse_failures") or []
blocked = False
if failures:
    blocked = True
    st.error(
        f"**Graph blocked: {len(failures)} file(s) failed to parse.** "
        "Enterprise mode never answers from a partial graph.\n\n"
        + "".join(f"\n- `{item['file']}` — {item['error']}" for item in failures)
    )

stale = staleness_report(graph)
if stale.is_stale:
    blocked = True
    st.error(
        f"**Graph blocked as stale — {stale.summary()}.** Rebuild it before querying.\n\n"
        + "".join(f"\n- changed: `{name}`" for name in stale.changed)
        + "".join(f"\n- missing: `{name}`" for name in stale.missing)
    )
if stale.unverifiable:
    blocked = True
    st.error(
        f"**Graph blocked:** {len(stale.unverifiable)} file(s) have no hash, so integrity "
        "cannot be verified. Rebuild with the current parser."
    )
if blocked:
    st.stop()

if not config.ALLOW_EXTERNAL_LLM:
    api_key = ""
    semantic_mode = "off"
    st.info("External model egress is disabled by policy; retrieval remains fully local.")
elif not api_key and config.ALLOW_BROWSER_API_KEY:
    api_key = st.text_input("Temporary NVIDIA API key", type="password")
elif not api_key:
    st.info("No managed model credential is configured; retrieval remains available locally.")

# ---------- chat state ----------

if st.session_state.get("chat_target") != target:
    st.session_state.chat_target = target
    st.session_state.messages = []

starter_question = None
if not st.session_state.messages:
    st.markdown(
        '<div class="graphify-empty"><div class="graphify-empty-icon">⌘</div>'
        '<strong style="color:#eaf2ff;font-size:1.05rem">Ask your codebase, not a chatbot</strong>'
        '<div style="margin-top:.35rem">Start with a symbol below or write your own question.</div></div>',
        unsafe_allow_html=True,
    )
    qualified = []
    seen = set()
    preferred_types = {"method", "function", "hook", "component", "class"}
    for node in graph.symbols:
        if node.get("type") not in preferred_types:
            continue
        name = graph.qualified_name(node)
        if name not in seen:
            seen.add(name)
            qualified.append(name)
        if len(qualified) == 3:
            break
    if qualified:
        prompt_columns = st.columns(len(qualified))
        for index, (column, name) in enumerate(zip(prompt_columns, qualified)):
            with column:
                if st.button(f"✦  Explain {name}", key=f"starter-{target}-{index}",
                             use_container_width=True):
                    starter_question = f"What does {name} do?"

for msg in st.session_state.messages:
    avatar = "◈" if msg["role"] == "assistant" else "●"
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["content"])


def render_details(result, blocks):
    icon, note = CONFIDENCE_BADGE[result.confidence]
    strategy_note = STRATEGY_HELP.get(result.strategy, "retrieval completed")
    st.markdown(
        f'<div class="graphify-confidence"><div style="font-size:1.05rem">{icon}</div>'
        f'<div><strong>{html.escape(result.confidence.title())} confidence</strong><br>'
        f'<span>{html.escape(note)} · {html.escape(strategy_note)}</span></div></div>',
        unsafe_allow_html=True,
    )
    if not show_internals:
        return

    with st.expander(f"⌘  Source evidence · {len(blocks)} matched node(s)", expanded=False):
        evidence_tab, path_tab = st.tabs(["Evidence", "Retrieval path"])
        with evidence_tab:
            if not blocks:
                st.caption("No source node was selected.")
            for index, block in enumerate(blocks):
                node = block["node"]
                st.markdown(
                    f"**`{block['qualified_name']}`** &nbsp; · &nbsp; `{node['type']}` &nbsp; · &nbsp; "
                    f"`{node['file']}#L{node['line_start']}-{node['line_end']}`"
                )
                if node.get("signature"):
                    st.caption("Signature")
                    st.code(node["signature"], language=node.get("language") or "text")
                st.caption("Verified source range")
                st.code(block["snippet"], language=node.get("language") or "text")
                if index < len(blocks) - 1:
                    st.divider()
        with path_tab:
            attempted = result.debug.get("attempted", [])
            st.markdown("**Layers attempted**")
            st.write(" → ".join(attempted) if attempted else "No retrieval layers recorded")
            detail_a, detail_b = st.columns(2)
            detail_a.metric("Strategy", result.strategy)
            score = result.debug.get("best_score")
            detail_b.metric("Top BM25 score", f"{score:.2f}" if score is not None else "—")
            if result.debug.get("query_tokens"):
                st.markdown("**Query tokens**")
                st.code("  ".join(result.debug["query_tokens"]), language="text")
            if result.debug.get("embedding_scores"):
                st.markdown("**Embedding similarities**")
                st.write(result.debug["embedding_scores"])

    if blocks:
        dot = viz.neighbourhood_dot(graph, blocks)
        if dot:
            with st.expander("◇  Relationship map", expanded=False):
                st.graphviz_chart(dot, use_container_width=True)
                st.caption("Solid blue: internal · dashed green: local binding · "
                           "dotted grey: external/library")


st.markdown('<div class="graphify-section">CONVERSATION</div>', unsafe_allow_html=True)
typed_question = st.chat_input(
    f"Ask anything about {target}…", max_chars=config.MAX_QUESTION_CHARS
)
question = starter_question or typed_question
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user", avatar="●"):
        st.markdown(question)

    with st.chat_message("assistant", avatar="◈"):
        with st.spinner("Mapping your question to verified symbols…"):
            result = retrieve(graph, question, api_key=api_key, model=model,
                              semantic_mode=semantic_mode)
            blocks = gather_context(graph, result.matches)

        render_details(result, blocks)

        if not blocks and result.suggestions:
            st.info("Closest symbols that do exist: "
                    + ", ".join(f"`{s}`" for s in result.suggestions))

        answer = ""
        if not blocks:
            answer = f"ANSWER: Not found in the knowledge graph for {target}."
            if result.reason:
                answer += f"\n{result.reason}"
            st.markdown(answer)
        elif not api_key:
            answer = ("_External explanation is unavailable; local retrieval found: _"
                      + ", ".join(f"`{block['qualified_name']}`" for block in blocks))
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
                    st.caption(f"Streaming was unavailable; answered by `{used_model}`.")

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

                errors = [finding for finding in findings if finding.severity == "error"]
                if errors:
                    answer = "ANSWER: Withheld because grounding verification failed."
                    placeholder.error(answer)
                    st.error(verification.summarize(errors))
                elif findings:
                    st.warning("Verifier findings — check the cited code before trusting this answer:\n\n"
                               + verification.summarize(findings))
                elif blocks:
                    st.success("Verified: every symbol name and line range in the answer "
                               "matches the graph.")
            except LLMError as e:
                answer = f"**LLM error:** {e}"
                placeholder.error(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})

st.markdown(
    '<div class="graphify-footer">GRAPHIFY · deterministic parsing · grounded retrieval · verified answers</div>',
    unsafe_allow_html=True,
)
