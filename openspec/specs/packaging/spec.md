## ADDED Requirements

### Requirement: Project uses `uv` for environment, dependencies, build, and publish

The project SHALL be managed by `uv`. `pyproject.toml` SHALL declare build backend (`hatchling` or equivalent uv-native backend) and shall be installable via `uv add odoo-instance-sdk` as a library dependency.

#### Scenario: Library add
- **WHEN** `uv add odoo-instance-sdk` is run in another project
- **THEN** the package is added as a dependency and importable

### Requirement: Runtime dependencies are minimal

The `pyproject.toml` SHALL declare runtime dependencies as exactly: `httpx` (pinned `>=0.27,<1.0`), `msgspec`, and `platformdirs` (pinned `>=4.3,<5`). No other runtime dependencies SHALL be introduced.

#### Scenario: Runtime dependencies enumerated
- **WHEN** the published metadata is inspected
- **THEN** runtime dependencies are `httpx>=0.27,<1.0`, `msgspec`, and `platformdirs>=4.3,<5`
- **AND** no `psutil` or `pytest` appear as runtime dependencies

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