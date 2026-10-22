## Context

The approved base is `origin/main` at `23dd4ea390393bb0f902f2e057fc2ca56483dd94`. The existing monitor is an optional FastAPI/React application with loopback Host checks, typed msgspec/OpenAPI contracts, double-submit CSRF for mutations, and a single canonical `Snapshot`. The durable SQLAlchemy/Alembic catalogue already records `backups.downloaded_at`, backup events, completed/incomplete restore provenance, environment state/errors, and append-only environment events. Current remote Odoo passwords come from named `ODCLI_REMOTE_<NAME>_MASTER_PASSWORD` or legacy `ODCLI_TEST_MASTER_PASSWORD`; GitLab member credentials come from the existing member-to-token-key mapping and environment-file chain.

MYL-515 (`fix-named-remote-and-init-contracts`) makes a named remote database optional and keeps database discovery solely in refresh. This change is compatible by keying an Odoo secret only by normalized remote URL and by leaving database selection to that resolver. The planning package owns no MYL-515 model/discovery changes.

Repository rules require lazy optional dashboard imports, SDK-first boundaries, canonical typed responses, no direct unsafe process execution, and no secret projection. The attached v0.7 HTML is input, not authority.

### Ponytail full gate

Only one genuinely new subsystem is retained: the encrypted secret store, because neither the operational catalogue nor environment files can satisfy encrypted editable records with independent key loss semantics. The fresh audit found that the repository already supplies most surrounding behavior, so the implementation SHALL extend those seams directly rather than add parallel layers:

- Admin host/auth: mount Starlette Admin inside the current FastAPI application; no second server, user table, custom login form, or authorization framework.
- Administrator settings: reuse `internal.paths`, owner-only `O_NOFOLLOW` validation, and atomic file-write patterns; add only the two new path/config values, with no settings database or generic filesystem wrapper.
- Secret mutations: expose the minimum create/update/delete methods on the SQLCipher store adapter and call them from Admin; no SDK `Command` hierarchy, repository/service facade, or duplicated DTO mapping.
- Catalogue screens: extend the existing `BackupProjection`/keyset page and `BackupCatalog` environment/event read seams with one shared allowlisted projection per screen; no generic query layer, reporting database, ORM mirror, import/export layer, or write service.
- Error safety: reuse the existing sanitizer and define allowlisted projections; no generic exception renderer.
- Backup age: extend existing restore/database-event lookup and canonical monitor planning to join `backups.downloaded_at`; no timestamp cache, second endpoint, or server-created-time discovery.
- Remote Odoo resolution: insert one URL-keyed secret lookup before the existing environment chain; no second source resolver or database field in the secret record.
- GitLab resolution: insert one member-id/normalized-host lookup before the existing mapping/environment chain; retain the generated internal token-key mechanism.
- Concurrency: use a per-record revision compare-and-swap inside the same SQLCipher transaction methods; no lock service, unit-of-work abstraction, or event-sourcing framework.
- Verification: extend existing dashboard/package tests, real-Odoo coverage, `e2e_gitlab_member_workflow.py`, and the test-repository numbered-slot wrapper; no new umbrella coordinator or second validator/evidence framework.

## Goals / Non-Goals

**Goals:**

- Provide one authenticated local administrator with safe secret create/update/delete operations.
- Preserve exact legacy fallback order while making encrypted records authoritative when present.
- Expose the three requested catalogue views as paginated, filterable, read-only data.
- Show only proven source-backup download time/age on the relevant local database card.
- Keep secrets, raw paths, configuration JSON, and raw exception text out of UI, APIs, logs, repr, OpenAPI examples, and generated evidence.

**Non-Goals:**

- Multiple administrator identities, roles, account recovery, remote identity providers, or a user table.
- Storing a remote database name with an Odoo secret or changing MYL-515 discovery semantics.
- Migrating legacy `.env` secrets into SQLCipher automatically or deleting legacy values.
- Catalogue mutation, restore commands, import/export, raw SQL, or arbitrary sort/filter expressions in Admin.
- Deriving an upstream backup creation timestamp or inferring provenance from a database name alone.
- Exposing the Admin in headless mode or bypassing the existing trusted-proxy/Host boundary.

## Decisions

### 1. One mounted Admin application shares the monitor boundary

Non-headless monitor composition mounts Starlette Admin at `/admin`, backed by its `AuthProvider` and `SessionMiddleware`. The existing loopback/trusted-proxy Host boundary remains outermost. Admin authentication covers every Admin page and endpoint; logout invalidates the signed session. The monitor SPA adds only a link. Headless composition registers neither Admin nor its mutations.

The administrator config contains exactly an accepted password hash and a stable random session-signing key in an owner-only file below the user config root. Startup fails closed on missing, blank, malformed, symlinked, non-regular, or over-permissive configuration and never creates a default password.

Alternative: reuse Caddy Basic Auth. Rejected because local loopback mode has no Caddy and the requirement is an application login. Alternative: custom form/user table. Rejected because the package provider already supplies the required single-user flow.

### 2. One encrypted store, separate random key file, two record types

One small SQLCipher adapter below the user data root stores schema metadata plus `remote_odoo_secret` and `gitlab_member_secret` records. It uses the selected DBAPI directly and reuses repository owner-only/atomic-file patterns rather than introducing a general persistence framework. A distinct random key file below the user config root is created atomically with mode `0600`; the database and parent private directories are owner-only. `ODCLI_SECRET_KEY_FILE` may override only the key-file path. Raw key material is never accepted in environment variables.

Odoo identity is canonical normalized base URL and stores one nonblank master password; it never stores a database. GitLab identity is normalized opaque member ID plus normalized lowercase HTTPS host and stores login, token, and an automatically generated stable internal token key. Records include an integer revision and timestamps. Uniqueness is enforced on each identity.

If neither database nor key exists, the secret store initializes on the first Admin write. Read-only consumers treat that as no encrypted records and continue to legacy lookup. Every partial/mismatched state—database without key, key without a valid database after initialization, wrong key, corrupt schema, or unreadable matching record—fails closed with a bounded typed error. No destructive recreation or fallback occurs.

Alternative: put encrypted rows in `catalog.sqlite3`. Rejected because the catalogue explicitly forbids a second role and must remain usable without secret key material. Alternative: OS keychain. Rejected because agents/monitor need the same filesystem-scoped user-root contract across supported platforms.

### 3. Secret fields are write-only and updates are compare-and-swap

Admin list/detail/form projections never return password, token, ciphertext, key identifiers, or length-derived placeholders. Starlette Admin calls the store adapter's direct methods; no separate Query/Command/service layer is added. Create requires a nonblank secret. Edit submits the record revision; blank secret inputs retain the existing secret, while nonblank values replace it. Delete also checks the revision. A stale update/delete returns a bounded conflict and preserves the newer record. Separate identities commit independently through short transactions and SQLite busy handling.

The draft's “two administrators” wording is interpreted as two concurrent editor sessions/processes authenticated with the one configured administrator identity; it does not add a second account.

Alternative: process-wide lock only. Rejected because it cannot prevent stale writes across monitor/agent processes.

### 4. Encrypted record presence controls fallback

Remote Odoo operations normalize the configured source URL, query SQLCipher, and use its nonblank master password when a record exists. GitLab operations query by normalized member ID and host and use the stored login/token/token-key mapping when present. Only an exact “record absent” result delegates to the current environment and `.env` chain in its unchanged order.

Wrong key, corrupt/unreadable store, invalid record, blank decrypted value, duplicate identity, or lookup I/O error is an operation error. These conditions never fall through. Secret-bearing objects retain redacted `repr`; errors and logs identify only the secret kind and safe normalized identity where allowed.

Alternative: merge field-by-field with `.env`. Rejected because it makes provenance ambiguous and can combine mismatched credentials.

### 5. Catalogue Admin extends existing bounded projections

`BackupCatalog` already provides `BackupProjection`, `BackupProjectionPage`, keyset pagination, environment rows, event rows, restore provenance, and typed public/private SDK boundaries. The change extends those exact read seams with stable `(timestamp, sequence/id)` ordering, bounded page size, and the minimum allowlisted fields required by the three screens. Filters are typed exact/date-range parameters, never SQL fragments. The shared projections are:

- backups with download time, safe source label, database, format, byte size, lifecycle status, project identity/display hint, and a detail page of allowlisted backup events;
- incomplete operations/errors from nonterminal backup/restore/environment/recovery state with object kind/identity, state, time, and `sanitize_last_error` output;
- environment history with project/environment display identity, timestamp, operation, and bounded result/state.

Raw filesystem paths, repository roots, `runtime_json`, `applied_settings_json`, configuration payloads, command output, and raw messages/errors are never selected into the shared safe projections. Admin consumes those projections directly and exposes no create/edit/delete action; it SHALL NOT introduce a second DTO/conversion layer.

Alternative: bind Starlette Admin directly to catalogue SQLAlchemy tables. Rejected because generic model introspection would expose unsafe columns and write actions.

### 6. Download time follows current completed database provenance

The canonical monitor planning pass resolves each displayed project/environment database against the latest completed restore/database event for the exact active cluster identity, host, port, and database. Only `source_kind=catalogue` with a surviving backup row and nonnull `downloaded_at` produces `BackupFreshness(downloaded_at, age_seconds)`. Local archives, bootstrap events, incomplete restores, dropped/replaced databases, cluster mismatch, missing backup rows, or null timestamps produce an explicit unknown freshness state.

Age is computed from the snapshot's UTC `generated_at`, clamped at zero for clock skew, and formatted client-side from the typed duration while retaining the exact timestamp. It is separate from runtime readiness. The snapshot route and generated TypeScript remain the single source; no second browser request is introduced.

Alternative: use environment `backup_id` alone. Rejected because it can be stale after database replacement and does not prove current database provenance.

### 7. Optional dependency and generated-contract boundaries remain intact

Starlette Admin, the password-hash verifier, and the selected SQLCipher DBAPI are dashboard/secret-management optional dependencies; core SDK and CLI help remain importable without them. Secret resolution imports the lightweight store adapter only when a configured store can exist and returns typed unavailable errors when required support is missing. OpenAPI schemas and generated TypeScript cover only safe DTOs; no secret input/output schema contains stored secret values.

Alternative: make all dependencies core. Rejected because it breaks the established lightweight import/package contract.

## Risks / Trade-offs

- [SQLCipher wheels/builds vary across Linux, macOS, and Windows] → pin one supported DBAPI in the optional extra, add isolated-package/import tests and compatibility checks, and fail with an actionable dependency error rather than opening the file as plain SQLite.
- [A generic Admin integration may accidentally expose model fields or actions] → use custom views backed directly by the shared allowlisted catalogue projections, deny mutation for catalogue resources, and contract-test routes/actions and response keys.
- [Secret lookup changes high-value authentication paths] → test exact hit/absent/error matrices at remote Odoo and GitLab public boundaries and retain the existing fallback function unchanged behind one adapter.
- [Catalogue rows can change between pages] → use deterministic ordering and short read transactions; pagination is a live view, not a snapshot/export guarantee.
- [Restore provenance may be stale or ambiguous] → require exact current cluster/database identity and a completed catalogue restore; uncertainty is rendered as unknown.
- [Session key/password-hash loss blocks Admin access] → fail closed with recovery guidance; never regenerate implicitly because that would invalidate sessions or authorize a default account.
- [Concurrent editor conflicts reduce convenience] → return a clear reload-and-retry conflict while preserving the newer value.

## Migration Plan

No operational catalogue migration is required. Installation with the updated dashboard extra adds the Admin and encrypted-store dependencies. Operators create the owner-only administrator config explicitly; the SQLCipher key/database are created atomically on first secret write. Existing environment/`.env` credentials remain authoritative until a matching encrypted record is created. Rollback leaves the SQLCipher files untouched and legacy credentials continue to work; operators must not delete legacy values until the new version is accepted. Snapshot/OpenAPI additions are additive and generated clients are refreshed in the same change.

## Open Questions

None. The single-identity concurrent-session interpretation, fail-closed partial-store states, current-database provenance rule, and MYL-515 resolver boundary remove the draft ambiguities without selecting new product semantics.
