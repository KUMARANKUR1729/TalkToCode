# Threat Model

## Scope, assets, and assumptions
This model covers source ingestion/parsing, knowledge-graph artifacts, grounded retrieval, the Streamlit UI, and optional NVIDIA model calls. Assets include proprietary source/snippets, graph and embedding artifacts, user questions/model answers, tenant/repository authorization data, API/repository credentials, audit evidence, availability, and artifact integrity. Repository content, filenames, graph JSON, questions, model output, browsers, dependencies, and external services are untrusted. The current deployment is single-process/local and has no tenant or OIDC control; production controls below are requirements, not implemented features.

Actors include authorized developers/operators, a compromised account, a malicious repository contributor, an unauthenticated network attacker if the UI is exposed incorrectly, a compromised dependency/image/runner, an external model or telemetry provider, and an insider with infrastructure access.

## Trust boundaries
Requests cross browser→Streamlit today; the production target adds browser→OIDC proxy/API→authorized control tier. Data also crosses control tier→queue→isolated worker, worker→repository snapshot, worker/app→artifact storage, app/worker→model egress gateway→external provider, and all services→secret and telemetry systems. Tenant identity and repository scope must survive every asynchronous hop.

## Threats and required mitigations
| Threat | Impact | Current signal/control | Required production mitigation |
|---|---|---|---|
| Internet exposure or session spoofing | Unauthorized code access/actions | Streamlit UI only; no app auth | Keep UI internal; OIDC-aware proxy/API, TLS, secure sessions/CSRF policy, short timeouts, re-authentication for sensitive actions |
| IDOR/cross-tenant graph, source, job, or artifact access | Confidentiality breach | No tenancy implemented | Server-side tenant/repository authorization on every request and worker message; tenant-scoped object keys/credentials; negative authorization tests |
| Malicious source, path/symlink tricks, parser bugs, decompression bombs | Escape, arbitrary reads, worker DoS | Extension allowlist and no intentional code execution | Canonicalize paths, reject traversal/symlink escapes, cap files/bytes/depth/time, read-only snapshots, sandbox non-root workers, seccomp/MAC, patch parsers, fuzzing |
| Prompt injection embedded in source or question | Policy bypass/data disclosure | Grounded prompt and answer verification reduce hallucination, not injection | Treat source as data, fixed system policy, tool isolation, output validation, no model-controlled authorization/actions, red-team tests |
| Excessive or unauthorized model egress | Proprietary code/privacy leakage and cost | Offline mode exists; online mode directly calls configured NVIDIA endpoints | Classification/redaction, tenant consent, approved model/region list, contractual retention controls, egress proxy, deny-by-default firewall, quotas and audited policy decisions |
| API key leakage via `.env`, logs, image, UI, or errors | Provider compromise/cost | Docker context excludes `.env*`; CI has no secrets | Managed secret store/workload identity, secret scanning, masked structured logs, no browser-supplied persistent keys, rotation/revocation playbook |
| Graph/source mismatch or artifact tampering | Plausible incorrect answers | Per-file hashes and staleness warning; verification checks citations | Signed/content-addressed immutable snapshots and graphs, provenance linkage, fail closed on mismatch, authorization and audit for rebuild/promotion |
| Model output/Markdown abuse | Misleading or active browser content | Streamlit rendering and verifier; not a sanitizer guarantee | Encode/sanitize output, restrictive CSP at proxy, disable unsafe HTML, safe-link policy, display untrusted-output warning |
| Resource exhaustion and retry amplification | Queue/UI outage and provider spend | Request timeout/retry bounds exist | Rate limits, request/body limits, bounded queues, per-tenant quotas, concurrency/circuit breakers, cancellation, autoscaling and dead-letter handling |
| Dependency, CI action, base image, or artifact compromise | Build/runtime takeover | Pinned Python package versions; least-permission secretless CI | Hash/lock dependencies, pin actions/images by digest, SBOM/provenance/signing, isolated ephemeral runners, scanning, trusted registry and admission verification |
| Telemetry or backup leakage | Long-lived source disclosure | Central telemetry/backups not implemented | Default-redact content/tokens, tenant access controls, encryption, retention/deletion, immutable audit logs, restore authorization and access reviews |
| Repudiation and weak incident evidence | Delayed/incorrect response | Local logs only | Correlated append-only audit events for identity, tenant, repo, job, artifact, policy/model decision; synchronized clocks and protected retention |

## Abuse cases and security tests
Test anonymous and expired sessions, cross-tenant identifiers, unauthorized rebuild/download/model calls, path traversal and symlink fixtures, huge/deep repositories, malformed syntax trees/graph JSON, prompt injection in comments and questions, malicious Markdown/URLs, stale or swapped artifacts, queue replay/duplicate jobs, provider timeout/429/5xx behavior, secret/log redaction, egress allowlisting, and backup restoration into an isolated account. CI currently covers parser invariants and offline retrieval only; it is not evidence that these production controls exist.

## Residual risk and review triggers
Even grounded retrieval can disclose sensitive source or generate incorrect advice. Human review remains necessary for consequential use. Revisit this model when adding source upload/clone, tenancy, authentication, new parsers, worker execution, model/tool providers, persistent chat, sharing/export, or a public endpoint, and after material incidents or provider-contract changes.
