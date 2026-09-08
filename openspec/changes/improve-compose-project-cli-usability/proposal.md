## Why

An imported Compose project currently needs manual manifest, Odoo configuration, and ignore-file repair before project commands are dependable. The CLI also obscures operation identity and exact dry-run commands, mixes project and global catalogue data, and presents several human-facing results in forms that are difficult to inspect or can report false success.

## What Changes

- Allow an explicit non-interactive `init --yes` overwrite while preserving refusal by default and side-effect-free dry-run behavior.
- Generate a project-local Odoo runtime config for Compose projects from the existing config-generation path, bind it to the owned PostgreSQL cluster without publishing its password, preserve the imported source config, and keep local ignore rules inside `.odcli`.
- Give every runtime progress event a stable step identity and concise operation context, while retaining elapsed time, exit status, redaction, and machine-output isolation.
- Scope owned catalogue listings to the resolved project by default, require `--all-projects` for global output, and make Rich list output single-owner, tabular, and human-sized without changing JSON/TOON byte values or schemas.
- Resolve project-bound Odoo endpoints with one precedence rule: explicit command override, then `preferred_http_port`, then `odoo.conf` fallback.
- Make all bounded Rich output human-oriented and make every Rich dry-run display each real sanitized `ProcessStep` command in addition to semantic context.
- Expose stored environment worktree paths through `env list` machine results and a read-only `env path [ENVIRONMENT]` command, without publishing local paths through monitor/FastAPI/dashboard snapshots.
- Repair and verify catalogue environment-child foreign keys while preserving rows and sequential schema versioning.
- Make module updates pass a real module-name list to Odoo, reject empty selection, and fail when the requested modules are not confirmed in the result.
- Measure environment Git activity against each environment's recorded `base_ref`, consistently in recorded and direct collectors, without fetching or mixing uncommitted changes.
- Make the local address probe match an immediate reusable Odoo bind so a recently closed connection does not falsely block module tests while a live listener remains occupied.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-init`: Add explicit overwrite, Compose-owned generated runtime configuration, and project-local ignore behavior.
- `cli-odcli`: Define project-scoped catalogue selection, consistent human rendering, endpoint precedence, worktree-path CLI access, truthful module updates, and exact Rich dry-run command visibility.
- `command-execution`: Require identifiable progress events and ensure the shared Rich projection retains every real sanitized process command.
- `environment-monitor`: Use recorded environment baselines for Git activity while keeping local worktree paths out of the public snapshot.
- `backup-catalog`: Preserve and repair environment child-table foreign keys through the sequential migration contract.
- `local-odoo-testing`: Treat a reusable recently closed loopback address as available without weakening live-listener rejection.

## Impact

Affected areas include project initialization and generated config handling, CLI context/output/renderers, environment and backup catalogue queries, monitor Git probes, Odoo module automation, address probing, catalogue migration tests, command-output contract tests, and user documentation. Existing dependencies and public HTTP/dashboard schemas remain unchanged; no setup wizard, merge engine, renderer framework, second metrics collection, or new configuration abstraction is introduced.
