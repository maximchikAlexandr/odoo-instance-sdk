## Why

An initialized project already records enough Odoo runtime information to launch its main checkout, but instance commands currently require a catalogued development environment. This makes `odcli run` unusable from the primary repository unless the user creates an unrelated worktree environment or repeats runtime paths on the command line.

## What Changes

- Resolve instance command context from either a development environment or the nearest initialized project.
- Allow `odcli run` to launch the main project checkout using runtime values stored in `.odcli/project.toml`.
- Keep explicit environment selection and exact worktree matching higher priority than project fallback.
- Keep environment lifecycle commands environment-only; a project context is not a synthetic environment and does not appear in the environment catalogue.
- Preserve command passthrough, dry-run output, redaction, process ownership, and exit-code behavior for project-based runs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-odcli`: Instance commands gain shared `environment | project` context resolution, including project-based `odcli run` behavior and explicit rejection by environment-only operations.
- `client-config`: The client can construct an Odoo instance directly from initialized project configuration without creating an environment record.

## Impact

- Affects CLI context resolution, instance construction, `odcli run`, and other commands that currently assume a `DevelopmentEnvironment`.
- Adds tests for resolution precedence, main-checkout launch, environment-only rejection, dry-run output, argument forwarding, and secret redaction.
- Updates user documentation for context-dependent instance startup.
- Does not change the environment catalogue schema, create worktrees, or add dependencies.
