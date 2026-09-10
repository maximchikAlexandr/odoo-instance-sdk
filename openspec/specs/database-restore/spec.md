## Purpose

Restoring a catalog Backup onto a local Odoo instance with postcondition checks and restore tracking.
## Requirements
### Requirement: Восстановление базы

`instance.databases.restore()` MUST принимать существующий доступный `Backup`, target database name и параметры Odoo 19.0 `copy` и `neutralize_database`.

Перед HTTP request метод MUST проверить:

- local instance guard;
- наличие master password (raised `MasterPasswordRequiredError` if `None` — см. ADDED requirement "Mutating DB methods require password at call time" ниже);
- наличие соответствующей catalog row;
- state `available`;
- совпадение metadata объекта с catalog;
- существование и читаемость file;
- отсутствие target database.

Метод MUST отправлять multipart request в `POST /web/database/restore` и MUST NOT автоматически удалять существующую target database.

POST body MUST содержать `"name": target_database_name`.

Для copy checkout (`copy=True`) restore MUST использовать `neutralize_database=True`.

Target DB NEVER перезаписывается и не удаляется для повторной попытки автоматически.

После ответа Odoo метод MUST подтвердить `exists(target_name) == True`. HTTP 200 или redirect сам по себе MUST NOT считаться успехом.

Mapping write (шаги 1-2 ниже) MUST выполняться ТОЛЬКО после успешного `exists(target_name) == True` postcondition. Если postcondition fails (база не создалась), mapping MUST NOT быть записан.

После успешного postcondition, если инстанс имеет cluster-ключ (`db_port is not None`), метод MUST:
1. вызвать `catalog.record_restore(db_host, db_port, target_database_name, str(backup.id))`;
2. (record_restore вставляет restores row и database_events "restored" row атомарно — см. `database-restore-tracking` spec).

Для инстансов без cluster-ключа (`db_port is None`) метод MUST NOT писать в `restores` или `database_events`.

`restore()` вызывает `exists()` дважды (pre-guard и postcondition); каждый вызов MAY запускать reconciliation (через `list()`). Это приемлемо (идемпотентно). SDK MUST NOT оптимизировать, пропуская reconciliation.

#### Scenario: Restore catalog backup

- **WHEN** target instance локальный, target database отсутствует и передан доступный `Backup`
- **THEN** SDK восстанавливает database и возвращает result только после подтверждения через list endpoint

#### Scenario: Restore sends target name in POST body

- **WHEN** `restore(backup, target_database_name="comerta_x")`
- **THEN** POST body содержит `"name": "comerta_x"`

#### Scenario: Copy checkout restore neutralizes

- **WHEN** copy checkout вызывает `restore(..., copy=True, neutralize_database=True)`
- **THEN** target DB restored and neutralized

#### Scenario: Forged или stale Backup

- **WHEN** metadata объекта не совпадает с catalog либо file отсутствует
- **THEN** restore не отправляет HTTP request и выбрасывает типизированную backup error

#### Scenario: Restore с cluster-ключом пишет mapping

- **WHEN** `restore()` успешно выполнен на from_config()-инстансе с `db_host="localhost"`, `db_port=5432`, postcondition `exists()` подтверждён
- **THEN** catalog содержит restores row и database_events "restored" для target database

#### Scenario: Restore HTTP success но postcondition fail

- **WHEN** HTTP restore вернул 200, но `exists(target_name)` возвращает False
- **THEN** `RestoreFailedError` raises, restores и database_events НЕ пишутся

#### Scenario: Restore без cluster-ключа

- **WHEN** `restore()` успешно выполнен на __call__()-инстансе, postcondition подтверждён
- **THEN** HTTP restore завершён, restores и database_events не содержат новых строк

### Requirement: Модель запуска из готового backup

Поддерживаемый flow MUST начинаться с `Backup`, скачанного через `instance.databases.backup()` или найденного через `client.backups`.

SDK MUST NOT предоставлять создание пустой базы, module-selection resource, отдельный test resource или автоматическую политику повторного скачивания.

Решение использовать найденный backup или скачать новый MUST принимать вызывающий код по `Backup.downloaded_at`.

#### Scenario: Переиспользование свежего backup

- **WHEN** `client.backups.latest()` вернул существующий file
- **THEN** вызывающий код может сравнить `downloaded_at` со своим threshold и передать тот же `Backup` в restore

### Requirement: Mutating DB methods require password at call time

`backup()`, `restore()`, `drop()` MUST требовать master password в момент mutating DB call и поднимать `MasterPasswordRequiredError`, если `master_password is None`.

`MasterPasswordRequiredError` MUST NOT подниматься при construction instance (`from_config()`, `from_environment()`, `__call__()`).

`list()` и `exists()` MUST NOT требовать master password.

#### Scenario: Backup without password

- **WHEN** `instance.databases.backup()` и `master_password is None`
- **THEN** `MasterPasswordRequiredError` raised before HTTP request

#### Scenario: Restore without password

- **WHEN** `instance.databases.restore(backup, "target")` и `master_password is None`
- **THEN** `MasterPasswordRequiredError` raised before HTTP request

#### Scenario: List without password

- **WHEN** `instance.databases.list()` и `master_password is None`
- **THEN** list succeeds (no password needed for read)

### Requirement: Observable restore-plan execution

Restore execution SHALL emit typed lifecycle events for logical plan-step start, completion, and failure. When explicitly requested, process-backed steps SHALL also emit sanitized stdout/stderr chunks associated with that step. Event observation SHALL not change plan contents, execution order, return values, exit codes, or captured subprocess output, and secrets SHALL be redacted before an event reaches a consumer.

#### Scenario: Step lifecycle is observable
- **WHEN** a multi-step restore executes with an observer
- **THEN** each executed logical step produces ordered start and terminal events with stable step identity

#### Scenario: Streaming is opt-in and redacted
- **WHEN** command-output streaming is enabled and a restore child writes stdout or stderr containing a secret
- **THEN** the consumer receives step-associated sanitized chunks while the final captured result preserves its existing contract

### Requirement: Restore from a registered local backup

CLI-private/internal restore orchestration SHALL accept an exact available catalogue backup UUID and a project PostgreSQL target, perform no remote Odoo backup request or repeat download, and delegate through existing public SDK methods to the existing restore implementation for validation, neutralization, postconditions, and restore audit. It SHALL add no public SDK method. One backup UUID SHALL remain reusable for multiple restores into distinct new database names.

#### Scenario: Registered backup is restored

- **WHEN** an available backup UUID passes identity, readability, checksum, format, cluster, and target-name preflight
- **THEN** the existing restore path creates the new database, confirms its postcondition, and records provenance to that UUID
- **AND** the source archive remains available

#### Scenario: Backup cannot be used

- **WHEN** the UUID is unknown, not available, missing, unreadable, changed, corrupt, or unsupported
- **THEN** restore fails before database creation and before project default configuration changes

#### Scenario: Target already exists

- **WHEN** the requested exact target exists in the selected cluster
- **THEN** restore refuses and does not overwrite, drop, rename, or select another database

#### Scenario: Same UUID is restored twice

- **WHEN** two invocations use one UUID with two free target names
- **THEN** both restore audit records retain the same backup UUID and distinct database identities

#### Scenario: Public restore methods remain canonical

- **WHEN** local UUID restore orchestration is added and public methods are characterized
- **THEN** `test_discovered_public_methods` reports the unchanged canonical method set

### Requirement: Restore failure and interruption retention

Restore failure or Ctrl-C SHALL preserve the source backup and SHALL report the known target database plus whether database creation and atomic project-default switching were confirmed. It SHALL not present a partial restore as success or automatically delete a confirmed database.

#### Scenario: Interrupt during restore

- **WHEN** Ctrl-C occurs after the target name is reserved but before restore completion
- **THEN** the CLI exits 130, keeps the backup, reports the target and known database/default-switch state, and closes the renderer

#### Scenario: Default switch fails

- **WHEN** database restore succeeds but the atomic project configuration switch fails
- **THEN** the operation fails with the restored database reported as retained and the prior default remains authoritative

### Requirement: Replace an existing COPY environment from a retained backup

CLI-private/internal replacement orchestration SHALL reuse the existing catalogue-backup validation, project PostgreSQL ownership, preparation/restore, postcondition, locking, progress, dry-run, confirmation, failure-context and audit primitives. Under canonical environment, backup and cluster locks, it SHALL revalidate a stopped, non-removed COPY environment, its exact target and absence of active target-database sessions before mutation. With no explicit force contract, it SHALL NOT terminate active sessions. It SHALL reject a caller-supplied `--target`, move the current proven-owned database and contained filestore to unique rollback names, restore the selected database and matching filestore to the unchanged exact target name, apply the existing requested admin-password reset, confirm the restored database and provenance, and only then remove the rollback artifacts. The environment ID, name, branch, worktree, generated config, HTTP binding, target database, and Python binding SHALL remain unchanged. Shared/source databases SHALL never be accepted by this route.

#### Scenario: Successful replacement preserves identity

- **WHEN** a stopped COPY environment and retained backup pass all planning and execution revalidation
- **THEN** its exact target database and filestore contain the selected backup, its environment identity/bindings are unchanged, and current provenance points to that backup

#### Scenario: Database and filestore match

- **WHEN** replacement reports success
- **THEN** both the database and filestore originate from the same validated backup and rollback artifacts are absent

#### Scenario: Dry-run is exact and inert

- **WHEN** replacement is invoked with `--dry-run`
- **THEN** the plan exposes sanitized exact process commands and honest database/filesystem/catalogue actions without locking for mutation or changing any resource

#### Scenario: Active target sessions block replacement

- **WHEN** any active target-database session is observed during planning or execution revalidation
- **THEN** replacement terminates no session and starts no database, filestore, catalogue or configuration mutation

#### Scenario: Exact recorded target is mandatory

- **WHEN** replacement receives the existing `--target` option even if it names the recorded database
- **THEN** it rejects the conflicting option before mutation instead of allowing target substitution

### Requirement: Replacement compensation and retry state

If replacement fails after the prior database or filestore is moved aside, including failure of a requested existing admin-password reset before final provenance commit, compensation SHALL remove only newly created artifacts whose ownership is proven and restore both prior rollback artifacts to the exact target names. Proven successful compensation SHALL leave the previous backup/provenance authoritative. If full compensation cannot be proven, the environment SHALL enter an explicit `cleanup_failed` retryable state, retain all known artifacts and sanitized identities, and SHALL NOT advertise the new backup as active. Repeating replacement or removal SHALL re-read and validate those retained identities before any mutation.

#### Scenario: Restore fails and compensation succeeds

- **WHEN** the new restore fails after the old database and filestore are moved aside and both can be restored safely
- **THEN** the previous usable database, filestore and provenance are restored and the new backup is not advertised as active

#### Scenario: Compensation is incomplete

- **WHEN** a replacement failure cannot safely restore every prior artifact or remove every partial new artifact
- **THEN** the environment records `cleanup_failed` with retained-artifact evidence and no false active-backup claim

#### Scenario: Concurrent identity changes

- **WHEN** any database, filestore, backup, cluster or environment identity differs at execution revalidation
- **THEN** replacement aborts before mutation or compensates the already-started stage without touching an unproven resource

#### Scenario: Requested admin-password reset fails

- **WHEN** the existing requested admin-password reset fails after restore but before final provenance commit
- **THEN** replacement applies the same compensation or `cleanup_failed` contract and does not advertise the selected backup as active
