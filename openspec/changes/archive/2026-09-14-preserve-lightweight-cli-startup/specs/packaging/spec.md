## ADDED Requirements

### Requirement: Installed metadata is the CLI version source

The root Click version option SHALL obtain the `odoo-instance-sdk` version from installed distribution metadata using Click and standard-library packaging facilities. The CLI SHALL NOT duplicate the project version as a command-local literal or add a runtime dependency for version discovery.

#### Scenario: Installed wheel reports its metadata version

- **WHEN** an isolated environment installs a built wheel and runs `odcli --version` outside a project
- **THEN** the command exits `0` and its output contains the version declared by that wheel's distribution metadata

#### Scenario: Version support adds no dependency

- **WHEN** the built wheel metadata is inspected after the change
- **THEN** its runtime dependency set is unchanged by version discovery
