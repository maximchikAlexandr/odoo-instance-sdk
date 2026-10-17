## MODIFIED Requirements

### Requirement: Primary checkout Python environment preparation

The public SDK SHALL provide one immutable primary-checkout venv command builder and convenience operation accepting a Python executable or uv selector and a venv path. The same private preparation owner SHALL also supply every create, validate, dependency, origin, interpreter, and lock step used by opt-in project initialization; init SHALL NOT implement a second preparation path or construct a preparation command during execution. Preparation SHALL resolve a relative venv from the project root, create a missing venv through the existing uv process boundary, resolve the platform-specific venv interpreter, jointly compile non-empty shared dependency inputs and install them into the selected venv using existing uv planning and locking, refresh an existing valid venv without recreating it, and atomically persist the interpreter and trusted origin only after all preparation processes succeed. A non-empty staged dependency lock SHALL be published only after successful synchronization and preparation persistence, with previous manifest, origin, and lock bytes restored if publication fails. An empty input set SHALL create or validate the venv without dependency compilation. Dry-run SHALL expose the redacted immutable plan and perform no filesystem, process, lock, or manifest mutation.

#### Scenario: Missing venv is created and prepared
- **WHEN** primary preparation targets a missing venv with non-empty discovered inputs
- **THEN** the plan creates the venv, jointly compiles and installs the inputs, and persists its interpreter and trusted origin after success

#### Scenario: Existing venv is refreshed
- **WHEN** primary preparation targets an existing valid venv
- **THEN** it skips creation, refreshes dependencies through the same planned inputs, and persists that venv interpreter without claiming replacement by the requested selector

#### Scenario: Empty dependency input set
- **WHEN** shared discovery returns no requirement files
- **THEN** preparation creates or validates the venv and persists its interpreter and trusted origin without compiling, installing, or publishing a dependency lock

#### Scenario: Process failure preserves persisted state
- **WHEN** venv creation, validation, dependency compilation, or installation fails
- **THEN** preparation fails without publishing a new interpreter, trusted origin, or dependency lock

#### Scenario: Persistence failure restores prior bytes
- **WHEN** manifest, trusted-origin, or dependency-lock publication fails after preparation processes succeed
- **THEN** the previous manifest, origin, and dependency lock are restored and staged files are removed

#### Scenario: Init and standalone preparation share one owner
- **WHEN** standalone preparation and integrated init receive the same captured project config, Python selector, and venv path
- **THEN** their preparation portions contain the same ordered inputs, process argv, locks, validation, persistence actions, and failure behavior

#### Scenario: Dry-run is mutation-free
- **WHEN** primary preparation is previewed standalone or within init
- **THEN** it reports Python, venv, interpreter, ordered inputs, uv argv, origin/manifest/lock actions, and following lifecycle steps without mutation or secret exposure
