## ADDED Requirements

### Requirement: Compose bootstrap appends exact lifecycle evidence

After a self-contained Compose initialization or first project run actually creates database `tmp` and the existing SQL readiness check proves `base` is installed, the system SHALL append one `bootstrapped` event to the existing `database_events` lifecycle for the normalized cluster endpoint and exact database `tmp`. The event SHALL contain the exact current active `cluster_id` and project-owned data directory and SHALL keep restore-only fields null. The catalog write SHALL be an explicit mutating action in the same immutable command plan and SHALL validate the active cluster claim before committing.

The bootstrap executor SHALL distinguish a database created by its captured spawn from a database that was already ready. Dry-run, external PostgreSQL, a skipped spawn, readiness failure, missing or pending cluster claim, identity mismatch, and a pre-existing unrecorded `tmp` SHALL NOT append or backfill `bootstrapped`. A catalog-write failure after database creation SHALL fail the operation and leave the database unauthorized for guarded deletion; later readiness SHALL NOT infer ownership.

#### Scenario: Successful bootstrap appends exact event

- **WHEN** the captured bootstrap spawn creates `tmp`, its readiness probe returns `installed`, and the current project cluster exactly matches an active catalog claim
- **THEN** execution appends one `bootstrapped` event with that endpoint, `tmp`, `cluster_id`, and project-owned data directory and no restore provenance

#### Scenario: Existing ready tmp is not adopted

- **WHEN** the initial readiness probe finds an already valid `tmp` but the latest exact event is not `bootstrapped`
- **THEN** the spawn and record action are skipped and no bootstrap evidence is appended or backfilled

#### Scenario: Failed bootstrap creates no authority

- **WHEN** the bootstrap spawn, readiness probe, active-claim validation, or event append fails
- **THEN** initialization fails and no successful `bootstrapped` event is committed

#### Scenario: Preview and external PostgreSQL are inert

- **WHEN** bootstrap is previewed with `--dry-run` or the project uses external PostgreSQL
- **THEN** the public plan remains secret-free and no `database_events` row is written

#### Scenario: Bootstrap event action is inspectable

- **WHEN** Compose initialization or first-run bootstrap is planned
- **THEN** its immutable plan contains the bootstrap spawn, probes, readiness verification, and conditional `bootstrapped` record action that execution consumes without rebuilding process or identity inputs
