## MODIFIED Requirements

### Requirement: `env remove`

`EnvironmentManager.remove_command()` SHALL accept `force_connections` and route it into the existing `build_database_drop_command()` with terminate scope limited to the exact COPY database being removed. Without `force_connections`, removal SHALL stay fail-closed. The error message SHALL name `--force-connections` now that the flag exists; it MAY also name `odcli stop`. Protected/default/shared databases and sessions of other databases SHALL NOT be affected. The CLI `odcli env rm` SHALL expose `--force-connections` and follow the multi-target deletion contract.

#### Scenario: Dirty worktree blocks remove

- **WHEN** `env remove` для environment с dirty worktree
- **THEN** remove блокируется, error

#### Scenario: Occupied port blocks remove

- **WHEN** `env remove` и `socket.bind((http_interface, http_port))` fails (port occupied)
- **THEN** remove блокируется как ownership-unknown; HTTP response только diagnostic

#### Scenario: Shared source DB never dropped

- **WHEN** `env remove` для `shared` environment
- **THEN** source DB не удаляется ни при каких flags

#### Scenario: Drop postcondition checked

- **WHEN** `env remove` для `copy` environment, target DB drop succeeds
- **THEN** postcondition `exists(target_db) is False` verified; if fails → `cleanup_failed`

#### Scenario: Drop refused on cluster identity mismatch

- **WHEN** `env remove` для `copy` environment, но target DB cluster identity не совпадает с recorded (e.g. DB moved to different cluster) OR no recorded restore/backup ownership
- **THEN** drop refused, `cleanup_failed` с причиной; target DB не удаляется

#### Scenario: Idempotent missing artifact

- **WHEN** `env remove` и owned artifact уже отсутствует
- **THEN** считается идемпотентным успехом, записывается в audit

#### Scenario: Partial failure → cleanup_failed

- **WHEN** `env remove` частично fails (e.g. worktree remove error)
- **THEN** state `cleanup_failed`, повторный `remove` продолжает с оставшихся artifacts

#### Scenario: Multiple UUIDs resolved independently

- **WHEN** `env remove UUID1 UUID2` runs with UUIDs from different projects
- **THEN** each target's repository and cluster context is resolved separately

#### Scenario: No arguments preserves cwd semantics

- **WHEN** `env remove` runs from inside an exact registered worktree
- **THEN** it resolves exactly that environment

#### Scenario: Planning preflight aborts before mutation

- **WHEN** one target in a multi-target call is unknown
- **THEN** the command aborts with no changes and a sanitized error

#### Scenario: remove with force-connections terminates only the COPY database sessions

- **WHEN** `remove_command(force_connections=True)` runs for a COPY environment
- **THEN** only sessions of the exact COPY database are terminated and other databases' sessions are untouched

#### Scenario: remove without force-connections names the existing flag

- **WHEN** `remove_command()` runs against a COPY database with an active session
- **THEN** removal is blocked and the error names `--force-connections`, not a missing option

### Requirement: Generated `odoo.conf`

For a new isolated environment, the generated `odoo.conf` SHALL include an environment-owned `logfile = <environment-root>/odoo.log` and the file SHALL be created together with the other environment artifacts. For an old or partial isolated config without `logfile`, the same fallback SHALL apply: `odoo.log` next to the effective config is chosen and created before detached spawn. A full self-contained `init` SHALL set `data_dir` to the absolute `{project_root}/.odcli/filestore` in the generated `odoo.conf`.

#### Scenario: Atomic 0600 config

- **WHEN** generated config записывается
- **THEN** atomic write (`os.replace`), права `0600`, исходный config не изменяется

#### Scenario: Source logfile rewritten to env-owned path

- **WHEN** source `odoo.conf` contains `logfile = /tmp/shared.log`
- **THEN** generated config has an absolute `logfile` under the environment root and the source file is unchanged; the log file itself is not created

#### Scenario: Absent logfile preserved

- **WHEN** source `odoo.conf` has no `logfile`
- **THEN** generated config also has no `logfile`

#### Scenario: Repo-local addons rebased

- **WHEN** `addons_path` содержит repo-local entry `./addons`
- **THEN** generated config содержит rebased path внутри worktree

#### Scenario: External Odoo core preserved

- **WHEN** `addons_path` содержит external `/opt/odoo/addons`
- **THEN** generated config сохраняет `/opt/odoo/addons` без изменений

#### Scenario: new environment gets an owned logfile

- **WHEN** a new isolated environment is created
- **THEN** its generated `odoo.conf` contains `logfile = <environment-root>/odoo.log` and the file is created with the other artifacts

#### Scenario: old environment without logfile uses fallback

- **WHEN** an old isolated environment without `logfile` runs detached
- **THEN** the same fallback chooses `odoo.log` next to its effective config and creates it before spawn

#### Scenario: self-contained init records data_dir

- **WHEN** a full self-contained `init` runs
- **THEN** the generated `odoo.conf` contains `data_dir` set to the absolute `{project_root}/.odcli/filestore`

