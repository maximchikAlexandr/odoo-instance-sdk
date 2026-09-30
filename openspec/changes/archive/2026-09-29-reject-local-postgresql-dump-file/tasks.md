## 1. Typed Local-Format Policy

- [x] 1.1 Add path-redacted `BackupPolicyError` subclasses with stable codes `backup_unsupported_format` and `backup_unknown_format`, and add focused exception/transport assertions for their exact sanitized messages and details.
- [x] 1.2 Add a pure typed bounded-prefix classifier for recognized ZIP-family content, PostgreSQL custom dumps beginning with `PGDMP`, and unknown content; cover all outcomes and short/forged prefixes without filename or subprocess inputs.

## 2. Verified Local Restore Preflight

- [x] 2.1 Extend the no-follow verified-file evidence to retain only the classification prefix from the same descriptor used for SHA-256 and stable identity checks, preserving change-during-capture failures and path redaction.
- [x] 2.2 Apply the classifier only to `LocalArchiveRestoreSource` immediately after verified capture: continue recognized ZIP input through existing `validate_zip()`, raise the exact typed dump/unknown diagnostics before snapshots or effects, and leave catalogued `BackupFormat.DUMP` validation and restore behavior unchanged.
- [x] 2.3 Add a parameterized internal capture/preflight matrix for valid Odoo ZIP, `PGDMP` content, malformed ZIP-family content, and unknown bytes, asserting source preservation, no private snapshot, no `pg_restore`/database mutation, exact code/message, and absence of caller paths or content in errors.

## 3. Public Regression and Quality Gates

- [x] 3.1 Add a parameterized public `odcli db restore --file FILE --dry-run --format json` regression matrix for valid ZIP, PostgreSQL custom dump, malformed ZIP, and unknown bytes; assert the valid sanitized plan or exact typed failure and fail the test if rejected inputs reach any database/process/catalogue/default-switch effect.
- [x] 3.2 Run the focused backup-validation, database-preparation, and CLI database tests, then run repository lint, formatting, type-check, and full unit-test gates required by the project; repair regressions without broadening local native-dump support.
