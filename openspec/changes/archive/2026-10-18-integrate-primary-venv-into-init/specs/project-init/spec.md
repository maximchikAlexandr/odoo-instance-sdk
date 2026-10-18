## ADDED Requirements

### Requirement: Opt-in initialization prepares the primary virtual environment

The public init SDK command and convenience operation SHALL accept an optional primary venv path. When absent, initialization SHALL retain its existing plan, side effects, results, and retry behavior. When present, initialization SHALL resolve the path with the existing primary-preparation rules, require an effective Python executable or uv selector from the existing init input resolution, and capture one immutable plan that writes or verifies the resolved project manifest before reusing the existing primary-venv preparation owner and before any Compose or Odoo bootstrap effect. The validated platform-specific interpreter inside that venv SHALL be persisted as project Python and SHALL be the interpreter captured for later Odoo bootstrap.

#### Scenario: Omitted venv preserves initialization
- **WHEN** an init caller omits the primary venv path
- **THEN** the plan and execution contain no venv preparation steps and retain the existing initialization behavior

#### Scenario: Fresh init captures ordered preparation
- **WHEN** a fresh project is initialized with a primary venv path and an effective Python selector
- **THEN** one immutable plan orders manifest persistence before create-or-validate, dependency preparation, origin/interpreter/lock publication, Compose startup, and Odoo bootstrap

#### Scenario: Missing Python fails before mutation
- **WHEN** a primary venv path is requested but init resolution produces no effective Python executable or uv selector
- **THEN** planning fails with a typed error before writing the manifest, creating the venv, acquiring a mutating lock, or starting Compose or Odoo

#### Scenario: Prepared interpreter drives bootstrap
- **WHEN** integrated preparation succeeds and Compose initialization requires Odoo bootstrap
- **THEN** the captured bootstrap process uses the validated interpreter inside the selected venv rather than the source selector written by the earlier manifest phase

#### Scenario: Empty requirements still prepare the interpreter
- **WHEN** integrated preparation discovers no dependency inputs
- **THEN** the plan creates or validates the venv, publishes trusted origin and the concrete interpreter, omits compile, sync, and dependency-lock publication, and continues to bootstrap

### Requirement: Integrated preparation has phase-consistent retry and failure semantics

Integrated init SHALL retain the standalone preparation operation's locks, validation, process boundary, provenance, staged lock, and atomic persistence guarantees. A failure before preparation publication SHALL NOT persist the target interpreter, trusted origin, or new dependency lock, but SHALL retain the already completed init manifest/config phase and SHALL NOT claim rollback of venv process effects. A failure after successful preparation SHALL retain the prepared state and SHALL prevent successful init completion until remaining Compose/Odoo postconditions pass. Repeating the same opted-in init SHALL verify or repair init artifacts, refresh the selected valid venv, and resume later lifecycle steps without bypassing immutable planning.

#### Scenario: Create or validation failure stops before bootstrap
- **WHEN** venv creation or validation fails during integrated init
- **THEN** init returns failure, later dependency and Compose/Odoo steps do not run, and no target interpreter, trusted origin, or new dependency lock is published

#### Scenario: Compile or sync failure stops before bootstrap
- **WHEN** dependency compilation or synchronization fails during integrated init
- **THEN** init returns failure, the prior manifest remains at its resolved pre-preparation Python state, existing persisted preparation state is restored, and Compose/Odoo steps do not run

#### Scenario: Publication failure restores preparation state
- **WHEN** origin, interpreter, or dependency-lock publication fails after venv processes complete
- **THEN** the preparation transaction restores its previous manifest, origin, and lock bytes and init does not start Compose or Odoo

#### Scenario: Bootstrap failure keeps successful preparation
- **WHEN** preparation publishes successfully but Compose startup or Odoo bootstrap fails
- **THEN** init returns failure while retaining the concrete interpreter, trusted origin, and dependency lock for an identical retry or standalone recovery

#### Scenario: Identical retry refreshes then resumes
- **WHEN** an existing project's requested manifest is identical and init is repeated with the same venv path
- **THEN** init verifies the manifest, validates and refreshes the venv through the captured plan, then executes only the required later lifecycle work

#### Scenario: Damaged existing venv fails closed
- **WHEN** the target path has an interpreter-shaped file but does not validate as a virtual environment
- **THEN** integrated init fails before dependency synchronization, preparation persistence, Compose startup, or Odoo bootstrap

### Requirement: Integrated Python replacement remains explicit and honest

The resolved init Python SHALL be the creation selector for a missing venv. An existing valid venv SHALL retain the standalone refresh contract and SHALL NOT be silently deleted or recreated when the caller changes `--python`; its validated concrete interpreter SHALL remain runtime truth. A changed explicit Python input that changes the requested manifest SHALL retain existing confirmation and `--yes` rules. Plans and results SHALL distinguish the requested selector, venv path, and concrete interpreter without claiming that an existing environment was recreated.

#### Scenario: Missing venv uses changed selector
- **WHEN** a caller selects a missing venv path and supplies a new Python selector
- **THEN** the captured create step passes that selector to uv and the resulting venv interpreter becomes project Python

#### Scenario: Existing venv is not recreated for changed selector
- **WHEN** a caller supplies a changed Python selector with an existing valid selected venv
- **THEN** existing overwrite confirmation applies, preparation refreshes without a create step, and output reports the actual existing venv interpreter

#### Scenario: Headless replacement retains confirmation safety
- **WHEN** a changed Python selector makes an existing manifest differ in no-input or machine mode
- **THEN** init refuses before mutation unless `--yes` is supplied
