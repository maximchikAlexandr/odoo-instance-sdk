## ADDED Requirements

### Requirement: Self-update preserves installed optional extras

For a supported uv-tool installation, inspect/snapshot SHALL capture the normalized installed direct requirement and requested optional-extra set from uv tool metadata. Install, automatic rollback, and emitted recovery argv SHALL use the same validated package source and exact extra set at their respective target/snapshot revision. `odcli update` SHALL NOT add extras to a core-only install or drop `[dashboard]` from an install that has it; missing, malformed, or ambiguous installation-composition evidence SHALL fail preflight before replacement.

#### Scenario: Dashboard installation updates
- **WHEN** installed provenance identifies `odoo-instance-sdk[dashboard]` from the supported Git source
- **THEN** target install and any rollback/recovery requirement include `[dashboard]` and the resulting tool retains FastAPI, Starlette, and Uvicorn

#### Scenario: Core-only installation updates
- **WHEN** installed provenance identifies the supported package without optional extras
- **THEN** update preserves the core-only composition and does not install dashboard dependencies

#### Scenario: Extra provenance is ambiguous
- **WHEN** uv-tool metadata cannot prove the installed package requirement or extra set
- **THEN** update returns a preflight failure before `uv tool install` and supplies a sanitized manual/recovery next step

### Requirement: Updated dashboard installation is verified

When the preserved installation includes `dashboard`, post-install maintenance SHALL verify in the new interpreter that the declared dashboard dependencies import and the packaged SPA entry assets exist before update commit/cleanup. An isolated regression SHALL update a dashboard-enabled uv-tool installation to an exact target revision, restart `odcli monitor --headless`, and prove its health/API response without a manual reinstall.

#### Scenario: Dashboard dependency was lost
- **WHEN** target replacement lacks a required dashboard import or packaged SPA entry asset
- **THEN** update verification fails under the existing rollback/incomplete-update contract and does not report success

#### Scenario: Monitor works after restart
- **WHEN** a dashboard-enabled update succeeds and the new `odcli monitor --headless` process is started
- **THEN** monitor starts without FastAPI/Starlette/Uvicorn import failure and its documented health/API request succeeds
