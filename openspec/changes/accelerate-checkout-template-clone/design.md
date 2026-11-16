# Design

## Context

See proposal.md for motivation. Catalogue head on this branch is Alembic revision `0015`, not the historical v3/v4 names in issue #4. COPY checkout already plans one immutable command, restores through the Odoo database API, and records ownership in `environments` plus `environment_copy_journal`. `build_psql_specification` is the only production `psql` launcher. `checkout.py`, `checkout_artifacts.py`, and `cleanup.py` are at the 1000-line production limit, so template behavior has to live in a new module and enter those files through a short call.

## Goals / Non-Goals

**Goals:**

- Add strategy `template` as an opt-in branch of the existing checkout command snapshot.
- Keep strategy `backup` on the current restore steps and probes.
- Persist strategy and the exact filestore path in revision `0016`.
- Fail closed on direction B without a shared-filestore implementation.

**Non-Goals:**

- Remote or cross-cluster template copy, hardlinks, reflinks, or a shared writable filestore.
- A second psql helper, a database driver, a filestore service, or a daemon.
- Changing `InstanceFactory.from_environment()` away from ready-only.

## Decisions

### 1. New module owns the template plan and its execution

Template preflight, maintenance SQL, filestore publication, neutralization consumption, and recorded-path cleanup live in `resources/environment/checkout_template.py`. Checkout planning calls one validator. Step assembly calls one step builder. Execution calls one runner instead of `_do_copy_restore` when the strategy is `template`. Removal calls one cleanup planner when the row strategy is `template`.

Alternative: extend `settings.py` and `cleanup.py` in place. Rejected because both files are already at the production line limit and the backup path must stay untouched.

### 2. Maintenance SQL stays inside the captured psql specification

The template plan adds captured steps, all built with `build_psql_specification` against the maintenance database `postgres`:

- session count for the source, excluding the maintenance backend;
- `CREATE DATABASE` through `format('%I', :'source')` / `:'target'` and `\gexec`, with `-v ON_ERROR_STOP=1`;
- postcondition probe of cluster identity, source existence, and target existence;
- `DROP DATABASE` for the recorded target, skipped after success and consumed on failure or remove;
- neutralization marker read from `ir_config_parameter`.

Filestore publication is an in-process `PreparedAction`, not a shell command. The action copies with `shutil.copytree` into a sibling directory named from the environment id, then `os.rename`s it onto the contained target path. No hardlinks.

The Odoo shell step reuses `_build_shell_script_step` with a nonce derived from the environment id and the generated config path, so the checkout fingerprint does not gain a random secret file. The script calls `odoo.modules.neutralize.neutralize_database(env.cr)` with `commit=True`. A separate captured read must return the value `true` before finalize. The helper that binds the creating environment is private to the template module and refuses any state other than `creating` or any path that differs from the plan.

Alternative: neutralize through the database manager HTTP API. Rejected because a template clone has no backup ZIP and the issue requires the shell marker.

### 3. Template does not start the auxiliary restore runtime

`_command_from_snapshot` attaches the auxiliary Odoo session only for strategy `backup`. Template preflight uses the captured psql probes instead of the source HTTP database list. An existing target still raises `DatabaseAlreadyExistsError` before artefacts.

### 4. Revision 0016 adds nullable columns and backfills backup rows

`ALTER TABLE` adds `db_copy_strategy` and `target_filestore_path`. Upgrade sets `db_copy_strategy='backup'` where `db_mode='copy'`. Checks:

- strategy is null, `backup`, or `template`;
- `shared` rows have null strategy and null template path;
- `template` rows have null `backup_id`, a target database, and a non-empty path;
- non-template rows have a null template path.

`backup_id` stays nullable for in-progress backup COPY rows. Ready backup ownership remains an application invariant, matching today's insert-then-update flow. Downgrade drops the columns only when no `template` row exists.

`create_environment` writes the new columns. `_row_to_env` reads them. `DevelopmentEnvironment` and `EnvironmentCheckoutPlan` gain the fields with defaults so existing constructors keep working.

### 5. Cleanup keys off the recorded path

Template rollback and `env remove` drop the journal target database with the captured or rebuilt maintenance `DROP` only when the catalogue name matches, and delete the filestore only when its resolved no-follow path equals `target_filestore_path`. Source paths are never passed to those operations. A template journal may use the existing `prepared` / `restored` / `dropped` stages with a null backup id; removal does not demand a backup row.

### 6. Direction B is a recorded failure, not a hidden implementation

`openspec/changes/accelerate-checkout-template-clone/research.md` records each gate experiment as not proven and the decision as fail. No `FilestoreMode`, pool table, status command, or GC command is added. A shared-mode request raises `ConfigError` before mutation. Template rows cannot satisfy a backup-id plus SHA-256 pool key because `backup_id` is null.

Alternative: implement the pool behind a flag and skip the Odoo tests. Rejected by the issue's fail-closed gate.

## Risks / Trade-offs

- [Template clone still needs a quiet source] → active sessions fail the plan; the product does not terminate them.
- [Filestore copy can be large even when the database clone is fast] → the action reports copied bytes and a missing source as a warning; it does not switch to links.
- [Neutralization depends on a real Odoo shell] → unit tests drive the captured steps with a fake process result; the marker check still fails closed when the value is not `true`.
- [SQLite table checks cannot express every ready-state backup rule] → migration checks cover the new columns; backup restore tests keep the existing journal behavior.
- [Shared filestore remains slow for repeated restores] → accepted until a later issue supplies the Linux and macOS gate.

## Migration Plan

1. Ship revision `0016` with the Alembic migration and the schema metadata together.
2. Existing catalogues upgrade in place; backup COPY rows gain strategy `backup` and a null path.
3. Rollback of the application requires downgrade to `0015`, which refuses if a template environment exists. Remove those environments first.
4. No data backfill beyond the strategy default. No process restart beyond the normal CLI.

## Open Questions

None. The historical v3/v4 schema numbers are resolved as revision `0015` → `0016`. The shared-filestore gate is resolved as fail.
