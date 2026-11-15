# Spec Delta

## ADDED Requirements

### Requirement: Optional auto-translate after export
`odcli translations export` SHALL accept `--auto-translate`, repeatable `--context`, and `--model`. Without `--auto-translate`, export SHALL keep today's catalog bytes, validation, and result shape, and `--context` or `--model` SHALL be a usage error. With `--auto-translate`, export SHALL finish the existing export successfully and only then fill missing entries in the catalogs that export just wrote. The flag SHALL stay visible when `odcli-codex` is not installed. Invoking it in that case SHALL fail before any further mutation with a message that contains `uv tool install --with-executables-from odcli-codex odoo-instance-sdk`. The extension SHALL NOT start Odoo, re-export catalogs, or replace core serialization.

#### Scenario: Export without the flag is unchanged
- **WHEN** `translations export` runs without `--auto-translate`
- **THEN** the written catalogs and validation evidence match the export-only command
- **AND** no Codex package is imported

#### Scenario: Missing extension fails with an install hint
- **WHEN** `--auto-translate` is used and `odcli-codex` cannot be imported
- **THEN** the command exits non-zero
- **AND** the error contains `uv tool install --with-executables-from odcli-codex odoo-instance-sdk`
- **AND** catalogs already written by the successful export remain in place

#### Scenario: Successful export then fills only missing entries
- **WHEN** export succeeds and `--auto-translate` is set with the extension installed
- **THEN** each exported catalog is filled under the translation-fill rules
- **AND** the result still identifies the exported files and their validation
