## Context

See proposal.md. Сегодня восемь instance-команд делают одно и то же:

```
_make_client()
_resolve_ready_env()
_verify_env_runtime()
from_environment()
```

плюс четыре emit/fail и `get_catalog()` из list/run. `internal/context.py` уже резолвит project/env. `EnvironmentResource` уже пишет checkout/sync/remove events.

## Goals / Non-Goals

**Goals:** удалить копии скелета; CLI не трогает catalog; `use` на том же слое, что остальные env events.

**Non-Goals:** `CliApp` как сервис на 8 методов, mixin-разрез `BackupCatalog`, новый `cli_cmds.py` junk drawer, vscode rewrite.

## Decisions

### D1: `ready_instance(ctx)`, не класс-фасад

В `internal/context.py` (уже канон resolve):

```
ready_instance(ctx) -> tuple[OdooClient, DevelopmentEnvironment, OdooInstance]
```

Внутри: один client, two-rule resolve, ready+runtime checks, `from_environment()`. Port check остаётся только в `run` — это единственный особенный шаг, не метод на объекте.

`ctx.obj` остаётся мелким: flags `--project`/`--env`. Новый тип `CliApp` не нужен: это wrapper над функциями, которые уже есть.

### D2: Один emit

Одна функция envelope v1 (`result`+`data`, `provenance`, `dry_run`) и один fail (exit 1 / usage 2). `_emit_json` / `_env_json` / `_emit_command_json` / `_fail` / `_env_fail` / `_usage_fail` / `_emit_command_error` удаляются.

### D3: `use` пишет `EnvironmentResource`

`EnvironmentResource.record_use(env)` делает то же, что сейчас `_record_use_event`: `last_used_at` + `use/succeeded`. CLI `run` вызывает его после free-port preflight. Это тот же объект, что уже пишет checkout/sync/remove. Не public `client.catalog`, не новый resource.

### D4: CLI не вызывает `get_catalog()`

`env list` backup-exists идёт через `client.backups` (уже public) или маленький internal `observe_environment(client, env)` рядом с doctor — не через catalog row. `record_use` уходит с CLI. `vscode_generate` / `from_environment` catalog reads — не этот change.

### D5: Файлы после collapse, не до

Сначала сжать команды. Потом:

```
cli.py                 # group, --project/--env, тонкие команды
internal/context.py    # + ready_instance
internal/cli_output.py # emit/fail, только если не влезает в context.py
internal/cli_env.py    # env *, только если checkout/list plan dicts всё ещё жирные
```

`cli_cmds.py` не заводим: после `ready_instance` instance-команды — это Click wrappers, им не нужен свой модуль.

### D6: Catalog не режем

`BackupCatalog` остаётся один класс / один файл. EnvironmentResource уже отделяет env operations от backup history. Mixin `EnvironmentStore` — pass-through с другим именем.

## Risks / Trade-offs

- [Тесты патчат `_make_client`] → патч `ready_instance` или `OdooClient` construction, не aliases на старые helpers.
- [`record_use` на public class] → аддитивный метод, не breaking; не экспортируем новый тип в `__all__`.

## Open Questions

Нет.
