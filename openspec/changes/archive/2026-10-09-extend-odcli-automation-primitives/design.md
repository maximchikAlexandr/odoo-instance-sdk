## Context

The current baseline contains project/environment selection through `ResolvedContext` and `RuntimeView`, the common `internal/dbprep` pipeline, atomic project manifest replacement, detached runtime identity/readiness/stop, safe `ModuleResource` discovery, `GitResource` plus exact-lease sync, owner-only dotenv loading, central process redaction, the canonical monitor/msgspec/OpenAPI/generated-TypeScript path, and the React/Mantine panel.

It now also contains the merged Multica integration: `packages/odcli-multica`, a pinned public `multica-py`, typed checkout and daemon compatibility checks, `VerifiedTaskContext`, finite context/preparation commands, core caller-owned checkout adoption, non-destructive cleanup, and focused unit/integration acceptance coverage. Those contracts are baseline, not work for this change. The remaining behavior is a paired project database/filestore binding, root-creator credentials extending the verified Multica context, richer read-only module facts, Multica-extension Git/GitLab operations, one Caddy publication file, and publication fields in the existing monitor/UI path.

This revision applies `ponytail full`: reuse the merged integration and existing concrete resources; do not create a second Multica adapter, binding, adoption model, or command surface.

## Goals / Non-Goals

**Goals:**

- Add independently callable project, module, Multica Git/GitLab, publication, and panel primitives by extending existing concrete resources and the existing extension distribution.
- Keep database/filestore publication atomic and preserve exact runtime/process ownership.
- Resolve GitLab authority from the verified task's root human creator and keep credentials child/request-local.
- Publish stable HTTPS routes with one locked, validated OdCLI-owned Caddy file.
- Preserve one monitor/OpenAPI/TypeScript/UI data path and fail closed at proxy/security boundaries.

**Non-Goals:**

- Reimplementing typed Multica checkout/context/preparation, core adoption, its persistence/migrations, or caller-owned checkout cleanup.
- Making the core wheel import `odcli-multica`, adding another Multica distribution/adapter, or calling private Multica HTTP.
- A workflow coordinator, plugin/provider registry, second module index, publication database, generic ingress control plane, or reusable abstraction with one implementation.
- Caller-provided user IDs, machine-account fallback, DNS management, Nginx or non-GitLab providers, public Caddy administration, pgAdmin publication, automatic merge, or arbitrary addon roots.
- A new acceptance harness, requirement-trace database, duplicate architecture inventory, or repeated full-gate matrices.

## Decisions

### 1. Treat merged Multica binding and adoption as authoritative baseline

`MulticaOdooClient.context_command()` already verifies checkout containment, issue/run/project/workspace membership, repository identity, daemon/runtime ownership, pagination completeness, and compatibility. `prepare_command()` already delegates to the captured public core adoption command; core cleanup already preserves caller-owned checkout bytes. MYL-409 SHALL consume these results and SHALL NOT add `checkout_owner`, another adoption command, another binding store, or another dependency declaration.

Alternative: retain the old MYL-409 adoption tasks beside the merged implementation. Rejected because they conflict with the accepted COPY preparation contract and duplicate schema, lifecycle, and tests.

### 2. Store the paired project binding in the existing manifest

Add only the managed filestore identity and ordered addon repositories needed beside `default_source_database`. After the existing restore postconditions pass, compare the captured binding and atomically replace the manifest once. Project runtime reads that pair; download-only and failed/stale operations keep the prior pair.

Alternative: a project-binding catalog table. Rejected because it creates a second authority for data already owned by the project manifest.

### 3. Extend verified Multica context, not checkout discovery

The extension starts from `VerifiedTaskContext.issue_id` and `workspace_id`. It follows public typed `Issue.parent_id` reads with a visited set, requires every issue to remain in the same workspace, and accepts only a root with `creator_type=member` and a non-empty creator ID. The existing `multica-py` model already exposes `parent_id`, creator evidence, issue get, and workspace members. A small owner-only mapping file maps root user ID plus exact GitLab HTTPS host to login and reserved token environment-key name; the existing project dotenv loader supplies the token.

No new interface, factory, provider registry, checkout lookup, persisted association, or package is introduced.

### 4. Keep Multica-specific Git/GitLab routing in the extension

For a proven HTTPS GitLab host, the existing process plan receives `GIT_TERMINAL_PROMPT=0` and a child-only `GIT_ASKPASS` helper. Remote URL, argv, and Git configuration remain unchanged. The `odcli-multica` CLI exposes raw Git passthrough, credential-aware sync composition, and GitLab MR publication while reusing core Git plans/results. The core wheel remains independently installable and does not import the extension.

MR publication uses installed `httpx`, exact project/source/target matching, one bounded description file, and create-or-update with no automatic retry of create. No forge client framework or credential store is added.

### 5. Build module context on demand

Extend `ModuleResource` to traverse ordered allowed repositories using its current safe manifest parsing and first-root precedence. Query Git facts through the existing process boundary and installed versions through the selected database path. Each unavailable source returns one bounded availability reason. No cache, provider registry, recursive parent scan, or second index is created.

### 6. Use one OdCLI-owned Caddy route file as publication state

User settings name the domain, local Caddy control target, Basic Auth values, trusted proxies, panel label, and one owned route file. A global publication lock protects read-modify-write. Publish/unpublish generates a complete candidate, validates it, atomically swaps the owned file for reload, and restores the prior bytes if reload fails. Stable URLs derive from existing project/environment IDs. Monitor reads this generated file and the exact runtime record; there is no publication table, route repository, rollback journal, or second state model.

The owned file contains only OdCLI routes. Caddy handles HTTPS and Basic Auth natively. Route templates cover Odoo HTTP/assets/attachments/bus and the panel route; unknown hosts match nothing.

### 7. Extend the existing monitor and panel end to end

Add one minimal publication value to the canonical Python snapshot: state, external URL, and reason. OpenAPI and generated TypeScript carry it to React. Local runtime URL stays separate. `create_app()` gets an explicit trusted-proxy policy; loopback remains the default, while external mode accepts only exact proxy peers/Hosts and validates forwarded origin for secure CSRF. React changes only the existing views and uses the server-provided URL.

The central core CLI registry is written once during terminal convergence to register only completed `publish` and `unpublish` commands. Multica Git/GitLab commands remain owned by the extension CLI.

### 8. Verify each boundary once

Each non-trivial change gets the smallest focused check that proves its branch, parser, or trust boundary. Existing repository format/lint/type/unit/integration/dashboard/codegen/package gates run once after integration. Existing disposable Odoo fixtures may perform one opt-in Caddy smoke when prerequisites exist; no new acceptance harness, trace matrix, inventory suite, or repeated full-gate work is added.

## Risks / Trade-offs

- **Pinned Multica contract changes** → retain the existing compatibility gate and extend tests against its public `Issue`/workspace member models; do not add private transport fallback.
- **Token reaches child/provider diagnostics** → use per-command private values plus the existing redaction boundary and one secret-canary check.
- **Concurrent route edits lose entries** → serialize the single owned file with the existing lock convention and test one concurrent update case.
- **Caddy reload rejects a candidate** → validate first, restore prior bytes on reload failure, and never edit unowned ingress files.
- **Proxy trust is misconfigured** → reject wildcard/incomplete peers or Hosts; keep local mode as default.
- **Caller-owned checkout cleanup touches user data** → reuse the merged adoption cleanup unchanged; publication cleanup may block unregistering but never gains checkout ownership.
- **Single route file is not a general ingress database** → accepted by design; add a richer store only if future requirements need multiple writers or non-Caddy backends.

## Migration Plan

1. Add backward-compatible project binding fields; no environment or Multica schema migration is added by this change.
2. Extend the existing Multica extension context with root-creator credentials and its CLI with Git/GitLab leaves.
3. Add project binding, module context, and the single-file Caddy publisher at their existing boundaries.
4. Extend monitor/OpenAPI/generated TypeScript and update the current panel; register core publication commands once.
5. Run focused checks and the existing repository gates once; run the optional Caddy/Odoo smoke only when its existing prerequisites are available.

Rollback disables external monitor mode, uses `unpublish` for owned routes, and deploys the previous package. Additive project fields remain backward-compatible. The merged Multica context/adoption package and caller-owned checkout lifecycle remain untouched.

## Open Questions

None. DNS/TLS reachability, a dedicated Caddy instance that includes the OdCLI-owned file, and valid user mappings are deployment prerequisites, not new implementation subsystems.
