## Why

Future Multica skills need independently callable, typed OdCLI primitives for preparing a main checkout, inspecting module context, acting as the root issue creator in Git/GitLab, and publishing local Odoo runtimes. The current SDK already has reusable database, runtime, module, Git, monitor, and HTTP seams, but it lacks the cross-system identity contract, checkout adoption, public-route lifecycle, and several read-only facts required to compose those workflows safely.

## What Changes

- Extend main-checkout database refresh/restore and detached runtime operations so `--project PATH` can prepare, bind, start, inspect, and stop one project-owned database/filestore/runtime without creating a worktree.
- Add explicit adoption of a caller-owned Multica checkout while keeping ordinary environment creation responsible for its own worktree.
- Extend module inventory with repository provenance, installed state, versions, dependency paths, and related Git changes across ordered, explicitly allowed addon roots; unavailable sources remain distinguishable from empty results.
- Add one concrete Multica adapter inside the existing distribution that resolves the current checkout through public `multica-py`, walks to the root issue, resolves its human creator, and maps that identity to host-scoped GitLab credentials loaded from the existing owner-only project dotenv.
- Extend `odcli git` with native `--` passthrough and HTTPS credentials scoped to one child process/host; reuse the same resolver for `git sync` while preserving rebase and exact-lease publication safety.
- Add typed GitLab merge-request create-or-update publication with unambiguous open-MR matching, file-backed descriptions, configurable assignee, and the resolved Multica issue link.
- Add typed `publish`/`unpublish` operations for project and environment runtimes through one OdCLI-owned Caddy route file, including stable external addresses, validated reload, HTTPS, Basic Auth, Odoo proxy semantics, and lifecycle cleanup.
- Extend monitor/OpenAPI/TypeScript contracts with publication state and external URLs, make the panel safe behind an explicitly trusted proxy/Host, and restyle the existing UI to a compact Odoo-like operational interface.
- Keep CLI results machine-readable, public SDK results frozen and typed, process launches on the shared execution boundary, and all secret-bearing values redacted.

## Capabilities

### New Capabilities

- `multica-user-credentials`: Resolve a Multica checkout to the root issue's human creator and map that identity to host-scoped GitLab credentials without fallback to the machine account.
- `gitlab-merge-request`: Create or update exactly one matching open GitLab merge request through a typed SDK/CLI operation and return its stable publication result.
- `instance-publication`: Reconcile one OdCLI-owned Caddy route file and stable HTTPS identities for project, environment, and monitor runtimes.

### Modified Capabilities

- `cli-odcli`: Add adoption, Git passthrough, GitLab MR, publish/unpublish, and complete project-selector command/output contracts.
- `client-config`: Add canonical user publication settings while keeping secrets outside public configuration models and output.
- `command-execution`: Carry host-scoped ephemeral child credentials and Caddy/GitLab effects through inspectable, redacted command/action plans.
- `project-database-preparation`: Bind a successful refresh/restore database and filestore to the selected main checkout atomically while preserving the prior binding on failure.
- `database-restore`: Return complete project restore identity and retain the previous project binding on every failed or interrupted restore.
- `instance-runtime-binding`: Persist project-owned detached runtime identity and readiness outcome so status/stop address the same captured runtime.
- `development-environment`: Adopt an existing Multica-owned checkout without creating or deleting its worktree and retain its external ownership boundary.
- `odoo-module-workflow`: Expose one read-only, repository-aware filesystem/installed/change inventory with ordered safe roots and explicit partial availability.
- `odoo-git-workflow`: Support native Git passthrough and HTTPS/token-backed sync with per-process isolation while preserving current safety checks.
- `environment-monitor`: Add publication state, local endpoint, external URL, and unavailability reason for both project and environment runtimes.
- `dashboard-http-api`: Support an explicit trusted external Host/proxy mode while preserving same-origin CSRF and existing API protection.
- `web-sdk-codegen`: Regenerate typed publication fields and make the React client consume only the canonical OpenAPI contract with relative URLs.

## Impact

The change affects CLI registration, project binding, one environment ownership field, runtime/database preparation, module and Git resources, process redaction, HTTP transport, the optional dashboard server, OpenAPI generation, the React monitor, and focused documentation/tests. It adds the required public `multica-py` dependency and a concrete in-package adapter; it does not add a plugin framework, provider registry, publication database, generic ingress control plane, skill orchestration, or LLM analysis, replace native Git behavior, modify production Odoo, or make Caddy's administrative API externally reachable.
