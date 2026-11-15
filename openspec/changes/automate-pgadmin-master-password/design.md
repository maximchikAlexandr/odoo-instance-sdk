# Design

## Context

See proposal.md for why. The pinned image runs desktop mode (`SERVER_MODE=False`) as UID 5050. pgAdmin 9.15 documents `MASTER_PASSWORD_HOOK` for server mode with OAuth, Kerberos, or webserver authentication only. Desktop mode uses the OS secret store, which this container does not have, and otherwise asks the browser to set a master password. The current open path already imports `servers.json`, mounts a passfile, and checks the imported server row. It does not submit a master password or run `SELECT 1`.

## Goals / Non-Goals

**Goals:**

- One private master-password file and one supported loopback unlock on every successful open.
- Fail closed for unsafe files and for an existing pgAdmin profile OdCLI cannot unlock.
- Prove the selected database with `SELECT 1` through the passfile already refreshed into `/var/lib/pgadmin/.pgpass`.
- Put the unlock and the query in the same immutable command snapshot as the existing Docker phase.

**Non-Goals:**

- Disabling `MASTER_PASSWORD_REQUIRED`.
- Editing or resetting pgAdmin SQLite, including `DELETE /browser/master_password`.
- A pgAdmin query-tool session or an undocumented object-browser deep link.
- Mounting `master.env` or placing the password in Docker configuration.

## Decisions

1. **File location and shape.** `~/.odcli/pgadmin/master.env` sits beside `private/` and `data/` so the existing mount contract cannot include it. The only line is `PGADMIN_MASTER_PASSWORD=...`, written atomically at mode `0600` with no POSIX ACL for UID 5050. Validation rejects symlinks, other owners, any mode other than `0600`, extra keys, and an empty value before Docker mutation when the file already exists.

2. **Container setting.** Add non-secret `PGADMIN_CONFIG_USE_OS_SECRET_STORAGE=False` to the required environment set used by `docker run` and `container_matches`. Leave `MASTER_PASSWORD_REQUIRED` at the image default of true and do not set `MASTER_PASSWORD_HOOK`. An older container missing the new variable fails the match and is recreated, which is the existing reconfigure path. Alternative rejected: keep the keyring attempt and depend on pgAdmin falling through after `NoKeyringError`. That is an exception path, not the configured desktop contract.

3. **Unlock transport.** After readiness, an in-process `ActionStep` `pgadmin.master_password.unlock` posts JSON `{"password": "...", "submit_password": true}` to the loopback master-password route with the session cookie from the root page. The password stays on the private reconciliation carrier and in the request body. The step accepts success only when the response says the key is present and `is_error` is false. A read-only SQLite check of whether `user.masterpass_check` is set decides generation versus reuse: null check and no file generates; set check and no file, or a rejected password, raises `PgAdminUnavailableError` and does not reset. Alternative rejected: `MASTER_PASSWORD_HOOK`, because the pinned documentation limits it to server-mode external authentication.

4. **Authentication proof.** Add captured step `pgadmin.container.authenticate` after unlock. It is `docker exec` of the image Python, connecting with `psycopg` and `passfile=/var/lib/pgadmin/.pgpass`, executing `SELECT 1`, and exiting non-zero unless the value is `1`. Host, port, user, and database come from the already non-secret server JSON. The PostgreSQL password is not an argument. Both the reuse and create/reconfigure branches run unlock and this query before returning `PgAdminOpenResult`.

5. **Dashboard.** Environment cards render Open pgAdmin from `pgadmin.state`. Only `eligible` enables it. The click calls generated `openPgAdmin` and opens `result.url` with `noopener,noreferrer`. Project cards do not show the action. Failures render a fixed sanitized message.

## Risks / Trade-offs

- [pgAdmin CSRF or response envelope differs on the pinned digest] → The unlock parser accepts the documented JSON envelope and fails closed on any other response. The opt-in pinned-image smoke covers the real route.
- [An operator already set a master password in the UI] → Open fails until they remove the pgAdmin data directory themselves. OdCLI will not reset it.
- [Read-only SQLite sees a schema without `masterpass_check`] → Open fails closed instead of guessing.
- [Query tool is not automated] → The user still opens the imported server in the browser. `SELECT 1` proves the credential, which is the acceptance bar in #147.

## Migration Plan

Existing containers lack `PGADMIN_CONFIG_USE_OS_SECRET_STORAGE=False`, so the next open recreates the shared container and keeps `/var/lib/pgadmin`. Rollback is reverting the change; a generated `master.env` can remain because it is outside the repository and is not read by older code.
