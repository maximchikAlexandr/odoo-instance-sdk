## Why

SDK уже умеет запускать/останавливать локальный Odoo, делать backup/restore/drop и хранить backup-каталог. Но разработчику или агенту, который хочет изолированно поработать над веткой Odoo-проекта, не хватает короткого core loop: создать изолированный worktree с собственным `odoo.conf`, переиспользовать или создать Python-окружение, привязать БД (общую или копию), запустить Odoo/shell из этого окружения одной командой и потом cleanly удалить его. Сегодня всё это делается вручную: Git worktree, копирование `odoo.conf`, правка портов/путей, активация venv, привязка БД — и нет единого реестра, который бы знал, какие окружения существуют, чем они владеют и как их убрать.

Issue #3 добавляет агент-ориентированный CLI `odcli` и минимальный Python API для жизненного цикла локального Odoo-окружения. Первый продукт — короткий core loop, а не IDE вокруг Odoo.

## What Changes

- **NEW capability `project-init`**: `odcli init` создаёт idempotent secret-free project manifest `.odcli/project.toml` интерактивно, headless или через импорт VS Code launch profile; runtime artifacts остаются в platformdirs user directories и связываются с project через canonical Git common dir.
- **NEW capability `development-environment`**: публичные типы `ProjectConfig`, `DevelopmentEnvironment`, `EnvironmentCheckoutOptions`, `EnvironmentState`, `EnvironmentDatabaseMode`; `EnvironmentResource` (`checkout`/`sync_python`/`get`/`list`/`remove`) как `OdooClient.environments`. Сюда же входят preflight, worktree, generated `odoo.conf`, DB modes, Python reuse/create, `env sync`/`list`/`remove`. Git/`uv`/flock/generated config internal. Нет public `history()`, `EnvironmentEvent`, `runtime_json` на модели.
- **NEW capability `instance-runtime-binding`**: `InstanceConfig.command_prefix`/`default_cwd`; `from_environment()`; `from_config()` без обязательного пароля и без Python prefix (`command_prefix=None`, fallback `OdooClientConfig.executable`).
- **NEW capability `cli-odcli`**: один Click entry point `odcli`; command surface `init`, `env checkout|sync|list|remove`, `run`, `shell`, `doctor`, `eval`, `exec`, `module`, `translations export`, `deps verify`, `vscode generate`; two-rule context; `--json`; `doctor` read-only coordinator.
- **MODIFIED capability `backup-catalog`**: catalog переезжает из `user_cache_dir` в durable `user_data_dir`; schema v2 → v3 миграция; `BackupCatalog` internal, public API `client.backups` + `client.environments`.
- **MODIFIED capability `client-config`**: `OdooClient` получает `environments`; `InstanceConfig` получает `command_prefix`/`default_cwd`; master password перестаёт быть инвариантом конструкции instance.
- **MODIFIED capability `server-lifecycle`**: `OdooInstance` получает `run_foreground()`/`shell()`/`run_shell_script()`; `run()`/`start()` используют instance prefix, затем client fallback.
- **MODIFIED capability `database-restore`**: `restore()` отправляет `name` в POST body; mutating DB methods требуют пароль отдельно.

## Capabilities

### New Capabilities

- `project-init`: `odcli init` — project manifest discovery/defaults, input modes, VS Code launch import.
- `development-environment`: public types, `EnvironmentResource`, checkout/worktree/config/DB/Python/sync/list/remove, internal locks.
- `instance-runtime-binding`: `command_prefix`/`default_cwd`, `from_environment()`, `from_config()` без пароля и без Python prefix.
- `cli-odcli`: Click surface, context, `--json`, `doctor`, eval/exec/module/translations/deps/vscode.

### Modified Capabilities

- `backup-catalog`: durable path, schema v3, `environments` + `environment_events`.
- `client-config`: `OdooClient.environments`, `InstanceConfig.command_prefix`/`default_cwd`, master password не инвариант.
- `server-lifecycle`: `run_foreground`/`shell`/`run_shell_script`, instance prefix.
- `database-restore`: `restore()` POST body `name`.
- `models-types`: `StartConfig.from_odoo_config(path)` записывает фактический `path`.

## Delivery Slices

Все срезы входят в закрытие issue.

| Slice | Scope | Capability |
|---|---|---|
| 1 | MVP | Click, project init/import, durable catalog, two-rule context, locks |
| 2 | MVP | Worktree, generated config, shared/copy DB and cleanup |
| 3 | MVP | Reused or explicitly isolated Python environment and `env sync` |
| 4 | MVP | `from_environment`, `command_prefix`/`cwd`, `run_foreground`, interactive `shell`, `run_shell_script` |
| 5 | MVP | `doctor` |
| 6 | MVP | `eval`, `exec`, `module`, `translations export`, `deps verify` |
| 7 | MVP | `vscode generate` |

Каждый MVP slice оставляет public API совместимым со следующими. Fast PostgreSQL template clone остаётся отдельным backlog issue #4.

## Impact

- **API**: `ProjectConfig`, `DevelopmentEnvironment`, `EnvironmentCheckoutOptions`, `EnvironmentState`, `EnvironmentDatabaseMode`, `EnvironmentResource`; `InstanceFactory.from_environment()`; `OdooInstance.run_foreground()` / `shell()` / `run_shell_script()`; `InstanceConfig.command_prefix`/`default_cwd`; `from_config()` без обязательного пароля и без Python prefix; `StartConfig.from_odoo_config(path)`; `restore()` POST `name`. Нет public `history()`, `EnvironmentEvent`, `runtime_json` на модели.
- **Storage**: `BackupCatalog` schema v2 → v3, миграция path cache→data_dir, новые таблицы `environments` + `environment_events`.
- **Dependencies**: add `click>=8.2,<9`, `json5>=0.15,<1`. НЕ add: GitPython/Dulwich/pygit2, virtualenv manager, ORM/config/port libraries, psutil/process daemon.
- **CLI**: новый `[project.scripts] odcli = "odoo_instance_sdk.cli:cli"`.
- **Code**: `client.py` (`environments`), `config.py` (`command_prefix`/`default_cwd`), `models.py` (`StartConfig.from_odoo_config` fix), `resources/instance.py` (`from_environment`/`run_foreground`/`shell`/`run_shell_script`), `resources/database.py` (`restore` POST body), `storage/backup_catalog.py` (schema v3 + path migration), new `resources/environment.py`, new `cli.py`, new project manifest.
- **External tools**: system Git (worktree), `uv` (venv/pip compile/sync), `fcntl.flock` (Unix only, MVP).

## Acceptance Criteria

- AC1: `odcli init` (wizard/headless/Comerta-like JSONC import) создаёт idempotent secret-free project manifest; CLI help показывает полный synopsis включая eval/module/translations/vscode.
- AC2: default shared checkout создаёт worktree/config/lock, reuses recorded project venv (`owned=false`) and never deletes it; `--create-venv` separately proves owned isolated creation. Git/`uv`/flock не торчат в public API.
- AC3: copy checkout E2E validates contained DB/filestore names, creates recorded backup/target DB, never overwrites existing target and reaches `ready` only after postconditions.
- AC4: один durable catalog мигрирует v2 history в `user_data_dir`; backups metadata, environments и events живут в том же файле; ZIP могут остаться в cache; второго SQLite нет; `list`/`doctor` видят одну картину без claim process ownership.
- AC5: default human output and leaf `--json` are stable; внутри worktree context inferred; вне worktree нужны `--project`/`--env` или positional selector; единственный `ready` и recency никогда не выбираются молча; dry-run mutates nothing.
- AC6: `fcntl.flock` internal to SDK serializes checkout/mutations, permits shared runtime readers and releases automatically after process death. CLI не вызывает lock API.
- AC7: `from_environment()` и `from_config()` создают ordinary `OdooInstance` без обязательного master password; prefix/cwd живут на instance; `from_config` оставляет `command_prefix=None`; mutating DB methods требуют пароль отдельно; run/shell preserve raw streams. `OdooClient` остаётся фасадом `instance`/`backups`/`environments`.
- AC8: `StartConfig.from_odoo_config(path)` записывает фактический `path`; runtime argv содержит ровно один `--config`; второй временный config из-за `db_password` не создаётся для persistent `0600` generated conf.
- AC9: remove dry-run shows recorded ownership; real/idempotent cleanup refuses dirty/occupied conflicts, never deletes shared DB/branch and preserves audit rows.
- AC10: ruff, strict mypy, full pytest and one disposable local Odoo lifecycle integration pass for path init→checkout→run/shell→remove.
- AC11: eval/exec/module/test/translation/dependency/`vscode generate` use captured local Odoo/uv primitives, no RPC fallback and no new public resources; commits, test ports, safe translation paths and `ru_RU→ru.po` behave as documented.

## Non-goals

- Background daemon/detached run/log storage or persisted process registry/`running` provisioning state.
- XML-RPC/JSON-RPC/HTTP implementation or fallback for `eval`, `exec`, `module` and translation commands.
- Public `GitWorktree`, `PythonVenv`, `LockManager`, `ModuleResource`, `TranslationResource`, `EnvironmentEvent`, `client.catalog`, `client.doctor`, второй SQLite, `history()` / `list(verify=)` на resource.
- Module install/uninstall, automatic `doctor --fix`/bulk prune, `--older-than`, runtime methods on environment resources.
- Auto-sync deps on `run`/`shell`.
- Silent environment defaults: last-used and single-ready selection.
- Killing external processes, deleting branches, remote restore/drop or PostgreSQL cluster copy.
- VS Code tasks/hooks/attach import; installing `uv`/OS packages/toolchains.
- Windows locking support; MVP uses Unix `fcntl`.
- Installing languages or inventing translation text; export writes only Odoo-generated payloads.
- Compliance-grade audit.