## ADDED Requirements

### Requirement: One common process table per inventory section

The Rich projection of `ProcessInventory` SHALL preserve the existing shared-resource, main-checkout, and environment sections and their deterministic order. Within each section it SHALL render exactly one common bordered process table with primary columns `Type`, `State`, `PID / scope`, `Processes`, `CPU`, `Memory`, and `Details`. Odoo groups, the proven-owned PostgreSQL cluster/container, attributable PostgreSQL backend groups, and bounded external process contributions SHALL use these same columns instead of product-specific table shapes or prose.

Storage footprint MAY remain a separate section fact and SHALL NOT be represented as a process row. A section with no proven process entry SHALL still render an explicit empty/unavailable row in its common table rather than omitting the table or inventing a process.

#### Scenario: Mixed checkout uses one table

- **WHEN** one checkout has an Odoo process group, an attributable PostgreSQL backend group, and an external contribution
- **THEN** its section contains one bordered table with three rows under the same seven primary columns

#### Scenario: Owned cluster uses the common shape

- **WHEN** a shared-resource section contains a proven-owned PostgreSQL cluster/container
- **THEN** that cluster is one row in the section's common process table and is not emitted as a separate prose line or incompatible table

#### Scenario: Empty stopped checkout stays visible

- **WHEN** a stopped checkout has no running process and its typed inventory keeps the owner visible
- **THEN** its section contains one explicit stopped/unavailable row in the common table

### Requirement: Product-specific process facts belong in Details

The common table SHALL map type-specific facts into `Details` without creating additional primary columns. An Odoo row SHALL retain database, endpoint, branch, and commit when available. An owned PostgreSQL cluster row SHALL retain endpoint, server version, active/total connections, container identity, and typed availability reasons when available. A PostgreSQL backend-group row SHALL retain database, connection count, attribution reason, and connection identity when available. An external-contribution row SHALL retain source, stable local identity, and availability/completeness reason.

The `PID / scope` cell SHALL distinguish host-visible, Docker-VM, and unavailable scopes. `Processes`, CPU, and memory SHALL use only values present in the typed inventory; unavailable values SHALL remain explicit and SHALL NOT be derived from unrelated container or Odoo metrics.

#### Scenario: Odoo details preserve identity

- **WHEN** an Odoo group has database, endpoint, branch, and commit facts
- **THEN** the Odoo row places those facts in `Details` and keeps the shared primary columns unchanged

#### Scenario: Backend group explains attribution

- **WHEN** a backend group is attributed by `unique_database`
- **THEN** its row shows the database, connection count, and attribution reason in `Details` and uses its typed PID scope and metrics

#### Scenario: Docker VM metrics remain unavailable

- **WHEN** a PostgreSQL backend group has Docker-VM PID scope and no host-visible CPU or memory
- **THEN** the row shows Docker-VM scope, explicit unavailable CPU/memory, and does not copy container aggregate metrics into the backend row

#### Scenario: External contribution explains availability

- **WHEN** a bounded external contribution is unavailable or partial
- **THEN** its row retains source and stable identity when present and displays the typed availability/completeness reason in `Details`

### Requirement: Process table projection does not change inventory semantics

The unified human table SHALL be a pure projection of the existing frozen `ProcessInventory`. It SHALL NOT add a collector, re-sample a snapshot, scan arbitrary system processes, guess Odoo client PIDs, infer ownership, or change JSON/TOON field sets. One PID SHALL retain the single owner assigned by the inventory, and shared resources SHALL remain shown exactly once.

#### Scenario: Rich projection performs no collection

- **WHEN** a frozen `ProcessInventory` is rendered as Rich
- **THEN** no monitor, Docker, PostgreSQL, filesystem, Git, or process collector is invoked

#### Scenario: Shared backend is not duplicated

- **WHEN** a backend group is owned by shared resources because its database is used by multiple checkouts
- **THEN** it appears once in the shared section table and in no checkout table

#### Scenario: Machine projections remain identical

- **WHEN** the same frozen `ProcessInventory` is emitted as JSON or TOON after the Rich redesign
- **THEN** its decoded machine result is unchanged from the pre-redesign typed projection
