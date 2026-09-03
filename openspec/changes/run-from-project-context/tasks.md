## 1. Characterize Context Contracts

- [x] 1.1 Add resolver tests for explicit environment, exact worktree, explicit project, nearest project, and missing context precedence; verify invalid explicit environments never fall back.
- [x] 1.2 Add characterization tests for environment-based `run` command capture, dry-run, record-use timing, native exit codes, and interruption behavior; verify the focused CLI tests pass unchanged.

## 2. Construct Instances from Projects

- [x] 2.1 Implement `InstanceFactory.from_project(ProjectConfig)` using existing config, URL, runtime, and PostgreSQL helpers; verify unit tests cover valid construction and every required missing/invalid field.
- [x] 2.2 Resolve project-relative paths and effective immutable runtime defaults without rewriting source config; verify tests cover Python, Odoo entry point, config, cwd, port, database, and default arguments.
- [x] 2.3 Verify project instance construction performs no environment catalogue writes or events with a recording/failing catalogue test double.

## 3. Resolve Shared Runtime Context

- [x] 3.1 Introduce the minimal typed `environment | project` resolved-context value and shared resolver in the CLI context layer; verify precedence and provenance tests pass.
- [x] 3.2 Add one environment-only narrowing helper with an actionable error; verify lifecycle and environment-owned command tests cannot receive a synthetic project environment.
- [x] 3.3 Refactor port preflight to consume the effective instance binding rather than a mandatory environment model; verify occupied/free port tests pass for both context kinds.

## 4. Enable Project-Based Run

- [x] 4.1 Adapt `odcli run` to use the shared runtime context and conditionally record environment use; verify a main-checkout run needs no Python, Odoo-bin, or config CLI arguments.
- [x] 4.2 Preserve literal-delimiter passthrough, protected-override checks, project default argument order, dry-run output, native streams, exit codes, and Ctrl+C cleanup; verify focused integration tests cover both context kinds.

## 5. Align Remaining Instance Commands

- [x] 5.1 Migrate project-capable instance commands to the shared resolver and verify their existing output/exit contracts plus project-context cases.
- [x] 5.2 Make commands requiring environment-owned artifacts reject project context explicitly and verify no catalogue mutation or subprocess launch occurs on rejection.
- [x] 5.3 Verify `odcli env list` does not display the main project checkout and environment lifecycle commands remain environment-only.

## 6. Documentation and Full Verification

- [x] 6.1 Update README/help examples to show context-driven `odcli run` from an initialized main checkout and explicit environment selection; verify examples match the Click help surface.
- [x] 6.2 Run formatting, lint, type checking, focused unit/integration tests, and the repository full test gate; verify all complete successfully.
- [x] 6.3 Run `openspec validate run-from-project-context --strict` and verify the change remains valid after implementation.
