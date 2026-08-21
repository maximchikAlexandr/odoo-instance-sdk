## Context

SDK сегодня: `OdooClient` (`instance`, `backups`), `OdooInstance` (process + DB), `BackupCatalog` v2 в cache. Нет CLI, project manifest, worktree, durable environment ownership.

Этот change добавляет provisioning рядом с runtime, не вместо него. Поведение — в specs; здесь только швы и спорные решения.

## Goals / Non-Goals

**Goals:** core loop `init` → `checkout` → `run`/`shell`/`eval`/`module` → `remove`; CLI тонкий adapter; один durable SQLite; Git/`uv`/flock internal.

**Non-Goals:** daemon, RPC fallback, public Git/Venv/Lock/Module/Translation/`EnvironmentEvent`, `history()` на resource, auto-sync на run, `--older-than`, Windows locks.

## Decisions

### D1: Два модуля, не три runtime

```
OdooClient
├── instance       # InstanceFactory → OdooInstance (process, DB)
├── backups        # BackupResource
└── environments   # EnvironmentResource (worktree, config, python, ownership)
```

`DevelopmentEnvironment` — frozen record. Side effects только на `EnvironmentResource`. CLI не держит process table и не берёт flock.

SDK всегда получает explicit project path и environment selector. Cwd inference — только CLI.

### D2: Persistence — тот же catalog

Один файл `user_data_dir/catalog.sqlite3`, schema v3. Таблицы `environments` / `environment_events` живут в modified `backup-catalog`, не в отдельном capability. `runtime_json` — колонка catalog, не поле public модели. Events пишет SDK, читает `doctor` internally.

### D3: Runtime identity на instance

`command_prefix` / `default_cwd` задаёт только `from_environment()`. `from_config()` и `instance(base_url=...)` оставляют `command_prefix=None`; fallback — `OdooClientConfig.executable`.

Пароль не нужен при construction. `MasterPasswordRequiredError` — на `backup`/`restore`/`drop`.

`StartConfig` — `models-types`. `restore()` POST `name` — `database-restore`. `run_foreground` / `shell` / `run_shell_script` — `server-lifecycle`.

### D4: `run`/`shell` не пишут deps

Единственный write path для lock/venv — `env sync`. `run`/`shell` держат `LOCK_SH`. Drift — `doctor` / `deps verify`.

### D5: Context — два правила

1. cwd внутри exact registered worktree → project + environment из записи.
2. Иначе только `--project` и `--env` / positional.

Запрещено: last-used, silent single-ready, global default project.

### D6: Spec layout

Один delta-spec `development-environment` на provisioning (включая checkout/list/remove). Persistence — modified `backup-catalog`. Нет `environment-checkout` / `environment-catalog`.

Automation CLI (`eval`/`module`/`translations`/`vscode`) — MVP, описана в `cli-odcli`.

## Risks / Trade-offs

- Cache→data migration: exclusive lock, legacy не удаляется, durable authoritative.
- Reuse venv = shared pip mutations; isolation только `--create-venv`.
- Port не резервируется между checkout и run: повторный `socket.bind`.
- Нет process handle → занятый port = `port-conflict`.
- `fcntl` Unix-only.

## Open Questions

Нет.

## References

- [Git worktree porcelain format](https://git-scm.com/docs/git-worktree)
- [VS Code debug configuration](https://code.visualstudio.com/docs/debugtest/debugging-configuration)
