# Operations Runbook and Production Readiness

## Local/internal baseline
Use Python 3.13. Install the exact versions in `requirements.txt`, run `python -m compileall -q app.py run_graphify.py smoke_test.py graphify parsers tests`, `python -m pytest`, and `python smoke_test.py --offline`. Build graphs with `python run_graphify.py --all`; start locally with `streamlit run app.py`. Offline smoke mode performs no model request, although `smoke_test.py` loads local dotenv configuration; CI provides no secrets. Never inspect, copy, bake, log, or commit `.env`.

Build the container with `docker build -t graphify:<revision> .`. Repository source
and derived outputs are deliberately excluded from the image and must be mounted
or supplied by the production artifact layer at runtime; never bake customer code
or `.env` into an image. Run the container only behind the required trusted internal
boundary. The image runs as UID/GID 10001 and its healthcheck calls Streamlit
`/_stcore/health`. That endpoint indicates process readiness only; it does not prove repository access, graph freshness, authorization, artifact storage, queue, model availability, or end-to-end correctness.

## Production prerequisites (not currently implemented)
Keep Streamlit local/internal and put an OIDC-aware reverse proxy or authenticated API boundary in front of production services. Enforce tenant/repository authorization at request, job, storage, and egress boundaries. Use managed secrets/workload identity, immutable versioned artifact/object storage, a durable bounded queue with isolated workers and dead-letter handling, centralized redacted logs/metrics/traces/audit, and governed external-model egress. Deploy images by immutable digest with signing/provenance and a read-only root filesystem; provide only bounded scratch/output mounts.

## Release, deploy, and rollback
Promote the same signed image digest through environments after compile, pytest, offline smoke, image scan, policy checks, and environment-specific integration/security tests. Record source revision, dependency lock/hash set, SBOM, provenance, image digest, schema/artifact compatibility, and approver. Use canary or blue/green rollout; verify authentication, authorization denials, queue depth, worker completion, artifact reads, and model-egress policy. Roll back traffic to the last approved digest; do not mutate or overwrite graph artifacts. Define compatibility and migration procedures before changing graph formats.

## Artifact lifecycle and backup/restore
Treat repository snapshots, `knowledge_graph.json`, semantic caches, questions, and answers according to source classification. Production should write content-addressed/versioned artifacts with encryption, immutability/retention, checksums, provenance links, tenant isolation, and lifecycle deletion; local `codebase/` and `outputs/` are not a production store.

Back up authorization/policy configuration, queue metadata needed for recovery, audit configuration/evidence, and object/artifact manifests according to approved RPO; repository snapshots may be recoverable from the source system only if that dependency is documented. Use encrypted cross-account/region copies where policy requires. At least quarterly, restore into an isolated environment, verify checksums/signatures and tenant ACLs, rebuild a sample graph, run offline smoke, measure RTO/RPO, and record evidence. Never restore production secrets; reissue them. Required decisions: data inventory, backup owner, frequency, retention, legal hold, deletion propagation, recovery region, **RPO [TBD]**, and **RTO [TBD]**.

## Observability and SLO placeholders
Emit structured events keyed by request/job ID, tenant, repository, artifact version, parser/model/policy decision, latency, status, and retry count; redact source, prompts, answers, tokens, credentials, and sensitive filenames by default. Monitor proxy 4xx/5xx, authz denials, queue age/depth, worker saturation/failures, parse failures, stale graphs, artifact integrity failures, model latency/errors/token spend, egress denials, and backup/restore results.

Service targets require owner approval: availability **[TBD % / window]**; authorized API/UI latency **[TBD p95/p99]**; graph-job completion **[TBD p95 by repository-size class]**; successful jobs **[TBD %]**; artifact durability **[TBD]**; model-dependent answer availability **[TBD and separate from retrieval]**; security-event acknowledgement **[TBD]**; error-budget policy **[TBD]**. Define synthetic probes beyond the Streamlit health endpoint and alert routing/escalation before launch.

## Incident response
1. Detect and classify; open an incident channel/timeline and assign commander, operations, communications, security/privacy, and scribe roles.
2. Contain: disable affected OIDC clients/routes/tenants, pause queues/workers, deny model egress, quarantine artifacts, and revoke/rotate credentials without destroying evidence.
3. Scope using protected audit events: identities, tenants, repositories, jobs, artifact versions, model destinations, time range, and backups. Avoid placing source or secrets in tickets/chat.
4. Eradicate and recover with a reviewed image/artifact, restore or rebuild from verified snapshots, validate authorization and offline smoke, then resume gradually while monitoring.
5. Notify legal/privacy/customers/providers under approved timelines; publish an advisory when appropriate. Complete a blameless review, track corrective actions, and update threats/runbooks/tests.

## Provider and ownership decisions still required
Choose and assign owners for OIDC/claims, proxy/API/WAF/rate limits, authorization policy, repository ingress, queue/workers/sandboxing, object storage/KMS/immutability, secret/workload identity, registry/signing/SBOM/provenance, model provider/region/retention/egress proxy, telemetry/SIEM/audit retention, networking/TLS/DNS, backups/DR, on-call/status communications, privacy/legal residency and deletion, cost quotas, and all SLO/RTO/RPO values.
