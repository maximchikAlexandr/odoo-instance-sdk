## Why

Manual alpha testing exposed nine independent lifecycle, recovery, monitoring, and CLI-contract gaps: checkout can declare an unusable owned Python environment ready, COPY cleanup cannot complete while Odoo is stopped, dry-run failures lose provenance, and common resource operations lack safe concise entry points. Recovery cannot replace a selected environment backup or stop its proven-owned process, repeated ticket work has no deterministic branch allocation, and existing environments can silently drift from current project settings.

## What Changes

- Validate an SDK-owned Python environment with one bounded Odoo entry-point preflight before checkout can become `ready`; preserve retryable cleanup on failure.
- Remove an SDK-owned COPY database through the existing guarded direct PostgreSQL drop path, including safe retry from `cleanup_failed`, without requiring an Odoo listener.
- Add the canonical aliases `env create|ls|rm`, `backup ls|inspect|rm`, `db ls|rm`, `postgres ps`, `resource ls`, and `module ls` while keeping every current spelling compatible and behaviorally identical.
- Select one process-tree memory value: macOS physical footprint and the existing RSS total elsewhere, with one platform-neutral machine field and no fabricated fallback.
- Preserve the resolved invocation's `dry_run` value in success and failure envelopes through the shared failure boundary.
- Add `db restore BACKUP_UUID --replace` for the selected stopped COPY environment, preserving its identity and exact database name while restoring matching database/filestore provenance through existing restore and compensation primitives.
- Add top-level `stop` for the selected environment, terminating only a process whose persisted live identity proves ownership and treating confirmed absence as success.
- **BREAKING (CLI):** make the `env create`/`env checkout` positional argument an uppercase Jira ticket key, remove CLI `--name`, and deterministically allocate the next never-reused `<TICKET>[_N]` branch from local refs, catalogue history, and current `origin` heads. The public SDK's exact-branch checkout input and `EnvironmentCheckoutOptions.name` remain compatible.
- Persist a secret-free, field-specific snapshot of successfully applied environment settings and expose read-only `in_sync|drifted|unknown` diagnostics through `doctor` and the future `env show` projection.
- Exclude `.localhost` browser-session isolation from implementation; it remains separate research.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `development-environment`: Require owned-runtime validation, direct retryable COPY cleanup, CLI Jira branch allocation inputs, and field-specific applied-settings evidence.
- `cli-odcli`: Define canonical compatible aliases, Jira-key checkout, drift-aware doctor output, selected-environment replacement restore, and safe stop entry points.
- `command-execution`: Preserve dry-run invocation provenance in the shared failure envelope.
- `environment-monitor`: Select and label one platform-correct process-tree memory value across Rich and machine output.
- `database-restore`: Safely replace the database and filestore of an existing stopped COPY environment while preserving environment identity and recoverable failure state.
- `instance-runtime-binding`: Resolve and stop only the persisted live process identity owned by the selected environment.

## Impact

The authoritative base is `origin/main` at `f0eb29a9a96ad4a62c4113d9bf2acf7b4c14c4cf`, whose second parent `344c7be6bda0873ae8423061d837babcbec15e9a` is the final MYL-121 result. MYL-121's project scoping, Rich/progress, endpoint, worktree-path, module, Git-baseline, socket, catalogue and output-boundary changes are baseline behavior and are not reimplemented here.

Affected areas include Click registration/help, shared failure envelopes, environment checkout/sync/removal, Git ref inspection, catalogue schema and doctor projections, direct PostgreSQL ownership/drop integration, retained-backup restore coordination, runtime identity validation/termination, process metrics, generated schemas, README, and focused contract/regression tests. Existing lifecycle, context resolution, catalogue, lock ordering, process execution, preparation/restore, compensation, redaction, confirmation, output and `PUBLIC_LEAF_CASES` boundaries remain authoritative; no Jira client/configuration, branch registry/counter, generic diff engine, watcher, dependency resolver, lifecycle/restore coordinator, process supervisor, renderer hierarchy, second command inventory, or dependency is added.
