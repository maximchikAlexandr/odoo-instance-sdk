## ADDED Requirements

### Requirement: Public CLI excludes numbered lifecycle management
The public `odcli` command tree SHALL NOT register a `slot` group or any numbered-installation create, list, inspect, replace, allocate, or remove leaf. Public help, characterization, output-format inventories, execution-boundary documentation, and real-Odoo command matrices SHALL describe only the supported product CLI and SHALL NOT advertise the private coordinator.

#### Scenario: Root help is unchanged by numbered tooling
- **WHEN** root help and the canonical public-leaf inventory are generated
- **THEN** they contain no `slot` command and their counts and documentation match the non-slot public surface

#### Scenario: Private coordinator is not a console script
- **WHEN** wheel entry points and package command registration are inspected
- **THEN** numbered management has no Click registration or package console-script entry point

### Requirement: Alternate launchers delegate the ordinary CLI surface
The single shared launcher renderer SHALL produce both `odcli-N` and `odcli-fix-ISSUE`. Each launcher SHALL hold its lifecycle lock for the full child lifetime, set only selected-root and alternate-identity values required by the SDK, and delegate arguments and exit status without shell interpretation. Except for direct self-update, the installed revision's ordinary public CLI surface SHALL remain available.

#### Scenario: Arguments and exit status are preserved
- **WHEN** an operator invokes `odcli-N` or `odcli-fix-ISSUE` with an ordinary supported command
- **THEN** exact argument boundaries and child exit status are preserved and the lifecycle lock remains held until the child exits
