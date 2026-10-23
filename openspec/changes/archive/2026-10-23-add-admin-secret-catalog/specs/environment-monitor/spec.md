## ADDED Requirements

### Requirement: Monitor reports proven source-backup download freshness

The canonical `Snapshot` SHALL include a typed nullable/unknown backup-freshness projection for every displayed project or environment database. A known projection SHALL contain the exact UTC `downloaded_at` of the currently proven source backup and a nonnegative `age_seconds` computed from the snapshot `generated_at`. Unknown provenance SHALL be encoded explicitly and SHALL NOT fail unrelated snapshot sections.

The React monitor SHALL render the exact download date and a human age label on the corresponding database card, separate from runtime readiness. It SHALL render `Backup download date unknown` when provenance or timestamp is unknown. The browser SHALL consume this from the existing snapshot and SHALL NOT make a second catalogue request.

#### Scenario: Current catalogue restore shows exact date and age

- **WHEN** the exact current local database resolves to a completed catalogue backup downloaded five days before snapshot generation
- **THEN** the snapshot and card SHALL show that UTC timestamp and an age equivalent to five days

#### Scenario: Unknown origin is honest

- **WHEN** the database was bootstrapped, restored from a local archive, replaced, incompletely restored, has mismatched cluster identity, or lacks a download timestamp
- **THEN** the card SHALL display `Backup download date unknown` and SHALL NOT show an older or inferred date

#### Scenario: Future timestamp is clamped

- **WHEN** clock skew makes a proven `downloaded_at` later than snapshot generation
- **THEN** the exact timestamp SHALL be retained and `age_seconds` SHALL be zero rather than negative

#### Scenario: Freshness cannot hide the snapshot

- **WHEN** freshness lookup fails independently of runtime collection
- **THEN** the affected freshness SHALL become unknown with a bounded section outcome while other project/environment data remains available
