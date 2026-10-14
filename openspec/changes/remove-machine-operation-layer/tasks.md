## 1. Delete the machine-only surface

- [x] 1.1 Delete `src/odoo_instance_sdk/operations/`, `commands/contract.py`, `commands/operation.py`, their Click registrations, operation-only context and exception branches, root lazy exports, provider entry-point metadata, and consumer/Go proof without adding a replacement registry or adapter.
- [x] 1.2 Delete operation-only provider fixtures, packaging/unit/integration tests, machine documentation, and machine CLI traceability rows; remove their references from repository rules, README, SDK/execution docs, and architecture/documentation inventories.
- [x] 1.3 Remove the applied `machine-operation-contracts` main capability and machine additions to `cli-odcli` and `command-execution`, while keeping the archived graph-1 change immutable.

## 2. Repair only directly coupled shared seams

- [x] 2.1 Restore test-owned `PublicLeafCase`/`PUBLIC_LEAF_CASES` as the exhaustive public CLI characterization and generate the command matrix from it, rejecting missing/extra leaves and missing disposition/rationale without a production inventory.
- [x] 2.2 Repair only shared CLI registration, context, output, exception, export, packaging, documentation, and test assertions that directly referenced removed machine symbols; preserve ordinary Rich/JSON/TOON, dry-run, native transport, exit, sanitization, ownership, approval, interruption, rollback, and cleanup behavior.
- [x] 2.3 Confirm retained monitor/database/backup/catalogue/recovery code, migration `0008_replacement_recovery`, OpenAPI, and generated web types are unchanged except for a necessary direct import repair; any retained public behavior or data-format change requires a planning revision.

## 3. Verify and publish the reduced candidate

- [x] 3.1 Run the focused operation-removal, CLI characterization/surface/output/security, architecture, documentation, packaging, and any directly affected monitor/database/recovery tests; do not add a new test framework, script, fixture set, or live E2E.
- [x] 3.2 Run strict OpenSpec validation, format/Ruff, strict mypy, required repository tests/checks, generated-file checks, package checks, and `git diff --check`; resolve only regressions caused by the reduced diff.
- [x] 3.3 Publish one exact implementation-parent candidate and obtain required GitHub CI, including compatibility only in GitHub; record the exact SHA for the subsequent standard four-profile parent review.
