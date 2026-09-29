## ADDED Requirements

### Requirement: Compose bootstrap records exact disposable database origin

After a self-contained Compose initialization or first project run actually creates database `tmp` and the existing SQL readiness check proves `base` is installed, the system SHALL record one current bootstrap-origin binding for that exact canonical project, active `cluster_id`, Compose project, named volume, database endpoint, database name `tmp`, and project-owned data directory. The catalog write SHALL be an explicit mutating action in the same immutable command plan and SHALL validate the active cluster claim before committing. It SHALL NOT be represented as a restore row.

The bootstrap executor SHALL distinguish a database created by its captured spawn from a database that was already ready. Dry-run, external PostgreSQL, a skipped spawn, readiness failure, missing or pending cluster claims, identity mismatch, and a pre-existing unrecorded `tmp` SHALL NOT create or backfill bootstrap-origin evidence. A catalog-write failure after database creation SHALL fail the operation and leave the database unauthorized for guarded deletion until a later verified SDK bootstrap creates fresh evidence; it SHALL NOT infer ownership on retry.

#### Scenario: Successful bootstrap publishes exact origin

- **WHEN** the captured bootstrap spawn creates `tmp`, its readiness probe returns `installed`, and the current project cluster exactly matches an active catalog claim
- **THEN** execution records one bootstrap-origin binding containing that project, cluster, Compose, volume, endpoint, `tmp`, and project-owned data-directory identity without inserting a restore row

#### Scenario: Existing ready tmp is not adopted

- **WHEN** the initial readiness probe finds an already valid `tmp` but no current bootstrap-origin binding exists
- **THEN** the spawn is skipped and no bootstrap-origin evidence is created or backfilled

#### Scenario: Failed bootstrap creates no authority

- **WHEN** the bootstrap spawn, readiness probe, active-claim validation, or origin write fails
- **THEN** initialization fails and no successful bootstrap-origin binding is committed

#### Scenario: Preview and external PostgreSQL are inert

- **WHEN** bootstrap is previewed with `--dry-run` or the project uses external PostgreSQL
- **THEN** the public plan remains secret-free and no bootstrap-origin catalog row is written

#### Scenario: Bootstrap origin write is inspectable

- **WHEN** Compose initialization or first-run bootstrap is planned
- **THEN** its immutable plan contains the bootstrap spawn, probes, readiness verification, and conditional origin-record action that execution consumes without rebuilding process or identity inputs
