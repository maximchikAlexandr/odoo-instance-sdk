## Context

`EnvironmentSnapshot.storage` already carries a bounded, ownership-aware `StorageFootprint`: worktree bytes, an owned venv, an owned COPY database/filestore, and environment-local artifacts. The monitor currently omits an equivalent field from `ProjectSummary`, although every project plan has a canonical registered repository root. Numbered and ordinary environment worktrees live under the global OdCLI user root rather than inside the registered checkout, so the main checkout and environment measurements have separate ownership roots.

The React application already uses one generated `Snapshot`, one shared card component, and client-side project filtering. Starlette Admin 1.0 provides its own sidebar and default index panels from each view's `menu_label`; the current five views only set `display_name`, leaving built-in navigation labels blank. Secret views return composite route identities from `get_pk_value`, but list-row routing still indexes the serialized `pk_attr`, which is only `member_id` for GitLab records and therefore cannot resolve the detail record by `(member_id, host)`.

## Goals / Non-Goals

**Goals:**

- Make timestamps, filtering, resource totals, and Admin navigation understandable without weakening canonical typed contracts.
- Reuse existing bounded storage probes, snapshot caching, generated client, shared card, Starlette Admin navigation/index, and encrypted-store safe projections.
- Keep storage ownership honest and prevent double attribution between main checkout and environment cards.
- Repair GitLab secret detail navigation while keeping secrets write-only and revision-safe.

**Non-Goals:**

- No storage cleanup action, reclaimability claim, recursive resource browser, per-file breakdown, or new resource catalogue.
- No server-side running filter or second snapshot endpoint.
- No custom Admin dashboard, templates, CSS theme, model ORM, or sixth Admin entity.
- No change to secret schema, encryption, credential precedence, authentication, authorization, or runtime start/stop semantics.

## Decisions

### D1. Add one project checkout footprint using the existing storage boundary

Add a frozen `ProjectStorageFootprint` with `total_bytes` and `complete` to `ProjectSummary`. Measure only the canonical registered repository root with the existing bounded, symlink-skipping directory-size helper and cache it under stable project identity for the existing expensive-section TTL. Environment cards continue using their existing `StorageFootprint.total_bytes`; project storage does not sum environment rows, Compose volumes, backups, or external/shared databases.

When the `storage` section is not requested or the directory probe fails, return `complete=false` with no invented successful total. The public snapshot remains path-free. This preserves separate ownership: the project card means the main checkout tree, and each worktree card means the already-defined environment-owned footprint.

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

- New storage subsystem: unnecessary; `storage_footprint` and the monitor TTL cache already provide the required probe and failure semantics. Decision: remove.
- Server-side filter/API: unnecessary because the complete snapshot is already polled and the requested predicate is purely presentational. Decision: remove.
- Custom Admin navigation/dashboard: unnecessary because Starlette Admin's `menu_label`, icons, sidebar, and `DefaultIndexView` already provide the requested UI. Decision: remove.
- Composite-key framework: unnecessary; two secret projections can share one narrow safe Admin identity encoder/decoder. Decision: keep only that helper.
- New browser/E2E framework: unnecessary; reuse the existing dashboard commands, numbered slot wrapper, and independent browser walkthrough. Decision: remove.

## Risks / Trade-offs

- Main checkout traversal may be expensive for very large repositories. The existing bounded helper timeout/fallback behavior and expensive-section TTL limit repeated work; unavailable measurement is shown as incomplete instead of blocking the snapshot.
- A repository could contain an unusual nested environment worktree. Canonical OdCLI-created environments are outside the repository root; if an adopted legacy layout violates that invariant, the measurement is incomplete/ambiguous and implementation SHALL not claim separate totals until containment is excluded.
- Browser-local timestamp text varies by locale. Tests SHALL assert a fixed injected locale/timezone for deterministic formatting while production follows the user agent.
- Changing secret route IDs invalidates already-open Admin detail URLs after an edit because the revision advances. That is desirable stale-session behavior; the committed update redirects through the newly serialized identity.

## Migration Plan

No database or configuration migration is required. Regenerate OpenAPI and TypeScript artifacts in the implementation commit. Existing secret rows receive their Admin identity only in the safe in-memory projection. Rollback removes the additive project storage field and UI behavior without changing catalogue or secret-store bytes.

## Open Questions

None. Existing ownership roots, Starlette Admin 1.0 primitives, and generated snapshot contracts resolve the reported alternatives without leaving implementation choices open.
