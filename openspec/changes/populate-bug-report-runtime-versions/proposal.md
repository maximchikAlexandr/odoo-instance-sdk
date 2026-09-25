## Why

Bug-report drafts currently write `unknown` for Odoo and PostgreSQL even when the current managed project exposes deterministic runtime versions. This removes useful reproduction context and contradicts the existing promise to include applicable versions while preserving offline draft creation.

## What Changes

- Resolve the current managed project, when one is available, through the existing project-context mechanism during `bug-report init`.
- Discover Odoo and PostgreSQL versions through bounded, read-only providers backed by the resolved project/runtime configuration.
- Populate each version independently and retain `unknown` only for the provider that is unavailable, malformed, times out, or fails.
- Keep draft creation available outside managed projects, avoid starting services, and prevent credentials or other secrets from entering the report or command plan.
- Add SDK and public-CLI regression coverage for successful discovery, independent fallback, bounded failure, dry-run, and redaction behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `bug-report`: strengthen draft initialization so applicable managed-project Odoo and PostgreSQL versions are discovered safely and recorded independently.

## Impact

- Affected production areas: `src/odoo_instance_sdk/bug_report.py` and focused private helpers for project-aware version discovery.
- Reused contracts: current-project resolution, `ProjectConfig`, immutable command planning, and `internal.proc` captured execution.
- Affected tests: `tests/unit/test_bug_report.py` plus the existing public CLI regression path.
- Public command names, arguments, result models, draft layout, permissions, submit workflow, and dependencies remain compatible.
