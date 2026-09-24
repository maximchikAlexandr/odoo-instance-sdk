## Purpose

Define the supported packaging, dependency, build, and publication contract for the SDK.

## Requirements

### Requirement: Project uses `uv` for environment, dependencies, build, and publish

The project SHALL be managed by `uv`. `pyproject.toml` SHALL declare build backend (`hatchling` or equivalent uv-native backend) and shall be installable via `uv add odoo-instance-sdk` as a library dependency.

#### Scenario: Library add
- **WHEN** `uv add odoo-instance-sdk` is run in another project
- **THEN** the package is added as a dependency and importable

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

### Requirement: Production Python file line limit

CI SHALL run a simple check that rejects manually maintained production Python files larger than 1000 physical lines. Generated or vendored code SHALL be excluded with an explicit marker; an allowlist of existing large files SHALL NOT be created. Behaviour-preserving refactor along confirmed responsibility boundaries SHALL bring oversized files under the limit without changing user behavior, public imports, CLI command names, immutable command plans, Rich/JSON/TOON output, or FastAPI/OpenAPI contracts.

#### Scenario: Line limit enforced

- **WHEN** CI runs the line-limit check against production Python files
- **THEN** any manually maintained file over 1000 physical lines is rejected unless it is generated or vendored

#### Scenario: No existing-file allowlist

- **WHEN** the line-limit check configuration is inspected
- **THEN** it does not grandfather existing oversized files through a permanent allowlist

### Requirement: Strict mypy and ruff

The project SHALL use `mypy` in strict mode (`mypy --strict` or equivalent configuration) and `ruff`. The ruff configuration SHALL be based on `multica-py/ruff.toml` with `known-first-party = ["odoo_instance_sdk"]`. The v0.1 codebase SHALL pass both `mypy` and `ruff check` with zero errors.

#### Scenario: Lint check
- **WHEN** `ruff check .` is run
- **THEN** it exits 0

#### Scenario: Type check
- **WHEN** `mypy --strict src/odoo_instance_sdk` is run
- **THEN** it exits 0

### Requirement: CI runs ruff and mypy on every push and PR

The project SHALL provide a CI workflow (GitHub Actions) that runs `ruff check .` and `mypy --strict src/odoo_instance_sdk` on every push to `main` and on every pull request. The CI SHALL use `uv` to install dependencies and run the checks. The CI SHALL fail on any non-zero exit code from either tool.

#### Scenario: Push to main triggers CI
- **WHEN** a commit is pushed to `main`
- **THEN** the CI workflow runs `ruff check .` and `mypy --strict src/odoo_instance_sdk`
- **AND** the workflow fails if either tool exits non-zero

#### Scenario: Pull request triggers CI
- **WHEN** a pull request is opened against `main`
- **THEN** the CI workflow runs `ruff check .` and `mypy --strict src/odoo_instance_sdk`

### Requirement: Build wheel and sdist

The project SHALL build both a wheel and an sdist via `uv build`. Both artefacts SHALL be valid for upload to PyPI.

#### Scenario: Build both
- **WHEN** `uv build` is run
- **THEN** both `dist/*.whl` and `dist/*.tar.gz` are produced
- **AND** both can be unpacked and contain `pyproject.toml`, README, LICENSE, and the `odoo_instance_sdk` package

### Requirement: Package metadata is complete

The `pyproject.toml` SHALL populate at minimum: `name`, `version` (static, SemVer, independent of Odoo version), `description`, `readme` (path to README), `license` (matching repository LICENSE), `requires-python` (`>=3.12`), `authors`, and a classifiers list appropriate for PyPI.

The package version MUST NOT embed the Odoo version. The Odoo compatibility target is stated in `description` and classifiers, not in `version`. See ADR-0001.

#### Scenario: Metadata present
- **WHEN** `pyproject.toml` is inspected
- **THEN** `name`, `version`, `description`, `readme`, `license`, `requires-python`, `authors` are all set
- **AND** `version` follows SemVer and does not contain the Odoo major version
- **AND** `requires-python >= "3.12"`

### Requirement: Bounded Expression dependency

The project SHALL keep Expression under the repository's bounded runtime dependency policy and reproducible lock only while the measured payoff remains non-negative. Production imports SHALL remain limited to pure internal planning modules; package metadata paths, Click registration, public execution models, process effects, cleanup, and serializers SHALL not import Expression.

The GitHub #35 vertical slice SHALL append a reproducible post-slice row to `docs/adr/0002-bounded-expression-checkout-assessment.md`. The row and adjacent explanation SHALL identify the exact pre/post revisions and affected native-argument planning functions, count `ast.If`, `ast.IfExp`, and `ast.Match` nodes before and after, count Expression boundary adapters/unwraps introduced by the slice, and evaluate the same stop condition used by the preliminary checkout assessment. If introduced adapters/unwraps exceed planning branches removed, the implementation SHALL remove Expression and its lock entry while retaining concrete typed stage signatures; otherwise it SHALL record why bounded retention remains justified. A slice that introduces zero Expression boundary operations SHALL still record the mandatory measurement and SHALL not claim that the checkout result waived it.

#### Scenario: Checkout pipeline payoff is measured

- **WHEN** the first checkout planning slice is complete
- **THEN** the ADR records before/after planning branches and Expression adapter/unwrap count
- **AND** the result remains a preliminary checkout assessment only
- **AND** a positive checkout result does not waive the mandatory reassessment after the #35 vertical slice

#### Scenario: Issue #35 vertical slice is complete

- **WHEN** native run-argument planning, validation, command capture, and parity tests are complete
- **THEN** the ADR contains the reproducible post-#35 branch-versus-adapter/unwrap row and stop-condition outcome
- **AND** strict tests verify that the post-#35 row is no longer pending
- **AND** Expression is removed before broader adoption if adapters/unwraps introduced by the slice exceed planning branches removed

#### Scenario: Issue #35 does not use Expression

- **WHEN** the native-argument slice uses a small pure validator and introduces no Expression adapter or unwrap
- **THEN** the ADR still records a zero adapter/unwrap count, the measured branch delta, and the evaluated retention/removal outcome

#### Scenario: Metadata startup runs

- **WHEN** fresh interpreters import `odoo_instance_sdk`, run `odcli --help`, or run `odcli --version`
- **THEN** Expression and `odoo_instance_sdk.internal.proc` remain absent from `sys.modules`

### Requirement: Architecture regression gates

The architecture regression gates SHALL include: the `PUBLIC_LEAF_CASES` SDK-first contract; line-specific ban on bare `httpx` outside `internal/transport/` files; zero `xmlrpc.client.ServerProxy` under `src/`; the `self-update` lock `odcli-update.lock` and new-process migration; and the E/F inventory plus cleanup. Findings SHALL shrink; a new finding SHALL NOT be silenced by an undocumented allowlist.

#### Scenario: Direct process launch is added

- **WHEN** production code adds `subprocess.run` or `subprocess.Popen` outside `internal/proc`
- **THEN** CI fails and identifies the launch site

#### Scenario: Output boundary is bypassed

- **WHEN** production code adds `print`, `click.echo`/`secho`, direct stdout/stderr writes, or `Console().print` outside the documented boundary
- **THEN** CI fails and identifies the output site

#### Scenario: Imprecise annotation is added

- **WHEN** a production annotation contains explicit `Any` or bare `object`, including quoted or qualified forms
- **THEN** CI fails and identifies the annotation

#### Scenario: transport gate bans bare httpx

- **WHEN** the architecture gate runs on `src/`
- **THEN** direct `import httpx`, `httpx.get/request/stream`, and `httpx.Client` creation outside the line-specific `internal/transport/` allowlist are rejected

#### Scenario: src has no ServerProxy

- **WHEN** the architecture gate runs on `src/`
- **THEN** `xmlrpc.client.ServerProxy` is absent

#### Scenario: E/F test quality gate

- **WHEN** the E/F test quality gate runs after `ef-inventory.md` is applied
- **THEN** no listed E/F-grade tests remain and coverage is not below the recorded merge-base baseline

### Requirement: Architecture rules are repository-local

`AGENTS.md` SHALL state the process, immutable preview/execution, public command sibling, bounded output, typed pipeline, no-`Any`/`object`, third-party narrowing, and no vague/single-use abstraction rules from GitHub #45 so future changes consume these boundaries.

#### Scenario: Future agent reads repository rules

- **WHEN** a contributor opens repository-local `AGENTS.md`
- **THEN** the required execution, output, typing, and minimal-abstraction invariants are explicit and match the enforced gates

### Requirement: Optional host tools are not package dependencies
Neither `msgfmt` nor `git-absorb`, any wrapper/downloader/installer, or an `odcli-absorb` package SHALL appear in core or optional Python dependency metadata. Commands SHALL discover these host executables on demand and doctor SHALL report optional capability without making unrelated health fail. [Sources: GH#54; GH#65]

#### Scenario: Build package metadata
- **WHEN** wheel, sdist, and dependency inventories are checked
- **THEN** no gettext or git-absorb package boundary has been introduced

### Requirement: Installed metadata is the CLI version source

`odcli --version` SHALL read optional PEP 610 `direct_url.json` via `importlib.metadata`. If `vcs_info.commit_id` is a hex string of length at least 7, the human version output SHALL append the first 7 characters, e.g. `odcli, version 0.1.0 (6a984c7)`. Wheel/sdist installs without `direct_url.json` SHALL keep the package version. Malformed or missing metadata SHALL safely fall back to the package version. The command SHALL stay fast, SHALL NOT call Git, SHALL NOT require a checkout or network, and SHALL NOT import operation-only dependencies. No build-time Git dependency, separate version registry, or network check SHALL be introduced.

#### Scenario: Installed wheel reports its metadata version

- **WHEN** an isolated environment installs a built wheel and runs `odcli --version` outside a project
- **THEN** the command exits `0` and its output contains the version declared by that wheel's distribution metadata

#### Scenario: Version support adds no dependency

- **WHEN** the built wheel metadata is inspected after the change
- **THEN** its runtime dependency set is unchanged by version discovery

#### Scenario: VCS install shows commit

- **WHEN** `odcli --version` runs on a uv-tool VCS install
- **THEN** the output shows the package version and the actually installed short commit

#### Scenario: wheel install shows package version only

- **WHEN** `odcli --version` runs on a wheel/sdist install without `direct_url.json`
- **THEN** the output shows only the package version

#### Scenario: malformed metadata falls back

- **WHEN** `odcli --version` runs with malformed `direct_url.json`
- **THEN** the output shows only the package version and does not crash

### Requirement: E/F test quality cleanup

The list of E/F-grade tests SHALL be produced first as `openspec/changes/address-alpha-testing-defects-74/ef-inventory.md` using design D17 heuristics (nodeid, grade, reason, rewritten|replaced|deleted). Each listed test SHALL then be rewritten, replaced, or deleted per that table. Deletion SHALL preserve behavioural coverage elsewhere; a "did not crash" assertion SHALL NOT be accepted as a replacement. Line and branch coverage SHALL NOT drop below the merge-base percents recorded in that file.

#### Scenario: no E/F tests remain in the changed scope

- **WHEN** the E/F test quality gate runs on the changed scope
- **THEN** no E/F-grade tests remain

#### Scenario: each E/F test has a recorded decision

- **WHEN** the cleanup record is inspected
- **THEN** each original E/F test has a rewritten/replaced/deleted decision with a reason

#### Scenario: coverage does not drop

- **WHEN** the full test suite runs after the cleanup
- **THEN** line and branch coverage are not below baseline
