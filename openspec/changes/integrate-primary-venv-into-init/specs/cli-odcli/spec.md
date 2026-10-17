## ADDED Requirements

### Requirement: Init can prepare the primary project virtual environment

`odcli init` SHALL expose one optional `--venv PATH` input. Its presence SHALL opt into the public init SDK's integrated primary-venv plan and its absence SHALL preserve existing CLI behavior. The existing resolved `--python` input SHALL be the venv creation selector; `--venv` with no effective Python selection SHALL fail before mutation. The callback SHALL remain transport-only and SHALL support the established dry-run, Rich, JSON, TOON, redaction, confirmation, and typed-failure contracts. The separate `odcli prepare-venv` command SHALL remain available with its current signature for explicit refresh and recovery.

#### Scenario: CLI omission is backward compatible
- **WHEN** a user invokes `odcli init` without `--venv`
- **THEN** CLI parsing, SDK delegation, plan, output, and side effects retain their current behavior

#### Scenario: CLI delegates integrated preparation
- **WHEN** a user invokes `odcli init --python 3.12 --venv .venv`
- **THEN** the callback delegates one captured init command that persists project inputs, prepares `.venv`, records its concrete interpreter, and uses it before Compose/Odoo bootstrap

#### Scenario: Integrated dry-run is complete and inert
- **WHEN** a user invokes opted-in init with `--dry-run` in Rich, JSON, or TOON mode
- **THEN** output truthfully identifies dry-run and presents the full redacted ordered plan without files, locks, child processes, Compose, Odoo, or registration mutation

#### Scenario: Existing project refresh does not early-return
- **WHEN** a manifest is already identical and the user supplies `--venv`
- **THEN** init does not return the ordinary no-op before SDK execution and instead verifies the manifest and refreshes the selected environment

#### Scenario: Preparation failure uses init failure transport
- **WHEN** any integrated venv preparation stage fails
- **THEN** Rich, JSON, and TOON report a sanitized typed init failure with non-zero exit status and do not emit a successful init result

#### Scenario: Standalone recovery remains public
- **WHEN** integrated init leaves a recoverable venv after failure or a user requests an explicit later refresh
- **THEN** `odcli prepare-venv --python TEXT --venv PATH` remains available and retains its existing plan and execution contract
