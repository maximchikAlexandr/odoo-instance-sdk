## Context

`main` already has the MYL-55 typed CLI/output foundation, one immutable command/executor boundary under `internal/proc`, `ResolvedContext` with environment-or-project instances, `InstanceFactory.from_project()`, and project-context `odcli run`. The remaining GitHub #53 items expose places that still narrow immediately to `DevelopmentEnvironment` (`commands/test.py`, `module_update`, `internal/vscode_generate.py`), render implementation-heavy plans (`commands/output.py`), reject a port before creating a previewable command (`cli.run`), derive monitor projects only from environment catalogue rows (`resources/monitor.py`), and parse eval results from one combined Odoo-shell stdout stream.

The current SQLite catalogue persists only `environment_runtime`, so it cannot durably discover or own a default-checkout runtime. The root help also uses a one-off `_RichHelpGroup`, while nested Click help and validation errors remain unstyled. Database deletion exists at the Odoo database-manager layer, but #53 requires a stronger cluster-bound CLI workflow with PostgreSQL session checks and default-database protection.

This change crosses CLI, SDK execution, SQLite schema, HTTP/OpenAPI, and React boundaries. It must preserve the repository's single-runner, typed-output, no-synthetic-environment, secret-redaction, and lightweight-startup rules. Destructive verification is restricted to disposable clusters.

## Goals / Non-Goals

**Goals:**

- Close all still-open acceptance criteria in GitHub #53 with explicit, testable behavior.
- Reuse the existing environment-or-project resolution and immutable command model.
- Make human progress and dry-run output useful without weakening machine contracts.
- Persist, monitor, serialize, and render project-owned runtimes as first-class typed state.
- Keep destructive database behavior fail-closed and auditable.

**Non-Goals:**

- Creating synthetic development environments for initialized projects.
- Adding another process runner, renderer hierarchy, monitor endpoint, DTO tree, or generic service layer.
- Changing normal raw-stream behavior of `run`, interactive `shell`, `psql`, or followed logs.
- Searching arbitrary parent directories for dotenv files, evaluating shell syntax, or exporting secrets globally.
- Running destructive acceptance checks against a user's working instance or database.

## Decisions

### 1. Extend `ResolvedContext` into the single owner-neutral command input

Project-capable callbacks will consume the already resolved `ResolvedContext` and an owner-neutral runtime view containing repository/worktree root, `StartConfig`, database, HTTP binding, Python/Odoo prefix, base-ref provenance, and exclusive owner identity. Environment-only lifecycle commands will continue to call `require_environment()`.

`commands/test.py`, module update, and VS Code profile construction will accept this view instead of reaching back into the catalogue or branching into separate implementations. Result documents will expose `owner_kind`, `project_id`, nullable environment identity, and common worktree/runtime fields. Explicit `--env` precedence stays in the existing resolver.

Alternative considered: construct a temporary `DevelopmentEnvironment` for the default checkout. Rejected because it corrupts environment lifecycle semantics and directly violates the existing context contract.

### 2. Load `.odcli/.env` after project resolution with a conservative parser

A small internal loader will inspect exactly `<resolved-root>/.odcli/.env`, verify owner-only POSIX permissions where meaningful, parse UTF-8 `KEY=VALUE` assignments with quoted/unquoted values, and merge them underneath an immutable snapshot of `os.environ`. It will reject malformed syntax, interpolation, command substitution, NULs, and unreadable files. The merged mapping is passed only to code that explicitly needs it; it is not written back to global `os.environ`.

The existing `captured_child_environment()`/`sanitized_child_environment()` boundary remains authoritative for which values may reach children. `ODCLI_TEST_MASTER_PASSWORD` is consumed by restore coordination but stripped from unrelated child environments and every public projection. Init/doctor documentation and tests will ensure `.odcli/.env` is ignored and owner-readable only.

Alternative considered: add `python-dotenv`. Rejected because the required grammar is deliberately small and shell-like expansion would increase both dependency and secret-handling surface.

### 3. Add observation to the existing command executor, not a second execution path

`Command.run()`/`RunContext` and the production process executor will accept an optional typed observer. It receives logical step start/completion/failure events and, only when enabled, sanitized stdout/stderr chunks tagged with `step_id`. The executor still owns spawn, capture, exit status, and cleanup; the final `CommandResult` remains unchanged. Restore passes the observer only for interactive Rich progress or explicit command streaming. Machine formats run silently and emit one final document.

Alternative considered: rerun commands through a Rich-specific subprocess helper. Rejected because it would split preview/execution parity and process ownership.

### 4. Keep one plan; add semantic summary fields and precondition observations

Execution plans will carry typed semantic observations for goal, target, mutation, precondition status, and warning. `_rich_plan_projection()` will render only these decision fields and collapse related internal probes. JSON/TOON continue serializing the complete existing plan, including redacted exact process steps and fingerprint.

Port availability moves into captured run planning as a precondition observation. Preview returns a plan even when the observation fails; `.run()` revalidates and refuses before spawn. The same mechanism supports database existence/default/session preconditions.

Alternative considered: command-specific Rich dry-run renderers. Rejected because #53 explicitly requires a shared projection and divergence would grow with each command.

### 5. Implement database drop as a cluster-bound inspectable operation

The database resource will expose a command sibling built from the existing PostgreSQL specification/transport. Planning resolves the project cluster and exact quoted identifier, rejects PostgreSQL system/template names, reads configured-default status, and queries active sessions. Execution revalidates those observations, optionally terminates only sessions attached to the exact target, issues `DROP DATABASE` through a maintenance database, verifies absence, and then records the catalogue event/reconciliation.

Confirmation and force policy stays in the CLI after full plan construction: `--yes` controls consent, `--force-default` permits the protected configured database, and `--force-connections` permits session termination. Credentials use the existing child environment/private plan projection, never argv.

Alternative considered: call Odoo's `/web/database/drop`. Rejected because it cannot provide the required project-cluster identity and active-session guarantees and depends on a running Odoo endpoint.

### 6. Replace custom root formatting with bounded `rich-click` configuration

`rich-click` becomes a bounded core dependency. The root and nested groups use its Click-compatible command classes/configuration; `_RichHelpGroup` is removed. Metadata decorators provide missing one-line descriptions and a small stable panel map. Result emission and Rich `Table`/`Live` output remain in existing modules. Tests pin startup imports, completion, errors, redirects, widths, and representative help pages.

Alternative considered: expand `_RichHelpGroup` into a custom formatter for every command. Rejected because #53 excludes a custom `HelpFormatter` and would duplicate Click's validation/help behavior.

### 7. Register initialized projects and migrate runtime storage to exclusive owners

The catalogue receives a minimal durable `projects` registration keyed by the canonical repository/git identity already used for monitor project IDs, plus a generalized runtime table with `owner_kind` and `owner_id` uniqueness. Migration backfills project registrations from existing environments and copies `environment_runtime` rows as environment-owned records before retiring the old table. Project resolution/init upserts registration; it does not create an environment or lifecycle event.

`from_environment()` and `from_project()` attach the same private runtime-binding type with different owners. Foreground lifecycle upserts/clears through one catalogue API. Monitor planning begins from registered projects, joins optional environments and both runtime owner kinds, and reuses current process, readiness, Git, storage, and PostgreSQL collectors. `ProjectSummary` gains an additive nullable project-runtime field; environment snapshot fields stay compatible. OpenAPI is regenerated from msgspec, then the generated TypeScript client and React project card render the new field.

Alternative considered: scan the filesystem on every monitor request. Rejected because it is nondeterministic, unbounded, and cannot provide a reliable global project inventory.

### 8. Frame eval user output inside the existing shell payload

The eval wrapper will redirect user-code stdout to an in-memory buffer and emit one nonce-framed typed payload containing `result`, `user_stdout`, and structured `user_error`. Startup stdout remains outside the payload. Exception serialization keeps type, message, and bounded traceback/source context; truncation removes oldest unrelated startup material first and records a flag. The CLI maps this payload into Rich and machine envelopes without raw writes.

Alternative considered: infer user output by subtracting known startup lines. Rejected because Odoo startup logs vary and caused the reported diagnostic loss.

## Risks / Trade-offs

- **[Large cross-cutting scope]** → Implement and review as ordered vertical slices with contract tests after each slice; do not combine schema, UI, and destructive DB behavior in one unverified step.
- **[SQLite migration could lose runtime state]** → Use a transactional versioned migration, backfill first, assert exclusive ownership, and retain migration fixtures from pre-change schemas.
- **[Streaming can leak secrets across chunk boundaries]** → Redact through the existing projection before callbacks and test secrets split across chunks; never enable raw chunks in machine modes.
- **[Preview observations become stale]** → Revalidate every safety-critical precondition immediately before mutation and fail closed on disagreement.
- **[`rich-click` may increase startup cost or alter completion]** → Keep imports at the Click registration boundary and retain the existing lightweight-startup and command-tree characterization gates.
- **[Project test defaults may be ambiguous]** → Fail unless an explicit `--base` or non-`HEAD` configured checkout base exists; never guess a remote branch.
- **[Project registry can retain deleted paths]** → Treat missing manifests as unavailable sanitized project state and provide deterministic cleanup only in a separately authorized change; do not silently delete registrations during reads.

## Migration Plan

1. Add dependency/lock changes and help characterization without changing command result rendering.
2. Add the owner-neutral runtime input and move VS Code/module/test paths onto it.
3. Add dotenv loading at resolved-project construction and security tests.
4. Extend plans/observer support, then adopt it for Rich dry-runs, port preconditions, and restore progress.
5. Add guarded database-drop SDK/CLI behavior and verify mutations only on disposable PostgreSQL clusters.
6. Apply the transactional catalogue migration and owner-neutral foreground persistence; verify old environment runtime fixtures still load.
7. Extend monitor models/collector, HTTP schema/codegen, and dashboard rendering.
8. Add eval payload framing and output regressions, then run full Python, frontend, architecture, build, and disposable integration suites.

Rollback of application code is safe after restoring the pre-migration catalogue backup. Because the schema migration replaces runtime storage, deployment tooling SHALL copy the SQLite catalogue before migration; rollback SHALL restore that copy rather than asking older code to read the new schema.

## Open Questions

None. The externally visible flag names, project changed-test fallback, runtime ownership representation, dotenv failure policy, and destructive verification boundary are fixed by these artifacts.
