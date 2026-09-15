## MODIFIED Requirements

### Requirement: Runtime dependencies are minimal

The `pyproject.toml` SHALL declare core runtime dependencies as exactly:

- `httpx>=0.27,<1.0`
- `msgspec>=0.18,<1.0`
- `platformdirs>=4.3,<5`
- `click>=8.2,<9`
- `rich-click>=1.9,<2`
- `json5>=0.15,<1`
- `psutil>=5.9,<7`
- `rich>=15,<16`
- `python-toon==0.1.3`
- `expression>=5,<6`
- `alembic>=1.13,<2`
- `sqlalchemy>=2,<3`

Rich SHALL remain the command-result terminal renderer, while `rich-click>=1.9,<2` SHALL render only Click help and Click-generated usage/validation errors; Textual, curses wrappers, and alternative CLI frameworks SHALL NOT be added. `python-toon` SHALL be used in-process through `from toon import encode, decode, DecodeOptions`; strict verification SHALL invoke `decode(encoded, DecodeOptions(indent=2, strict=True))`. The project SHALL NOT contain a custom TOON encoder/decoder or invoke a Node subprocess. The dependency SHALL remain exactly pinned. The checked fixture source SHALL be the project's committed envelope fixtures derived from CLI envelope v1 and snapshot schema v2, with TOON syntax expectations traced to the published TOON specification v4.1 (2026-07-26). The supported contract is semantic round-trip of those project envelopes, not a claim that the dependency implements every v4.1 production. A dependency or fixture-source upgrade SHALL require deliberately updating the pin and fixtures together.

SQLAlchemy SHALL be used for Core schema metadata and Alembic integration only (no ORM); repository queries SHALL NOT be translated away from `sqlite3` by this change. Alembic SHALL be the catalogue migration ledger; the `PRAGMA user_version` chain SHALL NOT remain as a migration ledger after the transition.

Optional extras SHALL remain:

```toml
[project.optional-dependencies]
dashboard = [
  "fastapi>=0.141,<1.0",
  "starlette>=1.3.1,<2.0",
  "uvicorn>=0.30,<1.0",
]
```

React SPA assets SHALL ship in sdist + wheel; Node.js SHALL NOT be required for the installed package. No Pydantic or docker-py runtime dependency SHALL be introduced.

#### Scenario: Runtime dependencies enumerated

- **WHEN** the published wheel metadata is inspected
- **THEN** core runtime dependencies are exactly the twelve listed dependencies with the specified bounds/pins and no Textual, Typer, Cyclopts, Pydantic, docker-py, or Node runtime

#### Scenario: TOON implementation is in-process and pinned

- **WHEN** TOON output is generated in an isolated installed wheel
- **THEN** `python-toon==0.1.3` encodes via `toon.encode` in the Python process and no custom encoder or Node executable is used

#### Scenario: TOON pin conforms for supported envelopes

- **WHEN** representative success/error envelopes containing nested objects, uniform arrays, empty collections, booleans, nulls, numbers, and escaped strings are encoded
- **THEN** `toon.decode(encoded, DecodeOptions(indent=2, strict=True))` returns the original JSON value for the committed CLI-envelope-v1/snapshot-v2 fixtures, including nested project/environment objects and null fields, and their checked syntax matches the cited v4.1 productions

#### Scenario: Terminal rendering responsibilities are bounded

- **WHEN** installed metadata and CLI imports are inspected
- **THEN** Rich renders command results, `rich-click>=1.9,<2` renders only Click help/errors, and no Textual or alternate CLI framework is present

#### Scenario: Rich is the sole terminal renderer

- **WHEN** installed metadata and CLI imports are inspected
- **THEN** Rich provides Table/Live/Status/Progress for command results, `rich-click>=1.9,<2` is confined to Click help/errors, and no Textual or alternate parser/rendering framework is present

#### Scenario: dashboard extra pulls web dependencies

- **WHEN** `pip install odoo-instance-sdk[dashboard]` is run
- **THEN** installed dependencies include core `psutil`, `fastapi`, `starlette`, and `uvicorn`

#### Scenario: Monitor command hint when dashboard extra missing

- **WHEN** `odcli monitor` runs and `fastapi`/`uvicorn` are not installed
- **THEN** exits `1` with message containing `pip install odoo-instance-sdk[dashboard]`

#### Scenario: Obsolete metrics extra is absent

- **WHEN** the published metadata is inspected
- **THEN** it exposes no `metrics` optional extra and does not require an extra install for process collection

## ADDED Requirements

### Requirement: Production Python file line limit

CI SHALL run a simple check that rejects manually maintained production Python files larger than 1000 physical lines. Generated or vendored code SHALL be excluded with an explicit marker; an allowlist of existing large files SHALL NOT be created. Behaviour-preserving refactor along confirmed responsibility boundaries SHALL bring the thirteen currently oversized files under the limit without changing user behavior, public imports, CLI command names, immutable command plans, Rich/JSON/TOON output, or FastAPI/OpenAPI contracts.

#### Scenario: Oversized file fails CI

- **WHEN** CI runs the line-limit check against production Python files
- **THEN** any manually maintained file over 1000 physical lines is rejected unless it is generated or vendored

#### Scenario: No existing-file allowlist

- **WHEN** the line-limit check configuration is inspected
- **THEN** there is no allowlist of pre-existing oversized files