## Context

See proposal.md — Why. Сегодня:

```
cli.py (~1400 lines)
├── Click registration + global --project/--env
├── init / env / run / shell / doctor / eval / exec / module / translations / deps / vscode
├── 4 JSON emitters + 4 fail helpers
├── resolve/verify/from_environment copied per command
└── _record_use_event() → client.get_catalog()

internal/context.py     # two-rule resolve (keep)
internal/doctor.py      # read-only checks (keep)
internal/vscode_import.py
    └── _handle_config/_db/_port/_dev/_dropped/_overlay

storage/backup_catalog.py
├── connect + schema v1→v7
├── backups / backup_events
├── restores / database_events
└── environments / environment_events / copy journal
```

Публичные контракты уже зафиксированы в `cli-odcli`, `backup-catalog`, `development-environment`. Этот design только раскладывает швы.

## Goals / Non-Goals

**Goals:**

- Тонкий `odoo_instance_sdk.cli:cli` + focused internal command modules.
- Один typed application context вместо dict `ctx.obj` + scattered helpers.
- Один output path; CLI rendering не пишет catalog.
- Один SQLite owner; environment persistence отдельно от backup/restore history.
- Net size down: удалить duplicate emitters и single-use argv handlers.

**Non-Goals:**

- Новые команды, JSON schema, public resource, repository, второй SQLite.
- Schema migration (v7 остаётся).
- Перенос list-reconciliation в `doctor` или в `EnvironmentResource.list()`.
- Windows locks, daemon, PostgreSQL cluster, template clone.

## Decisions

### D1: `cli.py` остаётся модулем-registrar, команды — `internal/cli/`

```
src/odoo_instance_sdk/cli.py          # group, --project/--env, add_command
src/odoo_instance_sdk/internal/cli/
├── app.py        # CliApp
├── output.py     # one envelope + fail
├── init.py
├── env.py
├── runtime.py    # run, shell
├── doctor.py
├── automation.py # eval, exec, module, translations, deps
└── vscode.py
```

Почему не package `odoo_instance_sdk/cli/`: entry point `odoo_instance_sdk.cli:cli` сохранился бы, но `cli` стал бы public package и размазал adapter. Internal package явнее говорит «не часть SDK facade».

Почему не один `internal/cli.py`: снова монолит.

`from odoo_instance_sdk.cli import cli` MUST работать. Тесты, которые патчат `odoo_instance_sdk.cli._make_client`, переезжают на `CliApp` — compatibility shims на старые private helpers не оставляем (это и есть pass-through).

### D2: `CliApp` — единственный orchestration object

`click.Context.obj` держит `CliApp`, не `dict`.

```
CliApp
├── project_flag / env_flag / cwd
├── client() -> OdooClient          # one per invocation
├── resolve_project() -> Path
├── resolve_environment(...) -> DevelopmentEnvironment
├── require_ready_environment()
├── verify_runtime(env)
├── port_is_free(env) -> bool       # distinct from verify_runtime; run only
├── instance_from(env) -> OdooInstance
├── record_use(env)                 # CLI caller only; delegates to helper
└── provenance() -> envelope provenance dict
```

Resolution делегирует в существующий `internal/context.py`. Новых правил нет. Port preflight — отдельный метод `CliApp.port_is_free()`; test patches целятся в него, не в `cli._check_port_free`.

`record_use` — internal helper рядом с environment persistence (`storage/environments.py` / соседний module). `CliApp.record_use()` — единственный CLI caller. `output.py` и command printers catalog не импортируют. Новый public method на `EnvironmentResource` не добавляем: issue запрещает менять public Python API.

Alternative considered: `EnvironmentResource.record_use()`. Чище по границе, но расширяет public class. Отклонено.

### D3: Один output module

`internal/cli/output.py` владеет текущим v1 envelope целиком: `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, `warnings`; на success — `result` и `data` с одним payload (`result` — stable key, `data` — v1 alias); на error — `error.code` + sanitized `error.message`. Command modules передают command, payload, context, provenance, dry_run, error. `project_source` / `environment_source` остаются в `provenance`, не переезжают в `context`. `_env_dict` / `_checkout_plan_dict` остаются data adapters в `env.py`, потому что JSON shape не равен dataclass fields 1:1. stdlib `dataclasses.asdict` — только если keys и types совпадают с текущим envelope; иначе explicit mapping и justification в review.

### D4: Catalog — mixins на одном owner, не repository

```
storage/
├── backup_catalog.py     # BackupCatalog: connect, pragma, migrate, close
├── backups.py            # BackupStore mixin: download/list/history/delete
├── restores.py           # RestoreStore mixin: restore/drop tracking
└── environments.py       # EnvironmentStore mixin: env rows, events, copy journal
```

```
class BackupCatalog(BackupStore, RestoreStore, EnvironmentStore):
    # owns sqlite3.Connection + schema
```

Почему mixins, а не отдельные store objects: resources уже зовут `catalog.create_environment()` / `start_download()`. Отдельные store + тонкий facade = pass-through, который issue запрещает. Mixin переносит SQL as-is, без rename layer.

Почему не три модуля функций от raw connection: тогда каждый resource начинает знать про `conn`, и schema owner распадается.

`CURRENT_SCHEMA_VERSION` и migrations остаются в owner module. Имя `BackupCatalog` можно оставить — rename в `SdkCatalog` не обязателен и сам по себе не уменьшает size.

### D5: VS Code argv — одна table-driven петля

`_handle_config` / `_handle_db` / `_handle_port` / `_handle_dev` / `_handle_dropped` / `_handle_overlay` заменяются одной loop + table `(match, consume)`. Mapping (`-c` → source_config, drop `-u/-i`, overlays reported) не меняется. `ast.literal_eval()` не используем: launch args уже list из json5, custom literal parser в src нет.

### D6: List reconciliation остаётся presentation

`_reconcile_environment` в `env list` — read-only table enrichment. Это не catalog write и не `doctor`. Перенос в `EnvironmentResource.list()` изменил бы public return type. Оставляем в `internal/cli/env.py`.

### D7: Quality gate

Полный pytest + ruff + strict mypy до и после. Контракт — существующие CLI/SDK tests. Добавляются узкие tests: CLI command modules не импортируют catalog mutating API; `CliApp.record_use` пишет event; catalog modules делят один connection; root `cli.py` не определяет command bodies.

## Risks / Trade-offs

- [Тесты патчат private CLI helpers] → обновить patches на `CliApp`; не держать shims.
- [Mixin diamond / import cycles] → stores не импортируют друг друга; только owner знает schema.
- [asdict меняет JSON types] → explicit mapping, если UUID/datetime/enum не совпали.
- [Слишком мелкий split] → не больше семи command modules; `automation.py` держит eval/exec/module/translations/deps вместе.

## Migration Plan

1. Сначала `CliApp` + `output.py` внутри текущего `cli.py` (behavior lock).
2. Вынести command modules, обновить test patches.
3. Вынести catalog mixins без schema change.
4. Сжать vscode argv parser.
5. Удалить мёртвые helpers. Rollback = revert merge; data migration нет.

## Open Questions

Нет. Public API freeze и один SQLite — из issue #9; package-vs-module и mixin-vs-store — зафиксированы выше.
