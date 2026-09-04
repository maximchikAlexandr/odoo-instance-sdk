## MODIFIED Requirements

### Requirement: Context-aware command resolution

Instance commands MUST resolve one runtime context in this order:

1. An explicit `--env SELECTOR`; failure to resolve it MUST be terminal and MUST NOT fall back.
2. The exact registered worktree containing the current directory.
3. An explicit `--project PATH`, or otherwise the nearest initialized project manifest found upward from the current directory to the Git/filesystem boundary.
4. Otherwise an actionable context-resolution error.

The first two cases produce an environment context; the third produces a project context. Resolution MUST NOT select an environment by recency or because it is the only ready environment. Project fallback MUST NOT create or catalogue a synthetic environment.

#### Scenario: Explicit environment wins

- **WHEN** an instance command receives a valid explicit `--env` while current directory is inside an initialized project
- **THEN** it uses the selected environment and does not fall back to project context

#### Scenario: Invalid explicit environment does not fall back

- **WHEN** an instance command receives an unknown or ambiguous explicit `--env`
- **THEN** it fails with the environment resolution error before project resolution or runtime work

#### Scenario: Exact worktree wins over project

- **WHEN** an instance command runs inside an exact registered worktree with no explicit environment
- **THEN** project and environment are inferred from that worktree record

#### Scenario: Main checkout uses project context

- **WHEN** `odcli run` executes in an initialized main checkout with no explicit environment and no exact worktree match
- **THEN** it resolves the nearest project manifest and uses project context

#### Scenario: Project is not an environment

- **WHEN** an instance command resolves project context
- **THEN** no environment record is created, selected, or added to `odcli env list`

#### Scenario: Inside registered worktree

- **WHEN** `odcli run` executes inside an exact registered worktree
- **THEN** project and environment are inferred from the worktree record

#### Scenario: Outside worktree without flags

- **WHEN** `odcli run` executes outside an initialized project and registered worktree without `--env` or `--project`
- **THEN** it fails with guidance to initialize/select a project or select/cd into an environment

#### Scenario: Single ready not silently selected

- **WHEN** a project has exactly one ready environment, current directory is not in its worktree, and no `--env` is supplied
- **THEN** that environment is never selected implicitly and project fallback is used only when the project itself is initialized

### Requirement: Environment resolution for instance commands

Instance commands (`run`, `logs`, `shell`, `eval`, `exec`, `test`, `module`, `translations`, `deps verify`, and `vscode generate`) MUST consume the shared `environment | project` resolver. Commands whose required state is available from either context MUST operate on both. A command that requires environment-owned state or lifecycle metadata MUST reject project context with an actionable error and MUST NOT fabricate an environment.

Test target, working-directory, and addon resolution MUST begin only after runtime context is resolved and MUST NOT select a different environment or project.

#### Scenario: Explicit environment precedes addon selection

- **WHEN** `odcli --env <uuid> test sale` runs
- **THEN** environment resolution completes before addon selection

#### Scenario: Project-capable command accepts main checkout

- **WHEN** a project-capable instance command runs under an initialized main checkout without `--env`
- **THEN** it uses the project runtime configuration

#### Scenario: Environment-only command rejects project context

- **WHEN** a command requiring environment-owned artifacts resolves only a project context
- **THEN** it returns an actionable error without catalog mutation or subprocess launch

#### Scenario: Explicit --env

- **WHEN** `odcli --env <uuid> test sale` runs
- **THEN** the environment is resolved from the explicit selector before addon selection

#### Scenario: Ambiguous name

- **WHEN** `odcli --env "feat" test sale` matches two environments
- **THEN** it fails with the candidate list and performs no addon, Git, preflight, project fallback, or Odoo work

### Requirement: `odcli run`

`odcli run` SHALL launch the resolved Odoo runtime from either a ready environment or an initialized project. For project context, it SHALL derive the Python executable, Odoo entry point, source Odoo config, runtime working directory, preferred HTTP port, default database, default run arguments, and project PostgreSQL binding from `.odcli/project.toml` and the referenced config. Missing required runtime fields or files SHALL fail before process construction with a sanitized actionable error.

The command SHALL preserve the existing literal `--` delimiter rule, exact passthrough argument order, protected runtime-identity validation, free-port preflight, dry-run rendering, inherited native streams, foreground process-group cleanup, and exit-code behavior. Project context has no environment use metadata, so it SHALL NOT call `EnvironmentResource.record_use()`; environment context SHALL retain its existing record-use behavior after successful preflight and before execution.

#### Scenario: Project run needs no runtime path arguments

- **WHEN** `odcli run` executes from an initialized main checkout whose manifest references valid Python, Odoo entry point, and config
- **THEN** it constructs and launches the foreground command without requiring those paths as CLI arguments

#### Scenario: Project defaults and passthrough compose deterministically

- **WHEN** project context defines default run arguments and the caller supplies allowed arguments after `--`
- **THEN** the captured command contains project defaults followed by the exact caller arguments in their original order

#### Scenario: Project dry-run has no side effects

- **WHEN** `odcli run --dry-run` resolves project context
- **THEN** it emits the bounded execution plan without starting Odoo, mutating the environment catalogue, or writing use metadata

#### Scenario: Environment run retains metadata behavior

- **WHEN** `odcli run` resolves a ready environment and the port preflight succeeds
- **THEN** it records environment use exactly once before executing the captured foreground command

#### Scenario: Native process contract is context-independent

- **WHEN** an environment-based or project-based foreground run exits non-zero or is interrupted
- **THEN** native streams are preserved, the actual exit code is returned, and interrupt cleanup returns exit `130`

#### Scenario: Port conflict deterministic error

- **WHEN** `odcli run -- --dev=reload` finds the effective bound port occupied
- **THEN** it returns `port-conflict` with ownership unknown and performs no foreground command construction, use update, config change, or process launch

#### Scenario: Free port starts Odoo

- **WHEN** `odcli run -- --dev=reload --log-level debug --dev=xml` finds the effective port free
- **THEN** it captures `run_foreground_command` once with the exact delimiter arguments and executes that captured command

#### Scenario: Delimiter is required for native arguments

- **WHEN** a caller invokes `odcli run --dev=reload` without the `--` delimiter
- **THEN** Click reports an unknown-option usage error with exit code `2` before SDK resolution or launch

#### Scenario: Bare positional input is rejected

- **WHEN** a caller invokes `odcli run sale` without a literal `--`
- **THEN** the command reports a usage error with exit code `2` and performs no SDK resolution, use update, command construction, or launch

#### Scenario: Protected override is rejected before spawn

- **WHEN** `odcli run -- --database other` or another protected runtime-identity override is invoked
- **THEN** the SDK validator returns a sanitized error before command execution and no child process starts

#### Scenario: Dry-run and execution use one captured argv

- **WHEN** the same allowed delimiter arguments are supplied to dry-run and normal execution under a recording executor
- **THEN** dry-run displays the exact captured foreground step and normal execution consumes it without reconstructing argv

#### Scenario: Native TTY and exit behavior remain unchanged

- **WHEN** `odcli run -- --workers=2` executes normally, exits non-zero, or is interrupted
- **THEN** inherited stdin/stdout/stderr remain native, the real exit code is returned, and interrupt cleanup preserves exit `130`

### Requirement: Instance commands share one ready path

Project-capable instance commands MUST obtain the client, resolved `environment | project` context, and `OdooInstance` through one shared internal path. Command bodies MUST NOT duplicate context precedence, runtime verification, instance construction, or client construction. Environment-only commands MUST narrow the shared result explicitly and reject project context.

Port preflight remains specific to `run`.

#### Scenario: Eval and run share resolve

- **WHEN** `odcli eval 1` and `odcli run` execute under the same supported context
- **THEN** both resolve that context through the shared path rather than command-specific helpers

#### Scenario: Lifecycle remains environment-only

- **WHEN** an environment lifecycle operation is invoked from only the main project checkout without an environment selector
- **THEN** it does not treat the project as a development environment

## ADDED Requirements

### Requirement: Project restore postconditions use the database authority

When `odcli db refresh --restore` targets a project PostgreSQL cluster, existence checks before and after restore MUST query that PostgreSQL endpoint directly when a PostgreSQL probe is available. The checks MUST NOT infer absence solely from the running Odoo database-manager list because an Odoo process constrained by `--database` can omit a newly restored database. An inconclusive PostgreSQL probe MUST fail closed rather than silently converting the result into confirmed absence.

#### Scenario: Running Odoo is restricted to the previous database

- **WHEN** restore creates the target in the project PostgreSQL cluster but `/web/database/list` only returns the database selected when Odoo started
- **THEN** the post-restore check confirms the target through PostgreSQL and the refresh proceeds to its remaining steps

#### Scenario: Direct probe confirms absence

- **WHEN** the planned PostgreSQL post-restore probe completes successfully with no matching database
- **THEN** restore fails with the retained-backup and retained-database safety context
