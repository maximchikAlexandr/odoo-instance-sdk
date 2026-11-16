# Tasks

## 1. Catalogue strategy columns

- [x] 1.1 Add revision `0016` and schema columns `db_copy_strategy` and `target_filestore_path`, backfill existing COPY rows to `backup`, and verify the migration test preserves shared and backup rows and refuses downgrade while a template row exists.
- [x] 1.2 Persist and read the new columns on environment create and `_row_to_env`, and verify a round-trip unit test returns strategy and path.

## 2. Template checkout contract

- [x] 2.1 Add `DatabaseCopyStrategy` and the checkout option, plan, and environment fields with default `backup`, and verify the default options test still passes with strategy `backup`.
- [x] 2.2 Add `--db-copy-strategy` to `env checkout` / `env create` and verify a CLI help or parser test accepts `backup` and `template` and defaults to `backup`.
- [x] 2.3 Implement template preflight, captured psql clone steps, isolated filestore publication, neutralization marker check, and recorded-target cleanup in the new template module, and verify quoting, containment, active-session, dry-run, marker, and cleanup tests pass.
- [x] 2.4 Keep strategy `backup` on the existing restore path and verify an existing COPY checkout unit test still passes.

## 3. Fail-closed filestore research

- [x] 3.1 Record the failed shared-filestore research decision and reject a shared filestore request before mutation, and verify the rejection test plus the absence of a pool API.

## 4. Integration check

- [x] 4.1 Run the focused template, migration, and checkout unit tests and `openspec validate --change accelerate-checkout-template-clone --strict`, and verify both succeed.
