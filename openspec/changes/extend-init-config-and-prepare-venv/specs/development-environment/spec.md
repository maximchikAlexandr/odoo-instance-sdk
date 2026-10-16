## ADDED Requirements

### Requirement: Shared project dependency input discovery

The SDK SHALL expose one dependency-input discovery implementation used by project virtual-environment preparation, managed `env checkout --create-venv`, and `env sync`. It SHALL recursively find every directory containing `__manifest__.py` under each configured addon root at arbitrary depth; include an existing `requirements.txt` in each discovered module directory and its immediate parent; include an existing `requirements.txt` beside the configured `odoo-bin`; append explicitly configured requirement files; canonicalize paths; remove duplicates by canonical path while preserving first occurrence; and expose the final ordered file list in the immutable operation plan.

#### Scenario: Nested addon modules are discovered
- **WHEN** configured addon roots contain modules at different nesting depths
- **THEN** discovery finds every module identified by `__manifest__.py` recursively

#### Scenario: Module and parent requirements are included
- **WHEN** a discovered module and its immediate parent each contain `requirements.txt`
- **THEN** both files are included once in deterministic discovery order

#### Scenario: Odoo core requirements are included
- **WHEN** `requirements.txt` exists beside the configured `odoo-bin`
- **THEN** that file is included in dependency inputs

#### Scenario: Explicit requirements are appended and deduplicated
- **WHEN** an explicit requirement resolves to a path already discovered
- **THEN** the final plan lists that canonical path only at its first occurrence

#### Scenario: Missing explicit requirement fails clearly
- **WHEN** an explicitly configured requirement file does not exist or is not a regular file
- **THEN** planning fails with a typed error naming that input before any venv or lock mutation

#### Scenario: Missing discovered candidate is ignored
- **WHEN** a module, module parent, or Odoo-bin directory has no `requirements.txt`
- **THEN** discovery omits that candidate without error

#### Scenario: Managed worktree rebases project inputs
- **WHEN** shared discovery is used for a managed checkout
- **THEN** repository-local addon roots, Odoo bin, and explicit requirement paths are rebased to that worktree before canonicalization

### Requirement: Primary checkout Python environment preparation

The public SDK SHALL provide an immutable project environment preparation operation accepting a Python executable or uv selector and a venv path. It SHALL resolve relative venv paths against the repository root, create a missing venv with `uv venv <venv> --python <python>`, use the venv interpreter for dependency installation, jointly compile all discovered inputs through the existing uv process boundary, update dependencies in an existing venv, and atomically persist the resulting interpreter path to `.odcli/project.toml` only after successful preparation. All filesystem and process mutations SHALL be absent in dry-run.

#### Scenario: Missing venv is created and prepared
- **WHEN** preparation targets a venv path that does not exist
- **THEN** the plan creates it with the selected Python, resolves all dependency inputs together, installs the result, and persists its interpreter

#### Scenario: Existing venv is updated
- **WHEN** preparation targets an existing valid venv
- **THEN** it skips venv creation, resolves the same dependency inputs together, updates dependencies, and persists that venv interpreter

#### Scenario: Empty dependency input set
- **WHEN** discovery produces no requirement files
- **THEN** preparation creates or validates the venv and persists its interpreter without invoking uv dependency compilation

#### Scenario: Preparation plan exposes inputs
- **WHEN** preparation is previewed
- **THEN** the immutable plan lists the selected Python, venv path, resulting interpreter, ordered requirement files, uv argv, and manifest update without exposing secrets

#### Scenario: Preparation failure preserves manifest
- **WHEN** venv creation, dependency compilation, or installation fails
- **THEN** the operation fails and does not persist a new interpreter in `.odcli/project.toml`

#### Scenario: Dry-run performs no mutation
- **WHEN** project environment preparation runs with dry-run
- **THEN** it returns the resolved plan without creating a venv, compiling or installing dependencies, or modifying the manifest

### Requirement: Consistent dependency synchronization

Project preparation and managed-environment synchronization SHALL use the same discovery result and existing uv compile/install semantics. Managed owned environments SHALL continue to use `uv pip sync`; reused environments SHALL continue to use `uv pip install`; normal sync SHALL preserve pins; `--upgrade` SHALL update pins; uv writes SHALL remain serialized by the canonical Python-environment lock; and a failed compile SHALL not replace a valid prior lock.

#### Scenario: Checkout and sync share inputs
- **WHEN** a managed environment is created with `--create-venv` and later processed by `env sync`
- **THEN** both operations derive the same ordered dependency inputs for the same worktree snapshot

#### Scenario: Project and managed environments use ownership semantics
- **WHEN** dependencies are installed into a project venv, an owned managed venv, or a reused managed interpreter
- **THEN** project preparation updates its selected venv, owned managed sync uses exact sync, and reused managed sync preserves unrelated packages through install

#### Scenario: Failed recompile retains valid lock
- **WHEN** uv compilation fails while a valid dependency lock already exists
- **THEN** the valid lock is not replaced and the existing retained-lock outcome remains observable
