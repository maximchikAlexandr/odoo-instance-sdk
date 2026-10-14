## MODIFIED Requirements

### Requirement: Получение списка и проверка существования базы

`instance.databases.list()` SHALL call the Odoo 19.0 JSON-RPC endpoint `/web/database/list` and return a tuple of `Database` values in Odoo response order.

The SDK SHALL NOT guess a default database and SHALL NOT provide `resolve_default()`.

`list()` SHALL populate `backup` for each returned `Database`: when the instance has a cluster key (`db_port is not None`), each name SHALL use `catalog.latest_restore(db_host, db_port, name)` and project `None` as `NoBackup()`; without a cluster key every row SHALL use `NoBackup()`.

`instance.databases.exists(name)` SHALL observe exact membership using the same bounded Odoo/psql fallback rules as before. `list()`, `exists(name)`, `current()` and their command forms SHALL NOT append `database_events`, backfill catalogue state or otherwise reconcile missing databases. When tracked restore names are absent, their typed observation SHALL report exact missing names and the evidence source so an explicit reconciliation operation can act later.

If `list()` raises `DatabaseManagerUnavailableError`, `exists(name)` SHALL use psql only with a cluster key and `db_user`: confirmed present returns true; confirmed absent returns false plus an observational missing result; non-zero/timeout is inconclusive and propagates `DatabaseManagerUnavailableError`. Without cluster key or `db_user`, it SHALL propagate.

If listing is disabled or unavailable, methods SHALL raise `DatabaseManagerUnavailableError`, not return an empty tuple.

#### Scenario: Несколько удалённых баз

- **WHEN** remote Odoo returns several database names
- **THEN** `list()` returns one tuple row per name without choosing a default or writing catalogue state

#### Scenario: Listing недоступен

- **WHEN** Odoo does not provide database listing
- **THEN** the SDK reports an explicit typed error and does not reconcile the catalogue

#### Scenario: Missing tracked database is observed

- **WHEN** `exists("staging")` observes false and tracked restore evidence exists for that exact cluster/name
- **THEN** the result identifies `staging` as missing and no `dropped` event is written

#### Scenario: list() populate backup для каждой базы

- **WHEN** `list()` returns `("prod", "staging")` for a configured instance and restores contain only `prod`
- **THEN** the result is `(Database("prod", backup=<Backup>), Database("staging", backup=NoBackup()))` with no catalogue write

#### Scenario: list() без cluster-ключа

- **WHEN** `list()` runs on an instance without a cluster key
- **THEN** every `Database` has `backup=NoBackup()` and restores/database events are untouched

#### Scenario: Empty list with tracked restores is observational

- **WHEN** `list()` returns `()` while tracked restores contain `staging` and `test`
- **THEN** the observation reports both names as missing and appends no `dropped` event

## ADDED Requirements

### Requirement: Explicit database reconciliation operation

The SDK SHALL expose a typed previewable `reconcile_databases_command` that accepts or captures an exact database observation, lists proposed missing-name events, and revalidates cluster identity, tracked restore evidence and current database absence under the existing catalogue transaction before appending idempotent `dropped` events. A stale, inconclusive, foreign or mismatched observation SHALL fail before mutation.

#### Scenario: Confirmed missing database is reconciled

- **WHEN** preview identifies a tracked database as absent and execution revalidates the same exact absence and cluster identity
- **THEN** one idempotent `dropped` event is appended for that database and the typed result reports the change

#### Scenario: Database reappears before execution

- **WHEN** a previewed missing database is present during execution revalidation
- **THEN** no event is appended and the command fails with a stale-observation result

#### Scenario: Inconclusive probe does not reconcile

- **WHEN** neither Odoo listing nor the bounded psql fallback proves absence
- **THEN** reconciliation fails without a catalogue write

### Requirement: Required lifecycle owners call reconciliation explicitly

Startup/registration, destructive postconditions and existing repair paths that require catalogue reconciliation SHALL call the explicit reconciliation operation at their named mutation boundary. Monitor, inventory, list, exists, current and schema/contract discovery SHALL remain observational.

#### Scenario: Startup reconciliation remains available

- **WHEN** a startup or registration flow is contractually required to reconcile a proven missing database
- **THEN** it invokes the explicit reconciler after proof and records the same idempotent audit outcome

#### Scenario: Polling read remains inert

- **WHEN** automation repeatedly polls list, exists, current, monitor or inventory
- **THEN** no database event or catalogue row changes until an explicit reconciliation operation runs
