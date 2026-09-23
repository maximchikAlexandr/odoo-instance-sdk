## MODIFIED Requirements

### Requirement: `env remove`

`EnvironmentManager.remove_command()` SHALL accept `force_connections` and route it into the existing `build_database_drop_command()` with terminate scope limited to the exact COPY database being removed. Without `force_connections`, removal SHALL stay fail-closed. The error message SHALL name `--force-connections` now that the flag exists; it MAY also name `odcli stop`. Protected/default/shared databases and sessions of other databases SHALL NOT be affected. The CLI `odcli env rm` SHALL expose `--force-connections` and follow the multi-target deletion contract.

#### Scenario: remove with force-connections terminates only the COPY database sessions

- **WHEN** `remove_command(force_connections=True)` runs for a COPY environment
- **THEN** only sessions of the exact COPY database are terminated and other databases' sessions are untouched

#### Scenario: remove without force-connections names the existing flag

- **WHEN** `remove_command()` runs against a COPY database with an active session
- **THEN** removal is blocked and the error names `--force-connections`, not a missing option

### Requirement: Generated `odoo.conf`

For a new isolated environment, the generated `odoo.conf` SHALL include an environment-owned `logfile = <environment-root>/odoo.log` and the file SHALL be created together with the other environment artifacts. For an old or partial isolated config without `logfile`, the same fallback SHALL apply: `odoo.log` next to the effective config is chosen and created before detached spawn. A full self-contained `init` SHALL set `data_dir` to the absolute `{project_root}/.odcli/filestore` in the generated `odoo.conf`.

#### Scenario: new environment gets an owned logfile

- **WHEN** a new isolated environment is created
- **THEN** its generated `odoo.conf` contains `logfile = <environment-root>/odoo.log` and the file is created with the other artifacts

#### Scenario: old environment without logfile uses fallback

- **WHEN** an old isolated environment without `logfile` runs detached
- **THEN** the same fallback chooses `odoo.log` next to its effective config and creates it before spawn

#### Scenario: self-contained init records data_dir

- **WHEN** a full self-contained `init` runs
- **THEN** the generated `odoo.conf` contains `data_dir` set to the absolute `{project_root}/.odcli/filestore`