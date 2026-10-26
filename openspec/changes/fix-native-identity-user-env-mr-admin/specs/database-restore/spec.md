## ADDED Requirements

### Requirement: User-level administrator password fallback
Every noninteractive administrator-password reset used by `db reset-admin-password`, init, restore, refresh, and replacement SHALL resolve `ODCLI_ADMIN_PASSWORD` through the unified process > selected-project dotenv > user dotenv precedence. Interactive confirmed input SHALL retain priority in the existing eligible Rich path. The resolved secret SHALL be passed only to the existing reset operation and SHALL NOT appear in argv, Git configuration, catalog records, plans, output, logs, diagnostics, or generic child environments.

#### Scenario: User-only password resets administrator
- **WHEN** only `<ODCLI_USER_ROOT>/.env` defines nonblank `ODCLI_ADMIN_PASSWORD` and a noninteractive applicable reset reaches its reset step
- **THEN** the reset uses that password and reports only safe environment provenance

#### Scenario: Project overrides user password
- **WHEN** selected project and user dotenv files define different administrator passwords and no process value exists
- **THEN** reset uses the project value and neither value is disclosed

#### Scenario: Process overrides both files
- **WHEN** process, selected project, and user sources all define the administrator password
- **THEN** reset uses the process value and does not propagate lower-precedence values

#### Scenario: Dry-run and missing secret remain non-mutating
- **WHEN** dry-run is requested or no source supplies a nonblank password
- **THEN** no prompt or reset mutation occurs and the missing-secret path retains `admin_password_required`
