## ADDED Requirements

### Requirement: Hermetic psql startup configuration

Every SDK `psql` subprocess SHALL pass `-X` and SHALL remove `PSQLRC`,
`PGSERVICE`, `PGSERVICEFILE`, and `PGOPTIONS` from the inherited environment.
It SHALL retain the normal libpq password-file behavior: when no explicit
password is supplied, it SHALL omit `PGPASSWORD` and SHALL NOT override
`PGPASSFILE`, so `.pgpass` or a caller-selected `PGPASSFILE` remains available.

#### Scenario: Ambient psql startup inputs are ignored

- **WHEN** the parent process supplies `PSQLRC`, `PGSERVICE`, `PGSERVICEFILE`,
  or `PGOPTIONS`
- **THEN** the SDK psql subprocess receives none of those variables and argv
  contains `-X`

#### Scenario: Default password-file authentication remains available

- **WHEN** no database password is configured
- **THEN** the SDK psql subprocess omits `PGPASSWORD` and leaves `PGPASSFILE`
unchanged or absent for normal libpq password-file resolution

The shared transport MUST preserve the caller's endpoint boundary: restore
tracking with `db_host=None` omits `-h` and therefore uses libpq's Unix-socket
default; monitor database-size collection explicitly supplies `127.0.0.1` when
its host is absent and therefore uses TCP.

#### Scenario: Restore tracking keeps a missing host as Unix socket

- **WHEN** restore tracking invokes psql with `db_host=None`
- **THEN** argv omits `-h`

#### Scenario: Monitor database size defaults to TCP loopback

- **WHEN** monitor database-size collection has no configured host
- **THEN** argv contains `-h 127.0.0.1`
