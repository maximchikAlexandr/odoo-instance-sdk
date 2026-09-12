## ADDED Requirements

### Requirement: Optional msgfmt validation step
Translation export planning SHALL resolve `msgfmt` once with `shutil.which`; when present it SHALL capture the absolute executable and a process step equivalent to `msgfmt --check --statistics -o /dev/null -`, pass exported PO bytes on stdin with sanitized `LC_ALL=C`, and execute validation before existing atomic publication. When absent, export SHALL omit validation without failing. [Source: GH#54]

#### Scenario: Validator is available
- **WHEN** planning resolves an absolute `msgfmt` executable
- **THEN** dry-run and execution use the same captured executable, argv, environment, and stdin-producing export snapshot
- **AND** dry-run performs neither export nor file publication

#### Scenario: Validator is unavailable
- **WHEN** `msgfmt` is absent from PATH
- **THEN** no validation step or passed claim is emitted and existing export behavior remains unchanged

### Requirement: Validation result and publication safety
Successful validation SHALL expose bounded GNU output without parsing a second statistics model; non-zero validation SHALL fail with bounded diagnostics and preserve any existing destination, while successful warnings SHALL remain visible. Rich, JSON, and TOON SHALL carry equivalent validation meaning. [Source: GH#54]

#### Scenario: Validation succeeds with statistics
- **WHEN** `msgfmt` exits zero and emits warnings or statistics
- **THEN** the result identifies tool `msgfmt`, status `passed`, and bounded verbatim tool output before the destination is atomically replaced

#### Scenario: Validation fails
- **WHEN** `msgfmt` rejects generated PO bytes
- **THEN** export fails and an existing destination remains byte-for-byte unchanged
