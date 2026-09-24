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

`backup()`, `restore()`, and `drop()` SHALL still require the Odoo master password at call time and SHALL raise `MasterPasswordRequiredError` when it is absent, exactly as in the existing contract. The admin password reset is a separate, explicit secret assignment, distinct from the master password, using the same rules as `odcli db reset-admin-password` in `cli-odcli` (RICH `getpass` twice; otherwise `ODCLI_ADMIN_PASSWORD`; `--dry-run` does not prompt; missing secret is `admin_password_required`). After a successful reset, login as `base.user_admin` SHALL succeed on Odoo 13 and Odoo 19 via the real-Odoo XML-RPC test-support probe.

#### Scenario: Backup without password

- **WHEN** `instance.databases.backup()` и `master_password is None`
- **THEN** `MasterPasswordRequiredError` raised before HTTP request

#### Scenario: Restore without password

- **WHEN** `instance.databases.restore(backup, "target")` и `master_password is None`
- **THEN** `MasterPasswordRequiredError` raised before HTTP request

#### Scenario: List without password

- **WHEN** `instance.databases.list()` и `master_password is None`
- **THEN** list succeeds (no password needed for read)

#### Scenario: interactive reset prompts hidden

- **WHEN** a restore with `--reset-admin-password` runs with RICH output and without `--no-input`
- **THEN** the new password is prompted hidden with confirmation and is not echoed

#### Scenario: dry-run does not prompt

- **WHEN** a restore with `--reset-admin-password --dry-run` runs
- **THEN** no prompt occurs and no mutation occurs

#### Scenario: non-interactive reset without secret fails before mutation

- **WHEN** a restore with `--reset-admin-password --yes` runs without `ODCLI_ADMIN_PASSWORD` and without a RICH prompt path
- **THEN** it fails with `admin_password_required` before any restore/drop mutation

#### Scenario: secret provenance is reported

- **WHEN** a reset succeeds
- **THEN** the result reports the fact and provenance (`prompt` or `environment`) and the secret is absent from all outputs

#### Scenario: no implicit admin fallback

- **WHEN** a reset runs without a provided secret
- **THEN** no implicit `admin` or other common value is assigned

#### Scenario: admin login works after reset

- **WHEN** restore or `db reset-admin-password` succeeds on Odoo 13 or Odoo 19
- **THEN** XML-RPC login as `base.user_admin` with the assigned secret succeeds

### Requirement: Observable restore-plan execution

Restore execution SHALL emit typed lifecycle events for logical plan-step start, completion, and failure. When explicitly requested, process-backed steps SHALL also emit sanitized stdout/stderr chunks associated with that step. Event observation SHALL not change plan contents, execution order, return values, exit codes, or captured subprocess output, and secrets SHALL be redacted before an event reaches a consumer.

#### Scenario: Step lifecycle is observable
- **WHEN** a multi-step restore executes with an observer
- **THEN** each executed logical step produces ordered start and terminal events with stable step identity

#### Scenario: Streaming is opt-in and redacted
- **WHEN** command-output streaming is enabled and a restore child writes stdout or stderr containing a secret
- **THEN** the consumer receives step-associated sanitized chunks while the final captured result preserves its existing contract

### Requirement: Restore from a registered local backup

Restore SHALL record `data_dir` `{project_root}/.odcli/filestore` from the effective self-contained config in the restore binding alongside cluster/database/backup identity. Before restore, that path SHALL be verified to be a regular directory inside the project tree and not a symlink. `db rm` SHALL delete only the exact contained non-symlink filestore and SHALL return `deleted` or `absent` with the path. External config without a provable `data_dir` SHALL stay fail-closed `unknown`; no directory SHALL be deleted by database name or platform default.

For a self-contained project, auxiliary restore SHALL use bootstrap database `tmp` and SHALL NOT add hidden `--database=__odcli_restore__` or `--db-filter=^$`. After a successful restore, the project default SHALL switch to the restored database; `tmp` SHALL remain the bootstrap database and SHALL NOT be treated as a working backup copy. The regression flow (fresh owned Compose cluster → `tmp` init → stopped-project `db restore` → Database Manager `303` → PostgreSQL postcondition → default DB switch) SHALL be covered for Odoo 13 and Odoo 19.

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

#### Scenario: restore records data_dir

- **WHEN** a self-contained restore completes
- **THEN** the restore binding contains the canonical project-owned `data_dir` together with cluster/database/backup identity

#### Scenario: db rm deletes the proven filestore

- **WHEN** `odcli db rm <DB> --force-connections --yes` runs after a self-contained restore
- **THEN** the exact contained non-symlink filestore is deleted and the result reports `deleted` or `absent` with the path

#### Scenario: external config without data_dir stays unknown

- **WHEN** the effective config has no provable `data_dir`
- **THEN** `db rm` returns `filestore_state = unknown` and no directory is deleted by database name or platform default

#### Scenario: auxiliary restore uses tmp

- **WHEN** a self-contained project runs `db restore`
- **THEN** auxiliary restore uses `tmp` and does not add hidden `--database=__odcli_restore__` or `--db-filter=^$`

#### Scenario: restore switches default and keeps tmp as bootstrap

- **WHEN** a self-contained restore completes successfully
- **THEN** the project default switches to the restored database and `tmp` remains the bootstrap database

#### Scenario: regression flow for Odoo 13 and Odoo 19

- **WHEN** the regression flow runs (fresh owned Compose cluster → `tmp` init → stopped-project `db restore` → Database Manager `303` → PostgreSQL postcondition → default DB switch)
- **THEN** each step succeeds for Odoo 13 and Odoo 19

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

### Requirement: Restore while project Odoo is stopped
Project restore SHALL either capture and run a bounded auxiliary Database Manager using the existing runtime/config or select an existing safe non-HTTP restore path. If neither is possible, preflight SHALL fail before target reservation/creation with an actionable exact recovery command. Existing backup, target, filestore, ownership, postcondition, compensation, and default-switch guarantees SHALL remain. [Source: GH#64 §2]

#### Scenario: Restore valid ZIP on a free project port
- **WHEN** project Odoo is stopped and a valid filestore ZIP is restored
- **THEN** the command completes without manual startup, confirms database and filestore, switches effective default to target, and cleans up only its owned auxiliary runtime

#### Scenario: Listener is unrelated
- **WHEN** a foreign listener occupies the candidate port or auxiliary startup fails
- **THEN** the listener is neither used nor stopped and no false success or unconfirmed default is left

#### Scenario: Retry retained artifact
- **WHEN** restore is retried after a controlled interruption
- **THEN** it safely resumes or compensates the retained artifact without duplicate target or lost provenance

### Requirement: COPY-restore preserves managed PostgreSQL cluster identity

When a COPY environment restore is performed, the instance used for the copy restore SHALL carry the same active managed PostgreSQL cluster identity as an environment-bound instance, so `record_restore()` stores a `cluster_id` matching the active owned cluster claim. No new parameter is added to the public `record_restore()` signature. Absence of a claim SHALL NOT be substituted with a guess. The guarded direct PostgreSQL drop SHALL then work for fresh COPY environments because `restores.cluster_id` matches the active managed cluster.

When guarded drop-plan construction fails, the command SHALL report a sanitized primary reason and SHALL NOT swallow the failure into an uninformative `None` while retaining fail-closed behavior. Checks for dirty worktree, active runtime, cluster/volume identity, restore binding, and related resources SHALL NOT be weakened.

#### Scenario: Fresh COPY-restore records cluster_id

- **WHEN** a COPY checkout restores a fresh backup on a managed cluster
- **THEN** the new restore row has a `cluster_id` matching the active owned cluster claim

#### Scenario: Env rm shows guarded drop step

- **WHEN** `odcli env rm ENVIRONMENT_UUID --dry-run` runs for such a COPY environment
- **THEN** the plan contains the guarded database drop step

#### Scenario: Drop-plan failure reports primary reason

- **WHEN** guarded drop-plan construction fails
- **THEN** the command reports a sanitized primary reason instead of an uninformative `None`

### Requirement: Doctor MAY surface missing data_directory in a restore binding

`odcli doctor` MAY emit an existing-style finding when a restore binding exists without a `data_directory`. Doctor SHALL NOT auto-assign ownership, SHALL NOT delete filestore by database name, and this change SHALL NOT add a doctor subsystem.

#### Scenario: doctor does not auto-claim unknown paths

- **WHEN** doctor encounters an unknown existing filestore path without a proven binding
- **THEN** it does not assign ownership and does not delete the path
