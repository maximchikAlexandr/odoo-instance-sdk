## ADDED Requirements

### Requirement: Foreground lifecycle persists either owner kind

`OdooInstance.run_foreground_command()` SHALL use the single runtime binding attached by `from_environment()` or `from_project()` to persist and clear the current process identity. This SHALL remain part of the explicit foreground lifecycle and SHALL not apply to manual instances, shell, shell-script, background start, or stop operations.

#### Scenario: Project and environment use one lifecycle
- **WHEN** equivalent foreground commands start from project-owned and environment-owned instances
- **THEN** both use the same spawn/cleanup path and differ only in the exclusive persisted owner identity

### Requirement: Eval transport separates startup logs, user output, and result

The Odoo shell wrapper used by eval SHALL frame captured user stdout separately from startup stdout and the expression result. On user-code failure it SHALL retain the exception type, message, and relevant bounded traceback/source context even after long startup logs; on startup failure it SHALL classify the failure separately. Truncation SHALL be indicated and SHALL preferentially retain the exception and nearby failure context. The existing shell execution boundary, rollback default, non-zero failures, and secret redaction SHALL remain unchanged.

#### Scenario: Print-only eval has a null result
- **WHEN** evaluated code prints Unicode/multiline text and returns no value
- **THEN** the typed result is null and the exact bounded user output is available separately

#### Scenario: Long startup log does not hide exception
- **WHEN** user code raises after startup emitted more data than the diagnostic bound
- **THEN** the diagnostic still contains exception type/message and relevant failure context and marks truncation
