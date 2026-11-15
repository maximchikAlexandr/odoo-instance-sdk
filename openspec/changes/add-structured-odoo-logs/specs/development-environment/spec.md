# Spec Delta

## ADDED Requirements

### Requirement: Owned structured run logs
When structured Odoo logging is enabled, each run SHALL store its logfile and metadata under `<environment-root>/logs/<run_id>/`. The generated `odoo.conf` SHALL keep the environment-owned logfile and SHALL NOT be replaced by a credential-bearing copy. Environment removal SHALL delete a run directory only when it is owned by that environment and the existing removal provenance checks pass. Those run files SHALL count toward the environment `other_files_bytes` total. No retention or garbage collection SHALL be added.

#### Scenario: Generated config stays the environment logfile
- **WHEN** a structured run starts
- **THEN** the generated `odoo.conf` still points at the environment logfile and the process receives one run-specific logfile override

#### Scenario: Removal deletes owned run directories
- **WHEN** environment removal succeeds for an environment that owns structured run directories
- **THEN** those directories are removed and a run directory outside that environment is left untouched

#### Scenario: Run logs count as other files
- **WHEN** environment storage is measured and owned run logs exist
- **THEN** their bytes are included in `other_files_bytes`
