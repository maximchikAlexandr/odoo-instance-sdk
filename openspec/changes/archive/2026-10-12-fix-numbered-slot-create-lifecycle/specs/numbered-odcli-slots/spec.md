## ADDED Requirements

### Requirement: Numbered creation proves a complete isolated lifecycle

The repository-owned numbered workflow SHALL prove `create -> list -> inspect -> remove` against a caller-owned clone whose origin is transport-equivalent to the configured project and whose COPY archive is acquired from a named remote. Successful creation SHALL publish the smallest wholly free numbered identity only after the local target database, isolated filestore, checkout, owned Python environment, launcher, manifest, and catalogue environment satisfy their existing postconditions. Removal SHALL delete only those proven owned resources while preserving the reference checkout, source instance and database, source backup, canonical installation, and neighboring numbered identities.

#### Scenario: Complete lifecycle succeeds with equivalent remotes
- **WHEN** the configured project and numbered clone use equivalent scp-like and `ssh://` Git origins and all explicit project inputs are valid
- **THEN** create SHALL reach ready, list and inspect SHALL report the same bounded identity and resource linkage, and remove SHALL eliminate that identity's database, filestore, checkout, environment, launcher, manifest, and selected root

#### Scenario: Non-equivalent remote is rejected without partial resources
- **WHEN** the numbered clone resolves to a different canonical repository identity
- **THEN** create SHALL fail before adoption mutation, publish no launcher or ready manifest, and leave no newly owned database, filestore, checkout, environment, or tool artifacts after bounded cleanup

#### Scenario: Restore failure leaves no untracked partial slot
- **WHEN** backup acquisition, auxiliary startup, restore, or local target verification fails during numbered creation
- **THEN** no ready numbered identity SHALL appear in list or inspect, all safely proven partial owned resources SHALL be removed, and any unresolved destructive ownership SHALL remain explicitly linked for existing recovery rather than being silently deleted

#### Scenario: Neighbor and reference assets remain unchanged
- **WHEN** one numbered lifecycle succeeds or fails while a canonical installation, reference project, source database and backup, and another numbered identity exist
- **THEN** before/after evidence SHALL show those nonselected assets retain their identities and contents
