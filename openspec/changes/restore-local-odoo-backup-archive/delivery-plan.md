## Основание планирования

- Task: MYL-273 / GitHub #91.
- Change: `restore-local-odoo-backup-archive`.
- Current planning base: `origin/main` at `3d688b26b463d273e80fa46500226158d7d9fab1`; the rebase preserves the agreed product scope and evidence-backed estimate properties.
- Authoritative estimate: issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`. The weighted property is above the multi-package threshold.
- Executor basis: active engineering hours for one experienced developer familiar with Python, Click, msgspec, SQLite/Alembic, and this repository, without AI acceleration. Unattended CI, review queues, meetings, and external blocking are excluded.
- Confidence: medium. CLI and preparation paths, reusable snapshot validation, provenance readers, ownership gates, and test seams were inspected, but this repository currently has only the initial Alembic revision and the source-neutral table rebuild is a material bounded uncertainty.
- Calibration: evidence-based and historically uncalibrated; no comparable elapsed-duration benchmark was available. Tests were not run for estimation.
- Evidence: `commands/db.py`, `internal/dbprep/{source,source_binding,materialize,materialize_steps}.py`, `resources/database/backup_restore_parts/backup.py`, `storage/{catalog_schema,catalog/backup,catalog_migrations}.py`, `internal/pg/{inventory,drop}.py`, focused CLI/preparation/replacement tests, current main specs, and local history for Alembic adoption and restore hardening.
- Assumptions: scope remains limited to supported Odoo ZIP with dump and filestore; `--file --replace`, native dump, remote URL, conversion, and catalogue import stay excluded; the current auxiliary restore and output contracts remain reusable.

The delivery topology uses two independent foundation packages at the first execution frontier. They converge into one preparation/restore integration package, followed by CLI delivery and final repository verification. No WIP limit is encoded in the DAG.

## Этап 1 — независимые foundations

### WP-01 — Typed local source and immutable archive capture

- Task coverage: `1.1`, `1.2`, `1.3`, `1.4`, and `5.2`, each covered exactly once.
- Deliverable: a public frozen local-archive source and one reusable, mutation-free capture plus execution-time verified-snapshot primitive with complete security/cleanup regression evidence.
- Direct `depends_on`: none.
- Owned responsibility scope: public backup/source models and exports; restore-source coercion contract; Odoo ZIP evidence capture, no-follow identity/hash verification, private snapshot materialization, staging cleanup; directly related unit fixtures and security tests. Critical shared files are the backup model/export surface and `internal/dbprep/source.py`; WP-03 consumes these contracts but does not redefine them.
- Contract surface: `LocalArchiveRestoreSource(path: str)`; private captured archive evidence; source union/coercion; stable file identity and SHA-256; bounded ZIP/dump/filestore validation; sanitized cleanup semantics.
- Definition of done / evidence: public import/type characterization passes; catalogue capture regressions stay green; focused tests cover every invalid source class, insufficient space, swap/change after selection, snapshot-only consumption, source preservation, redaction, and success/failure/cancellation cleanup; no public resource method or dependency is added.
- Parallel-safety rationale: this package owns model/source-capture code and tests, while WP-02 owns catalogue schema/provenance and PostgreSQL ownership readers. Their write zones and independently testable contracts do not overlap.
- Stage / topological level: stage 1 / level 1.

### WP-02 — Source-neutral provenance and ownership migration

- Task coverage: `2.1`, `2.2`, `2.3`, `2.4`, and `5.4`, each covered exactly once.
- Deliverable: a migrated, constrained restore provenance model that represents catalogue and local-archive sources atomically and preserves inventory/drop safety without a fake backup row.
- Direct `depends_on`: none.
- Owned responsibility scope: Alembic catalogue revision, declarative catalogue schema, restore/event write and binding read paths, database inventory projection, guarded PostgreSQL/filestore removal ownership logic, and directly related migration/catalogue/drop tests and fixtures. Critical shared files are `catalog_schema.py`, the new migration, `storage/catalog/backup.py`, and `internal/pg/{inventory,drop}.py`; WP-03 calls the completed writer contract only.
- Contract surface: constrained `(source_kind, backup_id, source_sha256)` evidence; atomic restore/restored-event writes; nullable backup binding projections; exact cluster/data-directory ownership gate; lossless-only downgrade.
- Definition of done / evidence: current catalogue fixtures upgrade with row counts and UUID behavior preserved; invalid mixed evidence rolls back atomically; local rows contain no path or backup row; catalogue-only queries still return retained backups; source-neutral inventory works; owned local provenance authorizes guarded cleanup and null/mismatched evidence fails closed.
- Parallel-safety rationale: schema/readers/ownership code is independent of the archive capture implementation and public model owned by WP-01. The packages meet only through the documented provenance arguments consumed later by WP-03.
- Stage / topological level: stage 1 / level 1.

## Этап 2 — вертикальная интеграция

### WP-03 — Common local-archive preparation and restore flow

- Task coverage: `3.1`, `3.2`, `3.3`, `3.4`, and `5.3`, each covered exactly once.
- Deliverable: `EnvironmentResource.refresh_database_command()` restores a caller-owned valid Odoo ZIP through the existing guarded flow, emits a truthful immutable plan, records source-neutral provenance, and returns a path-redacted result without a `Backup`.
- Direct `depends_on`: `WP-01`, `WP-02`.
- Owned responsibility scope: environment/coordinator command construction; preparation action/process plans; source-aware preflight and target derivation; common private Database Manager transport; auxiliary runtime attachment; failure context/result projection; direct local-archive pipeline integration tests and fixtures. Critical shared files are `resources/environment/checkout.py`, `internal/dbprep/{materialize,materialize_steps,source_binding}.py`, and the database restore implementation; upstream source/provenance contracts are consumed as-is.
- Contract surface: immutable `Command` plan; validate/snapshot/cleanup `ActionStep` ordering; existing target reservation, neutralization, postcondition, reset, progress, failure retention, and default switch; `DatabasePreparationResult.backup=None`; atomic local provenance call.
- Definition of done / evidence: dry-run produces the bounded redacted plan without staging or mutation; valid ZIP integration restores dump and filestore through existing stages, confirms target/default, preserves source, removes staging, writes no backup row, and writes exact restore provenance; UUID and replacement regression suites remain unchanged.
- Parallel-safety rationale: integration begins only after both foundation contracts are complete. Keeping command planning, restore transport, and vertical integration evidence together avoids competing edits across the same restore pipeline.
- Stage / topological level: stage 2 / level 2.

## Этап 3 — пользовательский CLI-контракт

### WP-04 — CLI source selection and user-facing contract

- Task coverage: `4.1`, `4.2`, `4.3`, and `5.1`, each covered exactly once.
- Deliverable: the existing `odcli db restore` accepts exactly one UUID or `--file`, preserves all output/confirmation options, rejects unsupported combinations early, and documents the SDK/CLI behavior without a second leaf.
- Direct `depends_on`: `WP-03`.
- Owned responsibility scope: `commands/db.py` restore parsing/delegation/confirmation/failure context; public leaf characterization and parametrized CLI tests; CLI help and relevant SDK/execution documentation; generated real-Odoo projection only if its checked source changes. Critical shared files are the restore Click callback and `tests/unit/test_cli_database.py`; preparation internals remain owned by WP-03.
- Contract surface: optional positional UUID; `--file PATH`; exact-one validation; catalogue-only `--replace`; `--target`, reset, `--yes`, `--no-input`, dry-run, Rich/JSON/TOON, progress and exit-code compatibility; unchanged `PUBLIC_LEAF_CASES` identity.
- Definition of done / evidence: one parameterized public CLI matrix covers UUID-only, file-only, both, neither, and file-plus-replace; invalid combinations fail before context access; file-only delegates the typed source; machine confirmation and dry-run remain exact; help/docs contain no unsupported workflow; output contains no source path.
- Parallel-safety rationale: this package follows the completed SDK flow and owns the single CLI callback/test surface; splitting CLI and its contract tests would create an overlapping write zone without an independent deliverable.
- Stage / topological level: stage 3 / level 3.

## Этап 4 — delivery gate

### WP-05 — Repository-wide verification and handoff evidence

- Task coverage: `5.5`, covered exactly once.
- Deliverable: reproducible final verification evidence for the complete change, with all task checkboxes reconciled and no unrelated implementation or architecture regression.
- Direct `depends_on`: `WP-04`.
- Owned responsibility scope: OpenSpec task reconciliation and verification-only fixes directly caused by the completed change; focused and full non-real-Odoo tests; formatting, lint, strict typing, architecture/public-method inventories, generated contract checks, and strict OpenSpec validation. Production ownership remains with the predecessor package that introduced a failing behavior; WP-05 may apply only narrowly related verification repairs, fixtures, snapshots, docs, or service files not owned by an active sibling.
- Contract surface: repository quality gates and the aggregate acceptance evidence defined by the four delta specs; no new product behavior.
- Definition of done / evidence: all covered tasks are checked only after their evidence passes; focused suites, catalogue migration checks, formatter/linter, mypy, architecture/public API inventories, strict OpenSpec validation, and full non-real-Odoo tests succeed; environment-only exclusions are recorded precisely; final diff is scoped and clean of generated visual reports.
- Parallel-safety rationale: this is the convergence gate after CLI delivery; running it earlier would verify an incomplete graph and risk duplicate repair ownership.
- Stage / topological level: stage 4 / level 4.

## Coverage и frontier proof

| Work package | OpenSpec tasks | Direct dependencies | Level |
| --- | --- | --- | --- |
| `WP-01` | `1.1`–`1.4`, `5.2` | none | 1 |
| `WP-02` | `2.1`–`2.4`, `5.4` | none | 1 |
| `WP-03` | `3.1`–`3.4`, `5.3` | `WP-01`, `WP-02` | 2 |
| `WP-04` | `4.1`–`4.3`, `5.1` | `WP-03` | 3 |
| `WP-05` | `5.5` | `WP-04` | 4 |

Every task in `tasks.md` appears exactly once. Level 1 contains two simultaneously executable packages with disjoint critical write zones; later levels converge through direct dependencies only. Shared contracts are established by `WP-01` and `WP-02` before `WP-03` integrates them.
