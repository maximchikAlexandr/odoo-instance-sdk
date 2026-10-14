## MODIFIED Requirements

### Requirement: Snapshot v3 observation truth

Snapshot schema version 3 SHALL preserve existing version-2 fields and add a shared UTC observation time plus requested, completed and unknown section metadata. Each selected section SHALL report observed time, optional source age, completeness and a bounded sanitized reason when partial or unknown. Missing or unavailable data SHALL NOT be encoded as fresh zero/false values. The top-level HTTP snapshot schema version and existing project/environment arrays SHALL remain compatible. The embedded dashboard and the currently deployed external Moritora consumer SHALL accept a genuine candidate snapshot containing the additive observation field before graph-2 acceptance.

#### Scenario: One probe is unavailable

- **WHEN** Docker stats fail while catalogue and runtime collection succeed
- **THEN** the snapshot retains the successful sections, marks Docker incomplete/unknown with a sanitized reason and uses one shared observation time

#### Scenario: Unrequested differs from unknown

- **WHEN** storage is not requested and Git is requested but unavailable
- **THEN** storage appears only in the requested-section metadata as unrequested while Git is explicitly unknown/incomplete

#### Scenario: Existing Moritora consumes the additive snapshot

- **WHEN** the current external Moritora build reads a genuine candidate `/api/v1/snapshot` containing observation metadata
- **THEN** it parses and renders the existing project and environment rows without a crash, blank state, or loss of pre-existing fields
