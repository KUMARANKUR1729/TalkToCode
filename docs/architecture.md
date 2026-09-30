# Architecture and Enterprise Delivery Baseline

## Scope and current system
Graphify is a Python 3.13 application with a Streamlit UI (`app.py`). `run_graphify.py` walks local repositories and dispatches `.java`, `.c`, `.h`, `.py`, `.js`, `.jsx`, and `.mjs` files to tree-sitter parsers (Python uses the standard-library AST). It writes `outputs/<target>/knowledge_graph.json`; runtime retrieval loads that graph, verifies source hashes for staleness, slices grounded snippets from `codebase/`, applies exact/BM25/optional semantic retrieval, calls NVIDIA chat/embedding endpoints when configured, verifies citations, and renders the answer and Graphviz DOT in Streamlit. Tests and `smoke_test.py --offline` need no model credential or network.

The current UI is suitable only for a trusted local/internal environment. It has local filesystem coupling, process-local Streamlit sessions, synchronous parsing/model calls, and environment-variable secrets. It does not provide an enterprise identity, tenant, repository, policy, audit, queue, or durable-storage boundary.

## Current components and data flow
1. A user selects a local target and asks a question in Streamlit.
2. The app loads committed/local graph JSON and checks source hashes; an operator can synchronously rebuild a graph from local source.
3. Retrieval selects symbols and reads exact source line ranges. With semantic mode `off`, this remains local.
4. In `auto`, `embeddings`, or `llm` mode, selected inventories/snippets/questions can cross the model-provider boundary over HTTPS; the generated answer returns to the UI. The current provider endpoints are configured in `graphify/config.py`.
5. JSON graphs and optional semantic caches are source-derived artifacts and must inherit repository confidentiality.

## Trust boundaries
- **Browser to Streamlit:** currently trusted internal traffic with no application authentication; production must terminate TLS and authenticate with OIDC before any request reaches an application/API boundary.
- **Identity/policy boundary to tenant data:** not implemented; production must map identity and groups to tenant plus repository permissions and enforce them server-side on reads, rebuilds, jobs, artifacts, and model calls.
- **Control/API tier to workers:** not implemented; production should enqueue bounded, idempotent jobs and issue workers narrowly scoped workload identities.
- **Workers to untrusted repository content:** parser inputs may be malicious; workers require isolation, resource/time/size limits, no code execution, read-only source, and constrained scratch space.
- **Application/workers to artifact storage:** currently local directories; production requires encrypted, versioned immutable object/artifact storage with tenant-scoped access and retention/deletion policy.
- **Application/workers to external models:** currently direct NVIDIA HTTPS egress; production requires approved destinations/models, classification/redaction policy, tenant opt-in, quotas, logging, retention contracts, and deny-by-default network egress.
- **Runtime to secrets/telemetry:** currently environment variables/local output; production requires managed secrets or workload identity and centralized, access-controlled telemetry with redaction.

## Target production shape (required, not implemented)
Use an OIDC-aware reverse proxy/API gateway in front of a stateless control/API tier; keep Streamlit as an internal UI rather than the security boundary. The control tier authorizes tenant/repository actions, creates jobs, and returns job/artifact references. A durable queue feeds isolated parsing/retrieval/model workers. Workers read immutable repository snapshots and write content-addressed/versioned graphs to object storage. A policy-controlled egress gateway brokers external model calls. Central metrics, logs, traces, and audit events correlate identity, tenant, repository, job, artifact version, model, and policy decision without recording source or secrets by default.

## Delivery baseline
CI grants only read access, uses no secrets, installs the pinned `requirements.txt`, compiles Python, and runs pytest plus the offline smoke test on Windows and Linux. The multi-stage Python 3.13 image runs Streamlit as UID/GID 10001 and probes `/_stcore/health`. Version pins are not dependency hashes; image digest pinning, action SHA pinning, SBOM/provenance, signing, vulnerability scanning, registry policy, promotion, and admission verification still require organizational tooling.

## Provider decisions still required
Select the OIDC issuer and claims contract; reverse proxy/API gateway; policy engine and authorization data model; source ingress and repository credential broker; queue and worker isolation platform; object/artifact store, encryption keys, immutability and regional residency; managed secret/workload-identity service; model providers and egress gateway; telemetry/audit platform and retention; container registry/signing/provenance controls; backup service and recovery region; WAF/rate limiting; network segmentation/DNS/TLS; and privacy, legal, incident, SLO, RTO/RPO, retention, deletion, and cost-accountability owners.
