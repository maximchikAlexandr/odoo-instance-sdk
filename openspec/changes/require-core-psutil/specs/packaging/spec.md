## MODIFIED Requirements

### Requirement: Runtime dependencies are minimal

The `pyproject.toml` SHALL declare core runtime dependencies as exactly:
`httpx` (pinned `>=0.27,<1.0`), `msgspec`, `platformdirs` (pinned
`>=4.3,<5`), `click`, `json5`, and `psutil` (pinned `>=5.9,<7`). No other
runtime dependencies SHALL be introduced in the core dependency list.

Optional extras:

```toml
[project.optional-dependencies]
dashboard = ["fastapi>=0.141,<1.0", "starlette>=1.3.1,<2.0", "uvicorn>=0.30,<1.0"]
```

- Process collection and exact runtime identity are core SDK behavior.
- `dashboard` adds only FastAPI, Starlette, and Uvicorn; required for
  `odcli monitor`.
- React SPA assets ship in sdist + wheel; Node.js is not required for installed
  package.

#### Scenario: Runtime dependencies enumerated

- **WHEN** the published metadata is inspected
- **THEN** core runtime dependencies include `psutil>=5.9,<7` together with
  `httpx>=0.27,<1.0`, `msgspec`, `platformdirs>=4.3,<5`, `click`, and `json5`,
  and do not include `fastapi`, `uvicorn`, `pydantic`, or `docker-py`

#### Scenario: Obsolete metrics extra is absent

- **WHEN** the published metadata is inspected
- **THEN** it exposes no `metrics` optional extra and does not require an
  extra install for process collection

#### Scenario: dashboard extra pulls web dependencies

- **WHEN** `pip install odoo-instance-sdk[dashboard]` is run
- **THEN** installed dependencies include core `psutil`, `fastapi`, `starlette`,
  and `uvicorn`

#### Scenario: Monitor command hint when dashboard extra missing

- **WHEN** `odcli monitor` runs and `fastapi`/`uvicorn` are not installed
- **THEN** exits 1 with message containing `pip install odoo-instance-sdk[dashboard]`
