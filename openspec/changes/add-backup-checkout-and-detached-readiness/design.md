## Context

The current implementation was re-inspected against `origin/main` at `e35f426cf168ab7a781941772f3207689a961ec8` in addition to the planning branch input `e0d2128443a949e148a33f7e05c3a19c99a74d55`:

- `ProjectConfig.test_instance` and `--test-*` represent one legacy remote source. Project initialization and database preparation already use immutable commands, project locks, atomic manifest replacement, conservative `.odcli/.env` parsing, and `ODCLI_TEST_MASTER_PASSWORD`.
- Database preparation already downloads, validates, restores, neutralizes, records project ownership/provenance, and preserves failures. COPY checkout already has one journal/recovery path and a local-source auxiliary Database Manager lifecycle.
- The catalog now uses SQLAlchemy Core metadata and a linear Alembic ledger. Backups already carry project ownership, source URL/database/branch, events, restore links, environment links, checksum, size, and state. Exact UUID deletion already rechecks path identity under a per-backup lifecycle lock.
- Detached launch already has an immutable command, persisted project/environment runtime identity, bounded process cleanup, logfile evidence, and a health probe. `auxiliary_restore_identity.py` now proves PID/create-time/argv/cwd/config and exact listener ownership with `psutil`.
- `get_config_root()/user.toml` already supplies `backup.max_uncompressed_bytes`, but it has no public retention writer. The project dotenv permits arbitrary validated keys and gives process environment precedence.
- GitHub #20 and #24 delivered single-source refresh and branch provenance. #53 delivered the dotenv boundary. #11, #43, and #81 delivered reusable monitoring, detail, process inventory, and bounded Rich output. Open #17, #37, and #39 remain separate logging, inspection, and filestore research scopes.

The package must remain SDK-first: every mutating or spawning CLI leaf delegates once to a public typed command; all process execution remains in `internal/proc`; public plans remain frozen, JSON-safe, and secret-free.

## Goals / Non-Goals

**Goals:**

- Provide deterministic named remote selection, remote and retained-backup COPY checkout, environment-bound detached readiness, and safe backup retention through small public SDK primitives with identical CLI behavior.
- Preserve legacy single-source calls and data, reuse existing catalog/locks/plans/recovery, and keep source, branch, backup UUID, local base commit, and resulting environment traceable.
- Make every destructive decision previewable and execution-time revalidated without secret persistence or hidden fallback.

**Non-Goals:**

- No workflow engine, scheduler, daemon, source default, tags, provider framework, secret store, remote deployment, role/approval model, reporting UI, backup quota, database/environment garbage collection, or new client facade.
- No assertion that a declared Git branch attests the code deployed on a remote Odoo instance.
- No automatic fetch/pull/merge, secret-file rewrite, retry of completed primary mutations, or anonymization/network sandbox promise.

## Decisions

### 1. Named sources extend `ProjectConfig`; one public configurator owns edits

Add frozen `RemoteSourceConfig(name, base_url, database, git_branch)` values to `ProjectConfig.remote_instances`, serialized deterministically as `[remote_instances.NAME]`. Names match `[a-z][a-z0-9_]*`; URL credentials, missing database/branch, duplicate names after normalization, and unknown fields fail. The legacy `[test_instance]` remains unchanged.

Use the existing public project-init surface instead of adding `client.projects`: `list_remote_sources(project)`, `configure_remote_source_command(project, source, replace=False)` plus its convenience method, and `remove_remote_source_command(project, name)` plus its convenience method. `replace=False` implements add/idempotent-add; `replace=True` implements explicit update and preserves unspecified fields at the CLI adapter. Commands capture canonical repository identity, manifest bytes/fingerprint, and the complete resulting manifest; execution takes the project lock, rejects drift, and calls the existing atomic writer. Removal changes configuration only.

`init_project_command` accepts a tuple of typed remote entries; CLI init accepts repeatable `--remote NAME URL DATABASE GIT_REF`. Re-init without the option preserves all entries. Named entries satisfy remote configuration completeness without creating a legacy entry. Init reports the derived credential variable names but never prompts for or writes their values.

**Alternatives rejected:** a source registry/table duplicates the manifest; a fourth client facade adds no capability; one implicit default source makes scripts dependent on ordering; a generic configuration patcher is unnecessary.

### 2. Existing dotenv is the only secret boundary; origin variables are inert

For source `staging`, derive exactly `ODCLI_REMOTE_STAGING_MASTER_PASSWORD`. The configured name cannot nominate any other environment key. Read the registered project's existing owner-only `.odcli/.env`, then overlay the process environment per key. Missing or empty selected values fail before HTTP or local mutation. Worktrees resolve the registered canonical project root.

The selected normalized URL in `project.toml` is trusted. Remove calls to the old origin-approval helper from configured preparation/init/doctor paths. `ODCLI_TEST_INSTANCE_ORIGIN_PINS` and `ODCLI_REMOTE_<NAME>_ORIGIN` are ignored even when empty or malformed; do not generate, validate, migrate, or delete them. Keep `ODCLI_TEST_MASTER_PASSWORD` for legacy `[test_instance]`.

Preserve URL normalization, credential-bearing URL rejection, TLS verification, and the current cleartext non-loopback warning. The current HTTP client keeps redirects disabled; any redirect response is an error, so a password-bearing request is never replayed to another origin. Extend the existing secret-value collection, redacted projection, and child-environment denylist to every `ODCLI_REMOTE_<VALID_NAME>_MASTER_PASSWORD`; no secret enters argv, plans, fingerprints, exceptions, logs, or child processes.

**Alternatives rejected:** origin pins contradict the current trusted-manifest decision; rewriting `.env` risks destroying user-managed secrets; a vault/provider abstraction is a separate product decision.

### 3. Selection is explicit and reuses database preparation

Add `remote_name` to `DatabaseRefreshOptions` and the public preparation command. No name preserves the legacy `[test_instance]` behavior. A named-only project without a selector, an unknown name, or simultaneous legacy/named source inputs fails before effects. `db refresh --remote NAME` uses the selected URL/database/declared branch and source-specific password; `--restore` keeps the existing successful-restore/default-switch semantics. Source-aware coalescing and stale-plan identity use canonical project ID plus historical source name, normalized origin, database, and declared branch.

Diagnostics accept the same optional selector and report configuration, exact expected password-key presence, local Git ref availability, and restore prerequisites. Offline diagnostics do not contact Odoo. They report authentication as unverified until a real backup request has succeeded; they do not create a backup merely to diagnose it.

Persist nullable `source_name` on new backup rows and expose it in the existing backup projections. Legacy rows remain `None`; configuration edits never rewrite history. Declared branch and resolved local commit remain separate facts.

### 4. All COPY inputs converge after acquisition

Extend `EnvironmentCheckoutOptions` with mutually exclusive `remote_name` and `backup_id`, valid only for COPY and incompatible with `source_database`. Local-source COPY remains unchanged. Named-source COPY performs download-only preparation, then hands the resulting catalog UUID to the existing validation/restore/neutralization/journal pipeline; it does not create an intermediate baseline database and never switches the project default. Exact-backup COPY resolves the UUID locally and makes no source HTTP request.

A named source's branch is the default base. An explicit base may override only when existing provenance comparison can prove compatibility; a known mismatch fails. The base ref must already resolve locally to an exact commit before download. Missing refs fail with the existing fetch/sync guidance; there is no hidden Git mutation or fallback. A retained legacy backup with unknown branch requires an explicit base and returns an `unknown` warning.

Add a nullable journal ownership field with three semantic states: `owned` for the local-source backup created solely for this environment, `borrowed` for named-remote or retained UUID input, and `unknown` for migrated journals. Rollback/removal deletes only `owned`; borrowed/unknown archives survive. The new linear Alembic revision also adds nullable historical source name and pin state/event support. Existing rows migrate conservatively.

Before target mutation, reuse current catalog/file identity, checksum, archive-safety, disk reserve, collision, cluster, and lifecycle-lock checks. Known Odoo major mismatch blocks; missing version evidence is `unknown`. All paths create a new local database and separate writable filestore, neutralize, and verify postconditions before ready. A failure identifies exact retained backup/target state and one existing recovery operation; it never retries or substitutes another source/archive.

### 5. Detached readiness reuses exact runtime/listener proof

Add `wait_ready: bool = False` and `readiness_timeout: float = 60.0` to the public detached command/convenience operation. A timeout is meaningful only when waiting and must be finite and positive. The CLI permits `--wait-ready` only with root `--env` plus `--detach`; it never implements polling itself.

Extract the current PID/create-time/argv/cwd/config/listener-owner checks from `auxiliary_restore_identity.py` into one shared internal runtime proof consumed by both auxiliary restore and detached readiness; do not add a second scanner. After spawn and persisted identity, readiness repeatedly requires: the matching runtime row, the exact live process and listener owner, the expected environment/database binding from the captured config, and the existing health probe. An unrelated listener or unverifiable ownership never satisfies readiness.

On early exit, mismatch, or deadline, terminate only the handle/process group created by this command. Clear the runtime row only with the existing identity-conditional operation after confirmed exit. Preserve environment, worktree, database, logfile, bounded stderr tail, and recovery identity. If termination cannot be confirmed, retain the runtime row and return a typed cleanup-failure error naming the surviving owned process.

**Alternative rejected:** calling `wait_ready()` after `run_detached()` loses one-command failure cleanup and allows a healthy unrelated listener to satisfy the probe.

### 6. Retention policy stays in the existing `user.toml`

Add frozen `BackupRetentionPolicy(retention_days=14, auto_prune=False, path=...)`. Expose `client.backups.retention()` and `set_retention_command(retention_days=None, auto_prune=None)` plus its convenience method. The narrow updater owns only `backup.retention_days` and `backup.auto_prune`, preserves the existing `backup.max_uncompressed_bytes` and every unrelated table/key, writes atomically with user-only permissions, and performs no write for preview. It uses the standard library parser plus a focused section patch; no TOML or generic settings dependency is added.

Age is a non-boolean positive integer and enablement is a boolean. Missing settings use defaults. Malformed/unreadable settings fail explicit update/prune; automatic maintenance is skipped with a structured warning rather than substituting defaults. Project manifests cannot enable deletion.

### 7. Pinning and pruning extend `client.backups`

Expose `set_pinned_command(backup_id, pinned)`/`set_pinned()` and `prune_command(project)`/`prune()`. Pin/unpin are idempotent and audited. A prune plan captures policy fingerprint, UTC cutoff, project ID, exact candidate UUID/file identities/bytes, and protected/skipped UUIDs with reasons.

Candidates are project-owned, available, successfully downloaded SDK-managed regular payloads strictly older than cutoff. Protect:

- pinned backups;
- a backup whose lifecycle lock cannot be acquired because validation/restore/delete is active;
- backups referenced by non-removed environments or unresolved COPY/replacement recovery;
- the newest available backup in each historical source group;
- unknown ownership/timestamp/path identity and external/unowned files.

For named rows, a historical group is `(project_id, source_name)`; legacy/local rows use `(project_id, normalized source origin, database)`. Ties use current deterministic catalog ordering. Direct UUID deletion applies the same pin, active-use, environment/recovery, and newest-group protections; there is no force bypass. Historical restore audit alone does not protect every archive.

Execution takes the existing per-backup lifecycle lock and rechecks policy fingerprint, catalog state, file identity, pin/reference/busy/latest protections, and captured UUID membership immediately before each deletion. It never widens the plan. Changed/busy targets are skipped. Reuse the existing exact deletion/audit operation. Results report deleted/skipped/failed IDs and actual bytes; partial failure is truthful and repeat execution is idempotent. Catalog rows/events are never retention targets.

### 8. Automatic pruning is one explicit post-success command phase

When `auto_prune` is enabled, the outermost project-aware backup creation, refresh, local restore, or checkout command captures one prune subplan before primary mutation and appends it as sequential post-success actions. Use one private composer at those four public command boundaries, not middleware, a scheduler, or a context-variable framework. Nested internal calls invoke their unwrapped command and cannot attach another pass.

Run the captured pass only after primary success; revalidate every candidate and exclude the primary input/output backup UUIDs. Failure, cancellation, inspection, and dry-run skip it. Maintenance failure becomes a structured warning on the successful primary result and does not change its exit status; explicit `backup prune` failure remains non-success. This prevents callers from repeating a completed restore.

### 9. CLI remains a thin bounded projection

Add `remote ls/add/update/remove`, remote selectors on `db refresh`, `env checkout`, and `doctor`, detached readiness flags, and `backup retention/pin/unpin/prune`. Every mutating leaf delegates once to a public command, uses current confirmation/dry-run/redaction/error/output contracts, and is registered once in `PUBLIC_LEAF_CASES`. Interactive prune requires confirmation; non-interactive application requires `--yes`; dry-run requires neither. Existing monitor, environment detail/listing, process inventory, logs, stop, remove, and recovery operations are documented as the composition surface instead of adding a session manager.

## Risks / Trade-offs

- **A source name can later point elsewhere** -> each backup stores both historical name and normalized origin/database; plans display both and stale profile changes fail.
- **Declared branch is not deployed-code proof** -> display declared branch and resolved local commit separately; never claim attestation.
- **Listener ownership inspection can be unavailable** -> readiness fails closed and preserves logfile/runtime evidence; it never accepts a merely responsive port.
- **Protected archives can exceed retention age** -> report exact reasons and bytes; retention is intentionally not a quota.
- **Pre-primary pruning snapshots are conservative** -> an archive that becomes eligible during the primary operation waits for the next manual/successful pass; execution never expands a destructive set.
- **Idle projects do not auto-prune** -> explicit prune or the next successful project-aware operation is required; no daemon is introduced.
- **Current main moved after the input planning SHA** -> implementation must rebase onto its approved current main and preserve the newer auxiliary-runtime, output, and catalog contracts; the OpenSpec-only planning commit does not merge production changes.

## Migration Plan

1. Land named-source config/credential selection and remove origin approval while preserving legacy variables as no-ops.
2. Land the single linear Alembic revision and public backup/source projections, then exact-backup/named-remote COPY on the existing journal and recovery path.
3. Land shared listener proof and detached readiness.
4. Land retention policy, pin/delete protection, manual prune, then the explicit post-success composer.
5. Land CLI/documentation and two-source integration evidence after all public contracts are stable.

The catalog migration is forward-only like the current alpha ledger: backup and verify the existing SQLite catalog before upgrade, keep nullable conservative defaults, and do not run an older binary against a newer catalog. Disabling `auto_prune` is an independent operational rollback. Removing a named profile never removes secrets or operational artifacts.

## Open Questions

None. Product scope, compatibility, destructive protections, and failure semantics are fully specified above.
