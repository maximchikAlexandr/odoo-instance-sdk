## ADDED Requirements

### Requirement: Translation export plans final publication consequences

Translation export SHALL expose the applicable module `i18n` directory and final `.po` or `.pot` publication target in its existing execution plan. It SHALL distinguish directory/file creation from replacement, preserve export then optional `msgfmt` validation then atomic publication order, and SHALL NOT expose temporary files. The planned target SHALL be reused after Odoo resolves language metadata; if the exact ISO-derived filename cannot be proven during planning, the plan SHALL expose the known directory and uncertainty rather than equating `--language` with the filename.

#### Scenario: New PO file
- **WHEN** Odoo resolves an ISO language name whose final `.po` path does not exist
- **THEN** the plan and result identify creation of that file after successful export and any applicable validation

#### Scenario: Existing PO file
- **WHEN** the resolved final `.po` path already exists
- **THEN** the plan identifies replacement, warns that the existing translation will be replaced, and publication remains atomic

#### Scenario: POT export
- **WHEN** template export selects a `.pot` destination
- **THEN** the plan identifies the concrete `.pot` creation or replacement and does not claim a `.po` target

#### Scenario: ISO name is unresolved during preview
- **WHEN** the Odoo ISO language code cannot be obtained without execution and can differ from `--language`
- **THEN** preview identifies the module `i18n` directory, the reason the final name is unknown, and the condition that resolves it without fabricating a filename

#### Scenario: Msgfmt is available
- **WHEN** `msgfmt` is captured for validation
- **THEN** plan order is export, validation, publication and a failed validation leaves the prior destination unchanged

#### Scenario: Msgfmt is unavailable
- **WHEN** `msgfmt` is not available
- **THEN** the plan omits validation, retains export then publication order, and reports the skipped optional check without treating it as passed
