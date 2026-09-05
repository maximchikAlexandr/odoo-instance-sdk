## ADDED Requirements

### Requirement: Foreground lifecycle persists either owner kind

`OdooInstance.run_foreground_command()` SHALL use the single runtime binding attached by `from_environment()` or `from_project()` to persist and clear the current process identity. This SHALL remain part of the explicit foreground lifecycle and SHALL not apply to manual instances, shell, shell-script, background start, or stop operations.

#### Scenario: Project and environment use one lifecycle
- **WHEN** equivalent foreground commands start from project-owned and environment-owned instances
- **THEN** both use the same spawn/cleanup path and differ only in the exclusive persisted owner identity

### Requirement: Eval and exec transport separates startup logs, user output, and results

The Odoo shell wrapper shared by eval and exec SHALL frame captured user stdout separately from startup stdout and the expression/script result. On user-code failure it SHALL retain the exception type, message, and relevant bounded traceback/source context even after long startup logs; on startup failure it SHALL classify the failure separately. Truncation SHALL be indicated and SHALL preferentially retain the exception and nearby failure context. A valid framed user-code exception SHALL map to CLI envelope v1 as `ok=false`, sanitized `error.message`, and `error.details` containing exactly `result=null`, bounded `user_stdout`, non-null structured `user_error`, and boolean `truncated`; top-level `result` and `data` SHALL be absent. The error code SHALL be `eval_user_code_failed` for eval and `exec_user_code_failed` for exec. A non-zero command without a valid framed user-code error SHALL map to `eval_startup_failed` or `exec_startup_failed`, respectively, without fabricated framed details. The existing shell execution boundary, rollback default, non-zero failures, and secret redaction SHALL remain unchanged.

#### Scenario: Print-only eval has a null result
- **WHEN** evaluated code prints Unicode/multiline text and returns no value
- **THEN** the typed result is null and the exact bounded user output is available separately

#### Scenario: Long startup log does not hide exception
- **WHEN** user code raises after startup emitted more data than the diagnostic bound
- **THEN** the failure envelope remains `ok=false`, `error.details.user_error` contains exception type/message and relevant failure context, `error.details.user_stdout` preserves bounded user output, and `error.details.truncated` is true

#### Scenario: Framed user exception and exit status agree
- **WHEN** eval or exec produces a valid framed user-code exception
- **THEN** Rich, JSON, and TOON classify it as failure and the CLI exits `1`
- **AND** machine output never reports `ok=true` for that non-zero user-code outcome

#### Scenario: Exec failure classification is command-specific
- **WHEN** exec produces a valid framed user-code exception or fails before producing one
- **THEN** the envelope uses `exec_user_code_failed` with exact framed `error.details` or `exec_startup_failed` without `error.details`, respectively
