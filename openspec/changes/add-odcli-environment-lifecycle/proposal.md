## Why

SDK уже умеет запускать/останавливать локальный Odoo, делать backup/restore/drop и хранить backup-каталог. Но разработчику или агенту, который хочет изолированно поработать над веткой Odoo-проекта, не хватает короткого core loop: создать изолированный worktree с собственным `odoo.conf`, переиспользовать или создать Python-окружение, привязать БД (общую или копию), запустить Odoo/shell из этого окружения одной командой и потом cleanly удалить его. Сегодня всё это делается вручную: Git worktree, копирование `odoo.conf`, правка портов/путей, активация venv, привязка БД — и нет единого реестра, который бы знал, какие окружения существуют, чем они владеют и как их убрать.

Issue #3 добавляет агент-ориентированный CLI `odcli` и минимальный Python API для жизненного цикла локального Odoo-окружения. Первый продукт — короткий core loop, а не IDE вокруг Odoo.

## What Changes

- **NEW capability `project-init`**: `odcli init` создаёт idempotent secret-free project manifest `.odcli/project.toml` интерактивно, headless или через импорт VS Code launch profile; runtime artifacts остаются в platformdirs user directories и связываются с project через canonical Git common dir.
- **NEW capability `development-environment`**: публичные типы `ProjectConfig`, `DevelopmentEnvironment`, `EnvironmentCheckoutOptions`, `EnvironmentState`, `EnvironmentDatabaseMode`, `EnvironmentEvent`; `EnvironmentResource` с методами `checkout`/`sync_python`/`get`/`list`/`history`/`remove`, exposed как `OdooClient.environments`. Git/`uv`/`fcntl.flock`/generated config спрятаны за resource.
- **NEW capability `environment-checkout`**: preflight, Git worktree placement в user data dir, generated `odoo.conf` с `0600`, DB modes (shared/copy), Python environment reuse/create, `env sync`/`env list`/`env remove` с cleanup matrix и safety rules.
- **NEW capability `environment-catalog`**: расширение существующего `BackupCatalog` до schema v3 с таблицами `environments` и `environment_events`; one-time path migration из legacy `user_cache_dir`/`backups.sqlite3` в durable `user_data_dir`/`catalog.sqlite3` под exclusive catalog-migration lock; один durable SQLite на backups metadata + environments + events; второй catalog запрещён.
- **NEW capability `instance-runtime-binding`**: `InstanceConfig.command_prefix`/`default_cwd`; `InstanceFactory.from_environment()` без master password; `from_config()` без обязательного master password; `StartConfig.from_odoo_config(path)` фиксирует фактический `path`; runtime argv передаёт ровно один `--config`; `DatabaseResource.restore()` POST body содержит `name`; `OdooInstance.run_foreground()`/`shell()`/`run_shell_script()` как новые foreground/captured operations; `OdooClient` остаётся фасадом `instance`/`backups`/`environments`.
- **NEW capability `cli-odcli`**: один Click entry point `odcli`; MVP command surface `init`, `env checkout|sync|list|remove`, `run`, `shell`, `doctor`; post-MVP `eval`, `exec`, `module`, `translations export`, `deps verify`, `vscode generate`; context-aware resolution по двум правилам (registered worktree или explicit flags); stable `--json` envelope; `doctor` read-only coordinator.
- **MODIFIED capability `backup-catalog`**: catalog переезжает из `user_cache_dir` в durable `user_data_dir`; schema v2 → v3 миграция; `BackupCatalog` internal, public API `client.backups` + `client.environments`.
- **MODIFIED capability `client-config`**: `OdooClient` получает `environments`; `InstanceConfig` получает `command_prefix`/`default_cwd`; master password перестаёт быть инвариантом конструкции instance.
- **MODIFIED capability `server-lifecycle`**: `OdooInstance` получает `run_foreground()`/`shell()`/`run_shell_script()`; `run()`/`start()` используют instance prefix, затем client fallback.
- **MODIFIED capability `database-restore`**: `restore()` отправляет `name` в POST body; mutating DB methods требуют пароль отдельно.

## Capabilities

### New Capabilities

- `project-init`: `odcli init` — project manifest discovery/defaults, input modes, VS Code launch import.
- `development-environment`: публичные provisioning types и `EnvironmentResource` API contract.
- `environment-checkout`: checkout preflight, worktree, generated config, DB modes, Python env, sync/list/remove.
- `environment-catalog`: durable catalog schema v3, environments + environment_events, cache→data migration.
- `instance-runtime-binding`: `command_prefix`/`default_cwd`, `from_environment()`/`from_config()` без master password, `StartConfig.from_odoo_config(path)` fix, single `--config`, `restore()` POST body, `run_foreground`/`shell`/`run_shell_script`.
- `cli-odcli`: Click CLI surface, context resolution, `--json` envelope, `doctor`.

### Modified Capabilities

- `backup-catalog`: durable path, schema v3, internal catalog.
- `client-config`: `OdooClient.environments`, `InstanceConfig.command_prefix`/`default_cwd`, master password не инвариант.
- `server-lifecycle`: `run_foreground`/`shell`/`run_shell_script`, instance prefix.
- `database-restore`: `restore()` POST body `name`.
- `models-types`: `StartConfig.from_odoo_config(path)` записывает фактический `path`.

## Delivery Slices

Issue задаёт один целевой UX, но первый shippable продукт — MVP. Post-MVP срезы не расширяют public SDK новыми resources.

| Slice | Scope | Capability | Status |
|---|---|---|---|
| 1 | MVP | Click, project init/import, durable catalog, two-rule context, locks | MVP |
| 2 | MVP | Worktree, generated config, shared/copy DB and cleanup | MVP |
| 3 | MVP | Reused or explicitly isolated Python environment and `env sync` | MVP |
| 4 | MVP | `from_environment`, `command_prefix`/`cwd`, `run_foreground`, interactive `shell`, `run_shell_script` primitive | MVP |
| 5 | MVP | `doctor` | MVP |
| 6 | Post-MVP | captured automation: `eval`, `exec`, `module`, `translations export`, `deps verify` | Post-MVP |
| 7 | Post-MVP | `vscode generate` | Post-MVP |

Каждый MVP slice оставляет public API совместимым со следующими. Fast PostgreSQL template clone остаётся отдельным backlog issue #4.

## Impact

- **API**: новые публичные типы (`ProjectConfig`, `DevelopmentEnvironment`, `EnvironmentCheckoutOptions`, `EnvironmentState`, `EnvironmentDatabaseMode`, `EnvironmentEvent`, `EnvironmentResource`); новые методы `InstanceFactory.from_environment()`, `OdooInstance.run_foreground()`, `OdooInstance.shell()`, `OdooInstance.run_shell_script()`; новые поля `InstanceConfig.command_prefix`/`default_cwd`; revision `from_config()` (без обязательного master password); revision `StartConfig.from_odoo_config(path)`; revision `restore()` POST body.
- **Storage**: `BackupCatalog` schema v2 → v3, миграция path cache→data_dir, новые таблицы `environments` + `environment_events`.
- **Dependencies**: add `click>=8.2,<9`, `json5>=0.15,<1`. НЕ add: GitPython/Dulwich/pygit2, virtualenv manager, ORM/config/port libraries, psutil/process daemon.
- **CLI**: новый `[project.scripts] odcli = "odoo_instance_sdk.cli:cli"`.
- **Code**: `client.py` (`environments`), `config.py` (`command_prefix`/`default_cwd`), `models.py` (`StartConfig.from_odoo_config` fix), `resources/instance.py` (`from_environment`/`run_foreground`/`shell`/`run_shell_script`), `resources/database.py` (`restore` POST body), `storage/backup_catalog.py` (schema v3 + path migration), new `resources/environment.py`, new `cli.py`, new project manifest.
- **External tools**: system Git (worktree), `uv` (venv/pip compile/sync), `fcntl.flock` (Unix only, MVP).

## Acceptance Criteria

MVP. Issue можно закрывать по этим AC без post-MVP команд.

- AC1: `odcli init` (wizard/headless/Comerta-like JSONC import) создаёт idempotent secret-free project manifest; CLI help показывает только MVP synopsis.
- AC2: default shared checkout создаёт worktree/config/lock, reuses recorded project venv (`owned=false`) and never deletes it; `--create-venv` separately proves owned isolated creation. Git/`uv`/flock не торчат в public API.
- AC3: copy checkout E2E validates contained DB/filestore names, creates recorded backup/target DB, never overwrites existing target and reaches `ready` only after postconditions.
- AC4: один durable catalog мигрирует v2 history в `user_data_dir`; backups metadata, environments и events живут в том же файле; ZIP могут остаться в cache; второго SQLite нет; list/history/doctor видят одну картину без claim process ownership.
- AC5: default human output and leaf `--json` are stable; внутри worktree context inferred; вне worktree нужны `--project`/`--env` или positional selector; единственный `ready` и recency никогда не выбираются молча; dry-run mutates nothing.
- AC6: `fcntl.flock` internal to SDK serializes checkout/mutations, permits shared runtime readers and releases automatically after process death. CLI не экспортирует lock API.
- AC7: `from_environment()` и `from_config()` создают ordinary `OdooInstance` без обязательного master password; prefix/cwd живут на instance; `run` использует instance prefix, не только `OdooClientConfig.executable`; mutating DB methods требуют пароль отдельно; run/shell preserve raw streams, signals, bound config/DB and port safety. `OdooClient` остаётся фасадом `instance`/`backups`/`environments`.
- AC8: `StartConfig.from_odoo_config(path)` записывает фактический `path`; runtime argv содержит ровно один `--config`; второй временный config из-за `db_password` не создаётся для persistent `0600` generated conf.
- AC9: remove dry-run shows recorded ownership; real/idempotent cleanup refuses dirty/occupied conflicts, never deletes shared DB/branch and preserves audit rows.
- AC10: ruff, strict mypy, full pytest and one disposable local Odoo lifecycle integration pass for MVP path init→checkout→run/shell→remove. `run_shell_script()` покрыт как SDK primitive без CLI `eval`/`module`.

Post-MVP. Не блокируют закрытие MVP.

- AC11: eval/exec/module/test/translation/dependency/`vscode generate` use captured local Odoo/uv primitives, no RPC fallback and no new public resources; commits, test ports, safe translation paths and `ru_RU→ru.po` behave as documented.

## Non-goals

- Background daemon/detached run/log storage or persisted process registry/`running` provisioning state.
- XML-RPC/JSON-RPC/HTTP implementation or fallback for `eval`, `exec`, `module` and translation commands.
- Public `GitWorktree`, `PythonVenv`, `LockManager`, `ModuleResource`, `TranslationResource`, `client.catalog`, `client.doctor` or a second SQLite catalog.
- Module install/uninstall, automatic `doctor --fix`/bulk prune, runtime methods on environment resources.
- Killing external processes, deleting branches, remote restore/drop or PostgreSQL cluster copy.
- VS Code tasks/hooks/attach import; installing `uv`/OS packages/toolchains.
- Windows locking support; MVP uses Unix `fcntl`.
- Installing languages or inventing translation text; export writes only Odoo-generated payloads.
- Compliance-grade audit.
- MVP CLI surface beyond `init`, `env checkout|sync|list|remove`, `run`, `shell`, `doctor`.
- Silent environment defaults: last-used and single-ready selection.