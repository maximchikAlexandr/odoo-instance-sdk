## Context

The current Compose bootstrap path executes one captured Odoo spawn for `tmp`, proves `base` readiness through PostgreSQL, and returns. The catalog already has an append-only `database_events` lifecycle ordered by a global sequence: completed restore appends `restored`, successful reconciliation appends idempotent `dropped`, and each event can carry the exact endpoint, database, `cluster_id`, and data directory. Guarded `db rm` already validates the active project cluster claim, Docker volume/container labels, active bindings, sessions, and the same ownership projection again under the cluster lock.

The missing fact is only that this exact SDK bootstrap created `tmp`. The change therefore extends the existing lifecycle log rather than adding a second database-origin store.

## Goals / Non-Goals

**Goals:**

- Give a freshly created, stopped, exactly owned Compose project a supported `odcli db rm tmp` cleanup path.
- Append bootstrap evidence only after the captured spawn actually creates `tmp` and readiness succeeds.
- Reuse the existing lifecycle log, ownership gate, lock, destructive confirmations, reconciliation, and filestore safety.
- Make a later `restored` or `dropped` event revoke bootstrap authority by ordinary event ordering.
- Keep planning/preview output inspectable and secret-free.

**Non-Goals:**

- No new bootstrap database table, repository, CRUD lifecycle, or atomic finalizer.
- No whole-project or Docker-volume reset command.
- No adoption or backfill of pre-existing/legacy `tmp` databases.
- No weakening of exact restore provenance for ordinary databases.
- No public `DatabaseResource` or `PostgresCluster` cleanup method, automatic runtime stop, or implicit connection termination.

## Decisions

### D1. Reuse `database_events` with one `bootstrapped` event

Extend the existing event-type CHECK constraint with `bootstrapped`. Its payload SHALL use the existing exact `db_host`, `db_port`, `database_name`, `cluster_id`, and `data_directory` columns; `backup_id`, `source_kind`, and `source_sha256` remain null. The catalog writer accepts only database `tmp`, requires a non-null data directory, and validates that `cluster_id` resolves to a current active cluster claim before appending.

The next single-head Alembic migration rebuilds only the SQLite table constraint while preserving sequence values, rows, indexes, foreign keys, and metadata equivalence. It creates no new relation and synthesizes no legacy event.

Alternatives considered:

- A dedicated `bootstrap_databases` current-state relation: rejected because the append-only lifecycle already supplies exact identity, ordering, revocation, and idempotent drop reconciliation.
- Treat bootstrap as a restore source: rejected because no restore occurred and existing restore provenance must remain truthful.
- Infer ownership from `mode="compose"`, `tmp`, or a successful probe: rejected because those facts can describe foreign or legacy resources.

### D2. Record only an actual creation by the captured bootstrap flow

The shared bootstrap executor SHALL return a typed internal outcome distinguishing `created` from `already_ready`. The immutable init and first-run plans add one conditional catalog `ActionStep` after readiness verification. Only `created` calls the narrow event writer; `already_ready` skips the action and never backfills missing evidence.

The writer receives the already resolved endpoint, active cluster identity, and project-owned data directory. A write failure fails the command. A crash after database creation but before event publication leaves `tmp` unowned and therefore undeletable by the guarded path; later readiness does not adopt it.

Alternative considered: append `bootstrapped` for every successful readiness probe. Rejected because that would convert observation into ownership.

### D3. The latest exact event selects the ownership path

Add one narrow internal catalog reader for the latest `database_events` row by normalized endpoint and exact database, ordered by `sequence DESC`.

The guarded ownership gate keeps its existing common checks first: project-bound Compose cluster, exact active claim, active environment/runtime refusal, and Docker volume/container label inspection. It then applies this event rule:

- latest `bootstrapped`: accept only database `tmp`, matching non-null `cluster_id`, null restore fields, and a safe recorded data directory;
- latest `restored`: retain the existing completed restore-binding validation unchanged;
- latest `dropped` or no event: refuse as unknown current origin.

The resulting immutable ownership projection is computed during planning and again under the existing cluster lock. Any event or identity change makes the projections unequal and refuses before mutation.

### D4. Existing event order performs revocation and retry reconciliation

A completed restore already appends `restored`, so it naturally supersedes an older `bootstrapped` event without any extra restore mutation. A successful drop already calls `record_database_dropped`; the resulting latest `dropped` event revokes bootstrap authority.

For the interrupted case where PostgreSQL deletion succeeded but reconciliation did not, add the same existing `record_database_dropped` call to each authorized `idempotent_absent` return. At that point the command has already revalidated the latest `bootstrapped` event and exact cluster ownership under lock and PostgreSQL has proved absence. The helper remains idempotent, so no new finalizer or compare-delete API is needed.

### D5. Keep public and CLI surfaces stable

Users keep the existing command and flags: `odcli db rm tmp --force-default --yes`, adding `--force-connections` only under the existing explicit policy. Public SDK method discovery and typed CLI output remain unchanged. Preview may show sanitized `origin=bootstrap`; it exposes no password or secret.

### D6. Verify the narrow seams

Tests SHALL cover the migrated CHECK constraint and row preservation; validated `bootstrapped` append/latest-event ordering; `created` versus `already_ready`; conditional action visibility/consumption; latest `bootstrapped` success; later `restored`/`dropped` revocation; missing, legacy, foreign, malformed, changed, active-binding, session, default, and confirmation refusals; authorized already-absent reconciliation; unchanged restore behavior; and one public-boundary disposable Compose regression.

The Compose regression creates normal bootstrap `tmp`, stops Odoo, removes it through the public CLI path, then varies one identity component and proves no session termination, `DROP DATABASE`, volume deletion, or unrelated filestore deletion occurs.

## Risks / Trade-offs

- **SQLite cannot alter the event CHECK in place** → rebuild the existing table in one migration and verify rows, sequences, indexes, foreign keys, and metadata equivalence.
- **Database creation can succeed before event publication** → fail closed and never infer or backfill ownership from readiness alone.
- **A manual drop/recreate can leave an old event apparently current** → preserve the same recorded-origin trust model already used by restore bindings; any observed absence writes `dropped`, while unobserved out-of-band replacement remains outside the supported lifecycle.
- **The absent retry could reconcile the wrong target** → call it only after exact ownership revalidation under the existing lock and a direct PostgreSQL absence proof.
- **Bootstrap filestore may be absent or unsafe** → reuse contained-path cleanup; absence succeeds, unknown or unsafe paths are retained and reported.

## Migration Plan

1. Rebuild `database_events` through the next Alembic head with `bootstrapped` allowed and all historical rows/sequences preserved.
2. Start appending `bootstrapped` only for new successful SDK creations; do not synthesize events for legacy `tmp`.
3. Enable latest-event dispatch in guarded drop and absent-branch reconciliation in the same release.
4. Downgrade refuses while any `bootstrapped` row exists rather than silently discarding audit history; without such rows it restores the prior CHECK and behavior.

## Open Questions

None. The selected solution is the existing `database_events` lifecycle plus the existing guarded `db rm` path.
