# Spec Delta

## ADDED Requirements

### Requirement: One description source for Odoo commit
`odcli git commit` SHALL accept exactly one description source: a positional English line, `-` to read one line from stdin, or `--generate`. `--context` and `--model` SHALL be valid only together with `--generate`. Generated and piped descriptions SHALL be passed into the same immutable commit command used for a positional description. Preview and execution SHALL NOT rebuild that Git command after the description is accepted. `--generate` SHALL stay visible without `odcli-codex` and SHALL fail with the `uv tool install --with-executables-from odcli-codex odoo-instance-sdk` hint when the package is missing. The model SHALL NOT choose the prefix, module, ticket, or final commit message.

#### Scenario: Generated and piped descriptions share one commit message
- **WHEN** the same staged project, ticket, and description text are committed once with `--generate` and once by piping that description as `-`
- **THEN** both plans use the same final commit message and the same commit argv
- **AND** neither plan asks the model to emit the prefix or ticket

#### Scenario: Multiple description sources are rejected
- **WHEN** a positional description is combined with `--generate` or with `-`
- **THEN** the command fails before planning a commit

#### Scenario: Generate without the extension
- **WHEN** `--generate` is used and `odcli-codex` cannot be imported
- **THEN** the command fails before `git commit` and the error contains the `--with-executables-from odcli-codex` install hint

### Requirement: Read-only staged patch snapshot
Commit-description generation SHALL read staged patch text from one read-only public Git snapshot. That snapshot SHALL redact configured secrets, bound the returned text, and SHALL NOT stage, unstage, or commit. The public plan for that read SHALL NOT include the patch body. Dry-run of description generation SHALL NOT call Codex.

#### Scenario: Patch read does not mutate the index
- **WHEN** description generation inspects a staged change and then stops at dry-run
- **THEN** `git status` and the index tree are unchanged
- **AND** the public plan does not contain the patch body
