## ADDED Requirements

### Requirement: Server lifecycle в instance

`OdooInstance` MUST предоставлять методы `run()`, `start()`, `stop()`, `status()` и `wait_ready()` напрямую, без вложенного подресурса `instance.server`.

Process registry (зарегистрированные `OdooProcess` и subprocess handles) MUST храниться приватно на `OdooClient` и разделяться всеми instances. Публичный `client.server` MUST NOT существовать.

`instance.run()`, `start()`, `stop()` и `status()` MUST сохранять поведение существующего `ServerResource`: запуск Odoo executable, регистрация процесса, опрос статуса, остановка process group.

`instance.start(config: StartConfig)` MUST принимать `StartConfig` и возвращать `OdooProcess`. `StartConfig` остаётся `msgspec.Struct` с `forbid_unknown_fields=True`; поля не меняются. Метакласс `_StructMeta` удаляется.

#### Scenario: Запуск сервера через instance

- **WHEN** пользователь вызывает `instance.start(config)`
- **THEN** Odoo executable запускается, процесс регистрируется в общем registry на `OdooClient`, и возвращается `OdooProcess`

#### Scenario: Общий registry между instances

- **WHEN** два instance запускают по одному процессу через `instance_a.start(...)` и `instance_b.start(...)`
- **THEN** оба процесса зарегистрированы в одном registry на `OdooClient` и доступны через `instance_a.status(proc_a)` и `instance_b.status(proc_b)`