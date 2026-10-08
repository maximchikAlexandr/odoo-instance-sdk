## Context

`EnvironmentMonitor.processes_command()` currently prepares one outer `monitor.processes` action. Its callback constructs and runs `snapshot_command()`, then calls `build_process_inventory()` while the outer `RunContext` is active. When the snapshot identifies an attributable database, `_run_pg_stat_activity()` builds a new `run_psql()` specification whose step is absent from the outer command. The shared transport correctly rejects that late step as unplanned.

The repository already supplies all required boundaries: catalogue rows and generated Odoo configs identify the bounded database set and credentials, `build_psql_specification()` creates the exact private/public PostgreSQL pair, `RunContext.process_prepared()` enforces equality and single consumption, `RecordingExecutor` provides deterministic evidence, and `build_process_inventory()` accepts an injected backend runner. The fix therefore belongs at this existing preparation seam.

## Goals / Non-Goals

**Goals:**

- Capture one stable `snapshot_command()` and every eligible backend-attribution `PsqlSpecification` while constructing `processes_command()`.
- Put each attribution specification's exact prepared step in the outer private command and its redacted projection in the outer public plan.
- Consume a captured attribution specification once through the existing PostgreSQL/shared-process transport and map bounded failures to the existing typed backend degradation model.
- Preserve the one-snapshot `ProcessInventory` and unchanged Rich, JSON, and TOON projections.

**Non-Goals:**

- Changing public SDK signatures, `ProcessInventory` fields, CLI rendering, catalogue schema, PostgreSQL ownership rules, or the snapshot collector.
- Disabling plan validation, moving PostgreSQL launches outside `internal/proc`, adding a runner/provider framework, or sampling arbitrary postgres processes.
- Suppressing PostgreSQL attribution as a workaround.

## Decisions

### Capture catalogue credentials and the snapshot command at command construction

`processes_command()` will construct `snapshot_command(project_id=..., include_removed=False)` exactly once before defining the outer callback. The existing credential helper will be narrowed to return a deterministic frozen tuple of `DatabaseCredentials`, deduplicated by database from the same selected catalogue scope, instead of returning an opaque resolver that is first created during execution. The callback will reuse these captured values; it will not reopen configs or rebuild the snapshot command.

This keeps command behavior stable after construction and exposes the complete bounded database set early enough to prepare its process steps. The alternative—planning after the snapshot has executed—cannot satisfy the immutable ledger. Planning a generic wildcard step is rejected because a `PreparedStep` must preserve exact argv, environment, timeout, and secret-bearing private inputs.

### Reuse one exact `PsqlSpecification` per eligible database

For every captured credential record with a usable database user, construction will call the existing `build_psql_specification()` with the existing bounded query, maintenance database, five-second timeout, stable `process_inventory.<project_id>.<database>.pg_stat_activity` step id, and `_require_binary=False`. The builder already defines this deferred-capability contract: when `resolve_psql_executable()` returns no path, it still produces an inspectable exact `PreparedStep` whose executable is the literal `psql`, while preserving the complete captured argv, sanitized environment snapshot, timeout, password-bearing private overrides, and redacted public projection. It therefore does not raise `FileNotFoundError` during `processes_command()` construction. The resulting specifications will be sorted by database and appended after the `monitor.processes` action in the outer prepared-step tuple; the public `ExecutionPlan` will be derived from that exact tuple.

The callback will pass `build_process_inventory()` both a resolver over the frozen credentials and a concrete backend runner over the captured specification map. That runner will call `execute_psql(specification)`, convert the existing `ProcessResult` to the current `(returncode, stdout, stderr)` attribution input, and never call `run_psql()` or rebuild a specification. If the deferred literal `psql` cannot be spawned, the shared `SubprocessExecutor` raises `ProcessSpawnError`; if the bounded child exceeds its timeout, it raises `ProcessTimeoutError`; if either finite per-stream or combined output limit is exceeded, it raises `ProcessOutputLimitError` after terminating and reaping the child. The concrete runner catches only these three concrete process-boundary exceptions and maps them to the existing attribution classification inputs for `psql_missing`, `timeout`, and `query_failed`, respectively. Non-zero completed results flow through `_classify_backend_failure()`, and invalid successful payloads flow through the parser. `UnplannedStepError` and `DuplicateStepError` are not caught because they are ledger invariants, not availability outcomes. Missing credentials or a database absent from the captured map produces the existing typed unavailable result without launching a child. `context.skip_remaining()` remains responsible for explicitly closing planned steps that the final snapshot does not need.

This reuses the canonical PostgreSQL builder, executor, redaction, and ledger equality checks. A second transport or a generic planning abstraction would duplicate security-sensitive behavior and is therefore excluded.

### Make bounded failure classification complete at the inventory seam

The captured runner and attribution parser will convert `ProcessSpawnError`, `ProcessTimeoutError`, `ProcessOutputLimitError`, non-zero results, privilege/authentication/connectivity failures, and malformed successful output into the existing `BackendUnavailabilityReason` vocabulary. In particular, failed spawn of the captured literal or resolved executable maps to `psql_missing`, timeout maps to `timeout`, output-limit termination maps to `query_failed`, malformed JSON or a non-list payload maps to `invalid_response`, and other non-zero results continue through `_classify_backend_failure()`. These outcomes return `_BackendAttributionResult` values; they do not escape as whole-command failures. The runner SHALL NOT catch the base exception broadly; it catches only the three concrete process-boundary outcomes defined above.

The ledger exceptions `UnplannedStepError` and `DuplicateStepError` remain invariant failures and will not be swallowed. Regression tests must prove they are absent on the supported path rather than hiding them as degradation.

### Prove behavior at the public command boundary

Focused internal tests will retain parser and classification coverage. The principal regression will construct `EnvironmentMonitor.processes_command()` from a temporary catalogue/config fixture and execute it with `RecordingExecutor`: one case returns valid `pg_stat_activity` JSON and asserts the exact planned step was consumed once; paired non-zero and malformed-success results fail only one of two backend groups while preserving the healthy group, a running Odoo fact, and both planned executions. A dedicated public-boundary case will make `resolve_psql_executable()` return `None`, prove command construction still succeeds with an inspectable `psql` step, then make the injected executor raise `ProcessSpawnError` for that exact captured step and assert a successful inventory whose affected group reports `psql_missing`. A paired timeout case raises `ProcessTimeoutError`, and an output-limit case raises `ProcessOutputLimitError` and reports `query_failed`. These cases assert that ledger exceptions still propagate. Existing CLI output-mode inventory tests will prove that the result still follows the single SDK command and bounded output path without adding format-specific collection.

## Risks / Trade-offs

- **Catalogue state changes between capture and run** → the command intentionally keeps its captured database/credential boundary; a database appearing later is reported unavailable for that command and is picked up by the next freshly constructed command, matching immutable-command semantics.
- **The snapshot needs fewer databases than the catalogue capture** → unused attribution steps remain inspectable and are explicitly skipped; no extra query runs.
- **Passwords are captured in private process state** → the existing `PsqlSpecification` sanitized environment and public redaction remain the only projection path; tests assert the secret is absent from the plan, fingerprint, results, and error text.
- **`psql` is absent when the command is built** → `_require_binary=False` deliberately records literal `psql` as the exact deferred-capability executable; failed spawn is converted to `psql_missing`, while the plan remains inspectable and no late specification is constructed.
- **Failure handling could hide ledger defects** → only process/response failures map to typed degradation; unplanned and duplicate consumption exceptions remain fatal test failures.

## Migration Plan

No data or public API migration is required. Deliver the production seam and focused regressions in one change, run strict type/lint/unit/architecture checks, and roll back the single implementation commit if necessary; stored catalogue data and serialized inventory documents remain compatible.

## Open Questions

None. The existing process boundary, attribution query, typed reasons, and single-snapshot contract determine the implementation.
