## ADDED Requirements

### Requirement: Readiness check

`instance.wait_ready(proc, *, timeout=60.0)` MUST выполнять GET `/web/health?db_server_status=true` на normalized `OdooInstance.base_url` без HTTP Basic Auth и без master password.

Odoo 19.0 endpoint `/web/health` имеет `auth="none"` и возвращает JSON `{"status": "pass"}` с HTTP 200. Метод MUST:

1. периодически опрашивать endpoint в течение `timeout`;
2. на каждый poll проверять, что процесс ещё alive;
3. вернуть `ReadinessResult` с `ok=True` при `status == "pass"`;
4. выбросить `ProcessExitedBeforeReady`, если процесс завершился до readiness;
5. выбросить `ReadinessTimeoutError`, если readiness не достигнут за `timeout`.

Метод MUST NOT использовать Basic Auth и MUST NOT читать `master_pwd` из `InstanceConfig`.

#### Scenario: Ready сервер

- **WHEN** процесс запущен и `/web/health` возвращает `{"status": "pass"}`
- **THEN** `wait_ready` возвращает `ReadinessResult(ok=True)`

#### Scenario: Процесс упал до readiness

- **WHEN** процесс завершился до того, как `/web/health` ответил `pass`
- **THEN** `wait_ready` выбрасывает `ProcessExitedBeforeReady`

#### Scenario: Readiness timeout

- **WHEN** `/web/health` не отвечает `pass` в течение `timeout`
- **THEN** `wait_ready` выбрасывает `ReadinessTimeoutError`