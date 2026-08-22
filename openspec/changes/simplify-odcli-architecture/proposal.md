## Why

После #3 `cli.py` (~1400 строк) копирует один скелет на каждую instance-команду и сам пишет в catalog. Следующая команда скопирует его ещё раз. Поведение зелёное — можно удалить копии, не меняя контракт.

## What Changes

- **MODIFIED `cli-odcli`**: один `ready_instance(ctx)` закрывает resolve/verify/`from_environment()` для `run`/`shell`/`eval`/`exec`/`module`/`translations`/`deps`/`vscode`. Один emit/fail. Printers и env list не вызывают `get_catalog()`. `use` пишет существующий `EnvironmentResource`, не CLI. Команды, JSON v1, exit codes не меняются.
- `cli.py` остаётся registrar + тонкие Click wrappers. Жир env-команд (options, plan dicts) уходит в `internal/cli_env.py`, только если после collapse `cli.py` всё ещё толстый.
- Catalog файл не пилим: `BackupCatalog` уже owner одного SQLite. Дыра была в CLI→catalog, не в SQL в одном классе.

## Capabilities

### New Capabilities

- Нет.

### Modified Capabilities

- `cli-odcli`: один instance path; rendering не пишет persistence; CLI не открывает catalog.

## Impact

- **API / CLI / schema**: без breaking changes. На существующий `EnvironmentResource` добавляется `record_use()` — не новый resource, не новый facade.
- **Code**: `ready_instance` + emit рядом с `internal/context.py`; instance-команды сжимаются до обёрток; `_record_use_event` / `_make_client` / четыре fail-helper удаляются.
- **Out of scope**: баги #3, schema bump, mixin-stores, vscode argv rewrite, `asdict`, dashboard #11, template clone #4.
