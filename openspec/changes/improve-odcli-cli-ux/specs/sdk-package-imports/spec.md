## ADDED Requirements

### Requirement: CLI metadata paths defer operation-only imports

Importing the CLI and executing installed `odcli --help` or `odcli --version` SHALL NOT import Alembic, SQLAlchemy, database-preparation source modules, monitor collectors, or other operation-only dependency chains. Failure-context types and command implementations SHALL be imported only inside the handler or lazy loader that needs them, while declared public SDK exports SHALL retain identity and compatibility. The change SHALL use existing lazy registration, callback-local imports, and `TYPE_CHECKING`; it SHALL NOT add a lazy-import dependency or replace Click registration.

#### Scenario: Fresh metadata process stays lightweight
- **WHEN** an installed executable outside the checkout runs `odcli --help` or `odcli --version` in a fresh interpreter
- **THEN** it exits `0`, emits the expected metadata, and the prohibited operation-only modules are absent from `sys.modules`

#### Scenario: Operational error context remains available
- **WHEN** a command reaches a database preparation failure that carries typed retained-artifact context
- **THEN** the existing sanitized context is projected through the shared failure document after its handler loads the required type locally

#### Scenario: Public exports remain compatible
- **WHEN** callers resolve existing declared SDK exports after the import-boundary change
- **THEN** names, order, object identity, and unknown-attribute behavior remain unchanged
