## 1. Source contract and archive capture

- [x] 1.1 Add the frozen public `LocalArchiveRestoreSource(path: str)` model, lazy export, and strict public-import/type characterization without adding a public resource method.
- [x] 1.2 Extend the environment/coordinator restore-source annotations and coercion with one unambiguous local-archive variant while preserving remote and UUID compatibility.
- [x] 1.3 Refactor the existing Odoo ZIP validation into shared catalogue/local evidence capture that records manifest database name, safe bounded members, file identity, size, and SHA-256 without writing files.
- [x] 1.4 Implement project-owned exclusive mode-0600 snapshot materialization, identity/digest revalidation, snapshot-only consumption, and primary-error-preserving cleanup for success, failure, and cancellation.

## 2. Source-neutral restore provenance

- [ ] 2.1 Add the Alembic catalogue migration for constrained `catalogue` and `local_archive` provenance on `restores` and `database_events`, including deterministic existing-row backfill and lossless-only downgrade.
- [ ] 2.2 Update catalogue schema models and atomic restore/event writers to accept catalogue UUID evidence or local archive digest evidence and reject incomplete or mixed forms.
- [ ] 2.3 Update restore-binding, inventory, and catalogue-only backup readers for nullable backup IDs and source-neutral provenance without changing existing UUID projections.
- [ ] 2.4 Update guarded database/filestore removal ownership checks so exact active cluster and contained data-directory evidence remains authoritative for local archive rows and all unknown/mismatched cases fail closed.

## 3. Common preparation and restore flow

- [x] 3.1 Capture local archive evidence and source-derived default target during `refresh_database_command()` construction, adding honest validate/snapshot/cleanup `ActionStep` values to the immutable plan.
- [x] 3.2 Extend preparation preflight and execution to route the verified local snapshot through the existing auxiliary runtime, restore, neutralization, postcondition, optional admin reset, progress, failure-retention, and default-switch stages.
- [x] 3.3 Share the private Database Manager restore transport between catalogue `Backup` and local snapshot evidence, retaining catalogue locks/identity checks only for catalogue sources and recording the correct atomic provenance after success.
- [x] 3.4 Keep local archive results and interruption/failure contexts JSON-safe and path-redacted, with `DatabasePreparationResult.backup=None` and no backup catalogue insert.

## 4. CLI and documentation

- [ ] 4.1 Add optional positional `BACKUP_UUID` plus `--file PATH` to `db restore`, validate UUID-only/file-only/both/neither and catalogue-only `--replace` before context mutation, and delegate file selection through `LocalArchiveRestoreSource`.
- [ ] 4.2 Preserve confirmation, `--yes`, `--no-input`, `--dry-run`, output formats, target, reset, progress, exit-code, and single `PUBLIC_LEAF_CASES` contracts for both sources.
- [ ] 4.3 Update CLI help, Python SDK documentation, and execution-boundary/real-Odoo checked projections only where generated contract output actually changes.

## 5. Verification

- [ ] 5.1 Add one parametrized public CLI source-selection regression covering UUID-only, file-only, both, neither, and `--file --replace`, including early failure and SDK delegation assertions.
- [x] 5.2 Add focused archive tests for missing, unreadable, non-regular, symlink, invalid/incompatible/unsafe ZIP, insufficient space, changed-after-selection, snapshot-only reads, path redaction, source preservation, and staging cleanup.
- [x] 5.3 Add a restore-pipeline integration test proving a valid local Odoo ZIP restores `dump.sql` and filestore through the guarded stages with no backup row and with confirmed target/default behavior.
- [ ] 5.4 Add migration/catalogue/ownership tests for existing-row upgrade, constraints, atomic paired provenance writes, nullable readers, local owned cleanup authorization, and fail-closed external/mismatched evidence.
- [ ] 5.5 Run focused tests plus repository formatting, lint, strict typing, architecture/public-method inventories, OpenSpec strict validation, and the full non-real-Odoo test suite; record any environment-only exclusions.
