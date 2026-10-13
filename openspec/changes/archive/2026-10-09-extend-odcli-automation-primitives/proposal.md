## Why

Future Multica skills need independently callable, typed OdCLI primitives for preparing a main checkout, inspecting module context, acting as the root issue creator in Git/GitLab, and publishing local Odoo runtimes. The current SDK already has reusable database, runtime, module, Git, monitor, and HTTP seams. The current baseline also includes the independently packaged `odcli-multica` typed checkout/context/preparation integration and non-destructive caller-owned checkout adoption. The remaining gap is therefore narrower: extend those existing seams with root-creator credentials, richer read-only facts, and safe publication rather than rebuilding Multica binding or adoption.

## What Changes

- Extend main-checkout database refresh/restore and detached runtime operations so `--project PATH` can prepare, bind, start, inspect, and stop one project-owned database/filestore/runtime without creating a worktree.
- Extend module inventory with repository provenance, installed state, versions, dependency paths, and related Git changes across ordered, explicitly allowed addon roots; unavailable sources remain distinguishable from empty results.
- Extend the existing `odcli-multica` typed context with finite same-workspace parent traversal to the root human creator and host-scoped GitLab credentials loaded from the existing owner-only project dotenv.
- Add credential-aware native Git passthrough/sync and GitLab merge-request create-or-update to the existing `odcli-multica` distribution without making the core wheel depend on Multica.
- Add typed `publish`/`unpublish` operations for project and environment runtimes through one OdCLI-owned Caddy route file, including stable external addresses, validated reload, HTTPS, Basic Auth, Odoo proxy semantics, and lifecycle cleanup.
- Extend monitor/OpenAPI/TypeScript contracts with publication state and external URLs, make the panel safe behind an explicitly trusted proxy/Host, and restyle the existing UI to a compact Odoo-like operational interface.
- Keep CLI results machine-readable, public SDK results frozen and typed, process launches on the shared execution boundary, and all secret-bearing values redacted.

## Capabilities

### New Capabilities

- `multica-user-credentials`: Extend verified Multica task context to the root issue's human creator and map that identity to host-scoped GitLab credentials without fallback to the machine account.
- `gitlab-merge-request`: Create or update exactly one matching open GitLab merge request through the existing Multica extension SDK/CLI and return its stable publication result.
- `instance-publication`: Reconcile one OdCLI-owned Caddy route file and stable HTTPS identities for project, environment, and monitor runtimes.

### Modified Capabilities

- `cli-odcli`: Add project-selector preparation, publication, and complete command/output contracts while leaving Multica-specific Git/GitLab routing in `odcli-multica`.
- `client-config`: Add canonical user publication settings while keeping secrets outside public configuration models and output.
- `command-execution`: Carry host-scoped ephemeral child credentials and Caddy/GitLab effects through inspectable, redacted command/action plans.
- `project-database-preparation`: Bind a successful refresh/restore database and filestore to the selected main checkout atomically while preserving the prior binding on failure.
- `database-restore`: Return complete project restore identity and retain the previous project binding on every failed or interrupted restore.
- `instance-runtime-binding`: Persist project-owned detached runtime identity and readiness outcome so status/stop address the same captured runtime.
- `odoo-module-workflow`: Expose one read-only, repository-aware filesystem/installed/change inventory with ordered safe roots and explicit partial availability.
- `odoo-git-workflow`: Support Multica-extension native Git passthrough and HTTPS/token-backed sync with per-process isolation while preserving current safety checks.
- `environment-monitor`: Add publication state, local endpoint, external URL, and unavailability reason for both project and environment runtimes.
- `dashboard-http-api`: Support an explicit trusted external Host/proxy mode while preserving same-origin CSRF and existing API protection.
- `web-sdk-codegen`: Regenerate typed publication fields and make the React client consume only the canonical OpenAPI contract with relative URLs.

## Impact

The change affects project binding, runtime/database preparation, module and Git resources, the existing `packages/odcli-multica` context/CLI, process redaction, Caddy publication, the core publication CLI registration, HTTP transport, the optional dashboard server, OpenAPI generation, the React monitor, and focused documentation/tests. It reuses the merged `multica-py` dependency, `odcli-multica` package, typed context, and core adoption lifecycle; it does not add another Multica adapter/package, change adoption persistence, add a plugin framework, provider registry, publication database, generic ingress control plane, skill orchestration, or LLM analysis, replace native Git behavior, modify production Odoo, or make Caddy's administrative API externally reachable.
