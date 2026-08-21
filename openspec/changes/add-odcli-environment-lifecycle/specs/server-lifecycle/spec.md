## MODIFIED Requirements

### Requirement: Server lifecycle в instance

`OdooInstance` MUST предоставлять методы `run()`, `start()`, `stop()`, `status()`, `wait_ready()`, `run_foreground()`, `shell()` и `run_shell_script()` напрямую, без вложенного подресурса `instance.server`.

Process registry (зарегистрированные `OdooProcess` и subprocess handles) MUST храниться приватно на `OdooClient` и разделяться всеми instances. Публичный `client.server` MUST NOT существовать.

`instance.run()`, `start()`, `stop()`, `status()`, `run_foreground()`, `shell()`, `run_shell_script()` MUST использовать instance `command_prefix` (если set), затем client fallback на `OdooClientConfig.executable`.

`instance.start(config: StartConfig)` MUST принимать `StartConfig` и возвращать `OdooProcess`. `StartConfig` остаётся `msgspec.Struct` с `forbid_unknown_fields=True`; поля не меняются. Метакласс `_StructMeta` удаляется.

Существующий `OdooInstance.run(args) -> CommandResult` остаётся captured one-shot API без изменения семантики. Не перегружать его неявным выбором между capture и foreground server mode.

`OdooInstance.run_foreground()`, `shell()` и `run_shell_script()` — новые public operations (см. `instance-runtime-binding` spec для full contract). `shell()` и `run_foreground()` используют один internal foreground subprocess primitive, но остаются двумя ясными public operations. `EnvironmentResource` не получает runtime methods `run()`, `shell()`, `start()` или `stop()`.

#### Scenario: Instance prefix used over client fallback

- **WHEN** `instance` создан через `from_environment()` с `command_prefix=["/venv/bin/python", "/worktree/odoo-bin"]`
- **THEN** `run()`/`start()`/`run_foreground()`/`shell()`/`run_shell_script()` используют prefix, не `OdooClientConfig.executable`

#### Scenario: Client fallback for manual instance

- **WHEN** `instance` создан через `instance(base_url=...)` без `command_prefix`
- **THEN** `run()`/`start()` используют `OdooClientConfig.executable` как fallback

#### Scenario: Запуск сервера через instance

- **WHEN** пользователь вызывает `instance.start(config)`
- **THEN** Odoo executable запускается, процесс регистрируется в общем registry на `OdooClient`, и возвращается `OdooProcess`

#### Scenario: Общий registry между instances

- **WHEN** два instance запускают по одному процессу через `instance_a.start(...)` и `instance_b.start(...)`
- **THEN** оба процесса зарегистрированы в одном registry на `OdooClient` и доступны через `instance_a.status(proc_a)` и `instance_b.status(proc_b)`