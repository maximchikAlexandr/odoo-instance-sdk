## MODIFIED Requirements

### Requirement: Runtime dependencies are minimal

The `pyproject.toml` SHALL declare core runtime dependencies as exactly:

- `httpx>=0.27,<1.0`
- `msgspec>=0.18,<1.0`
- `platformdirs>=4.3,<5`
- `click>=8.2,<9`
- `json5>=0.15,<1`
- `psutil>=5.9,<7`
- `rich>=15,<16`
- `python-toon==0.1.3`

Rich SHALL be the only terminal-rendering dependency; Textual, curses wrappers, and alternative CLI frameworks SHALL NOT be added. `python-toon` SHALL be used in-process through `from toon import encode, decode, DecodeOptions`; strict verification SHALL invoke `decode(encoded, DecodeOptions(indent=2, strict=True))`. The project SHALL NOT contain a custom TOON encoder/decoder or invoke a Node subprocess. The dependency SHALL remain exactly pinned. The checked fixture source SHALL be the project's committed envelope fixtures derived from CLI envelope v1 and snapshot schema v2, with TOON syntax expectations traced to the published TOON specification v4.1 (2026-07-26). The supported contract is semantic round-trip of those project envelopes, not a claim that the dependency implements every v4.1 production. A dependency or fixture-source upgrade SHALL require deliberately updating the pin and fixtures together.

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
- **THEN** core runtime dependencies are exactly the eight listed dependencies with the specified bounds/pins and no Textual, Typer, Cyclopts, Pydantic, docker-py, or Node runtime

#### Scenario: TOON implementation is in-process and pinned

- **WHEN** TOON output is generated in an isolated installed wheel
- **THEN** `python-toon==0.1.3` encodes via `toon.encode` in the Python process and no custom encoder or Node executable is used

#### Scenario: TOON pin conforms for supported envelopes

- **WHEN** representative success/error envelopes containing nested objects, uniform arrays, empty collections, booleans, nulls, numbers, and escaped strings are encoded
- **THEN** `toon.decode(encoded, DecodeOptions(indent=2, strict=True))` returns the original JSON value for the committed CLI-envelope-v1/snapshot-v2 fixtures, including nested project/environment objects and null fields, and their checked syntax matches the cited v4.1 productions

#### Scenario: Rich is the sole terminal renderer

- **WHEN** installed metadata and CLI imports are inspected
- **THEN** Rich provides Table/Live/Status/Progress and no Textual or alternate parser/rendering framework is present

#### Scenario: Dashboard extra remains bounded

- **WHEN** `pip install odoo-instance-sdk[dashboard]` is run
- **THEN** installed extra dependencies are exactly FastAPI, Starlette, and Uvicorn within their existing bounds in addition to core dependencies

#### Scenario: Monitor command hint when extra missing

- **WHEN** `odcli monitor` runs and `fastapi`/`uvicorn` are not installed
- **THEN** exits `1` with message containing `pip install odoo-instance-sdk[dashboard]`
#### Scenario: metrics extra contains psutil only

- **WHEN** `pip install odoo-instance-sdk[metrics]` is run
- **THEN** installed extra dependencies are exactly `psutil>=5.9,<7` (plus existing core deps); no FastAPI/Uvicorn

#### Scenario: dashboard extra pulls metrics + fastapi + uvicorn

- **WHEN** `pip install odoo-instance-sdk[dashboard]` is run
- **THEN** installed extra dependencies include `psutil`, `fastapi`, `uvicorn` (via `metrics` + dashboard-specific)
