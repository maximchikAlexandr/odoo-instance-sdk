## Context

`EnvironmentSnapshot.storage` already carries an ownership-aware `StorageFootprint`: worktree bytes, an owned venv, an owned COPY database/filestore, and environment-local artifacts. Its preferred `du` probe has a ten-second timeout, but the Python traversal used after a missing, failed, or timed-out `du` has no deadline or entry budget at the approved base. The monitor currently omits an equivalent field from `ProjectSummary`, although every project plan has a canonical registered repository root. Numbered and ordinary environment worktrees live under the global OdCLI user root rather than inside the registered checkout, so the main checkout and environment measurements have separate ownership roots.

The React application already uses one generated `Snapshot`, one shared card component, and client-side project filtering. Starlette Admin 1.0 provides its own sidebar and default index panels from each view's `menu_label`; the current five views only set `display_name`, leaving built-in navigation labels blank. Secret views return composite route identities from `get_pk_value`, but list-row routing still indexes the serialized `pk_attr`, which is only `member_id` for GitLab records and therefore cannot resolve the detail record by `(member_id, host)`.

## Goals / Non-Goals

**Goals:**

- Make timestamps, filtering, resource totals, and Admin navigation understandable without weakening canonical typed contracts.
- Put the existing storage fallback behind an explicit finite-work boundary, then reuse that probe, snapshot caching, generated client, shared card, Starlette Admin navigation/index, and encrypted-store safe projections.
- Keep storage ownership honest and prevent double attribution between main checkout and environment cards.
- Repair GitLab secret detail navigation while keeping secrets write-only and revision-safe.

**Non-Goals:**

- No storage cleanup action, reclaimability claim, recursive resource browser, per-file breakdown, or new resource catalogue.
- No server-side running filter or second snapshot endpoint.
- No custom Admin dashboard, templates, CSS theme, model ORM, or sixth Admin entity.
- No change to secret schema, encryption, credential precedence, authentication, authorization, or runtime start/stop semantics.

## Decisions

### D1. Bound the existing fallback, then add one project checkout footprint

First make `_walk_size` finite without adding another subsystem: it SHALL accept one monotonic deadline ten seconds after fallback entry and a budget of 100,000 visited entries. A visited entry includes every yielded directory and every directory entry before symlink, resolution, deduplication, or stat handling, so skipped and failing entries cannot evade the budget. The implementation SHALL check the deadline before consuming each yielded directory and before processing each entry; reaching either limit before traversal completes returns `None`. Existing `du` execution retains its ten-second subprocess timeout. Tests SHALL inject the monotonic clock and synthetic walk entries instead of creating a huge real tree, and SHALL cover `du` missing/failure/timeout, deadline exhaustion, the 100,000-entry boundary, completion immediately below the boundary, symlinks, deduplication, and `OSError`.

Add a frozen `ProjectStorageFootprint` with nullable `total_bytes` and boolean `complete` to `ProjectSummary`. Measure only the canonical registered repository root with the now-bounded, symlink-skipping directory-size helper and cache it under stable project identity for the existing expensive-section TTL. Environment cards continue using their existing `StorageFootprint.total_bytes`; project storage does not sum environment rows, Compose volumes, backups, or external/shared databases.

When the `storage` section is not requested, `du` and fallback both fail, the fallback deadline expires, the entry budget is reached, or containment is ambiguous, return `ProjectStorageFootprint(total_bytes=None, complete=false)`; never publish a partial fallback sum. Existing environment storage keeps its established component/partial-total contract but receives the same finite helper failure. The public snapshot remains path-free. This preserves separate ownership: the project card means the main checkout tree, and each worktree card means the already-defined environment-owned footprint.

### D2. Keep display changes inside the existing React view model

Add one local instance filter with values `all` and `running`. `running` includes runtime states `ready` and `not_ready` for either ownership kind and composes with the project selector before rendering and counting. Filtering remains client-side over the authoritative polled snapshot and triggers no additional HTTP request.

Format `downloaded_at` with `Intl.DateTimeFormat` in the user's browser timezone using year, month, day, hour, and minute, with seconds omitted. Keep the original ISO value in `<time dateTime>`, and retain the separately supplied age. Render storage through the existing `formatBytes`; append a concise incomplete marker when `complete=false` and do not expose component paths or technical breakdowns.

The Admin anchor uses `/admin/`, the canonical mounted root, so proxy/browser navigation does not depend on a redirect from `/admin`.

### D3. Use Starlette Admin's built-in labels and index

Set an explicit `menu_label` and an existing icon identifier on each of the five current `BaseModelView` subclasses. Register them directly with `BaseAdmin.add_view` as today. The built-in sidebar and generated `DefaultIndexView` then list the same five entities with meaningful names and record panels. No custom view, dropdown, template override, CSS, or JavaScript is added.

### D4. Separate secret route identity from displayed fields

Give the safe Admin projection for each secret record a deterministic `admin_id` derived only from its normalized identity fields and current revision. Configure the secret views to use that value as `pk_attr`, exclude it from every visible/form/export/import surface, and parse it through one shared exact decoder for find/edit/delete. The GitLab value contains member ID, normalized host, and revision; the remote Odoo value contains normalized base URL and revision. Invalid or stale values fail closed through the existing not-found/conflict behavior.

Displayed `member_id`, `host`, and `base_url` remain unchanged, and password/token/key material is never included. This aligns Starlette Admin's built-in serialized row primary key, detail URL, edit URL, and revision token instead of overriding list serialization.

### D5. Preserve generated and optional-package boundaries

The additive project storage model flows through the existing msgspec schema bridge, canonical `openapi.json`, and exact-pinned TypeScript generator. React imports generated types only. Dashboard/Admin modules remain lazily optional, and core imports remain dependency-neutral.

### D6. Ponytail full subsystem gate

- New storage subsystem: unnecessary; extend the existing `storage_footprint` helper with a deadline/entry budget and reuse the monitor TTL cache. Decision: keep that narrow boundary repair and remove any separate scanner, worker, or service.
- Server-side filter/API: unnecessary because the complete snapshot is already polled and the requested predicate is purely presentational. Decision: remove.
- Custom Admin navigation/dashboard: unnecessary because Starlette Admin's `menu_label`, icons, sidebar, and `DefaultIndexView` already provide the requested UI. Decision: remove.
- Composite-key framework: unnecessary; two secret projections can share one narrow safe Admin identity encoder/decoder. Decision: keep only that helper.
- New browser/E2E framework: unnecessary; reuse the existing dashboard commands, numbered slot wrapper, and independent browser walkthrough. Decision: remove.

## Risks / Trade-offs

- Main checkout traversal may be expensive for very large repositories. The `du` timeout plus the fallback's ten-second monotonic deadline and 100,000-entry budget bound attempted work; either limit discards the partial sum and exposes an incomplete project measurement. The deadline is cooperative between filesystem operations, so an individual blocked local-filesystem call is not preempted; the entry budget still prevents an unbounded number of calls, and introducing a worker/process cancellation subsystem is outside this change.
- A repository could contain an unusual nested environment worktree. Canonical OdCLI-created environments are outside the repository root; if an adopted legacy layout violates that invariant, the measurement is incomplete/ambiguous and implementation SHALL not claim separate totals until containment is excluded.
- Browser-local timestamp text varies by locale. Tests SHALL assert a fixed injected locale/timezone for deterministic formatting while production follows the user agent.
- Changing secret route IDs invalidates already-open Admin detail URLs after an edit because the revision advances. That is desirable stale-session behavior; the committed update redirects through the newly serialized identity.

## Migration Plan

No database or configuration migration is required. Regenerate OpenAPI and TypeScript artifacts in the implementation commit. Existing secret rows receive their Admin identity only in the safe in-memory projection. Rollback removes the additive project storage field and UI behavior without changing catalogue or secret-store bytes.

## Open Questions

None. Existing ownership roots, Starlette Admin 1.0 primitives, and generated snapshot contracts resolve the reported alternatives without leaving implementation choices open.
