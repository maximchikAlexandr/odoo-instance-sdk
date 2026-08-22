## 1. Application context and shared output

- [ ] 1.1 Добавить `internal/cli/app.py`: typed `CliApp` с `client()`, `resolve_project()`, `resolve_environment()`, `require_ready_environment()`, `verify_runtime()`, `port_is_free()`, `instance_from()`, `provenance()`; delegation в существующий `internal/context.py`
- [ ] 1.2 Добавить `internal/cli/output.py`: один JSON envelope v1 с полным набором ключей `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, `warnings`; success — одинаковые `result` и `data`; error — `error.code` + sanitized message; human/error path с exit `1` / usage `2`
- [ ] 1.3 Добавить internal helper `record_use(env)` рядом с environment persistence (не public `EnvironmentResource` method): пишет `last_used_at` + `use/succeeded`; `CliApp.record_use()` — единственный CLI caller; `output.py` catalog не импортирует
- [ ] 1.4 Подключить `CliApp` в текущий `cli.py` (`ctx.obj = CliApp`) и перевести `run`/`shell`/`env list` на context+output без смены JSON/human контракта
- [ ] 1.5 Тесты: `record_use` пишет event; port-conflict не пишет use; output helpers не вызывают catalog; существующие CLI contract tests зелёные

**AC coverage**: typed context, rendering не мутирует persistence, single serialization path

## 2. CLI module split

- [ ] 2.1 Вынести `init` в `internal/cli/init.py`; корневой `cli.py` только регистрирует команду
- [ ] 2.2 Вынести `env checkout|list|remove|sync` и data adapters (`_env_dict`, `_checkout_plan_dict`, `_remove_plan_dict`, list reconciliation) в `internal/cli/env.py`
- [ ] 2.3 Вынести `run`/`shell` в `internal/cli/runtime.py`; `doctor` — в `internal/cli/doctor.py` (логика checks остаётся в `internal/doctor.py`)
- [ ] 2.4 Вынести `eval`/`exec`/`module`/`translations`/`deps` в `internal/cli/automation.py`; `vscode generate` — в `internal/cli/vscode.py`
- [ ] 2.5 Оставить в `cli.py` только group, `--project`/`--env` и `add_command`; entry point `odoo_instance_sdk.cli:cli` и `from odoo_instance_sdk.cli import cli` без изменения
- [ ] 2.6 Обновить test patches с `odoo_instance_sdk.cli._make_client` / `_resolve_ready_env` / `_check_port_free` на `CliApp.client` / `require_ready_environment` / `port_is_free`; не оставлять compatibility shims
- [ ] 2.7 Тесты: help surface; import entry point; command modules не конструируют `OdooClient` сами; public CLI assertions issue #3 без смены expected keys/exit codes

**AC coverage**: root module registration-only, public CLI contracts unchanged

## 3. Catalog cohesion

- [ ] 3.1 Вынести backup/backup_events SQL в `storage/backups.py` как mixin `BackupStore` с текущими method names (`start_download`, `success_download`, `list_backups`, …)
- [ ] 3.2 Вынести restores/database_events SQL в `storage/restores.py` как mixin `RestoreStore`
- [ ] 3.3 Вынести environments/environment_events/copy journal SQL в `storage/environments.py` как mixin `EnvironmentStore`; internal `record_use` helper использует этот mixin, `CliApp` не пишет SQL сам
- [ ] 3.4 Оставить `BackupCatalog` schema owner: connect, pragma, migrations v1→v7, `close`; `class BackupCatalog(BackupStore, RestoreStore, EnvironmentStore)`; mixins не импортируют друг друга
- [ ] 3.5 Не добавлять public repository/factory, `client.catalog`, второй SQLite, schema bump и pass-through wrappers
- [ ] 3.6 Тесты: один `catalog.sqlite3`; backup write и environment write — один connection/schema owner; `import odoo_instance_sdk` не даёт repository types; существующие catalog/environment/backup tests зелёные

**AC coverage**: one SQLite owner, cohesive environment persistence, no new public persistence API

## 4. Targeted simplification

- [ ] 4.1 Заменить `_handle_config|_db|_port|_dev|_dropped|_overlay` в `vscode_import.py` одной table-driven loop; mapping и Comerta fixture контракт без изменений
- [ ] 4.2 Заменить hand-written dataclass dumps на stdlib helpers только где JSON keys/types идентичны; иначе оставить explicit mapping
- [ ] 4.3 Аудит custom literal parsing: `ast.literal_eval()` не внедрять — launch args уже list из json5
- [ ] 4.4 Удалить мёртвые helpers после split (`_emit_json`, `_emit_command_json`, `_fail`/`_env_fail`/`_usage_fail` duplicates, `_make_client` в `cli.py`)
- [ ] 4.5 Тесты: `tests/unit/project/test_vscode_import.py` и `test_cli_init.py` без смены expected mapping; net implementation size уменьшился

**AC coverage**: targeted simplification, net size decrease

## 5. Quality gates

- [ ] 5.1 pytest (unit + integration, как в текущем CI) до и после каждого slice; падения только от сломанных private patches, не от смены контракта
- [ ] 5.2 ruff + strict mypy чистые
- [ ] 5.3 Проверить, что `__all__` / public types не выросли и `client.catalog` отсутствует
- [ ] 5.4 Сверить issue #9 AC: behavior #3 unchanged; root CLI wiring-only; one typed context; rendering не пишет persistence; one SQLite; no new public API; duplicates removed or justified

**AC coverage**: full quality gate, issue #9 acceptance
