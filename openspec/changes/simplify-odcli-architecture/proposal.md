## Why

После issue #3 `odcli` работает, но внутренности разъехались: один `cli.py` (~1400 строк) одновременно регистрирует Click, резолвит context, сериализует JSON, рисует таблицы и пишет `use`/`last_used_at` прямо в catalog; `BackupCatalog` владеет schema, backup/restore history, environments и copy journal. Это повышает стоимость ревью и риск расхождения контрактов при следующих командах. Issue #3 уже в `main` — можно упрощать структуру, не меняя поведение.

## What Changes

- **MODIFIED capability `cli-odcli`**: корневой модуль `odoo_instance_sdk.cli` остаётся entry point `odcli = "odoo_instance_sdk.cli:cli"`, но содержит только group registration и global options. Командные группы переезжают в focused internal modules. Повторяющиеся resolve/verify/instance/output живут в одном typed application context. CLI rendering MUST NOT писать lifecycle persistence. Имена команд, options, exit codes, human output и JSON envelope v1 не меняются.
- **MODIFIED capability `backup-catalog`**: один SQLite, один connection/schema owner, одна migration chain. Environment persistence отделяется от backup/restore history cohesive internal components. Нет public repository/factory, второго catalog, второго SQLite и pass-through wrappers, которые только переименовывают методы.
- **MODIFIED capability `development-environment`**: запись `last_used_at` и `use/succeeded` уходит с CLI output helpers за environment/application boundary. Public `EnvironmentResource` surface (`checkout`/`sync_python`/`get`/`list`/`remove`) не расширяется новым resource.
- Точечные упрощения без смены контракта: один success/error serialization path; VS Code argv parser без single-use handler indirection; stdlib serialization вместо hand-written dataclass dumps только там, где JSON shape идентичен; `ast.literal_eval()` не внедрять — launch args уже list из json5; удаление мёртвых helpers.
- Публичные command names, options, JSON v1 keys (`result`+`data`, `provenance`, `dry_run`), human output и exit codes issue #3 — freeze; существующие CLI contract tests остаются источником истины.

## Capabilities

### New Capabilities

- Нет. Это post-feature maintainability refactor, не новая user-facing capability.

### Modified Capabilities

- `cli-odcli`: внутреннее разложение CLI, typed application context, запрет catalog writes из rendering, единый output path. Публичные команды и JSON schema не меняются.
- `backup-catalog`: cohesive split environment persistence vs backup/restore history при одном SQLite owner.
- `development-environment`: lifecycle `use` persistence за environment/application boundary, не из CLI rendering.

## Impact

- **API**: публичный Python SDK и CLI contracts issue #3 не меняются. Нет нового public resource, repository interface, factory, daemon, второго SQLite. `OdooClient` остаётся фасадом `instance`/`backups`/`environments`. `get_catalog()` не становится public `client.catalog`.
- **Storage**: schema version и таблицы не меняются (сейчас `PRAGMA user_version = 7`). Меняется только организация кода вокруг одного connection owner.
- **Dependencies**: не добавляются. Не add ORM, persistence framework, CLI framework besides Click.
- **Code**: `src/odoo_instance_sdk/cli.py` → тонкий registrar + `internal/cli/`; typed context рядом с `internal/context.py`; `storage/backup_catalog.py` → schema owner + cohesive internal modules; `internal/vscode_import.py` argv loop; CLI tests, которые патчат `_make_client` / `_resolve_ready_env`, переезжают на application context.
- **CLI**: entry point, help surface, `--json` envelope, exit codes `0|1|2|130` без изменений.
- **Out of scope**: correctness/security gaps issue #3; PostgreSQL cluster management; new Odoo lifecycle; dashboard #11; fast template clone #4.
