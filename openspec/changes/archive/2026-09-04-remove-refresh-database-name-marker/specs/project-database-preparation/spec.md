## MODIFIED Requirements

### Requirement: Restore preflight and collision-free target

For `restore=True`, the workflow SHALL perform all non-mutating local rejection checks before starting the remote download: resolve the project and source config, assert the target Odoo endpoint is local, require its local master password, verify a ready local runtime/database-manager path, and require `PostgresCluster.ensure_running()` to complete successfully. Download-only SHALL NOT require local runtime or PostgreSQL readiness.

The workflow SHALL generate a new PostgreSQL-safe target name from the remote database, a UTC timestamp, and a collision-resistant suffix, without inserting a literal refresh marker. It SHALL validate the name with the existing database-name rules, remain within PostgreSQL's 63-byte identifier limit, and recheck `DatabaseResource.exists()` under the project preparation lock. It SHALL never drop, overwrite, or reuse an existing database.

#### Scenario: Restore preflight fails before download

- **WHEN** `restore=True` and the project PostgreSQL cluster cannot become healthy
- **THEN** no remote backup request starts and the project default remains unchanged

#### Scenario: Generated name collides

- **WHEN** a generated target name already exists
- **THEN** the workflow generates/rechecks another safe name and never drops or overwrites the existing database

#### Scenario: Generated name omits refresh marker

- **WHEN** the workflow automatically generates a restore target for source database `source` at timestamp `20260903090420` with suffix `2ee3a458a068`
- **THEN** the target is `source_20260903090420_2ee3a458a068` and does not contain `_refresh_`
