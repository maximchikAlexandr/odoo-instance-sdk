## MODIFIED Requirements

### Requirement: `EnvironmentCheckoutOptions` public type

`EnvironmentCheckoutOptions` MUST be a `msgspec.Struct` with `frozen=True` and these fields:

- `base_ref: str | None = None`
- `name: str | None = None`
- `config_path: Path | None = None`
- `db_mode: EnvironmentDatabaseMode = EnvironmentDatabaseMode.SHARED`
- `source_database: str | None = None`
- `target_database: str | None = None`
- `odoo_bin: Path | None = None`
- `python: str | Path | None = None`
- `create_venv: bool = False`
- `http_port: int | None = None`
- `hash_lock: Path | None = None`
- `hash_lock_sha256: str | None = None`

`base_ref` is the explicit per-call override; when it is `None`, checkout SHALL use `ProjectConfig.default_base_ref`, then `HEAD`. `source_database is not None` SHALL record explicit caller intent for the legacy-unknown provenance exception even when it equals the configured project default.

`create_venv` SHALL default to `false` and SHALL NOT come from a project manifest, VS Code profile, or cwd inference; only explicit `--create-venv` on the current checkout enables it. `hash_lock` and `hash_lock_sha256` SHALL default to `None`, SHALL be accepted only as a pair with `create_venv=True`, and SHALL NOT come from a manifest, profile, environment variable, or cwd inference. Checkout SHALL validate the pair, canonical regular-file path, lowercase SHA-256, and matching file bytes while constructing its immutable command and SHALL revalidate the digest immediately before the dependency process step.

#### Scenario: Default shared checkout

- **WHEN** `EnvironmentCheckoutOptions()` is used unchanged and the project has no default base
- **THEN** `db_mode=SHARED`, `create_venv=False`, `hash_lock=None`, `hash_lock_sha256=None`, and the effective base SHALL be `HEAD`

#### Scenario: Explicit source records legacy opt-in

- **WHEN** options explicitly contain `source_database="legacy_db"`
- **THEN** checkout MAY apply the warned unknown-provenance exception for that exact database

#### Scenario: Checkout hash-lock validation fails closed

- **WHEN** checkout receives only one paired input, a malformed or mismatched digest, a non-regular/unreadable path, or hash-lock inputs without `create_venv=True`
- **THEN** command construction SHALL fail before Git, uv, filesystem, catalogue, database, or environment mutation

#### Scenario: Checkout dry-run uses the immutable hash-lock plan

- **WHEN** public checkout dry-run receives a valid lock pair with `create_venv=True`
- **THEN** it SHALL expose the sanitized captured `uv pip sync --require-hashes` step and matching lock digest without spawning a process or changing any resource
- **THEN** later execution of that captured command SHALL consume the same argv, cwd, digest, and action order rather than rebuilding them

### Requirement: `EnvironmentResource` public API

`EnvironmentResource` MUST be exposed as `OdooClient.environments` and provide:

```python
def checkout(
    self,
    project: ProjectConfig | Path,
    branch: str,
    *,
    options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
) -> DevelopmentEnvironment: ...

def sync_python(
    self,
    selector: EnvironmentSelector,
    *,
    upgrade: bool = False,
    hash_lock: Path | None = None,
    hash_lock_sha256: str | None = None,
) -> DevelopmentEnvironment: ...

def sync_python_command(
    self,
    selector: EnvironmentSelector,
    *,
    upgrade: bool = False,
    hash_lock: Path | None = None,
    hash_lock_sha256: str | None = None,
) -> Command[DevelopmentEnvironment]: ...

def get(self, selector: EnvironmentSelector) -> DevelopmentEnvironment: ...

def list(
    self,
    *,
    project: ProjectConfig | Path | None = None,
    include_removed: bool = False,
) -> list[DevelopmentEnvironment]: ...

def remove(self, selector: EnvironmentSelector) -> None: ...
```

Selector SHALL be a UUID or exact name (`str | DevelopmentEnvironment`); ambiguity SHALL be an error. `history()` and `list(verify=)` SHALL NOT enter the public API. Events SHALL remain in the catalogue and `doctor` SHALL read them internally. Git, uv, `fcntl.flock`, hash/digest validation, and generated configuration SHALL remain internal implementation of `EnvironmentResource`, not a public module.

`sync_python()` SHALL delegate to `sync_python_command(...).run()` with the same arguments. Checkout convenience methods SHALL consume the command captured from their `EnvironmentCheckoutOptions`; no public convenience method or CLI adapter SHALL rebuild hash-lock argv, cwd, environment, digest evidence, or action order.

#### Scenario: Checkout returns DevelopmentEnvironment

- **WHEN** `client.environments.checkout(project, "feat/x")` succeeds
- **THEN** it SHALL return a `DevelopmentEnvironment` with `state=READY`

#### Scenario: Selector ambiguity is error

- **WHEN** `client.environments.get("feat")` matches two environments by name
- **THEN** it SHALL raise `EnvironmentConflictError` with details

#### Scenario: Sync convenience and command signatures agree

- **WHEN** callers pass the same selector, upgrade value, and hash-lock pair to `sync_python()` and `sync_python_command()`
- **THEN** both SHALL represent the same public operation and `sync_python()` SHALL execute the exact command snapshot returned by its command sibling

#### Scenario: Legacy public calls retain defaults

- **WHEN** an existing caller omits both new keyword arguments or constructs default checkout options
- **THEN** source compatibility and existing dependency behavior SHALL be preserved with both hash-lock values equal to `None`

### Requirement: Dependency compilation

Without explicit hash-lock inputs, Odoo Core and project requirements in both Python modes SHALL be compiled by one `uv pip compile` into the environment-owned `requirements.lock`.

- reused venv: `uv pip install --python <project-python> -r <lock>` SHALL preserve unrelated project tools;
- owned venv: `uv pip sync --python <environment-python> <lock>` SHALL provide isolation;
- uv writes SHALL be serialized by `flock` on the canonical Python-environment path;
- repository-local dependency files SHALL be rebased into the worktree, and the lock/fingerprint SHALL refer to that worktree;
- `env sync --upgrade` SHALL update pins, regular sync SHALL preserve them, and a failed compile SHALL NOT replace a valid lock;
- `run` and `shell` SHALL NOT call `sync_python`; only checkout and `env sync` SHALL write the lock/venv, while `doctor` and `deps verify` report drift.

When paired `hash_lock` and `hash_lock_sha256` inputs are supplied for an owned environment, checkout and `env sync` SHALL replace the discovery/compile branch with the hash-verified `uv pip sync --require-hashes` branch defined below. Hash-lock mode SHALL NOT rewrite the reviewed lock or fall back to compilation or installation.

Runtime prefix SHALL always be `[recorded-python, odoo-bin]`. `uv venv`, dependency argv construction, execution, and fingerprinting SHALL remain internal implementation of the existing environment resource rather than a new public venv module.

#### Scenario: Failed compile keeps valid lock

- **WHEN** `uv pip compile` fails in legacy mode and a valid `requirements.lock` already exists
- **THEN** the valid lock SHALL NOT be replaced and synchronization SHALL continue with that lock

#### Scenario: Hash-lock mode bypasses compilation by contract

- **WHEN** valid paired hash-lock inputs select an owned environment
- **THEN** dependency discovery and compile SHALL be absent from the immutable plan and the reviewed lock SHALL remain byte-identical

### Requirement: `env sync`

`env sync [ENVIRONMENT] [--upgrade] [--hash-lock PATH --hash-lock-sha256 SHA256]` MUST:

- preserve pins during regular synchronization and update them only with `--upgrade`;
- rebase repository-local dependency files into the worktree;
- serialize uv writes with `flock`;
- require `--hash-lock` and `--hash-lock-sha256` together, require a lowercase 64-hex digest and a regular readable file, and reject hash-lock mode with `--upgrade` or a reused/non-owned Python environment;
- in hash-lock mode, verify the exact file digest before mutation and capture `uv pip sync --python <owned-python> --require-hashes <canonical-lock>` through the existing immutable command/process boundary;
- in hash-lock mode, perform no Odoo/project requirement discovery, `uv pip compile`, or `uv pip install` fallback;
- expose the same paired inputs and semantics to `EnvironmentResource.sync_python()`, `sync_python_command()`, public `env sync`, and `env checkout --create-venv`, with the convenience methods delegating to the captured command rather than rebuilding it;
- preserve the existing compile/install behavior when both hash-lock inputs are absent.

#### Scenario: Sync upgrade

- **WHEN** `env sync --upgrade` runs without hash-lock inputs
- **THEN** pins SHALL be updated in `requirements.lock`

#### Scenario: Owned environment consumes an audited hash lock

- **WHEN** public checkout or `env sync` receives a regular lock file and its matching SHA-256 for an OdCLI-owned environment
- **THEN** the immutable plan SHALL contain exactly one hash-enforced dependency command using the owned interpreter and canonical lock path
- **THEN** execution SHALL use `uv pip sync --require-hashes` without discovery, compilation, or an install fallback

#### Scenario: Hash-lock validation fails before mutation

- **WHEN** either paired input is missing, the digest is malformed or mismatched, the lock is not a regular readable file, `--upgrade` is also supplied, or the environment is not owned
- **THEN** the operation SHALL fail before spawning uv or changing the environment lock, catalogue evidence, or installed packages

#### Scenario: Legacy synchronization remains compatible

- **WHEN** neither hash-lock input is supplied
- **THEN** checkout and `env sync` SHALL retain their existing discovery, compile, owned-sync, and reused-environment install behavior
