## Why

SDK уже умеет запускать/останавливать локальный Odoo и управлять environments, но запуск предполагает, что PostgreSQL-кластер уже доступен. Пользователь или агент должен отдельно идентифицировать и поднять PostgreSQL, иначе старт Odoo падает по скрытой причине, а destructive ownership-решения (можно ли остановить/удалить кластер) остаются неявными. Это блокирует one-command, agent-safe Odoo startup и оставляет работу с внешним сервисом опасной (можно принять чужой кластер за свой).

Issue #8 добавляет project-level PostgreSQL cluster ownership и lifecycle: один кластер на проект, разделяемый между development environments, два режима (external / compose), явная ownership-модель, preflight перед spawn Odoo и read-only `doctor`.

## What Changes

- **NEW capability `postgres-cluster`**: public `PostgresCluster` + `PostgresClusterState`; `from_project()` constructor; `mode`/`owned` read-only; `status()` (read-only), `ensure_running(timeout)` (idempotent), `stop(timeout)` (SDK-owned compose only); ownership determines lifecycle permission; redacted typed errors; Compose-managed runtime artifacts in platformdirs; one fixed Compose service (loopback port, named volume, `pg_isready` healthcheck, file-backed `POSTGRES_PASSWORD_FILE`), deterministic Compose project name, validated by `docker compose config`, started by `docker compose up --detach --wait`. A mutable manifest image is pulled and resolved to an OCI RepoDigest; the user must explicitly approve that exact digest outside the repository before it can start.
- **MODIFIED capability `project-init`**: `ProjectConfig` получает `postgres` section (`mode`, `image`, `port`, `user`) — non-secret intent only; external mode reuses connection values from source `odoo.conf`; `odcli init` получает `--postgres [external|compose]`, `--postgres-image`, `--postgres-port`, `--postgres-user`; interactive init prompts только для unresolved mode-specific values; `--no-input` forbids prompts и требует `--postgres-image` для compose; `--dry-run --json` сообщает resolved plan без записи; idempotency учитывает postgres section.
- **MODIFIED capability `cli-odcli`**: новая command group `odcli postgres` с `status` (read-only, `--json`), `up [--wait-timeout SECONDS]` (idempotent; compose → `up --detach --wait`; external → reachability check only), `stop [--timeout SECONDS]` (SDK-owned compose only, preserves volume); использует существующий project resolution — без project arg; `init` опции прокидываются в существующий init flow.
- **MODIFIED capability `instance-runtime-binding`**: `InstanceFactory.from_environment()` привязывает project cluster к результирующему `OdooInstance`; `OdooInstance` получает один internal dependency preflight, вызываемый из `run_foreground()`, `shell()` и `run_shell_script()` ровно один раз перед spawn; preflight делегирует readiness в `PostgresCluster.ensure_running()`; CLI не дублирует preflight; остановленный managed cluster стартует автоматически при запуске Odoo; при выходе Odoo project cluster остаётся running (могут быть другие environments); удаление одного environment никогда не останавливает и не удаляет shared cluster.
- **MODIFIED capability `readiness`/`doctor`**: `doctor` добавляет cluster checks (mode, ownership, endpoint, health) и Docker Compose availability без изменения состояния; `doctor` не поднимает и не останавливает cluster.

## Capabilities

### New Capabilities

- `postgres-cluster`: `PostgresCluster`/`PostgresClusterState`, ownership-based lifecycle, Compose-managed runtime artifacts, file-backed secret.

### Modified Capabilities

- `project-init`: `[postgres]` manifest section; `--postgres*` init options; non-secret intent only.
- `cli-odcli`: `odcli postgres` group; `--postgres*` wired into `init`.
- `instance-runtime-binding`: `from_environment()` привязывает cluster; `OdooInstance` dependency preflight перед spawn.
- `readiness` (or `doctor` cluster checks): cluster mode/ownership/endpoint/health + Docker Compose availability, read-only.

## Impact

- **API / CLI / schema**: без breaking changes. `PostgresCluster` — новый public тип; `ProjectConfig` получает optional `postgres` section (default `external`, обратная совместимость); `OdooInstance` получает preflight, который в external-режиме только проверяет reachability (для существующих проектов без `[postgres]` = `external` по умолчанию). Manifest schema расширяем, старые manifests валидны.
- **Code**: новый `resources/postgres.py` (или `internal/postgres.py`); `ProjectConfig` расширено; `internal/postgres_compose.py` (artefact generation + compose runner); CLI group `postgres`; `instance.py` preflight; `doctor.py` cluster checks.
- **Security**: passwords никогда не появляются в manifest, process args, logs, JSON, repr или exceptions; generated secret/config files `0600`.
- **Dependencies**: не добавляются. Используется stdlib + установленный `docker compose` CLI. Никаких docker-py, PyYAML, psycopg, второй SQLite registry, daemon или generic service manager.
- **Out of scope**: `postgresql.conf` generation/mounting; multiple clusters per project; replicas/pooling/remote Docker; image auto-selection и major-version upgrades; pull-policy management; arbitrary PGDATA binding/deletion; cluster delete/reset, `down -v`, pruning, physical backups; PG log commands; persisted health history; background daemon; creating/copying an Odoo database during `init`; cross-cluster template cloning (#4).
