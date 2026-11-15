## ADDED Requirements

### Requirement: Project configuration distinguishes assigned and preferred HTTP ports

`ProjectConfig` SHALL round-trip an optional `assigned_http_port` as OdCLI-managed, non-secret main-project state distinct from the operator-controlled `preferred_http_port`. The effective main-project port SHALL resolve in the order `preferred_http_port`, `assigned_http_port`, configured Odoo HTTP port, then `8069`. Manifest updates SHALL use the existing project lock and atomic writer. When an owned generated Odoo configuration exists, a changed automatic assignment SHALL be reflected there before the run command is constructed. OdCLI SHALL NOT edit an external/source Odoo configuration.

#### Scenario: Legacy project has no assignment

- **WHEN** a project manifest does not contain `assigned_http_port`
- **THEN** it loads successfully and resolves its configured HTTP port or `8069` when no explicit preference exists

#### Scenario: Automatic assignment round-trips

- **WHEN** OdCLI assigns a main-project HTTP port automatically
- **THEN** the exact value is persisted as `assigned_http_port` and survives a manifest load/write round trip without changing `preferred_http_port`

#### Scenario: Explicit preference wins

- **WHEN** both `preferred_http_port` and `assigned_http_port` are present
- **THEN** main-project configuration resolves the preferred port and retains the assigned value only as inactive automatic state

#### Scenario: Owned generated config follows assignment

- **WHEN** an automatic assignment changes for a project with an owned generated Odoo configuration
- **THEN** the manifest and generated configuration contain the selected port before runtime command capture
- **AND** a later generated-config repair preserves that same effective port

#### Scenario: Source config remains immutable

- **WHEN** an external-mode project selects an automatic port from a source Odoo configuration
- **THEN** OdCLI persists the assignment only in the project manifest and leaves the source configuration byte-for-byte unchanged

#### Scenario: Preview is mutation-free

- **WHEN** a main-project run is previewed while its automatic/default port is occupied
- **THEN** no assignment, manifest, generated configuration, runtime record, or process is written
