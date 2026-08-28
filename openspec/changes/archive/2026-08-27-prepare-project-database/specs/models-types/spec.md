## ADDED Requirements

### Requirement: Database preparation result types

Database preparation and provenance comparison SHALL use frozen `msgspec.Struct` data models and stable `StrEnum` values rather than CLI dictionaries. The shared typed result SHALL include operation mode, backup identity/path/size/checksum/download time, nullable source Git branch, branch origin (`explicit`, `configured`, `unknown`), optional restored database, whether admin reset and default switch completed, prior/effective defaults, retained-artifact state, and warnings. Secrets SHALL not be fields.

Checkout provenance SHALL use the stable values `matched`, `mismatched`, and `unknown`, with expected base ref and nullable recorded branch represented explicitly.

`EnvironmentCheckoutPlan` SHALL be the public frozen typed output of `EnvironmentResource.plan_checkout()` with exactly these fields: `name: str`, `branch: str`, `effective_base_ref: str`, `db_mode: EnvironmentDatabaseMode`, `source_database: str | None`, `target_database: str | None`, `python_mode: EnvironmentPythonMode`, `provenance: BackupProvenanceComparison`, `freshness: BackupFreshness`, `preparation_actions: tuple[DatabasePreparationAction, ...]`, and `warnings: tuple[str, ...]`. It SHALL contain no config values, passwords, filesystem paths, executable/argv values, prospective UUID, or private execution plan.

`EnvironmentCheckoutResult` SHALL contain only `environment: DevelopmentEnvironment` and the final secret-free `plan: EnvironmentCheckoutPlan` recalculated after preparation. Additive `EnvironmentResource.checkout_with_plan()` SHALL return it, while canonical `checkout()` SHALL continue returning `DevelopmentEnvironment`. `commands/env.py` SHALL consume `plan_checkout()`/`checkout_with_plan()` directly for Rich and shared JSON/TOON projection; private `_CheckoutPlan` remains internal and no CLI-only checkout-plan dictionary or DTO SHALL exist. `AdminPasswordResetResult` SHALL contain the bound database, completion state, XML ID, and an optional environment ID rather than requiring an environment for refresh.

#### Scenario: Download result is adapter-neutral

- **WHEN** a download-only refresh succeeds
- **THEN** the SDK returns a frozen typed result containing backup audit/provenance fields and no CLI envelope or secret field

#### Scenario: Unknown provenance is typed

- **WHEN** a legacy backup has no source branch
- **THEN** checkout planning represents the comparison as `unknown`, not an empty string or transport-specific sentinel

#### Scenario: Checkout models are adapter-neutral

- **WHEN** dry-run plans checkout or execution completes it
- **THEN** the CLI-facing additive methods return the public plan/result models, every CLI format projects those same models without `_checkout_plan_dict`, and canonical `checkout()` still returns `DevelopmentEnvironment`

### Requirement: Backup source branch model field

`Backup` SHALL add `source_git_branch: str | None = None`. The field SHALL carry declarative source provenance from catalog through list/latest/restore lookup and serialization. Legacy rows SHALL produce `None`. It SHALL not contain a commit SHA inferred from local or remote Git state.

#### Scenario: Provenance survives restore lookup

- **WHEN** a backup with `source_git_branch="release/19"` is restored and later loaded through the restore mapping
- **THEN** the returned `Backup.source_git_branch` equals `release/19`

## MODIFIED Requirements

### Requirement: NoBackup nullable model

`NoBackup` MUST быть `msgspec.Struct` с `frozen=True, forbid_unknown_fields=True` и теми же полями, что `Backup`, но нулевыми значениями:

```python
class NoBackup(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    id: uuid.UUID = uuid.UUID(int=0)
    source_base_url: str = ""
    database_name: str = ""
    format: BackupFormat | None = None
    filestore_requested: bool = False
    path: str = ""
    filename: str = ""
    size_bytes: int = 0
    sha256: str = ""
    downloaded_at: datetime = datetime.fromtimestamp(0, UTC)
    source_git_branch: str | None = None
```

Все default-значения MUST быть статическими (вычисляются при определении класса, НЕ `field(default_factory=...)`). `uuid.UUID(int=0)` (NIL UUID) и `datetime.fromtimestamp(0, UTC)` — frozen значения, безопасны как статические defaults.

`models.py` MUST импортировать `UTC` из `datetime`: `from datetime import UTC, datetime`.

Все поля MUST иметь default-значения, позволяя `NoBackup()` без аргументов.

`NoBackup` намеренно БЕЗ `kw_only=True` (соответствует `Backup`, который тоже без `kw_only`), чтобы поддерживать `NoBackup()` без аргументов через defaults.

Модель MUST NOT содержать методов с side effects.

Caller MAY различать `Backup` и `NoBackup` через `db.backup.format is not None` или `db.backup.id != uuid.UUID(int=0)`.

#### Scenario: Доступ к полям NoBackup

- **WHEN** `db.backup` is `NoBackup()`
- **THEN** `db.backup.downloaded_at` returns `datetime.fromtimestamp(0, UTC)`, `db.backup.format` returns `None`, `db.backup.id` returns `uuid.UUID(int=0)`, and `db.backup.source_git_branch` returns `None`

#### Scenario: Конструкция без аргументов

- **WHEN** `NoBackup()` вызывается без аргументов
- **THEN** возвращается instance со всеми нулевыми default-значениями
