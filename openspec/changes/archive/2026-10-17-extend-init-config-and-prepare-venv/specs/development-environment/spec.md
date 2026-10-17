## ADDED Requirements

### Requirement: Shared project dependency input discovery

Primary venv preparation, managed `env checkout --create-venv`, and unhashed `env sync` SHALL use one dependency-input discovery implementation. For each configured addon root it SHALL recursively and deterministically find module directories containing `__manifest__.py`, consider `requirements.txt` in each module and its immediate parent, then consider `requirements.txt` beside configured `odoo-bin` and explicitly configured requirement files. It SHALL canonicalize paths, retain the first occurrence, ignore absent discovered candidates, reject a missing or non-file explicit input with a typed error before mutation, and expose the ordered result in immutable plans. Managed callers SHALL rebase repository-local inputs to their worktree before canonicalization.

#### Scenario: Nested module and parent requirements are discovered

- **WHEN** configured addon roots contain modules at arbitrary nesting depths and requirements in module or immediate-parent directories
- **THEN** every existing candidate is included once in deterministic root and traversal order

#### Scenario: Odoo and explicit requirements follow addon discovery

- **WHEN** Odoo-core and explicit requirement inputs exist
- **THEN** they follow discovered addon inputs and a canonical duplicate remains only at its first occurrence

#### Scenario: Missing explicit input fails before mutation

- **WHEN** an explicitly configured requirement is missing or is not a regular file
- **THEN** planning raises a typed error naming that input before venv, lock, or manifest mutation

#### Scenario: Missing discovered candidate is ignored

- **WHEN** a module, its parent, or the Odoo-bin directory lacks `requirements.txt`
- **THEN** discovery omits that candidate without error

#### Scenario: Managed discovery uses worktree paths

- **WHEN** shared discovery plans a managed checkout or sync
- **THEN** repository-local addon roots, Odoo bin, and explicit requirements are rebased into that worktree

### Requirement: Primary checkout Python environment preparation

The public SDK SHALL provide one immutable primary-checkout venv command builder and convenience operation accepting a Python executable or uv selector and a venv path. It SHALL resolve a relative venv from the project root, create a missing venv through the existing uv process boundary, resolve the platform-specific venv interpreter, jointly compile non-empty shared dependency inputs and install them into the selected venv using existing uv planning and locking, refresh an existing valid venv without recreating it, and atomically persist the interpreter to `.odcli/project.toml` only after all preparation succeeds. An empty input set SHALL create or validate the venv without dependency compilation. Dry-run SHALL expose the redacted immutable plan and perform no filesystem, process, lock, or manifest mutation.

#### Scenario: Missing venv is created and prepared

- **WHEN** primary preparation targets a missing venv with non-empty discovered inputs
- **THEN** the plan creates the venv, jointly compiles and installs the inputs, and persists its interpreter after success

#### Scenario: Existing venv is refreshed

- **WHEN** primary preparation targets an existing valid venv
- **THEN** it skips creation, refreshes dependencies through the same planned inputs, and persists that venv interpreter

#### Scenario: Empty dependency input set

- **WHEN** shared discovery returns no requirement files
- **THEN** preparation creates or validates the venv and persists its interpreter without compiling or installing dependencies

#### Scenario: Failure preserves manifest

- **WHEN** venv creation, validation, dependency compilation, or installation fails
- **THEN** preparation fails without persisting a new interpreter

#### Scenario: Dry-run is mutation-free

- **WHEN** primary preparation runs with dry-run
- **THEN** it reports Python, venv, interpreter, ordered inputs, uv argv, and manifest action without mutation or secret exposure

## MODIFIED Requirements

### Requirement: Dependency compilation

Without explicit hash-lock inputs, the shared project dependency inputs in both managed Python modes SHALL be compiled by one `uv pip compile` into the environment-owned `requirements.lock`.

During checkout, `uv pip compile` SHALL receive `--python <target-python>`, where `<target-python>` is the exact resolved interpreter passed to the immediately following dependency installation step: the recorded project interpreter for a reused environment or the interpreter inside the newly created owned venv. The immutable checkout plan and its dry-run projection SHALL expose this same target without resolving or rebuilding it at execution time.

- reused venv: `uv pip install --python <project-python> -r <lock>` SHALL preserve unrelated project tools;
- owned venv: `uv pip sync --python <environment-python> <lock>` SHALL provide isolation;
- uv writes SHALL be serialized by `flock` on the canonical Python-environment path;
- repository-local dependency files SHALL be rebased into the worktree, and the lock/fingerprint SHALL refer to that worktree;
- `env sync --upgrade` SHALL update pins, regular sync SHALL preserve them, and a failed compile SHALL NOT replace a valid lock;
- `run` and `shell` SHALL NOT call `sync_python`; only checkout and `env sync` SHALL write the managed lock/venv, while `doctor` and `deps verify` report drift.

When paired `hash_lock` and `hash_lock_sha256` inputs are supplied for an owned environment, checkout and `env sync` SHALL replace the discovery/compile branch with the hash-verified `uv pip sync --require-hashes` branch defined below. Hash-lock mode SHALL NOT rewrite the reviewed lock or fall back to compilation or installation.

Runtime prefix SHALL always be `[recorded-python, odoo-bin]`. Managed-environment `uv venv`, dependency argv construction, execution, and fingerprinting SHALL remain internal implementation of the existing environment resource rather than a new public managed-venv module; the primary-checkout operation SHALL reuse only the minimum shared uv step construction and SHALL NOT add catalog lifecycle.

#### Scenario: Checkout compiles for reused Python

- **WHEN** checkout reuses an existing project virtual environment and compiles discovered dependency inputs
- **THEN** its compile and install steps SHALL both contain `--python` with the exact recorded project interpreter

#### Scenario: Checkout compiles for owned Python

- **WHEN** checkout creates an owned virtual environment and compiles discovered dependency inputs
- **THEN** its compile and sync steps SHALL both contain `--python` with the exact interpreter inside that owned environment

#### Scenario: Failed compile keeps valid lock

- **WHEN** `uv pip compile` fails in legacy mode and a valid `requirements.lock` already exists
- **THEN** the valid lock SHALL NOT be replaced and synchronization SHALL continue with that lock

#### Scenario: Hash-lock mode bypasses compilation by contract

- **WHEN** valid paired hash-lock inputs select an owned environment
- **THEN** dependency discovery and compile SHALL be absent from the immutable plan and the reviewed lock SHALL remain byte-identical

#### Scenario: Checkout and sync share discovery

- **WHEN** a managed environment is created with `--create-venv` and later processed by unhashed `env sync` at the same worktree snapshot
- **THEN** both immutable plans contain the same ordered shared dependency inputs
