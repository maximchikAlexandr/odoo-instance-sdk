## ADDED Requirements

### Requirement: Public typed pgAdmin operation

The public environment resource MUST expose an operation equivalent to `EnvironmentResource.open_pgadmin(selector: EnvironmentSelector) -> PgAdminOpenResult`. The operation MUST return a frozen, unknown-field-forbidden typed result and MUST be the single application path used by FastAPI; routes MUST NOT repeat environment resolution, catalog lookup, Compose ownership checks, database verification, credential loading, or Docker lifecycle orchestration.

`PgAdminOpenState` MUST be a string enum with exact values `started`, `reused`, and `reconfigured`. `PgAdminOpenResult` MUST contain exact fields `state: PgAdminOpenState` and `url: str`; `url` MUST be a loopback HTTP URL and MUST NOT contain credentials, an undocumented object-browser deep link, or environment-specific secrets.

#### Scenario: Public operation returns typed result

- **WHEN** an eligible environment is opened while no pgAdmin container exists
- **THEN** `EnvironmentResource.open_pgadmin(...)` returns `PgAdminOpenResult(state="started", url=<loopback-root-url>)`

#### Scenario: Route delegates to public resource

- **WHEN** `POST /api/v1/pgadmin/open` is called
- **THEN** the route passes only the requested environment ID to the public environment operation and maps its typed result/error without performing orchestration itself

### Requirement: Stable pgAdmin HTTP operation

`POST /api/v1/pgadmin/open` MUST publish operation ID `openPgAdmin`. Its named `PgAdminOpenRequest` body MUST contain exactly `environment_id: str`; raw strict msgspec decoding MUST reject malformed JSON, a missing or non-string `environment_id`, and every unknown field. Before decoding or delegation, the route MUST require `Content-Type: application/json`, an explicit exact same-origin `Origin`, an absent-or-`same-origin` Fetch Metadata value, and a per-session double-submit CSRF token. Every request-boundary or decode/validation failure MUST return status `422` with the named `HttpError` payload exactly `{"code":"invalid_request","message":"invalid request"}` and MUST NOT call `EnvironmentResource.open_pgadmin` or an injected opener. Status `200` MUST use named `PgAdminOpenResult`; other known errors MUST use named `HttpError` with mappings `404 environment_not_found`, `409 pgadmin_not_eligible`, `409 database_not_found`, and `503 pgadmin_unavailable`. `HttpErrorCode` MUST contain the corresponding exact string values, including `invalid_request`.

The operation MUST resolve the environment, project, cluster, and database from the current catalog/snapshot and generated Odoo configuration. It MUST support only lifecycle-ready environments with a resolved database on an SDK-owned healthy Compose PostgreSQL cluster.

#### Scenario: Browser cannot choose a database endpoint

- **WHEN** a request includes `host`, `port`, `database`, or credential fields in addition to `environment_id`
- **THEN** the response is `422` with `HttpError(code="invalid_request", message="invalid request")` and the public pgAdmin operation is not called

#### Scenario: Invalid request shapes share one sanitized contract

- **WHEN** the raw body is malformed JSON, omits `environment_id`, or supplies a non-string `environment_id`
- **THEN** each response is `422` with the same named `HttpError(code="invalid_request", message="invalid request")`, contains no decoder detail or submitted value, and the opener is not called

#### Scenario: Ineligible environment is rejected

- **WHEN** the selected environment is non-ready, its database is unresolved, or its project cluster is external, unowned, missing, stopped, or unhealthy
- **THEN** the operation returns `409 pgadmin_not_eligible` without creating pgAdmin files or changing Docker state

### Requirement: Database existence is verified before launch

After eligibility resolution and before any pgAdmin lifecycle mutation, the public operation MUST verify the selected database through the existing configured instance/database resource path. The check MUST use backend-resolved generated Odoo configuration and MUST NOT trust browser values or silently create, restore, install, update, or rename a database.

#### Scenario: Selected database is missing

- **WHEN** the catalog environment resolves a database name but the existing database resource confirms it does not exist
- **THEN** the operation returns `database_not_found` and does not start or reconfigure pgAdmin

#### Scenario: Database check is inconclusive

- **WHEN** database existence cannot be safely confirmed
- **THEN** the operation returns sanitized `pgadmin_unavailable` and does not treat the database as present

### Requirement: Single secure user-level pgAdmin lifecycle

The SDK MUST manage at most one pgAdmin container per OS user across all projects. The container MUST be created lazily by the first successful open operation, use the official `dpage/pgadmin4` image pinned by immutable digest, publish only to `127.0.0.1` on a deterministic-or-allocated user-level port, and MUST NOT mount the Docker socket. One user-global file lock MUST cover resolve/configure/create/start/reuse/reconfigure so concurrent clicks cannot create duplicate containers.

Every create/recreate MUST configure supported local single-user/no-login operation with exact non-secret settings `PGADMIN_CONFIG_SERVER_MODE=False`, `PGADMIN_REPLACE_SERVERS_ON_STARTUP=True`, and deterministic `PGADMIN_DEFAULT_EMAIL=odoo-instance-sdk@localhost.invalid`. It MUST set exact file references `PGADMIN_DEFAULT_PASSWORD_FILE=/run/odoo-instance-sdk/pgadmin-admin-password` and `PGPASS_FILE=/run/odoo-instance-sdk/pgpass`. The password and passfile MUST be read-only mounts at those paths, declarative `servers.json` MUST be read-only at `/pgadmin4/servers.json`, and persistent data MUST be read-write at `/var/lib/pgadmin`. After every successful create/recreate, the SDK MUST use a secret-free, ID-scoped command after ownership/fingerprint re-inspection to copy the selected mounted passfile to `/var/lib/pgadmin/.pgpass` as UID 5050 with mode `0600`. Neither API/UI nor the returned root URL MUST carry credentials; after readiness the root URL MUST be usable without a login form.

The SDK MUST connect the single container only to the selected SDK-owned Compose network(s) needed for declarative PostgreSQL access. The selected server definition MUST use backend-resolved container/network identity, `MaintenanceDB=<selected database>`, and `DBRestriction=<selected database>`; it MUST return only the pgAdmin root URL and MUST NOT depend on undocumented deep links. The reconciliation fingerprint MUST be a non-secret HMAC-SHA256 using a private per-user random key stored owner-only in the protected pgAdmin directory, over the public backend identity and selected PostgreSQL credential. Public identity and a candidate password without that key MUST NOT derive the label. A same-backend password rotation MUST therefore change the fingerprint, recreate the container, and refresh the active passfile instead of returning `reused`.

Matching-container reuse MUST accept inherited image environment variables in addition to the SDK configuration. Every SDK-required variable MUST be present with its exact required value; repeated SDK keys are valid only when every repeated value agrees, and a missing key or any conflicting duplicate MUST force reconfiguration rather than reuse.

#### Scenario: Concurrent first clicks create one container

- **WHEN** two eligible open operations race while pgAdmin is absent
- **THEN** the global lock produces one named user-level container and both calls return a valid loopback URL

#### Scenario: Fresh container root is usable without credentials

- **WHEN** the first eligible open creates pgAdmin
- **THEN** its startup metadata contains the exact server-mode, replacement, email, password-file, and passfile settings, the selected server is imported, the active `/var/lib/pgadmin/.pgpass` has mode `0600` and is refreshed from the selected mount, the returned root URL opens without a login form, and no secret literal appears in API/UI, argv, environment metadata, labels, or logs

#### Scenario: Existing matching container is reused

- **WHEN** an open operation targets the same effective server configuration as the running SDK-owned pgAdmin container
- **THEN** required startup settings, mounts, fingerprint, network, and selected server are verified, no duplicate/recreate occurs, and the result state is `reused`

#### Scenario: Inherited environment does not prevent reuse

- **WHEN** an inspect-shaped matching container includes extra image-inherited environment entries and exactly matching SDK entries
- **THEN** the container is reused, but a missing SDK entry or conflicting duplicate SDK value is rejected as non-matching

#### Scenario: Another project reconfigures the shared container

- **WHEN** an eligible database from another SDK-owned Compose project is opened
- **THEN** `servers.json` is atomically replaced, the same user-level container is recreated with `PGADMIN_REPLACE_SERVERS_ON_STARTUP=True`, the active passfile is refreshed with mode `0600`, a passwordless query from inside pgAdmin succeeds against the newly selected database, the new selected database is effective despite persistent `/var/lib/pgadmin`, and only then the result state is `reconfigured`

#### Scenario: Same backend password rotation recreates

- **WHEN** the backend host, port, user, network, and database remain unchanged but the selected PostgreSQL password changes
- **THEN** the non-secret HMAC fingerprint changes, the existing container is recreated rather than reused, the active passfile is refreshed, passwordless pgAdmin authentication succeeds with the new credential, and the old credential no longer authenticates

#### Scenario: Readiness retries only transient startup failures

- **WHEN** the loopback root refuses a connection transiently before becoming valid
- **THEN** readiness retries at a bounded interval until success or the monotonic deadline; repeated refusal returns unavailable at the deadline, while a login page, unsafe redirect, or invalid/security response fails without retry

### Requirement: Credentials stay in private generated files

The SDK MUST generate the pgAdmin administrator password and PostgreSQL passfile under a user-private data directory and MUST generate a per-user random HMAC key there once, reusing it for the lifetime of that user directory. On Linux the exact no-sudo strategy MUST retain invoking-user ownership and use these POSIX ACLs: traversal parents mode `0710` with `user::rwx,user:5050:--x,group::---,mask::--x,other::---`; secret/configuration files mode `0640` with `user::rw-,user:5050:r--,group::---,mask::r--,other::---`; the HMAC key MUST be owner-only mode `0600` and never mounted into the container; persistent data directory mode `0770` with `user::rwx,user:5050:rwx,group::---,mask::rwx,other::---` and the corresponding default ACL entries. The group-class mode bits are the required ACL mask; the owning group and other receive no effective access. The data bind mount MUST be read-write while secret/configuration mounts are read-only. The implementation MUST NOT chown host paths to root/5050 or add another named user/group ACL.

Before Docker mutation the SDK MUST verify numeric runtime UID `5050`, invoking-user ownership, exact mode bits, containment/type, effective ACL and ACL mask, absence of extra named user/group grants, required default data-directory ACL, and every mount destination/access flag. Missing POSIX ACL support, ineffective or widened ACLs, unsafe ownership/type/mode, symlink, containment failure, or mismatched mount behavior MUST fail closed as sanitized `pgadmin_unavailable`.

Secrets MUST be delivered through the exact image-supported file mounts/references, never literal API values, Docker argv values, environment metadata, labels, container names, server JSON, logs, exceptions, or OpenAPI. The deterministic admin email, boolean configuration, and file paths are non-secret; the declarative server JSON and lifecycle metadata MUST contain no password by structural construction of the canonical server/metadata builders, not by rejecting arbitrary password substrings.

Generated configuration MUST be written atomically. The bootstrap administrator password file MUST be reused rather than overwritten; the target-specific escaped PostgreSQL passfile MUST be atomically replaced when the selected project/database credential changes. Every reused or replaced credential file MUST revalidate ownership, type, modes, ACLs, containment, and mount contract before Docker mutation. This distinction MUST NOT be interpreted as permitting stale target credentials to survive cross-project reconfiguration.

#### Scenario: Docker invocation is secret-free

- **WHEN** the SDK starts or reconfigures pgAdmin
- **THEN** captured Docker argv, environment metadata, server JSON, labels, and logs contain no administrator or PostgreSQL password, while containing only the required non-secret configuration values and file paths

#### Scenario: Unsafe credential path fails closed

- **WHEN** a required pgAdmin path is a symlink, non-regular file, outside the SDK data root, has unsafe ownership/modes/ACLs, lacks effective UID 5050 access, or has a mismatched read-only/read-write mount
- **THEN** the operation returns a sanitized failure without starting or replacing the container

#### Scenario: Linux UID 5050 writes persistent data without sudo

- **WHEN** validated host-private paths are mounted into the official container on Linux
- **THEN** UID 5050 can traverse/read only the password, passfile, and server configuration mounts and can create persistent data under `/var/lib/pgadmin`, while `group::---` and `other::---` remain effective despite ACL-mask mode bits and the invoking user never runs sudo or chown
