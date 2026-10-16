## ADDED Requirements

### Requirement: Init config-generation options

`odcli init` SHALL expose repeatable `--addons-root PATH` and `--server-wide-module NAME` options that delegate to the public init SDK operation. The command SHALL preserve option order, distinguish omission from an explicitly empty replacement through the supported SDK/config input, and include resolved values and generated-config actions in established Rich, JSON, and TOON planning and result output.

#### Scenario: Repeated init options preserve order
- **WHEN** a user supplies repeated addon-root or server-wide-module options
- **THEN** the CLI delegates their values to the SDK in command-line order

#### Scenario: Init dry-run reports generated config
- **WHEN** `odcli init --dry-run` is invoked with config-generation options
- **THEN** output shows the resolved stored settings and generated-config action without writing files

#### Scenario: Headless overwrite uses existing confirmation
- **WHEN** config-generation options would replace existing init artifacts in no-input mode
- **THEN** the command refuses unless `--yes` is supplied and never prompts

### Requirement: Prepare project virtual environment command

`odcli prepare-venv` SHALL accept required `--python TEXT` and `--venv PATH` inputs, delegate planning and execution to the public SDK preparation operation, support the established `--dry-run`, Rich, JSON, and TOON behavior, and route every uv child process through the shared process boundary.

#### Scenario: Prepare missing venv
- **WHEN** `odcli prepare-venv --python 3.12 --venv .venv` targets a missing environment
- **THEN** the CLI presents the plan, executes the public SDK operation, and reports the persisted interpreter and dependency inputs

#### Scenario: Refresh existing venv
- **WHEN** `odcli prepare-venv` targets an existing valid environment
- **THEN** the CLI delegates a dependency refresh without recreating that environment

#### Scenario: Prepare dry-run
- **WHEN** `odcli prepare-venv --dry-run` is invoked
- **THEN** machine and human output truthfully mark dry-run and report the full redacted plan without mutation

#### Scenario: Missing explicit requirement is rendered
- **WHEN** dependency planning raises the typed missing-explicit-requirement error
- **THEN** the CLI returns the established failure envelope naming the missing path without a traceback
