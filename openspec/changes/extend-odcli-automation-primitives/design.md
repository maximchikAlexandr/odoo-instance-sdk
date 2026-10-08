## Context

The current baseline already contains the hard parts that should remain authoritative: project/environment selection through `ResolvedContext` and `RuntimeView`, the common `internal/dbprep` pipeline, atomic project manifest replacement, detached runtime identity/readiness/stop, safe `ModuleResource` discovery, `GitResource` plus exact-lease sync, owner-only dotenv loading, central process redaction, the canonical monitor/msgspec/OpenAPI/generated-TypeScript path, and the React/Mantine panel.

The missing behavior is integration at those seams: a paired project database/filestore binding, registration of a Multica-owned checkout, root-creator credentials for Git/GitLab, richer read-only module facts, one Caddy publication file, and publication fields in the existing monitor/UI path. This revision applies `ponytail full`: no subsystem is added where an existing resource, file, process boundary, or generated contract suffices.

## Goals / Non-Goals

**Goals:**

- Add independently callable project, adoption, module, Git/GitLab, publication, and panel primitives by extending existing concrete resources.
- Keep database/filestore publication atomic and preserve exact runtime/process ownership.
- Resolve GitLab authority from the root Multica issue's human creator and keep credentials child/request-local.
- Publish stable HTTPS routes with one locked, validated OdCLI-owned Caddy file.
- Preserve one monitor/OpenAPI/TypeScript/UI data path and fail closed at proxy/security boundaries.

**Non-Goals:**

- A workflow coordinator, plugin/provider registry, second module index, publication database, generic ingress control plane, or reusable abstraction with one implementation.
- A separate `odcli-multica` distribution/extra, private Multica HTTP calls, caller-provided user IDs, or machine-account fallback.
- DNS management, Nginx or non-GitLab providers, public Caddy administration, pgAdmin publication, automatic merge, or arbitrary addon roots.
- A new acceptance harness, requirement-trace database, duplicate architecture inventory, or test matrices beyond the smallest checks that prove each changed boundary.

## Decisions

### 1. Extend concrete resources; do not add an orchestration layer

Project/environment selection remains in `RuntimeView`; preparation remains in `internal/dbprep`; runtime work remains in `OdooInstance`; module and Git work stay in their existing resources; monitor remains the snapshot authority. New commands are thin adapters over those SDK operations.

Alternative: a new service coordinating prepare, run, publish, and MR. Rejected because the requested steps are independently callable and the service would duplicate plans, errors, and compensation.

### 2. Store the paired project binding in the existing manifest

Add only the managed filestore identity and ordered addon repositories needed beside `default_source_database`. After the existing restore postconditions pass, compare the captured binding and atomically replace the manifest once. Project runtime reads that pair; download-only and failed/stale operations keep the prior pair.

Alternative: a project-binding catalog table. Rejected because it creates a second authority for data already owned by the project manifest.

### 3. Adoption reuses the environment record with one ownership discriminator

`env adopt` fills the existing environment record from the proven external checkout and adds only `checkout_owner = multica|odcli`. It does not create a worktree or prepare a database. Existing owner-neutral runtime/database/module/monitor paths then work unchanged. Sync and removal branch only where filesystem ownership matters, so an adopted checkout is never moved, reset, cleaned, or deleted.

Alternative: a parallel adopted-environment model. Rejected because it would duplicate environment identity, runtime, and database behavior.

### 4. Use one concrete Multica adapter and one small mapping file

Add the required public `multica-py` dependency and one in-package adapter. It resolves the checkout binding, follows a finite same-workspace parent chain, and requires a human root creator. A small owner-only `multica.toml` maps user ID plus exact GitLab host to login and token environment-key name; the existing project dotenv loader supplies the token. No interface, factory, optional package, provider registry, or persistence model is introduced.

Alternative: an optional integration distribution. Rejected because there is one required provider and optional packaging adds import/configuration paths without reducing scope.

### 5. Reuse the process and HTTP boundaries for Git/GitLab

For a proven HTTPS GitLab host, the existing process plan receives `GIT_TERMINAL_PROMPT=0` and a child-only `GIT_ASKPASS` helper. Remote URL, argv, and Git configuration remain unchanged. Raw passthrough prepends only `git -C <root>`; local commands skip credential resolution. Existing sync retains rebase/check/exact-lease behavior. MR publication uses installed `httpx`, exact project/source/target matching, one bounded description file, and create-or-update with no automatic retry of create.

Alternative: a Git credential store or forge client framework. Rejected because both persist or generalize beyond the one requested GitLab flow.

### 6. Build module context on demand

Extend `ModuleResource` to traverse ordered allowed repositories using its current safe manifest parsing and first-root precedence. Query Git facts through the existing process boundary and installed versions through the selected database path. Each unavailable source returns one bounded availability reason. No cache, provider registry, recursive parent scan, or second index is created.

### 7. Use one OdCLI-owned Caddy route file as publication state

User settings name the domain, local Caddy control target, Basic Auth values, trusted proxies, panel label, and one owned route file. A global publication lock protects read-modify-write. Publish/unpublish generates a complete candidate, validates it, atomically swaps the owned file for reload, and restores the prior bytes if reload fails; the existing live configuration therefore remains usable. Stable URLs are derived from existing project/environment IDs. Monitor reads this generated file and the exact runtime record; there is no publication table, route repository, rollback journal, or second state model.

The owned file contains only OdCLI routes. Caddy handles HTTPS and Basic Auth natively. Route templates cover the Odoo HTTP/assets/attachments/bus paths and the panel route; unknown hosts match nothing.

Alternative: Caddy Admin API mutations plus a catalog-backed route control plane. Rejected because one locked file already provides deterministic ownership, validation, reload, and retry.

### 8. Extend the existing monitor and panel end to end

Add one minimal publication value to the canonical Python snapshot: state, external URL, and reason. OpenAPI and generated TypeScript carry it to React. Local runtime URL stays separate. `create_app()` gets an explicit trusted-proxy policy; loopback remains the default, while external mode accepts only exact proxy peers/Hosts and validates forwarded origin for secure CSRF. React changes only the existing views and uses the server-provided URL.

Alternative: a publication HTTP service or handwritten frontend type. Rejected because both duplicate the current canonical pipeline.

### 9. Verify each boundary once

Each non-trivial change gets the smallest focused check that proves its branch, parser, or trust boundary. Existing repository format/lint/type/unit/integration/dashboard/codegen/package gates run once after integration. Existing disposable Odoo fixtures may perform one opt-in Caddy smoke when prerequisites exist; no new acceptance harness, trace matrix, inventory suite, or repeated full-gate work is added to domain packages.

## Risks / Trade-offs

- **Public `multica-py` lacks the required checkout/issue traversal** → fail the integration explicitly and plan the missing upstream API; do not add private HTTP fallback.
- **Token reaches child/provider diagnostics** → use per-command private values plus the existing redaction boundary and one secret-canary check.
- **Concurrent route edits lose entries** → serialize the single owned file with the existing lock convention and test one concurrent update case.
- **Caddy reload rejects a candidate** → validate first, restore prior bytes on reload failure, and never edit unowned ingress files.
- **Proxy trust is misconfigured** → reject wildcard/incomplete peers or Hosts; keep local mode as default.
- **Adopted checkout cleanup touches caller data** → persist the ownership discriminator and guard the existing sync/remove filesystem steps.
- **Single route file is not a general ingress database** → accepted by design; add a richer store only if future requirements need multiple writers or non-Caddy backends.

## Migration Plan

1. Add backward-compatible project fields and one additive environment ownership column; existing environments default to OdCLI-owned.
2. Add adoption/Multica identity and project binding on the current resources.
3. Add Git/GitLab, module context, and the single-file Caddy publisher at their existing boundaries.
4. Extend monitor/OpenAPI/generated TypeScript and update the current panel.
5. Run focused checks and the existing repository gates once; run the optional Caddy/Odoo smoke only when its existing prerequisites are available.

Rollback disables external monitor mode, uses `unpublish` for owned routes, and deploys the previous package. Additive manifest fields and the ownership column remain backward-compatible; caller-owned checkouts and restored databases are not deleted by rollback.

## Open Questions

None. DNS/TLS reachability, a dedicated Caddy instance that includes the OdCLI-owned file, valid user mappings, and a compatible public `multica-py` release are deployment prerequisites, not new implementation subsystems.
