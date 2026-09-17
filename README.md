# Graphify — talk to your code, grounded in a real parse

Real parsers build a knowledge graph. A Streamlit app answers questions about
the code using only that graph plus an NVIDIA-hosted LLM. The model never reads
the codebase — it only explains facts it is handed.

## Quick start

```bash
python -m venv venv
.\venv\Scripts\Activate          # Windows;  source venv/bin/activate elsewhere
pip install -r requirements.txt
copy .env.example .env           # then put your NVIDIA_API_KEY in it
python run_graphify.py --all     # build graphs for all 4 sample codebases
streamlit run app.py
```

Pick a codebase in the sidebar and ask something, or copy a question from
`questions_to_ask.txt`.

```bash
python -m pytest                     # 155 tests, no network needed
python smoke_test.py --offline       # run the real questions, retrieval only
python smoke_test.py                 # same, with the LLM and the verifier
python -m graphify.check_models      # which models your key can actually reach
```

## Adding your own codebase

```bash
# 1. drop the source in
codebase/my-project/...

# 2. build its graph
python run_graphify.py my-project codebase/my-project     # or --all

# 3. structural checks — every codebase under codebase/ is discovered
#    automatically, so this covers the new one with no test edits
python -m pytest tests/test_parsers.py

# 4. add questions for it to questions_to_ask.txt under [my-project],
#    tagging any that should be refused, then
python smoke_test.py --target my-project --offline   # retrieval sanity
python smoke_test.py --target my-project             # full chain

# 5. look at it
streamlit run app.py
```

Step 3 is the part worth knowing about: `tests/conftest.py` discovers targets
from the filesystem, so a new codebase is immediately checked for duplicate node
ids, line ranges that fall outside their file, non-POSIX paths, missing hashes,
parse failures, edges pointing at missing nodes, fake self-recursion,
determinism, and whether each declaration really sits at the line range claimed
for it. What you add by hand is only the codebase-specific expectations.

`smoke_test.py` reads `questions_to_ask.txt` as a spec: a question tagged
`(must refuse)` fails the run if it produces an answer, which is how the
not-found guarantee stays honest as retrieval changes.

## Layout

```
parsers/                 syntax-tree parsing -> knowledge graph
  common.py              GraphAccumulator: qualified ids, deferred reference
                         resolution, edge dedupe, per-file content hashes
  java_parser.py         tree-sitter; classes, methods, fields, enums, records,
                         and receiver-type call resolution
  c_parser.py            tree-sitter; definitions, header prototypes, structs,
                         enums, typedefs, globals, real macros
  python_parser.py       stdlib `ast`; classes, functions, async, decorators,
                         docstrings, module constants
  js_parser.py           tree-sitter; components, hooks, HOC unwrapping, JSX
                         `renders` edges, local-scope tracking
run_graphify.py          CLI: walk a codebase, dispatch per file, write JSON

graphify/                everything the app thinks with — no Streamlit imports
  config.py              env-var configuration
  graph.py               loading, indexing, staleness detection, snippets
  lexical.py             identifier-aware tokenisation + BM25
  semantic.py            embeddings and chat-model symbol selection
  retrieval.py           the layered retrieval pipeline
  prompting.py           prompt construction + answer normalisation
  verification.py        automated grounding checks on the answer
  viz.py                 Graphviz DOT of the retrieved neighbourhood
  llm.py                 NVIDIA client: retries, streaming, model fallback
  check_models.py        availability probe

app.py                   Streamlit UI only
smoke_test.py            end-to-end runner driven by questions_to_ask.txt
tests/                   parser, retrieval, prompting and verification tests
codebase/                sample projects, auto-discovered by the test suite
outputs/                 generated graphs — also the golden test snapshots
```

## Why real parsers instead of an LLM reading code

An earlier version had an LLM *read* each file and *guess* line numbers. It
matched the JSON schema but not the property that matters: determinism. Same
file in, same graph out, every time — no skimming, no fatigue partway through a
long file, no misplaced macro.

That claim is now enforced rather than asserted. `tests/test_parsers.py`
re-parses every sample twice and compares, checks every node's range against the
committed graph, and reads each node's own line range back off disk to confirm
its declaration is really there.

## How answering works

Retrieval runs in layers, cheapest first, and reports which one fired. The
confidence you see is computed from that — it is not something the model claims.

| Layer | What it does | Confidence |
|---|---|---|
| qualified | exact `Class.member` match | HIGH |
| named-symbol check | question spells out an identifier that does not exist → refuse | NONE |
| exact | a question word *is* a symbol name (case-sensitive, 3+ chars) | HIGH |
| lexical | BM25 over identifier subwords, signatures, doc comments and code | MEDIUM / LOW |
| semantic | embeddings, or the chat model picking from the symbol inventory | LOW |
| suggestions | nothing matched → offer the closest real names | NONE |

For each matched symbol the app sends the model its verbatim snippet (read fresh
from disk using the graph's line numbers), plus callers and callees from the
graph's edges — and nothing else.

Call targets are labelled `internal`, `local` or `external`, so the model is
told that `setUser` is a value bound in an enclosing scope rather than being
left to assume any unfamiliar name must be a library function.

Afterwards the answer is verified automatically: does it use the real names, are
its `FILE#LINE` citations ones we actually provided, does it mention any
`Class.method` that does not exist? A violation triggers one corrective retry
before anything is presented as trustworthy.

### The not-found guarantee

Asked about `AuthService.refreshSession`, `useSession` or `auth_refresh_token`,
the app refuses and lists the closest real symbols. Two rules produce this:

* a real class with an unreal member is never answered from a same-named member
  of a different class
* a question that spells out something clearly identifier-shaped
  (`camelCase`, `snake_case`) which does not exist is refused rather than
  fuzzy-matched onto a similar name

This matters because the fuzzy layers that make "which part handles
authentication?" work are exactly what would otherwise let `useSession` be
quietly answered using `useAuth`.

## Staleness

The graph stores line numbers and the app slices files with them, so code edited
after a build would feed the model the wrong lines while sounding just as
confident. Every file node carries a content hash; the app compares it against
disk on load and refuses to stay quiet about a mismatch. Rebuild from the
sidebar or with `python run_graphify.py --all`.

## Configuration

All via `.env` — see `.env.example`.

| Variable | Purpose |
|---|---|
| `NVIDIA_API_KEY` | required |
| `NVIDIA_MODEL` | chat model; falls back automatically if unavailable |
| `NVIDIA_EMBED_MODEL` | embedding model for semantic retrieval |
| `GRAPHIFY_SEMANTIC` | `auto` / `embeddings` / `llm` / `off` |
| `GRAPHIFY_TIMEOUT`, `GRAPHIFY_MAX_RETRIES`, `GRAPHIFY_MAX_NODES` | tuning |

`GRAPHIFY_SEMANTIC=off` keeps retrieval entirely offline; only the final
explanation needs the network.

### On the model choice

The default is `meta/llama-3.2-11b-vision-instruct`. Using a *vision* model for
a text task looks like a mistake, and it was worth checking — but measured on
this task the alternatives reachable on this catalog are reasoning models that
emit their scratchpad before the answer and run 5-25x slower, with frequent
503s. `nvidia/nemotron-3-super-120b-a12b` does produce richer answers (it names
the specific helpers a function calls) and is selectable in the sidebar; leaked
reasoning text is stripped by `prompting.normalize_answer` either way.

Models get retired: several defaults that once worked now return HTTP 410.
`python -m graphify.check_models` tells you what your key can reach, and the
client walks down the known-model list rather than dying on one outage.

## Known limitations

* **No data-flow analysis.** Receiver types are resolved from declarations
  (Java fields, params, locals) and React HOC wrappers are unwrapped, which
  covers the common cases. A value that changes type through a chain of calls
  still resolves by name.
* **Ambiguity is flagged, not solved.** When a bare name matches several
  symbols, resolution prefers the same file and the definition over a
  declaration, and marks the edge `ambiguous`. Truly correct resolution needs
  full type inference.
* **Semantic retrieval depends on a hosted model.** With embeddings and the
  chat selector both unavailable, retrieval degrades to lexical only — which is
  why lexical was built to be good rather than a placeholder.
* **Method-level granularity.** The graph has no statement-level nodes, so
  "which line throws when the password is wrong" is answered from the enclosing
  method's snippet rather than pinpointed.
* **Retrieval reads whole files to build its index.** Fine for these samples;
  a large repository would want the index persisted alongside the graph.
* **Instance attributes are not fields.** Python class-level declarations
  (including `@dataclass` fields) become `field` nodes, but `self.x = ...` inside
  `__init__` does not — that needs flow analysis, not a syntax walk.
