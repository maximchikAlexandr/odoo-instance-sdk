# Spec Delta

## ADDED Requirements

### Requirement: Addon scaffold and config path commands

The public CLI SHALL expose `odcli scaffold MODULE [DEST]` and `odcli config path`. `scaffold` SHALL resolve the project with the existing project-context rules and SHALL NOT accept an environment selector. Repeatable `--with`, `--without`, and `--manifest KEY=VALUE` options SHALL be parsed and passed to the public scaffold command. `--dry-run` SHALL emit the shared bounded plan and SHALL NOT mutate. `config path` SHALL print the platform user config path used by scaffold and SHALL NOT create that file. Both leaves SHALL be recorded once in `PUBLIC_LEAF_CASES`.

#### Scenario: Scaffold help advertises dry-run and component flags

- **WHEN** the operator runs `odcli scaffold --help`
- **THEN** the help lists the module argument, optional destination, `--with`, `--without`, `--manifest`, and `--dry-run`

#### Scenario: Config path does not create a file

- **WHEN** the operator runs `odcli config path` and the user config file is absent
- **THEN** the command prints the absolute config path and exits successfully
- **AND** the file still does not exist
