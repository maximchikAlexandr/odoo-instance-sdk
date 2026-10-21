## ADDED Requirements

### Requirement: COPY preparation preserves the running primary
COPY checkout and adoption SHALL use the independent auxiliary Database Manager endpoint contract when a helper must be spawned. They SHALL NOT require stopping the primary Odoo merely because its HTTP port is occupied. Existing target database, filestore, HTTP port, ownership and compensation guarantees SHALL remain unchanged.

#### Scenario: Adopt alongside active primary
- **WHEN** COPY adoption requires a helper while the configured primary is listening
- **THEN** preparation uses an independent helper endpoint, preserves the primary and prepares the target under existing COPY isolation and cleanup rules
