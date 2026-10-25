## ADDED Requirements

### Requirement: Project disk aggregate
`ProjectStorageFootprint` SHALL expose `total_bytes: int | None`, `backup_bytes: int | None`, and `complete: bool`. For a selected project, the monitor SHALL calculate one project aggregate covering the main checkout and project artifacts, non-removed working-environment filesystem storage, each distinct related PostgreSQL database and contained filestore, and every available downloaded backup owned by that project. The aggregate SHALL count a canonical filesystem path, backup file identity, and normalized cluster-endpoint/database identity at most once. It SHALL NOT add the shared PostgreSQL cluster volume to every database or environment; logical database bytes SHALL represent related PostgreSQL storage once per distinct database.

The collector SHALL use existing bounded directory, PostgreSQL-size, filestore-containment, catalogue, and monitor command boundaries. `backup_bytes` SHALL be zero after a successful catalogue/filesystem inspection with no owned available backups, and `None` when the backup subtotal cannot be determined. `total_bytes` SHALL sum every known included component and SHALL be `None` only when no included component is measurable. `complete` SHALL be `True` only when every expected included component and the backup subtotal were measured; a missing or failed component SHALL preserve known subtotals while setting `complete=False`. Project storage SHALL retain the existing bounded cache and partial-result isolation.

#### Scenario: Aggregate includes distinct databases and backups once
- **WHEN** a project's main checkout and two working environments reference two distinct databases, one database is referenced more than once, and two project-owned downloaded backup records resolve to the same file
- **THEN** `total_bytes` includes checkout/artifact storage, working-environment filesystem storage, both database/filestore footprints, and the backup file exactly once
- **AND** `backup_bytes` contains that backup file's size exactly once

#### Scenario: No backups yields a measured zero subtotal
- **WHEN** project catalogue and filesystem inspection succeeds and the project owns no available downloaded backup file
- **THEN** `backup_bytes` is `0` and the absence of backups does not by itself make the footprint incomplete

#### Scenario: Missing component preserves a partial aggregate
- **WHEN** checkout and backup bytes are measurable but one related PostgreSQL database size is unavailable
- **THEN** `total_bytes` contains the known checkout and backup bytes, `backup_bytes` remains available, and `complete` is `False`

#### Scenario: Stopped runtimes retain disk accounting
- **WHEN** the main runtime or a working environment is stopped and its filesystem, database, filestore, or backup storage remains measurable
- **THEN** the corresponding storage contributes to the project aggregate without requiring a live Odoo process

### Requirement: Disk appears with runtime metrics
The dashboard SHALL render `Disk` in the same metric row as CPU and RAM for both main-checkout and worktree cards and SHALL remove the separate `Resources` detail row. Disk SHALL remain visible when the runtime is stopped whenever `total_bytes` is known. A main-checkout card SHALL label the value as a project total and SHALL append the measured backup subtotal, for example `Disk 800 MiB (230 MiB backups, project total)`. An environment card SHALL continue to show its environment footprint without claiming project-wide scope. Every incomplete footprint SHALL retain a visible incomplete indication, and an unavailable total SHALL display the existing unavailable marker without fabricating zero.

#### Scenario: Running instance metric row
- **WHEN** a running card has CPU, RAM, and measurable disk values
- **THEN** the card's metric row contains `CPU`, `RAM`, and `Disk` and its detail list contains no `Resources` row

#### Scenario: Stopped instance still shows Disk
- **WHEN** a stopped card has a measurable disk footprint
- **THEN** its metric row contains `Disk` while CPU and RAM remain omitted under the existing stopped-runtime behavior

#### Scenario: Project backup subtotal and scope are visible
- **WHEN** the project aggregate contains a measured total and backup subtotal
- **THEN** the main-checkout card shows both formatted values and identifies the disk value as the project total

#### Scenario: Incomplete and unavailable values remain honest
- **WHEN** a card's storage is incomplete or its total is unavailable
- **THEN** the dashboard marks the value incomplete or unavailable respectively and does not substitute a complete zero
