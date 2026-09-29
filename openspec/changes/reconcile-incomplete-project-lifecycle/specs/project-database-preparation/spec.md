## ADDED Requirements

### Requirement: Failed restore retains exact partial-target recovery evidence

Database preparation SHALL use its captured post-restore existence probe after a restore exception. WHEN that probe proves the preselected target exists in the active SDK-owned PostgreSQL cluster, THEN the catalogue SHALL persist a secret-free `incomplete` restore binding containing the exact cluster, endpoint, database, and captured catalogue-backup or local-archive provenance. The failure result SHALL identify the retained target and incomplete state. WHEN the probe fails, is unavailable, or does not prove the exact target in the active cluster, THEN no database ownership SHALL be inferred or recorded. An incomplete target SHALL NOT become the project default or satisfy a successful/coalesced restore result.

#### Scenario: Restore fails after target creation

- **WHEN** restore raises after PostgreSQL creates the selected target and the captured post-failure probe proves that target in the active owned cluster
- **THEN** preparation fails while publishing one exact incomplete binding and reports the retained database without switching the project default

#### Scenario: Target existence cannot be proved

- **WHEN** restore fails and the captured probe cannot prove the selected target in the active owned cluster
- **THEN** preparation keeps the original failure primary, reports the database as unconfirmed, and creates no recovery binding

#### Scenario: Retry an incomplete target

- **WHEN** a later supported restore retry selects the exact incomplete binding and its source and cluster evidence still match
- **THEN** preparation SHALL safely reconcile or replace that target through the existing collision and ownership gates and SHALL NOT treat it as a completed restore before all postconditions pass

### Requirement: Successful default switch synchronizes owned runtime config

WHEN a restore completes all requested postconditions and switches `default_source_database`, database preparation SHALL update the project manifest and the project-owned generated Odoo config under the existing preparation lock before returning success. The generated `db_name` and `dbfilter` SHALL select the new default while preserving all unrelated generated settings. A user-managed source config SHALL remain byte-for-byte unchanged. WHEN either owned write fails, THEN preparation SHALL report failure, compensate any preceding ordinary write from owner-validated captured content, and SHALL NOT emit `default_switched=true`.

#### Scenario: Successful restore switches effective database

- **WHEN** restore of `staging_restored` completes and default switching is enabled for a project using `.odcli/odoo.conf`
- **THEN** both `project.toml` and the generated config select `staging_restored` before success and the next `odcli run` uses that database and filter

#### Scenario: User-managed config is preserved

- **WHEN** preparation switches the default for a project whose effective source config is not project-owned generated config
- **THEN** the manifest is updated and the external source config remains byte-for-byte unchanged

#### Scenario: Generated config write fails

- **WHEN** the generated-config update fails during default switching
- **THEN** preparation restores the prior owned file state where an ordinary exception permits compensation, reports failure, and does not claim that the default switch completed
