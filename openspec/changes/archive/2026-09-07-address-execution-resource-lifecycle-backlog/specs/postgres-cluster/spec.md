## ADDED Requirements

### Requirement: Owned volume identity and reclaimability

An SDK-owned Compose cluster resource snapshot SHALL expose a stable redacted volume identity, measured host-usage bytes when the Docker runtime can provide them, and `reclaimable=false` while the cluster and volume are retained. `stop()` SHALL continue to stop processes without deleting data or presenting volume bytes as freed. External, shared, unresolved, or unmeasured volumes SHALL never be labelled SDK-reclaimable.

#### Scenario: Managed cluster is stopped

- **WHEN** `postgres stop` successfully stops an SDK-owned Compose cluster
- **THEN** its data volume remains present, its known usage remains a retained resource, and no reclaimed-byte claim is emitted

#### Scenario: External cluster

- **WHEN** the project uses an external PostgreSQL endpoint
- **THEN** resource inventory reports no owned/reclaimable Docker volume and does not inspect or mutate external volumes

#### Scenario: Shared or unknown volume

- **WHEN** volume ownership cannot be proven exclusively from the generated Compose identity
- **THEN** the volume is preserved and reported as non-reclaimable with unknown or shared ownership
