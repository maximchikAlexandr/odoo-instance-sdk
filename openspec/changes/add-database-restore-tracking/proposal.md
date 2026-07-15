## Why

Пользователю SDK нужно понимать, насколько свежий backup развёрнут на конкретной базе инстанса. Сейчас `restore()` выполняет HTTP, подтверждает `exists()` и забывает — каталог знает про backup'ы, но не про то, куда они были восстановлены. Нужна явная связь (database ↔ backup) с возможностью в любой момент получить `database.backup.downloaded_at`.

## What Changes

- **BREAKING**: `databases.list()` возвращает `tuple[Database, ...]` вместо `tuple[str, ...]`.
- Новая модель `Database` (`msgspec.Struct`, `frozen=True, forbid_unknown_fields=True, kw_only=True`): `name: str`, `backup: Backup | NoBackup`.
- Новая модель `NoBackup` (`msgspec.Struct`, `frozen=True, forbid_unknown_fields=True`) — nullable-объект с нулевыми значениями полей Backup (`uuid.UUID(int=0)`, `""`, `0`, `False`, `datetime.fromtimestamp(0, UTC)`, `format: BackupFormat | None = None`).
- Новые поля `InstanceConfig`: `db_host: str | None`, `db_port: int | None`, `db_user: str | None`, `db_password: str | None` (repr=False только для db_password) — cluster-ключ для restore-tracking; заполняются из `StartConfig` через `from_config()`.
- Новые таблицы в `backups.sqlite3` (schema v2): `restores` (append-only) и `database_events` (append-only, `restored` | `dropped`).
- `databases[0]` — индексирование по позиции из `list()` через `__getitem__`.
- `databases.current()` — возвращает `Database` для `configured_database_names[0]` (nullable-object с `name=""` если пусто/None); не угадывает default, а берёт явно указанное в odoo.conf имя.
- Ленивая сверка: `list()`/`exists()` проверяют соответствие restores-строк актуальному состоянию баз; пропавшая база → `database_events "dropped"` (идемпотентно).
- Fallback-verify через `psql -t -A` когда Odoo недоступен (для `from_config()`-инстансов с cluster-ключом); `psql` non-zero exit / timeout → inconclusive (`NoBackup`, без `dropped`).
- `restore()` пишет `restores` row + `database_events "restored"` ТОЛЬКО после успешного `exists(target_name) == True` postcondition (только для инстансов с cluster-ключом).
- `drop()` пишет `database_events "dropped"` (только для инстансов с cluster-ключом).

## Capabilities

### New Capabilities

- `database-restore-tracking`: persist restore history и backup-mapping per database в SQLite catalog; ленивая сверка с актуальным состоянием баз; psql fallback.

### Modified Capabilities

- `models-types`: новые модели `Database`, `NoBackup`; новые поля `InstanceConfig` (`db_host`, `db_port`, `db_user`, `db_password`).
- `backup-catalog`: schema v2, таблицы `restores` и `database_events`, миграция v0 → v2.
- `database-management`: `list()` возвращает `Database`, новые `__getitem__` и `current()`, `drop()` пишет `database_events`, сверка с catalog.
- `database-restore`: `restore()` пишет restore-mapping после postcondition; restore-tracking ограничен cluster-ключом.

## Impact

- **API**: `databases.list()` — breaking change (тип возвращаемого значения). Новые методы `__getitem__`, `current()`. Новые поля `InstanceConfig`.
- **Storage**: `BackupCatalog` schema миграция v0 → v2 (`PRAGMA user_version`). Новые таблицы, индексы по `(db_host, db_port, database_name)`.
- **Dependencies**: `psql` через `shutil.which` для fallback-verify (аналог `pg_restore`).
- **Code**: `DatabaseResource`, `BackupCatalog`, `InstanceConfig`, `models.py`. Тесты для миграции и сверки.
- **Restore-tracking scope**: только инстансы из `from_config()` (имеют cluster-ключ `db_port`). `__call__()`-инстансы (db_port=None): restore работает, mapping не пишется, `.backup` = `NoBackup`. Socket (`db_host=None`, `db_port` set) — cluster-ключ доступен, `db_host` нормализуется в `"socket"`.