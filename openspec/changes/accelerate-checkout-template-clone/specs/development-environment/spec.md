# Spec Delta

## MODIFIED Requirements

### Requirement: `EnvironmentDatabaseMode` enum

`EnvironmentDatabaseMode` MUST быть `enum.StrEnum` со значениями:

- `SHARED = "shared"`
- `COPY = "copy"`

COPY ownership depends on `db_copy_strategy`. Strategy `backup` owns the target database and `backup_id`. Strategy `template` owns the target database and the recorded `target_filestore_path`, and its `backup_id` MUST stay empty.

#### Scenario: Shared mode

- **WHEN** `db_mode = SHARED`
- **THEN** environment не владеет БД, `remove` не может `drop()` source DB

#### Scenario: Copy mode

- **WHEN** `db_mode = COPY` and the strategy is `backup`
- **THEN** environment владеет target DB and `backup_id`, and `remove` drops that target DB

#### Scenario: Template copy mode

- **WHEN** `db_mode = COPY` and the strategy is `template`
- **THEN** environment owns the target DB and recorded filestore path, `backup_id` is empty, and `remove` drops only that recorded target

### Requirement: `EnvironmentCheckoutOptions` public type

`EnvironmentCheckoutOptions` MUST be a `msgspec.Struct` with `frozen=True` and these fields:

- `base_ref: str | None = None`
- `name: str | None = None`
- `config_path: Path | None = None`
- `db_mode: EnvironmentDatabaseMode = EnvironmentDatabaseMode.SHARED`
- `db_copy_strategy: DatabaseCopyStrategy = DatabaseCopyStrategy.BACKUP`
- `source_database: str | None = None`
- `target_database: str | None = None`
- `odoo_bin: Path | None = None`
- `python: str | Path | None = None`
- `create_venv: bool = False`
- `http_port: int | None = None`
- `hash_lock: Path | None = None`
- `hash_lock_sha256: str | None = None`

`DatabaseCopyStrategy` MUST be a `StrEnum` with `BACKUP = "backup"` and `TEMPLATE = "template"`. `base_ref` is the explicit per-call override; when it is `None`, checkout SHALL use `ProjectConfig.default_base_ref`, then `HEAD`. `source_database is not None` SHALL record explicit caller intent for the legacy-unknown provenance exception even when it equals the configured project default.

`create_venv` SHALL default to `false` and SHALL NOT come from a project manifest, VS Code profile, or cwd inference; only explicit `--create-venv` on the current checkout enables it. `hash_lock` and `hash_lock_sha256` SHALL default to `None`, SHALL be accepted only as a pair with `create_venv=True`, and SHALL NOT come from a manifest, profile, environment variable, or cwd inference. Checkout SHALL validate the pair, canonical regular-file path, lowercase SHA-256, and matching file bytes while constructing its immutable command and SHALL revalidate the digest immediately before the dependency process step.

`db_copy_strategy` SHALL default to `backup`. It SHALL be meaningful only for `db_mode=copy`. `shared` checkout SHALL reject `template`.

#### Scenario: Default shared checkout

- **WHEN** `EnvironmentCheckoutOptions()` is used unchanged and the project has no default base
- **THEN** `db_mode=SHARED`, `db_copy_strategy=BACKUP`, `create_venv=False`, `hash_lock=None`, `hash_lock_sha256=None`, and the effective base SHALL be `HEAD`

#### Scenario: Explicit source records legacy opt-in

- **WHEN** options explicitly contain `source_database="legacy_db"`
- **THEN** checkout MAY apply the warned unknown-provenance exception for that exact database

#### Scenario: Checkout hash-lock validation fails closed

- **WHEN** checkout receives only one paired input, a malformed or mismatched digest, a non-regular/unreadable path, or hash-lock inputs without `create_venv=True`
- **THEN** command construction SHALL fail before Git, uv, filesystem, catalogue, database, or environment mutation

#### Scenario: Checkout dry-run uses the immutable hash-lock plan

- **WHEN** public checkout dry-run receives a valid lock pair with `create_venv=True`
- **THEN** it SHALL expose the sanitized captured `uv pip sync --require-hashes` step and matching lock digest without spawning a process or changing any resource
- **THEN** later execution of that captured command SHALL consume the same argv, cwd, digest, and action order rather than rebuilding them

#### Scenario: Default copy strategy is backup

- **WHEN** checkout options select `db_mode=copy` and omit `db_copy_strategy`
- **THEN** the captured plan uses strategy `backup`

### Requirement: `copy` DB mode

COPY mode MUST select exactly one source for strategy `backup`: existing local source database, explicit named remote, or exact catalog backup UUID. The default local-source `backup` path MUST create a ZIP with filestore through the existing backup operation and record environment ownership. It MUST require a reachable local source HTTP endpoint; arbitrary remote URLs in a local source config remain rejected. The existing caller-owned local-archive restore source SHALL NOT become a fourth COPY checkout input. Strategy `template` is not a ZIP source and MUST follow `checkout-template-clone` instead of creating a backup id.

The explicit remote path MUST download through named-source preparation and retain that backup independently of environment ownership. The retained-backup path MUST skip source HTTP/download entirely. Both SHALL restore to the configured local project cluster, not the remote cluster. All `backup` paths MUST create a new target with copied filestore and neutralization, verify target existence and neutralization before ready, and never overwrite/reuse an existing target on retry.

Before creating owned checkout artifacts, every `backup` COPY source SHALL use the existing preflight target lookup and SHALL reject an existing target with `DatabaseAlreadyExistsError`. During `backup` execution, the checkout coordinator SHALL consume the immutable `database.restore.exists-before` step exactly once immediately before restore and SHALL consume `database.restore.exists-after` exactly once after restore. Selected catalog backups and named remotes SHALL NOT perform an additional nested target lookup that aliases either planned restore probe. Dry-run and execution SHALL use the same ordered command snapshot and SHALL NOT add a third existence probe or a source-specific execution context.

#### Scenario: Copy checkout success

- **WHEN** local COPY checkout selects strategy `backup`, an available local source, and a new target
- **THEN** it records an owned backup, restores and neutralizes the target and marks ready after postconditions

#### Scenario: Source Odoo unavailable

- **WHEN** local COPY strategy `backup` requires an unavailable source HTTP endpoint
- **THEN** checkout fails with auditable recovery state and can be cleaned through existing removal

#### Scenario: Remote source Odoo refused

- **WHEN** the local-source `backup` path receives a non-local source HTTP endpoint without a named remote selector
- **THEN** checkout rejects it under the existing local-only constraint

#### Scenario: Explicit named remote source

- **WHEN** COPY strategy `backup` explicitly selects a configured remote
- **THEN** only backup acquisition contacts that remote, and target mutation remains local

#### Scenario: Selected backup consumes the planned probes once

- **WHEN** COPY checkout executes strategy `backup` from an available catalog backup into an absent target
- **THEN** it consumes `database.restore.exists-before` once, restores the selected backup, consumes `database.restore.exists-after` once, and reaches the established ready result
- **THEN** its successful dry-run and execution use the same immutable ordered plan without duplicate consumption

#### Scenario: Named remote uses the coordinator-owned probes

- **WHEN** COPY checkout executes strategy `backup` from an explicitly named remote into an absent local target
- **THEN** backup acquisition is followed by the same single coordinator-owned before/after restore probe pair without a nested existence call consuming either step early

#### Scenario: Existing target remains protected

- **WHEN** any COPY source resolves to a target database that already exists during artifact-free preflight
- **THEN** checkout raises `DatabaseAlreadyExistsError` before restore or template clone and does not overwrite the target or mutate the selected/borrowed backup
