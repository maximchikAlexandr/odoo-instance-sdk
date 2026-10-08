## 1. Configuration, Models, and Persistence Foundations

- [ ] 1.1 Add typed project fields for the paired managed filestore binding, ordered addon repositories, and non-secret Multica-user/GitLab mappings with backward-compatible manifest parsing, atomic serialization, validation, and focused tests.
- [ ] 1.2 Add owner-only canonical publication settings loading for domain, Caddy, Basic Auth hash, panel, and trusted-proxy values with permission/path/host validation, redacted representations, and tests.
- [ ] 1.3 Add frozen public/private models for Multica context, Git credentials, adoption ownership, module context availability, MR results, publication state/results, and monitor publication projections; update public exports without exposing secret fields.
- [ ] 1.4 Add catalog migrations and data access for externally owned adopted checkouts and project/environment publication rows, preserving legacy SDK-owned/unpublished defaults and migration equivalence.
- [ ] 1.5 Extend central redaction, public projection, fingerprint, and architecture inventories for Git helpers, GitLab authorization, publication settings, and Caddy steps with secret-canary tests.

## 2. Main Checkout Database and Runtime Binding

- [ ] 2.1 Extend common database preparation planning to capture the paired current project database/filestore binding and the restored filestore evidence without creating a second restore pipeline.
- [ ] 2.2 Publish database and safe contained filestore fields in one stale-checked atomic manifest replacement only after all refresh/restore postconditions; preserve the previous binding and retained-artifact evidence on every failure.
- [ ] 2.3 Extend refresh/restore typed results and Rich/JSON/TOON projections with nullable backup ID, target database, effective configuration, filestore, project, publication state, and stable sanitized failure reason.
- [ ] 2.4 Make project runtime planning require and consume the paired binding for generated Odoo configuration while preserving source config immutability and existing environment behavior.
- [ ] 2.5 Complete project `run --detach --wait-ready`, status, and stop result/identity coverage for readiness success, early exit, timeout, cleanup failure, PID reuse, owner mismatch, and stale bindings.
- [ ] 2.6 Add focused preparation/runtime/CLI integration tests proving download-only preservation, successful restore-to-run handoff, concurrent stale publication refusal, and no project/environment selector crossover.

## 3. Multica Checkout Adoption

- [ ] 3.1 Implement typed `EnvironmentResource.adopt_command()`/convenience operation that captures safe Git/project/Multica evidence and registers an externally owned checkout without Git, database, runtime, or filesystem mutation.
- [ ] 3.2 Add `odcli env adopt` with canonical project/environment provenance, bounded machine output, dry-run plan, stable conflicts, and SDK-first leaf inventory coverage.
- [ ] 3.3 Extend runtime, database, module, monitor, and publication resolution to consume adopted environments through the existing owner-neutral context.
- [ ] 3.4 Guard sync/removal/cleanup so an adopted checkout can be stopped, unpublished, and unregistered but never updated, cleaned, moved, reset, or deleted by OdCLI.
- [ ] 3.5 Add migration, retry, conflict, removal, and secret/path-containment tests that prove byte-for-byte external checkout ownership.

## 4. Multica Root-Creator and Credential Resolution

- [ ] 4.1 Add the optional `odcli-multica` integration package/extra pinned to a compatible public `multica-py` API and keep core/help imports dependency-neutral.
- [ ] 4.2 Resolve one checkout binding, workspace, issue, finite same-workspace parent chain, root issue, and human creator with typed failures for missing, ambiguous, cyclic, cross-workspace, or non-human lineage.
- [ ] 4.3 Resolve exact user/host/login/token-key mappings through the existing owner-only project dotenv with process precedence, reserved token-key validation, and no ambient/machine fallback.
- [ ] 4.4 Produce one private immutable credential context and safe public projection shared by adoption, Git, sync, and MR operations; add concurrent-user and echoed-secret tests.

## 5. Native Git and GitLab Merge Requests

- [ ] 5.1 Implement exact remote-host resolution and a packaged process-local non-interactive Git credential helper that changes neither remote URLs nor repository/global credential configuration.
- [ ] 5.2 Add `GitResource.passthrough_command()`/convenience operation and `odcli git -- <args>` raw transport with exact argv/cwd/stdout/stderr/signal/exit behavior and local-command credential bypass.
- [ ] 5.3 Extend existing `git sync` planning/execution to accept proven HTTPS GitLab origins with shared credentials while preserving clean/protected/upstream checks, rebase behavior, pre-push validation, exact force-with-lease, and stale-lease refusal.
- [ ] 5.4 Add the bounded GitLab transport and file-backed description snapshot/revalidation with typed authentication, authorization, validation, timeout, and protocol errors and no create retry.
- [ ] 5.5 Implement `publish_merge_request_command()`/convenience operation and `odcli gitlab mr publish` for exact project/source/target lookup, zero-create, one-update, many-error, issue-link idempotency, and exact assignee resolution.
- [ ] 5.6 Add native Git integration fixtures and GitLab fake-transport matrices covering SSH compatibility, HTTPS success/failure, two concurrent users, raw passthrough, sync conflicts, MR create/update/ambiguity, dry-run, and secret canaries.

## 6. Repository-Aware Module Context

- [ ] 6.1 Extend safe addon-root resolution across the selected checkout and ordered registered repositories while preserving containment, symlink rejection, first-root precedence, shadow identities, and deterministic de-duplication.
- [ ] 6.2 Add read-only repository Git fact capture and relate committed/staged/unstaged/untracked paths to resolved and shadowed modules without mutating Git.
- [ ] 6.3 Add read-only installed module/version collection for the selected ready database and keep filesystem and database versions as separate typed facts.
- [ ] 6.4 Implement one inspectable module context SDK/CLI operation with per-source availability and bounded reasons so unavailable data is not reported as empty or uninstalled.
- [ ] 6.5 Add multi-repository, duplicate-name, dependency-path, database-unavailable, Git-unavailable, unsafe-root, and no-mutation test matrices.

## 7. Caddy Publication and Lifecycle

- [ ] 7.1 Implement deterministic stable project/environment/panel host and route identifiers from persisted owner identities and validated publication settings.
- [ ] 7.2 Implement the publication lock, catalog snapshot, complete OdCLI-owned Caddy candidate generator, private temporary file, `caddy validate`/`reload` process plan, and last-known-good atomic publication/rollback.
- [ ] 7.3 Generate exact HTTPS, hashed Basic Auth, Host, proxy-header, Odoo page/assets/redirect/upload/download, and Odoo 19 bus/WebSocket routes plus the protected panel/API route.
- [ ] 7.4 Add public `publish_command()`/`unpublish_command()` operations and mutually exclusive project/environment CLI selectors with runtime identity/readiness probes, idempotent route reconciliation, dry-run, and typed results.
- [ ] 7.5 Integrate stop/restart publication availability and pre-removal environment route cleanup with retryable `cleanup_failed` behavior and external-checkout preservation.
- [ ] 7.6 Add concurrent publication, validation/reload failure, previous-route preservation, unknown Host/Auth, stopped/stale runtime, idempotent unpublish, removal retry, and secret/path safety tests.

## 8. Monitor, HTTP, and Generated Contracts

- [ ] 8.1 Extend canonical project/environment snapshots and collector planning with read-only publication correlation, separate local endpoint/external URL, typed availability, and component failure isolation.
- [ ] 8.2 Extend `create_app()`/`run_server()` and monitor CLI with explicit proxy mode, exact allowed Hosts/trusted peers, validated forwarded origin, secure CSRF cookie, local-mode compatibility, and external pgAdmin disablement.
- [ ] 8.3 Update msgspec/OpenAPI response metadata, deterministic `openapi.json`, generated TypeScript SDK, and stale-output tests for the canonical publication model.
- [ ] 8.4 Add HTTP tests for trusted Caddy forwarding, spoofed/untrusted headers, unknown Hosts, external same-origin CSRF, local mode, publication serialization, and sanitized component failures.

## 9. Odoo-Style Panel

- [ ] 9.1 Restructure the existing Mantine UI into compact Odoo-style project/environment navigation and tabular operational views without introducing a second component framework.
- [ ] 9.2 Render publication state/reason and separate local endpoint for project and environment runtimes; enable `Open Odoo` only for the generated external HTTPS URL and disable loopback-only pgAdmin externally.
- [ ] 9.3 Preserve relative API/assets, polling serialization, CSRF interception, accessibility, responsive layout, and generated-type-only data access through focused Vitest/React tests.

## 10. Documentation and Verification

- [ ] 10.1 Document independent project preparation/run/status/stop, adoption ownership, module context, root-creator credential mapping, Git passthrough/sync, MR publication, Caddy setup, external monitor security, and recovery procedures without secret examples.
- [ ] 10.2 Add requirement-to-test trace coverage and update CLI/public SDK/architecture inventories, README, Python SDK reference, changelog, and deployment prerequisites.
- [ ] 10.3 Run formatter, Ruff, strict mypy, unit/integration/dashboard tests, OpenAPI/codegen stale gates, packaging tests, architecture/security secret audits, and strict OpenSpec validation; record unavailable opt-in Caddy/Odoo prerequisites truthfully.
- [ ] 10.4 Run opt-in disposable Odoo 19 acceptance through Caddy for concurrent URLs, TLS/Auth, login, redirects, assets, attachments, bus/WebSocket, panel external origin, failed reload preservation, stop/restart, and environment removal when prerequisites are available.
