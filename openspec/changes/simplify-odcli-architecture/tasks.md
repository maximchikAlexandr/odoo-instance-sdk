## 1. Delete the copies

- [ ] 1.1 Добавить `ready_instance(ctx)` в `internal/context.py`; переписать `run`/`shell`/`eval`/`exec`/`module`/`translations`/`deps`/`vscode` на него
- [ ] 1.2 Свести emit/fail к одной функции; удалить `_emit_json` / `_env_json` / `_emit_command_*` / `_fail` / `_env_fail` / `_usage_fail` / `_make_client` / `_resolve_ready_env`
- [ ] 1.3 `EnvironmentResource.record_use(env)`; `run` вызывает его после free-port preflight; удалить `_record_use_event` и CLI `get_catalog()`
- [ ] 1.4 `env list` не вызывает `get_catalog()` (backup exists через `client.backups` или internal observe)
- [ ] 1.5 Вынести env-команды в `internal/cli_env.py` только если `cli.py` после 1.1–1.4 всё ещё держит plan/table жир; иначе оставить в `cli.py`
- [ ] 1.6 Перенацелить patches; существующие CLI contract tests зелёные без смены keys/exit codes

## 2. Gate

- [ ] 2.1 ruff + strict mypy + pytest как в CI
