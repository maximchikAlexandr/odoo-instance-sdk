## MODIFIED Requirements

### Requirement: Runtime dependencies are minimal

The `pyproject.toml` SHALL declare core runtime dependencies as exactly: `httpx` (pinned `>=0.27,<1.0`), `msgspec`, `platformdirs` (pinned `>=4.3,<5`), `click`, `json5`. No other runtime dependencies SHALL be introduced in the core dependency list.

Дополнительно (новое в этом change): project SHALL предоставлять optional extras:

```toml
[project.optional-dependencies]
metrics = ["psutil>=5.9,<7"]           # collector + typed models, без FastAPI
dashboard = ["odoo-instance-sdk[metrics]", "fastapi>=0.115,<1.0", "uvicorn>=0.30,<1.0"]
```

- `metrics` — `psutil` + Python collector + typed snapshot models; НЕ включает FastAPI/Uvicorn; приложение может использовать `EnvironmentMonitor` внутри своего процесса без зависимости от встроенного backend.
- `dashboard` — `metrics` + FastAPI + Uvicorn; требуется для `odcli monitor` (UI и headless).
- React/Mantine/Vite — build-time только (через `npm`/`pnpm` в `src/odoo_instance_sdk/web/`); готовые static assets (собранный SPA) включаются в package (sdist + wheel) через `uv_build` data inclusion; Node.js не требуется для установленного UI/headless/SDK режима.
- container inspection переиспользует установленный Docker CLI/Compose runner из `PostgresCluster`; docker-py не добавляется.
- Команда без требуемого extra (например `EnvironmentMonitor()` без `metrics`, или `odcli monitor` без `dashboard`) завершается с короткой actionable install hint, exit 1 (для CLI) или typed `MonitorExtrasMissingError` (для SDK).

`psutil` — единственная новая runtime dependency, и только за extra `metrics` (не core). `fastapi`/`uvicorn` — только за extra `dashboard` (не core, не `metrics`). Pydantic, docker-py, PyYAML, psycopg, второй SQLite registry — не добавляются.

#### Scenario: Runtime dependencies enumerated

- **WHEN** the published metadata is inspected
- **THEN** core runtime dependencies are `httpx>=0.27,<1.0`, `msgspec`, `platformdirs>=4.3,<5`, `click`, `json5`; no `psutil`, `fastapi`, `uvicorn`, `pydantic` or `docker-py` in core deps

#### Scenario: metrics extra contains psutil only

- **WHEN** `pip install odoo-instance-sdk[metrics]` is run
- **THEN** installed extra dependencies are exactly `psutil>=5.9,<7` (plus existing core deps); no FastAPI/Uvicorn

#### Scenario: dashboard extra pulls metrics + fastapi + uvicorn

- **WHEN** `pip install odoo-instance-sdk[dashboard]` is run
- **THEN** installed extra dependencies include `psutil`, `fastapi`, `uvicorn` (via `metrics` + dashboard-specific)

#### Scenario: No psutil in core install

- **WHEN** `uv add odoo-instance-sdk` (no extras) is run in another project
- **THEN** `psutil` is NOT installed; `EnvironmentMonitor()` raises `MonitorExtrasMissingError` with install hint

#### Scenario: Built assets included without Node.js

- **WHEN** the installed package is inspected after `pip install odoo-instance-sdk[dashboard]`
- **THEN** the React SPA static assets are present under `odoo_instance_sdk/web/` (or equivalent data location); no Node.js required to serve `odcli monitor`

#### Scenario: SDK mode without FastAPI

- **WHEN** an application uses `EnvironmentMonitor()` inside its own Flask worker with only `odoo-instance-sdk[metrics]`
- **THEN** `fastapi`/`uvicorn` are NOT installed; `snapshot()`/`watch()` work; built-in server not importable

#### Scenario: Monitor command hint when extra missing

- **WHEN** `odcli monitor` runs and `fastapi`/`uvicorn` not installed
- **THEN** exits 1 with message containing `pip install odoo-instance-sdk[dashboard]`