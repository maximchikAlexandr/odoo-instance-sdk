## Why

An unpinned main project currently resolves its generated/default Odoo HTTP port and fails when that port is occupied, even though OdCLI already has a bounded free-port allocator. Operators must edit configuration manually, and a publication attempted after that workaround can otherwise retain the stale endpoint.

## What Changes

- Distinguish an operator-pinned HTTP port from an automatically assigned main-project port in project configuration.
- Before a non-preview main-project run, validate an explicit pin exactly or allocate an available HTTP port through the existing allocator, persist the automatic assignment atomically, and regenerate the owned Odoo configuration before constructing the immutable run command.
- Reuse a still-available automatic assignment across restarts; replace it safely when it becomes unavailable; never silently replace an occupied explicit pin.
- Report the effective endpoint and whether an automatic fallback was selected in detached-run output, while keeping preview/read-only paths mutation-free.
- Verify that status and publication consume the captured runtime endpoint and never route to a stale configured default.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-init`: Persist and round-trip the distinction between an explicit preferred HTTP port and an OdCLI-assigned main-project HTTP port, including safe atomic updates of generated configuration.
- `instance-runtime-binding`: Select, validate, persist, report, and reuse the effective main-project HTTP port before foreground or detached execution while preserving explicit-pin conflict behavior and runtime identity consistency.

## Impact

- Affected public/model surfaces: `ProjectConfig`, detached launch result/output, and main-project runtime construction.
- Affected internals: the existing HTTP allocator, project manifest locking/writes, generated `odoo.conf` refresh, and main-project CLI run coordination.
- Affected consumers: main-project `run`, `status`, and `publish`; task environments and auxiliary restore allocation remain unchanged.
- No new dependency, port registry, allocator, background service, database migration, or publication subsystem is introduced.
