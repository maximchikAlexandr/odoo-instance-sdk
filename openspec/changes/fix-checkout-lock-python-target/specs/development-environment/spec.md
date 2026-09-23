## MODIFIED Requirements

### Requirement: Dependency compilation

Without explicit hash-lock inputs, Odoo Core and project requirements in both Python modes SHALL be compiled by one `uv pip compile` into the environment-owned `requirements.lock`.

During checkout, `uv pip compile` SHALL receive `--python <target-python>`, where `<target-python>` is the exact resolved interpreter passed to the immediately following dependency installation step: the recorded project interpreter for a reused environment or the interpreter inside the newly created owned venv. The immutable checkout plan and its dry-run projection SHALL expose this same target without resolving or rebuilding it at execution time.

- reused venv: `uv pip install --python <project-python> -r <lock>` SHALL preserve unrelated project tools;
- owned venv: `uv pip sync --python <environment-python> <lock>` SHALL provide isolation;
- uv writes SHALL be serialized by `flock` on the canonical Python-environment path;
- repository-local dependency files SHALL be rebased into the worktree, and the lock/fingerprint SHALL refer to that worktree;
- `env sync --upgrade` SHALL update pins, regular sync SHALL preserve them, and a failed compile SHALL NOT replace a valid lock;
- `run` and `shell` SHALL NOT call `sync_python`; only checkout and `env sync` SHALL write the lock/venv, while `doctor` and `deps verify` report drift.

When paired `hash_lock` and `hash_lock_sha256` inputs are supplied for an owned environment, checkout and `env sync` SHALL replace the discovery/compile branch with the hash-verified `uv pip sync --require-hashes` branch defined below. Hash-lock mode SHALL NOT rewrite the reviewed lock or fall back to compilation or installation.

Runtime prefix SHALL always be `[recorded-python, odoo-bin]`. `uv venv`, dependency argv construction, execution, and fingerprinting SHALL remain internal implementation of the existing environment resource rather than a new public venv module.

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
