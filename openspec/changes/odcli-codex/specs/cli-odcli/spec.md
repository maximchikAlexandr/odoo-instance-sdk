# Spec Delta

## ADDED Requirements

### Requirement: Optional Codex flags stay on existing leaves
`translations export` and `git commit` SHALL remain the same `PUBLIC_LEAF_CASES` leaves. Their help SHALL show `--auto-translate` and `--generate` respectively even when `odcli-codex` is not installed. Those flags SHALL NOT register new `odcli` subcommands, mutate the Click tree at runtime, or add a plugin marketplace. `odcli-codex` SHALL be a separate executable.

#### Scenario: Help shows the optional flags without the extension
- **WHEN** `odcli translations export --help` and `odcli git commit --help` run in an environment that cannot import `odcli-codex`
- **THEN** the help text contains `--auto-translate` and `--generate` respectively
- **AND** the command list does not gain a new leaf

#### Scenario: Context flags require the matching mode
- **WHEN** `git commit` receives `--context` or `--model` without `--generate`
- **THEN** the process exits with a Click usage error
- **AND** WHEN `translations export` receives `--context` or `--model` without `--auto-translate`
- **THEN** the process exits with a Click usage error
