## 1. Main Checkout Binding and Runtime

- [ ] 1.1 Extend the existing project manifest with backward-compatible managed-filestore and ordered-addon-repository fields; add focused parse/round-trip/path-validation checks.
- [ ] 1.2 Extend the common preparation pipeline to publish the database/filestore pair with the existing atomic manifest replacement and stale-binding check after successful restore postconditions.
- [ ] 1.3 Extend project refresh/restore results and CLI projections with the required backup, target, configuration, filestore, publication, and sanitized failure facts.
- [ ] 1.4 Make project detached run/readiness/status/stop consume the paired binding through the existing runtime identity path and add focused success, timeout, stale-identity, and prior-binding-preservation checks.

## 2. Multica Adoption and Root-Creator Identity

- [ ] 2.1 Add one additive `checkout_owner` environment field defaulting to OdCLI ownership and implement `env adopt` by registering the proven external checkout through the existing environment catalogue/resource path without Git, database, runtime, or filesystem mutation.
- [ ] 2.2 Guard the existing sync/remove filesystem steps so Multica-owned checkouts can be used, stopped, unpublished, and unregistered but never moved, reset, cleaned, updated by generic sync, or deleted.
- [ ] 2.3 Add the required public `multica-py` dependency and one concrete in-package adapter that resolves one checkout binding and finite same-workspace lineage to a human root creator.
- [ ] 2.4 Add one owner-only Multica-user/GitLab-host mapping file and reuse the existing project dotenv loader for reserved token keys, returning one private per-command credential context with safe public identifiers.
- [ ] 2.5 Add focused checks for adoption conflicts/non-mutation, external-owner removal, invalid lineage, missing/ambiguous host mapping, concurrent user isolation, and secret redaction.

## 3. Native Git and GitLab Merge Requests

- [ ] 3.1 Add a packaged child-only `GIT_ASKPASS` path on the existing process boundary for one proven HTTPS GitLab host without changing URLs, argv, or Git configuration.
- [ ] 3.2 Add `GitResource.passthrough_command()` and `odcli git -- <args>` with exact native cwd/stdout/stderr/signal/exit behavior; skip credential resolution for local-only commands.
- [ ] 3.3 Extend the existing sync plan to use the same HTTPS credential snapshot while retaining current SSH, rebase, check, branch-protection, and exact-lease behavior.
- [ ] 3.4 Add the concrete GitLab `httpx` calls and `gitlab mr publish` operation for exact project/source/target lookup, bounded file-backed description, issue link, assignee resolution, and create-or-update.
- [ ] 3.5 Add focused native-Git and fake-GitLab checks for local/remote passthrough, HTTPS sync, stale lease, two users, MR zero/one/many matching, provider failure, dry-run, and secret canaries.

## 4. Repository-Aware Module Context

- [ ] 4.1 Extend `ModuleResource` to inspect ordered safe addon roots in the selected checkout and declared repositories while preserving first-root precedence, shadow provenance, and containment.
- [ ] 4.2 Collect repository Git changes through the existing process boundary and installed module/version facts through the selected database path without executing manifests or adding a cache/index.
- [ ] 4.3 Add one read-only SDK/CLI context result that keeps filesystem, database, and Git availability independent and exposes bounded reasons for unavailable sources.
- [ ] 4.4 Add focused checks for duplicate names, dependency paths, Git/database unavailability, unsafe roots, change attribution, and no mutation.

## 5. Single-File Caddy Publication

- [ ] 5.1 Add one owner-only publication settings reader and deterministic project/environment/panel host labels using existing stable owner IDs.
- [ ] 5.2 Implement `publish_command()`/`unpublish_command()` and CLI leaves as a locked read-modify-write of one OdCLI-owned route file with runtime readiness, candidate validation, reload, prior-byte restoration on failure, and idempotent removal.
- [ ] 5.3 Generate only the required HTTPS, hashed Basic Auth, proxy-header, Odoo HTTP/assets/attachments/bus, and panel routes; keep unknown hosts unmatched and Caddy control local.
- [ ] 5.4 Integrate route availability with stop/restart and require successful route removal before environment removal, preserving the external checkout on cleanup failure.
- [ ] 5.5 Add focused checks for stable/distinct URLs, concurrent file updates, validation/reload failure, prior-byte preservation, auth/Host rejection, stopped runtime, unpublish, removal retry, and secret-free output.

## 6. Monitor, HTTP Contract, and Existing Panel

- [ ] 6.1 Extend the canonical project/environment snapshot with one publication value read from the owned route file and correlated with exact runtime state; preserve the separate local endpoint and isolate publication read failure.
- [ ] 6.2 Add explicit trusted-proxy mode to the existing FastAPI server with exact peer/Host/forwarded-origin checks, secure external CSRF, local-mode compatibility, and external pgAdmin disablement.
- [ ] 6.3 Register the completed top-level `gitlab`, `publish`, and `unpublish` Click objects in the central CLI registry, regenerate OpenAPI and the TypeScript SDK once from the canonical snapshot change, and keep relative API/assets under the external origin.
- [ ] 6.4 Restyle the existing Mantine views into compact Odoo-like project/environment navigation and tables; use only the server-supplied available external URL for `Open Odoo`.
- [ ] 6.5 Add focused monitor/HTTP/UI checks for project/environment publication states, spoofed forwarding, same-origin mutation, disabled actions, relative URLs, generated-type use, accessibility, and responsive layout.

## 7. Documentation and One Verification Pass

- [ ] 7.1 Document the independent project, adoption, module, Git/GitLab, publication, external-panel, ownership, prerequisite, and recovery flows without secret values.
- [ ] 7.2 Run the existing format, Ruff, strict mypy, unit/integration/dashboard, OpenAPI/codegen, packaging, and architecture/security gates once on the integrated branch; fix only failures caused by this change.
- [ ] 7.3 When existing disposable Odoo/Caddy prerequisites are available, run one focused smoke for HTTPS/Auth/login/assets/attachments/bus, panel origin, failed reload preservation, stop/restart, and removal; report unavailable prerequisites without adding a new harness.
