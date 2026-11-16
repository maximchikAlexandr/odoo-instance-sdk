# Proposal

## Why

Local COPY checkout still builds a ZIP backup and restores it through the Odoo database API. On a large database that path is much slower than a same-cluster PostgreSQL `TEMPLATE` clone plus a separate filestore copy. Issue #4 asks for that opt-in fast path, and for a fail-closed decision on backup-scoped shared filestore until its research gate can be proven.

## What Changes

- Add an explicit COPY strategy `template` beside the existing default `backup`. `backup` keeps today's ZIP, restore, neutralization, and ownership behavior.
- `template` is allowed only when source and target are safe names on one proven local PostgreSQL cluster. It clones with quoted `CREATE DATABASE ... TEMPLATE ...`, copies the filestore with `shutil.copytree` into a temporary sibling and an atomic rename, then neutralizes through the existing Odoo shell and accepts `ready` only after `database.is_neutralized=true`.
- Record `db_copy_strategy` and the exact owned `target_filestore_path` in catalogue revision `0016` (current head is `0015`; the historical v3/v4 numbers in the issue are not the live schema). Existing COPY rows become `backup` with an empty template path.
- Failure and `env remove` delete only the recorded target database and recorded filestore path. Source database and source filestore stay untouched. Active source sessions fail the clone and are not terminated.
- Template clones have no backup id and cannot enter a shared filestore pool.
- Direction B does not ship. The shared-filestore research gate requires disposable dual-Odoo proof on Linux and macOS, including targeted `_gc_file_store` suppression. That proof is not available here, so shared mode stays unimplemented and isolated filestore remains the only production mode.

## Capabilities

### New Capabilities

- `checkout-template-clone`: opt-in same-cluster PostgreSQL template checkout, isolated filestore copy, neutralization marker, and recorded-target cleanup.
- `backup-filestore-sharing`: fail-closed research decision that keeps filestore isolated and rejects a production shared pool.

### Modified Capabilities

- `development-environment`: COPY checkout options gain `db_copy_strategy`; default COPY still owns a backup, while template COPY owns a target database and filestore path with `backup_id` empty.
- `cli-odcli`: `odcli env checkout` / `env create` accept `--db-copy-strategy backup|template`, default `backup`.

## Impact

- Public SDK: `EnvironmentCheckoutOptions`, `EnvironmentCheckoutPlan`, and `DevelopmentEnvironment` gain strategy and, for environments, the recorded filestore path. `InstanceFactory.from_environment()` stays ready-only.
- Catalogue migration `0015` → `0016`, environment insert/read, checkout planning, execution, and COPY removal.
- No new database driver, daemon, scheduler, or second `psql` helper. Template maintenance SQL reuses `build_psql_specification`.
- Backup restore, shared mode, and remote/cross-cluster copy stay unchanged.
