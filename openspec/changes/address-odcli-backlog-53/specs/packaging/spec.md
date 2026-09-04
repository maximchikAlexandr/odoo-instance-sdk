## MODIFIED Requirements

### Requirement: Runtime dependencies are minimal

The `pyproject.toml` SHALL declare core runtime dependencies as exactly:

- `httpx>=0.27,<1.0`
- `msgspec>=0.18,<1.0`
- `platformdirs>=4.3,<5`
- `click>=8.2,<9`
- a compatible bounded `rich-click` release
- `json5>=0.15,<1`
- `psutil>=5.9,<7`
- `rich>=15,<16`
- `python-toon==0.1.3`
- `expression>=5,<6`

Rich SHALL remain the command-result terminal renderer, while `rich-click` SHALL be used only for Click help and Click-generated usage/validation errors. Textual, curses wrappers, Typer, and other CLI frameworks SHALL NOT be added. `python-toon` SHALL be used in-process through `from toon import encode, decode, DecodeOptions`; strict verification SHALL invoke `decode(encoded, DecodeOptions(indent=2, strict=True))`. The project SHALL NOT contain a custom TOON encoder/decoder or invoke a Node subprocess. The dependency SHALL remain exactly pinned. The checked fixture source SHALL be the project's committed envelope fixtures derived from CLI envelope v1 and snapshot schema v2, with TOON syntax expectations traced to the published TOON specification v4.1 (2026-07-26). The supported contract is semantic round-trip of those project envelopes, not a claim that the dependency implements every v4.1 production. A dependency or fixture-source upgrade SHALL require deliberately updating the pin and fixtures together.

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
- **THEN** core runtime dependencies are exactly the ten listed dependencies with specified bounds/pins and no Textual, Typer, Cyclopts, Pydantic, docker-py, or Node runtime

#### Scenario: TOON implementation is in-process and pinned
- **WHEN** TOON output is generated in an isolated installed wheel
- **THEN** `python-toon==0.1.3` encodes via `toon.encode` in the Python process and no custom encoder or Node executable is used

#### Scenario: TOON pin conforms for supported envelopes
- **WHEN** representative success/error envelopes are encoded
- **THEN** strict TOON decoding returns the original JSON value for committed fixtures

#### Scenario: Terminal rendering responsibilities are bounded
- **WHEN** installed metadata and CLI imports are inspected
- **THEN** Rich renders command results, `rich-click` renders only Click help/errors, and no alternate CLI framework is present

#### Scenario: Dashboard extra remains bounded
- **WHEN** `pip install odoo-instance-sdk[dashboard]` is run
- **THEN** installed extra dependencies remain FastAPI, Starlette, and Uvicorn within existing bounds in addition to core dependencies

#### Scenario: Monitor command hint when extra missing
- **WHEN** `odcli monitor` runs and `fastapi` or `uvicorn` is not installed
- **THEN** it exits `1` with a message containing `pip install odoo-instance-sdk[dashboard]`
