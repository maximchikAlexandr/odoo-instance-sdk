## MODIFIED Requirements

### Requirement: Alembic and SQLAlchemy Core replace the PRAGMA user_version chain

The catalogue SHALL use Alembic and SQLAlchemy Core (without ORM) as its migration ledger. One first Alembic revision SHALL create the complete current catalogue schema, constraints, and indexes in a single step. A clean install SHALL apply only that first revision and SHALL NOT run the historical v2–v16 chain. Known existing alpha catalogues SHALL be backed up via SQLite `.backup`, verified, and stamped with the first revision only after schema equivalence is confirmed.

After the controlled transition, the old `PRAGMA user_version` ledger, `_run_migrations()`, `_migrate_v*`, intermediate schema fixtures, and code serving only obsolete catalogue forms SHALL be removed from production code. Stale tables, fields, entities, and compatibility branches SHALL be removed only after production code and known catalogues no longer use them. Subsequent schema changes SHALL be separate linear Alembic revisions.

Complex data migrations and data-preservation checks SHALL remain explicit. Alembic autogenerate SHALL NOT be considered proof of migration correctness. ORM models SHALL NOT be added and repository queries SHALL NOT be translated away from `sqlite3` by this change.

Known unstamped catalogue recognition SHALL derive exact, version-explicit historical fingerprints from one canonical internal definition. The v16 repair path and pre-source-neutral provenance path SHALL reuse the same shared table/column differences while keeping their genuine version-specific required-bit and index differences explicit. Neither path SHALL stamp, repair, or upgrade a catalogue with an unrecognized extra/missing column, index, foreign key, or view.

#### Scenario: Clean install skips the historical chain

- **WHEN** a fresh catalogue is created
- **THEN** only the first Alembic revision runs and no v2–v16 step executes

#### Scenario: Known catalogue is migrated and stamped

- **WHEN** a known alpha catalogue is migrated
- **THEN** a SQLite backup is created first, the schema is verified equivalent to the first revision, and the revision is stamped

#### Scenario: Old migrator is removed

- **WHEN** production code is inspected after the transition
- **THEN** `PRAGMA user_version` as a migration ledger, `_run_migrations()`, and `_migrate_v*` are absent

#### Scenario: CI rejects multiple heads

- **WHEN** CI runs the Alembic gate
- **THEN** multiple Alembic heads and unverified schema-metadata divergence are rejected

#### Scenario: Shared historical omission applies to both known paths

- **WHEN** a field known to be absent from both supported historical shapes is added to the canonical current schema
- **THEN** the single historical fingerprint definition excludes it for both v16 repair and legacy-provenance recognition while preserving each variant's explicit index and nullability differences

#### Scenario: Unknown historical shape fails closed

- **WHEN** an unstamped catalogue differs from every exact supported historical fingerprint
- **THEN** it is neither stamped nor partially repaired and opening fails before Alembic upgrade mutates it
