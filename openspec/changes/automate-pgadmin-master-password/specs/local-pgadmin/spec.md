# Spec Delta

## ADDED Requirements

### Requirement: pgAdmin master password stays in a private env file

The open operation MUST persist the pgAdmin desktop master password at `~/.odcli/pgadmin/master.env`. The file MUST be a regular file owned by the invoking user with mode `0600`, MUST contain only the assignment `PGADMIN_MASTER_PASSWORD=<non-empty value>`, and MUST remain outside every repository and outside the pgAdmin container mounts. OdCLI MUST generate the value when the pgAdmin profile has no master-password check. It MUST reuse an already valid file across opens and container recreates. The value MUST be distinct from the pgAdmin administrator password and the PostgreSQL password, and MUST NOT appear in Docker environment metadata, argv, labels, logs, exceptions, public plans, API bodies, or the returned URL.

#### Scenario: First open creates the private file

- **WHEN** an eligible open finds no `master.env` and the pgAdmin profile has no master-password check
- **THEN** OdCLI generates the password, writes `~/.odcli/pgadmin/master.env` as mode `0600`, and does not mount that path

#### Scenario: Later opens reuse the stored password

- **WHEN** `master.env` already meets the ownership and mode contract
- **THEN** OdCLI reuses its value after a repeated open and after a container recreate, without asking the user to invent a password

### Requirement: Desktop master password is unlocked through pgAdmin

After the container is ready, the open operation MUST submit the stored master password only in the body of `POST http://127.0.0.1:<allocated-port>/browser/master_password`. `MASTER_PASSWORD_REQUIRED` MUST stay enabled. `MASTER_PASSWORD_HOOK` MUST NOT be configured. The operation MUST NOT edit pgAdmin's SQLite database and MUST NOT call pgAdmin's master-password reset. Success MUST require pgAdmin to report the master password present in process memory. Create and recreate MUST also set the non-secret `PGADMIN_CONFIG_USE_OS_SECRET_STORAGE=False`; a running container that lacks that exact setting MUST be reconfigured rather than reused. The returned URL MUST remain the credential-free loopback root.

#### Scenario: Normal open does not leave the set-master-password prompt

- **WHEN** an eligible open completes against the pinned pgAdmin image
- **THEN** the master-password check reports the key present in memory and the open result contains no password

#### Scenario: Existing profile is kept

- **WHEN** pgAdmin already has a master-password check and the stored password is missing or rejected
- **THEN** the operation fails as sanitized `pgadmin_unavailable`, leaves the pgAdmin data directory in place, and does not reset saved passwords

### Requirement: Unsafe master password storage fails closed

A symlink, non-regular file, wrong owner, mode other than `0600`, path outside the pgAdmin root, empty password, extra assignment, or unreadable `master.env` MUST fail the open without claiming authorization. A corrupt file MUST NOT be replaced when a master-password check already exists.

#### Scenario: Insecure file blocks the open

- **WHEN** `master.env` is mode `0644` or a symlink
- **THEN** the open fails sanitized and OdCLI does not recreate the container to bypass the file

### Requirement: Selected database access is proven with SELECT 1

After master-password unlock, the open operation MUST execute read-only `SELECT 1` against the selected database through the active pgAdmin passfile. The open MUST fail when the query does not return `1`. HTTP 200 and an imported server label MUST NOT count as authentication.

#### Scenario: Passfile authentication succeeds

- **WHEN** the selected database accepts the active passfile
- **THEN** `SELECT 1` returns `1` and only then the open result is returned

#### Scenario: Database authentication is denied

- **WHEN** the selected database rejects the passfile
- **THEN** the operation returns sanitized `pgadmin_unavailable` and does not report a successful open

## REMOVED Requirements

### Requirement: Dashboard access remains deferred

**Reason**: GitHub #147 now requires the dashboard to open pgAdmin after automatic master-password provisioning.
**Migration**: Environment cards show the typed Open pgAdmin action. The generated API and backend operation stay the same.
