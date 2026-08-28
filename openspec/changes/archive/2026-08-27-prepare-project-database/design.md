## Context

The repository already has the right concrete pieces but no project-level transaction boundary around them. `DatabaseResource.backup()` streams to a cataloged file, `restore()` rejects non-local targets and records `restores.backup_id` only after its existence postcondition, `BackupCatalog` owns the single WAL SQLite database and sequential migrations, `EnvironmentResource` plans checkout and copy-mode database work, `PostgresCluster` owns project PostgreSQL readiness, `write_manifest()` performs secret-checked atomic replacement, and `OdooInstance.run_shell_script()` executes ORM code under the instance lock.

The change must stack on the accepted MYL-55 CLI boundary before implementation. Its Click adapter belongs in `commands/db.py` and must consume typed SDK results through the shared Rich/JSON/TOON envelope; the planning branch contains no implementation and is based on current `origin/main`, so the implementer must rebase/merge the MYL-55 implementation foundation before touching the command layer.

The difficult boundary is failure after irreversible useful work. A downloaded backup or successfully restored database is not rolled back, because deletion would erase the audit/recovery artifact and contradict existing restore safety. Instead, the project manifest remains the commit point: only a fully successful requested flow changes `default_source_database`.

## Goals / Non-Goals

**Goals:**

- One concrete preparation workflow for manual refresh and checkout freshness.
- Declarative, auditable source-branch provenance from backup through restore and checkout.
- Fail-before-mutation checkout compatibility checks, including a narrow legacy-unknown escape hatch.
- Local ORM-only administrator reset with a strict secret/output boundary.
- Coalesced concurrent refreshes and atomic project-default switching.
- Typed adapter-neutral results that the MYL-55 CLI foundation can render directly.

**Non-Goals:**

- Backup/database garbage collection or retention policy.
- Cron, daemon, scheduler, background refresh, or named remote profiles.
- Template clone strategy, pgAdmin, password manager integration, or remote administrator reset.
- A `DatabaseService`, repository/provider abstraction, command bus, workflow engine, or second catalog/downloader.
- Automatic Git discovery from Odoo, automatic checkout password reset, or commit-level compatibility analysis.

## Decisions

### D1. Keep orchestration private and expose it through existing resources

Add `internal/database_preparation.py` with a small concrete coordinator and pure helpers for source resolution, provenance comparison, freshness, target naming, and manifest conflict detection. `EnvironmentResource.refresh_database(project, *, options=DatabaseRefreshOptions())` is the public entry point for manual callers. `EnvironmentResource.checkout()` invokes the same private coordinator in freshness mode, rather than calling the public wrapper recursively. `DatabaseResource` remains responsible for instance-bound backup/restore and gains the instance-bound admin reset operation.

Public frozen types live with other models: `DatabaseRefreshOptions`, `DatabasePreparationResult`, `AdminPasswordResetResult`, `BackupBranchOrigin`, `BackupProvenanceStatus`, `BackupProvenanceComparison`, `EnvironmentCheckoutPlan`, and `EnvironmentCheckoutResult`. `EnvironmentResource.plan_checkout()` returns the secret-free public plan. Additive `checkout_with_plan()` returns the public result containing the realized `DevelopmentEnvironment` and final public plan; canonical `checkout()` delegates to it and returns only `.environment`, preserving the main-spec API. Private `_CheckoutPlan` remains the execution input and may contain config values, paths, and argv; it is never serialized or nested in a public result. The preparation coordinator raises typed SDK errors carrying only sanitized retained-artifact identifiers.

Alternative considered: a new `DatabaseService` or `client.database_preparation` resource. Rejected because existing resources already define the application boundary and a new facade would duplicate ownership without another implementation.

### D2. Store only the declared branch on the backup row

Add `backups.source_git_branch TEXT NULL` through the next sequential `BackupCatalog` migration and include it in the fresh schema. `start_download()` receives it before HTTP begins; row conversion, list/latest/latest-restore, and identity verification round-trip it. Restore and environment rows continue to reference `backup_id`, so they derive provenance with an existing join/lookup and do not duplicate it. A restore-provenance lookup joins `restores.backup_id` to the audit row without filtering on backup state or file existence; available-backup queries remain separate for freshness and restore input.

`DatabaseResource.backup(..., source_git_branch=None)` validates and preserves caller text. The workflow separately reports whether that value came from `--source-branch`, `[test_instance].git_branch`, or was unavailable. Origin is operation-level audit, not another persistent column.

Alternative considered: metadata JSON or a provenance table. Rejected because the issue requires one nullable column and the single source relationship is already immutable by backup ID.

### D3. Model project configuration with one test profile and no secret field

Add `TestInstanceProjectConfig(base_url, database, git_branch=None)`, plus `ProjectConfig.test_instance`, `default_base_ref`, and `refresh_after_hours`. `ProjectConfig.load()` rejects unknown `[test_instance]` keys and validates finite positive freshness hours. `to_manifest()` emits only non-secret declarative values. `ODCLI_TEST_MASTER_PASSWORD` is captured and validated before preparation work and passed only to `client.instance(remote_url, master_password=...)`.

Repository-selected preparation requires HTTPS for non-loopback origins and an
exact canonical-origin approval supplied outside the repository through the
single comma-separated `ODCLI_TEST_INSTANCE_ORIGIN_PINS` environment variable.
Pins contain no secrets and are compared as lowercase scheme/host plus effective
port; loopback origins do not need a pin. The check happens before preparation
locking, readiness, database-manager access, HTTP, catalog mutation, or manifest
mutation. This control is intentionally limited to project preparation, so the
generic direct `DatabaseResource.backup()` contract remains unchanged.

The manifest writer's existing secret scan remains a defense in depth. There is deliberately no password CLI option, config key, catalog field, result field, or keychain abstraction.

Alternative considered: reusing local `source_config.admin_passwd` for remote download. Rejected because it binds different trust domains and makes secret provenance ambiguous.

### D4. Use a dedicated canonical project preparation lock

Add `database_preparation_lock_path(project_id)` and `database_preparation_artifact_lock_path(project_id, database_name)` to the existing lock module, keyed by the same canonical repository identity used by project PostgreSQL resources. The latter is the claimed exclusive shell/artifact lock for a refresh target that has no environment ID. Do not reuse the PostgreSQL lifecycle lock: preparation calls `PostgresCluster.ensure_running()`, so sharing that lock would create recursive acquisition/deadlock risk and would couple independent lifecycle ownership.

The preparation lock surrounds the decision recheck, required download/restore/reset, and manifest commit. After locking, the coordinator reloads the manifest and catalog state. Checkout callers may coalesce: if the preceding caller installed a fresh, compatible default, the waiter returns a typed reused/no-op result and proceeds. Manual refresh never coalesces merely because a backup is fresh; an explicit manual request always creates a new backup.

Lock ordering is preparation lock → PostgreSQL lifecycle lock inside `ensure_running()` → environment artifact lock for standalone reset or canonical project+target artifact lock for refresh reset. `_run_shell_script_exclusive()` acquires the final lock from the constructed instance; no code acquires them in reverse. CLI never acquires a lock.

Alternative considered: lock only manifest replacement. Rejected because concurrent callers would still create duplicate backups/databases and race target selection.

### D5. Preflight restore before the remote download

For `restore=True`, preparation resolves and validates the local source config and one `ProjectRuntimeBinding`: exact `(python_executable, odoo_bin)` command prefix, runtime cwd, and project `PostgresCluster`, using the same project/runtime resolution rules as checkout. It asserts the local URL, requires local `admin_passwd`, calls that cluster's `ensure_running()`, verifies the executable/cwd inputs, and proves the local database-manager path by listing databases. Only then does it create the remote instance and start `backup()`.

The target name uses a normalized remote database prefix plus `refresh`, a compact UTC component, and UUID entropy; the helper truncates by UTF-8 byte length, validates with the existing PostgreSQL/filestore guards, and checks absence under the preparation lock. A collision regenerates; it never falls back to dropping or overwriting.

Alternative considered: download first so the backup remains useful even when local preflight fails. Rejected because the issue explicitly requires restore preflight before download and avoids unnecessary large transfers for impossible restores.

### D6. Treat the manifest replacement as the commit point

The sequence under lock is:

1. Resolve current manifest/config/catalog state and preflight all known rejection cases.
2. Download the provenance-bearing ZIP/filestore backup.
3. For restore mode, call existing `restore(..., copy=True, neutralize_database=True)` and keep its mapping.
4. If requested, generate a mode-`0600` ephemeral config from the local source config with `db_name`/`dbfilter` set only to `target`; pass it and the preflighted `ProjectRuntimeBinding` to private `build_target_instance()`, which constructs `OdooInstance` with that config's `StartConfig`/database connection, the exact runtime command prefix/cwd, the ready project cluster, and `database_preparation_artifact_lock_path(project_id, target)`; call `reset_admin_password()` and remove the config in `finally`.
5. Reload the manifest, compare preparation-relevant fields against the locked baseline, replace only `default_source_database` with `msgspec.structs.replace`, and call `write_manifest()`.
6. Return the typed result.

Failures after steps 2 or 3 annotate the exception/result with backup ID and optional target database, but never compensate them. Because the default changes only at step 5, readers never observe a half-completed requested flow as the selected database. Unrelated manifest fields are preserved from the final reload; changes to test source, base, freshness, source config, PostgreSQL config, or current default constitute a conflict rather than a silent overwrite.

Alternative considered: switch the default immediately after restore and roll it back if reset fails. Rejected because rollback can itself fail and creates an observable interval with an unprepared default.

### D7. Reset `base.user_admin` through a minimal committed shell script

`DatabaseResource.reset_admin_password()` takes no database argument: it asserts a local instance and requires exactly one configured database, then runs the shell against that bound name. Refresh uses private `build_target_instance()` described in D6, not `InstanceFactory.from_config()`: the resulting instance carries the target-only ephemeral `StartConfig`, exact project `(python, odoo_bin)` prefix and cwd, bound ready `PostgresCluster`, and canonical target artifact lock. Standalone reset uses `client.instance.from_environment()` for the one resolved ready environment after verifying its generated config and recorded database agree. The method runs a constant, non-parameterized script via `_run_shell_script_exclusive(..., commit=True)` / the established shell path:

```python
user = env.ref("base.user_admin", raise_if_not_found=True)
user.ensure_one()
user.write({"password": "admin"})
result = {"xml_id": "base.user_admin", "updated": True}
```

The fixed password is never returned. The result identifies the bound database and reset completion but does not require or fabricate an environment ID. Checkout never calls this method; only explicit reset or refresh with the flag does.

Alternative considered: direct SQL or pre-hashing. Rejected because it bypasses Odoo password semantics and model hooks.

### D8. Compare branch provenance before any checkout mutation

Effective base precedence is explicit `EnvironmentCheckoutOptions.base_ref`, project `default_base_ref`, then `HEAD`. Comparison is deliberately textual after trimming and normalizing the conventional `refs/heads/` prefix; it does not resolve commits or guess branch aliases. A known mismatch is terminal before catalog/worktree/database mutation.

Provenance follows the latest restore mapping to its backup audit row regardless of whether the archive is present, readable, or currently restorable. A recorded branch remains known—and a mismatch remains terminal—while freshness independently classifies missing, deleted, unreadable, or otherwise unavailable backup input. Only an absent mapping/backup row or a null recorded branch is unknown. Such legacy unknown provenance proceeds only if `source_database` was passed explicitly in the current call; accepted unknown emits a stable warning.

The actual checkout performs freshness preparation first when required, then reloads the manifest and recalculates source, mapping, provenance, and target before its existing checkout plan. This avoids planning against the old default. Admin reset is never part of this path.

Alternative considered: allow all legacy unknown rows with a warning. Rejected because inferred defaults would silently bypass the safety feature indefinitely.

### D9. Keep dry-run read-only while applying identical decision functions

Split decision helpers from effects. Public `EnvironmentResource.plan_checkout()` opens the catalog only in SQLite read-only mode when it exists, does not initialize/migrate it, and projects decisions from private `_CheckoutPlan` into `EnvironmentCheckoutPlan`. Public plan fields are exactly `name`, `branch`, `effective_base_ref`, `db_mode`, `source_database`, `target_database`, `python_mode` (`create`/`reuse`), `provenance`, `freshness`, `preparation_actions`, and `warnings`; it contains no config values, secrets, paths, executable/argv values, UUID reservation, or private plan. Additive `checkout_with_plan()` performs/re-plans execution and returns `EnvironmentCheckoutResult(environment, public_plan)`. Canonical `checkout()` returns `checkout_with_plan(...).environment`. A missing catalog is unknown for provenance and missing/stale for freshness, but is not created.

Actual checkout rechecks under the preparation lock, so dry-run is informative rather than a reservation. Known mismatch and legacy-unknown decisions are identical in dry-run and execution.

Alternative considered: invoke preparation with a `dry_run` flag interspersed through side-effect code. Rejected because conditional effects are easy to leak and harder to prove read-only.

### D10. Keep the command adapter aligned with MYL-55

After rebasing the CLI foundation, create only `commands/db.py` and register its group from the stable entry point. The callbacks resolve context, construct typed options, invoke resources, and pass typed results/errors to the shared output policy. `commands/env.py` calls public `plan_checkout()` for dry-run and additive `checkout_with_plan()` for execution; other SDK callers retain canonical `checkout() -> DevelopmentEnvironment`. Shared structured projection serializes only the enumerated public models, while Rich reads the same fields. Delete `_checkout_plan_dict`; never serialize `_CheckoutPlan`; add no parallel CLI dictionary/DTO. `--reset-admin-password` without `--restore` is rejected by Click before resource invocation. There is no password prompt/option.

Rich, JSON, and TOON are projections of the same typed result. Failure rendering includes retained backup/target identifiers but all redaction tests use sentinel values for both local and remote master passwords and verify stdout, stderr, exceptions, repr, and structured payloads.

## Risks / Trade-offs

- [Long preparation lock during large downloads] → The lock is project-scoped, not global; it intentionally trades same-project concurrency for no duplicate databases. Emit measurable progress through the adapter without releasing ownership.
- [Local Odoo database manager may be unavailable even when PostgreSQL is healthy] → Preflight both paths before download and return a typed, redacted error.
- [Textual branches can refer to the same commit under different names] → Declarative provenance intentionally compares declared refs, not repository history; an explicit matching branch contract is safer and deterministic offline.
- [Retained databases accumulate after failures] → Surface exact retained identifiers and document manual cleanup; automated GC is explicitly deferred.
- [Manifest may be edited during a long refresh] → Re-read under lock before commit and reject relevant conflicts while preserving unrelated fields.
- [Dry-run can become stale immediately] → State that it is not a reservation and repeat all checks under lock during execution.
- [MYL-55 is a prerequisite branch rather than current main] → Require implementation to rebase/merge that accepted foundation first and keep database commits separate from foundation changes.

## Migration Plan

1. Rebase/merge the accepted MYL-55 implementation onto the feature branch before command work; resolve only the planned `commands/db.py` integration.
2. Add project/model types and backward-compatible manifest parsing/serialization. Legacy manifests continue with `None` defaults.
3. Add the next sequential catalog migration and fresh-schema column; test upgrade copies representing every currently supported schema version.
4. Extend backup row creation/conversion/identity, then add the private coordinator and resource methods.
5. Integrate checkout decisions/dry-run, then add the CLI adapter and output mapping.
6. Run migration, unit, failure-injection, concurrency, disposable integration, CLI parity/redaction, and full project gates before push.

Rollback of application code is safe for manifests because new keys are additive, but an older binary may reject the migrated schema or ignore the added column depending on its version. Database migration is therefore forward-only; rollback means restoring a pre-migration catalog backup or deploying a compatibility patch that tolerates the extra nullable column. The migration itself never rewrites existing backup data.

## Open Questions

None. The implementation choices and legacy-unknown policy are fixed by the delta specs.
