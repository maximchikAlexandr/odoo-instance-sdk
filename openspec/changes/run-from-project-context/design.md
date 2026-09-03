## Context

`commands.context.ready_instance()` currently resolves only a `DevelopmentEnvironment`, validates environment-owned files, and calls `InstanceFactory.from_environment()`. The main checkout already has a typed `ProjectConfig` containing runtime paths and defaults, but no equivalent instance factory path. See the proposal and delta specs for the required behavior.

## Goals / Non-Goals

**Goals:**

- Represent the resolved runtime source explicitly as environment or project.
- Reuse one resolver and one instance-construction boundary across instance commands.
- Build project instances from existing typed configuration without catalogue writes.
- Preserve existing command, process, output, and redaction contracts.

**Non-Goals:**

- Registering the main checkout as an environment.
- Changing environment lifecycle or catalogue schema.
- Auto-discovering arbitrary Python/Odoo binaries outside project configuration.
- Adding a compatibility layer or a new dependency.

## Decisions

### Use a small discriminated resolved-context value

Introduce one internal immutable value that contains the client, constructed instance, and exactly one source: `DevelopmentEnvironment` or `ProjectConfig`, plus provenance. The shared resolver applies explicit environment, exact worktree, then project precedence. Commands that need environment state narrow this value through one helper that raises the standard actionable resolution error.

This keeps the distinction visible and prevents optional environment fields from spreading through command bodies. Alternatives rejected: manufacturing a `DevelopmentEnvironment` (incorrect lifecycle semantics), or separate per-command project fallbacks (duplicated precedence and validation).

### Add `InstanceFactory.from_project(ProjectConfig)`

The factory validates and resolves project paths relative to the repository root, parses the referenced Odoo config through existing config helpers, binds the project PostgreSQL cluster, and constructs the same `InstanceConfig`/`StartConfig` consumed by `OdooInstance`. Project defaults are captured into the instance's start configuration so later command construction follows the existing process boundary.

Alternative rejected: translate project configuration into a temporary generated environment config, because it introduces unnecessary files and environment assumptions.

### Bind invocation-specific database and port without rewriting source config

Where project fields override the source Odoo config, construct the effective immutable start configuration in memory. Do not modify the user's Odoo config or create a generated environment config. Existing protected-override validation remains authoritative for caller passthrough arguments.

### Keep environment use metadata conditional

The run callback records use only when the resolved context contains an actual environment. Project runs have no catalogue identity and therefore perform no analogous write. Port checking reads the effective instance binding rather than requiring an environment model.

### Migrate commands through the shared resolver

First replace `ready_instance()` with the typed runtime-context resolver, then adapt project-capable commands. Commands whose behavior relies on generated config, retained logs, or other environment-owned artifacts must explicitly reject project context until their requirements define project behavior; they must not silently reconstruct it.

## Risks / Trade-offs

- [Project manifests may reference stale paths] → Validate required paths and configuration before building a process command and return sanitized field-specific errors.
- [Resolution precedence could change existing worktree behavior] → Characterize explicit environment and exact-worktree cases before adding project fallback.
- [Commands may accidentally assume environment metadata] → Make source narrowing explicit and cover every instance command with environment/project tests.
- [Project and environment command construction may drift] → Both factories produce the same `OdooInstance` execution abstractions and share protected-argument validation.

## Migration Plan

No persisted-data migration is required. Release the new factory and shared resolver together, update commands and documentation, then run unit, integration, type, lint, and OpenSpec validation gates. Rollback is a code revert; existing manifests and environment catalogue data remain compatible.
