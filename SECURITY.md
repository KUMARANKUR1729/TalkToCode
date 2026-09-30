# Security Policy

## Supported versions
This repository currently has no published release train. Security fixes target the default branch until maintainers define supported release versions and end-of-life dates.

## Reporting a vulnerability
Do not open a public issue containing exploit details, source code, credentials, or tenant data. Use the repository host's private vulnerability-reporting feature or the owning organization's approved security contact. The owner must publish that contact and response targets before external production use. Include affected revision, impact, reproduction steps, and suggested mitigation; use synthetic data and revoke any credential accidentally disclosed.

## Current security posture
Graphify is a Python 3.13 Streamlit application for local/internal use. It parses local Java, C/C headers, Python, and JavaScript/JSX source into JSON knowledge graphs, reads source snippets, and can send selected context to configured NVIDIA chat/embedding endpoints. Streamlit is **not** an internet-facing identity, authorization, tenancy, or API boundary. The repository does not currently implement OIDC, tenant/repository authorization, managed secrets, object-lock/immutable storage, distributed queues/workers, centralized telemetry, or model-egress policy enforcement.

Before production exposure, place the app behind an OIDC-aware reverse proxy or a separately authenticated API boundary; enforce tenant and repository authorization on every operation; use a managed secret store and short-lived workload identity; store source-derived artifacts in encrypted, versioned immutable artifact/object storage; move parsing/model work to bounded queues and isolated workers; emit centralized audit, metric, log, and trace telemetry; and govern external-model destinations, data classification, redaction, retention, and deny-by-default egress. These are required controls, not claims about this codebase.

## Operator expectations
- Never commit `.env`, API keys, repository credentials, source archives, or generated artifacts containing sensitive code. Build contexts exclude `.env*`; CI deliberately uses no secrets and runs the offline smoke test.
- Pin deployments to reviewed source and immutable image digests. Scan dependencies, images, and provenance using tools selected by the organization; `requirements.txt` pins versions but does not provide hashes or a software bill of materials.
- Treat repositories, questions, parser output, graph JSON, snippets, and model responses as untrusted and potentially confidential. Do not use production code or credentials in security reports.
- Restrict filesystem mounts, outbound network access, CPU, memory, execution time, and artifact size. Run the supplied container as its non-root user and do not grant a writable container filesystem except explicit scratch/output mounts.
- Keep semantic retrieval off when external-model egress is not approved. Offline retrieval does not make a generally exposed Streamlit process safe.

## Response process
Triage privately, preserve relevant audit evidence, contain access and model egress, rotate exposed credentials, identify affected repositories/tenants/artifacts, patch and test, publish an advisory when appropriate, and document lessons learned. Notification deadlines, severity taxonomy, on-call ownership, legal/privacy escalation, and disclosure timelines remain organizational decisions.
