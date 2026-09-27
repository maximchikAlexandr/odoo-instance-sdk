## Context

Source and GitHub review:
- `ProjectConfig.test_instance` currently holds one URL, optional database and branch; init accepts `--test-*`. Remote preparation reads `ODCLI_TEST_MASTER_PASSWORD`.
- `.odcli/.env` already has restrictive parsing, owner-only permissions and process-environment precedence. `get_config_root()/user.toml` already stores settings including `backup.max_uncompressed_bytes`.
- Preparation already downloads, validates, restores locally, neutralizes and records provenance. COPY checkout currently backs up a local source before restore.
- Catalog deletion/validation, lifecycle locks, COPY recovery, inventory and monitoring already exist. Named source management and age retention do not.
- GitHub #20/#24 explicitly deferred multiple sources and retention. Reuse their preparation/provenance work. #53's dotenv work has an implementation. #11/#43/#81 cover monitoring/inventory. Open #17/#37/#39 concern separate logging, analysis and storage research.

## Goals / Non-Goals

**Goals:** Small public primitives with identical Python/CLI behavior; reuse current plans, catalog, locks, validation, output and recovery.

**Non-Goals:** Workflow engine, scheduler, tags, vault, remote deployment, automatic deletion of environments/databases, monitoring UI or container backend. Names distinguish lab and staging. Neutralization is not anonymization or a network sandbox.

## Decisions

### 1. Named entries in the existing manifest

Extend `ProjectConfig` with typed `RemoteSourceConfig` entries, serialized as `[remote_instances.NAME]` with required `base_url`, `database`, `git_branch`. Requiring the database for new entries avoids remote-list ambiguity. Names match `[a-z][a-z0-9_]*` and are unique in the canonical project.

```toml
[remote_instances.lab]
base_url = "https://lab.example.invalid"
database = "lab_db"
git_branch = "develop"

[remote_instances.staging]
base_url = "https://staging.example.invalid"
database = "staging_db"
git_branch = "staging"
```

Expose public read/add/update/remove operations beside existing project-init APIs; use the existing atomic manifest writer and project lock, preserve unrelated values, reject stale plans. No fourth client facade. Identical add is a no-op; a different existing entry requires explicit update. Remove affects configuration only.

SDK init accepts typed entries; CLI init accepts repeatable `--remote NAME URL DATABASE GIT_REF`. Re-init without these inputs preserves entries. They satisfy the remote-config portion of completeness without a redundant test source; missing secrets are reported separately, never written into the manifest.

Preserve `[test_instance]` and `--test-*`. Without an explicit source, legacy refresh uses only that legacy entry. Named-only projects require a selector, even with one entry. No implicit first/default source.

### 2. Existing dotenv and trusted project URLs

For `staging`, derive only `ODCLI_REMOTE_STAGING_MASTER_PASSWORD` from the validated name. Do not let a repository name an arbitrary secret variable. Read process environment over the existing ignored, mode-0600 `.odcli/.env` at the registered project root; worktrees use that root, without copying credentials. Empty overrides fail rather than fall back.

The selected URL in `project.toml` is trusted for both named sources and legacy `[test_instance]`. Treat `ODCLI_REMOTE_<NAME>_ORIGIN` and the existing `ODCLI_TEST_INSTANCE_ORIGIN_PINS` as legacy no-op variables: do not require, compare, generate or recommend them. The named ORIGIN variables were only proposed, not shipped; no implementation of their check is needed. Existing values may remain in dotenv without affecting execution; do not rewrite user secret files. Preserve `ODCLI_TEST_MASTER_PASSWORD` compatibility, URL normalization/validation, TLS verification and the existing cleartext HTTP warning. Never forward secrets across origins on redirect. Both configuration files are agent-editable in the current trust model; a future external secret store is separate work, with no provider abstraction added here.

Never persist/print passwords or include them in plan fingerprints. Strip all named-source credential keys from child environments, including local Odoo, whether inherited or loaded from dotenv; only the selected remote HTTP request receives its password. Extend existing redaction and process-environment filtering. This cannot constrain an agent with unrestricted shell/filesystem access; external permissions remain necessary.

### 3. Source selection on current preparation and COPY

Add `remote_name` to public preparation options and `db refresh --remote NAME`. Manual refresh with `--restore` retains its documented default switch. Checkout uses download-only preparation then COPY restore: no intermediate baseline database and no project-default switch.

Add mutually exclusive `remote_name`/`backup_id` to `EnvironmentCheckoutOptions`, COPY-only and incompatible with local `source_database`. Examples respect existing ticket allocation:

```text
odcli env checkout TASK-123 --db-mode copy --remote staging
odcli env checkout TASK-124 --db-mode copy --backup BACKUP_UUID --base staging
```

For a named source its configured branch supplies the base unless explicitly provided. Resolve it to an available local commit before download; known source/base mismatch fails using existing branch comparison. Missing refs require explicit existing Git fetch/sync, not hidden pull/merge. Never fall back to current branch, project default DB or another source. Configured branch is declared provenance, not deployed-commit attestation.

Capture project/source name, URL/database/declared branch, resolved local base commit and resulting backup UUID in existing provenance/results. Use nullable fields for legacy data. Reuse/freshness decisions include actual source identity, not just label or database name. Recheck profile identity before effects; stale plans fail. Download UUID is an execution output of a captured source action, not an invented dry-run value.

All input paths converge on the existing COPY journal and restore path. Retained and named-remote backups are borrowed and survive rollback/removal. Only proven environment-owned local-COPY backups retain existing cleanup behavior. Local restore creates a new DB and separate writable filestore, neutralizes and verifies postconditions before ready. Hold the backup lifecycle lock; reuse archive, disk-reserve, collision and cluster checks. Known Odoo major mismatch blocks restore; unknown compatibility is reported, never invented.

Explicit sources bypass unrelated default-database freshness. Download failure never substitutes a stale backup. Callers may explicitly retry with a retained UUID. Existing recovery owns partial targets; errors identify retained resources and an applicable existing recovery action without automatically retrying mutation.

### 4. Extend diagnostics and readiness

Add a remote selector to existing public diagnostics and `doctor --remote NAME`: configuration, presence of passwords, Git ref and local restore prerequisites. Offline checks send no HTTP. Never claim backup authentication was verified without a successful password-bearing backup request, and never download an archive just to check readiness.

Detached `wait_ready` uses the current process registry and health probe, opt-in with a positive timeout (default 60 seconds). Confirm the exact process/environment/database binding, not an unrelated listener. On failure terminate only this launch's process and clear its matching identity; preserve the environment for inspection. Cleanup failure reports the surviving process and retains evidence.

### 5. Retention settings in user.toml

```toml
[backup]
retention_days = 14
auto_prune = false
```

Defaults are 14 days and automation off until configured. Positive integer age, boolean enablement, no project override of automatic deletion. Missing file uses defaults; malformed policy refuses manual pruning and skips automatic pruning with a warning. Preserve unrelated user settings.

Expose typed public read/update operations for these two fields, plus `backup retention` (inspect), `backup retention --days 14 --auto`, and `--no-auto`. Report the actual platformdirs path and effective values. No new YAML file or generic config framework.

### 6. Prune through the existing backup facade

Add `client.backups.prune_command(project=...)`/`prune()` and `set_pinned_command`/`set_pinned` for an exact UUID. Extend the existing Alembic catalog. Manual deletion also respects pins and active use; unpinning is a separate operation.

Pruning captures a UTC cutoff and exact candidate UUID/file identities for one project. Candidates are available, successfully downloaded, SDK-managed payloads older than the cutoff. Protect pinned backups, active operations, non-removed environment references, unresolved recovery journals and the newest available backup per source-origin/database group. Historical restore audit alone must not retain every archive forever. Skip unknown ownership/timestamps and external files. Never delete audit, databases, worktrees or arbitrary directory contents.

Return candidate/protected bytes, IDs and reasons. Under existing lifecycle locks, recheck file identity, references, pin state and latest-backup protection before deletion. Busy or changed candidates are skipped; execution never widens the captured set. A changed retention policy requires replanning, or skips automatic maintenance with a warning. Reuse idempotent deletion and report per-file outcomes accurately after failures.

If enabled, project-aware backup creation, refresh, restore and checkout attach one sequential post-success pass to the outermost command. Capture current candidates in that immutable command plan, then revalidate after primary success; exclude input/output backup IDs. Nested operations do not trigger duplicate passes. No cleanup on failure, cancellation, inspection or dry-run. Cleanup failure is a warning alongside primary success, so a caller does not retry a completed restore. No daemon or parallel worker.

### 7. CLI and existing operations

CLI parses typed options, invokes a public primitive and renders established bounded output. Register every new leaf in `PUBLIC_LEAF_CASES`. Reuse environment listing/detail, monitor, process inventory, stop and retryable removal; document how these compose with named sources instead of adding a session manager.

## Risks / Trade-offs

- Branch metadata is not deployed-code proof: report declared branch and actual local base commit separately.
- Neutralization does not remove personal data or block custom integrations: approved inputs and execution isolation belong to callers.
- Protected archives can exceed retention age: report reasons/bytes; retention is not a quota. Existing free-space checks still apply.
- Idle systems do not prune: explicit prune or the next successful operation is required.

## Migration Plan

Land source configuration/selection, then checkout/readiness, then retention with each group's tests and CLI documentation. Preserve legacy manifests/password keys/calls; retire origin approval checks and ignore their legacy variables; migrate nullable provenance and pin/ownership fields additively. Retain unknown ownership; default auto-prune off. Disable auto-prune independently when needed. Do not downgrade catalog history or run an old binary against an unsupported catalog version.
