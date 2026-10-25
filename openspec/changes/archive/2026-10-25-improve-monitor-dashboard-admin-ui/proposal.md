## Why

The monitor exposes useful runtime data but presents backup timestamps as raw ISO strings, cannot narrow the grid to running instances, and deliberately hides storage measurements that the backend already computes for worktrees. Its Admin link also targets the non-canonical path. In the administrator UI, the five registered resources have blank navigation labels because the views set `display_name` instead of Starlette Admin's `menu_label`, and GitLab secret detail navigation loses part of the record's composite identity.

## What Changes

- Render backup download timestamps in the browser's local timezone without seconds while preserving the exact machine-readable timestamp and relative age.
- Add an all/running instance filter that composes with the existing project selector and counts only displayed cards.
- Expose one bounded main-checkout storage footprint in the canonical snapshot and show human-readable total storage on both main-checkout and worktree cards, with an explicit incomplete marker when measurement is partial.
- Navigate to canonical `/admin/` and configure the five existing Starlette Admin views with visible built-in menu/index labels; do not add a custom navigation framework or duplicate dashboard.
- Give each secret record one stable Admin row/detail identity distinct from its displayed identity fields so GitLab member detail, edit, and delete routes preserve member, host, and revision.
- Extend focused Python, OpenAPI/codegen, React, Admin HTTP, packaging, and independent browser verification without changing authentication, secret write-only behavior, catalogue ownership, or runtime mutation policy.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `environment-monitor`: add a separate main-checkout storage footprint to each project summary.
- `web-sdk-codegen`: render readable backup time, running filtering, storage totals, and the canonical Admin URL through the generated snapshot contract.
- `dashboard-http-api`: expose all five existing Admin resources through built-in navigation and preserve stable secret detail identities.
- `secret-store`: provide a safe opaque Admin identity for remote Odoo and GitLab records without exposing stored secrets.

## Impact

The change affects monitor snapshot models/collection/cache planning, the msgspec/OpenAPI bridge and committed TypeScript generation, the React monitor card/filter presentation, Starlette Admin view configuration and secret projections, and directly related dashboard/package tests and documentation. It adds no dependency, database migration, background sampler, generic inventory service, custom Admin theme/navigation, or new HTTP endpoint.
