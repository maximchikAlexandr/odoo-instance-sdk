## Context

Graph revision 3 starts from published PR #137 head `0e0ccedd415af31bd9c4ecae67045fdbf1c54cad` over base `9e388b8f1d7ac1d1907922e07493a267007f8cfb`. The desired product diff is narrower than graph 2: delete the machine-only operation/session/provider/consumer layer and keep the monitor, database, and recovery work already implemented in the branch.

The machine layer is concentrated in `src/odoo_instance_sdk/operations/`, `commands/contract.py`, `commands/operation.py`, operation-specific context/exports/exceptions/registration, provider fixtures and entry-point metadata, operation-only tests, machine documentation, and three generated CLI rows. Shared files must be edited only where they import, register, export, describe, or assert that surface.

The retained code includes snapshot-v3 selection/observation and CPU semantics, database observation and explicit reconciliation, inert backup reads, `0008_replacement_recovery`, and structured replacement recovery. Those implementations are not rebuilt or broadly re-certified by this change.

## Goals / Non-Goals

**Goals:**

- Delete the machine layer as one coherent surface, including direct callers, tests, fixtures, configuration, exports, docs, and specs.
- Restore test-owned exhaustive CLI characterization and preserve ordinary SDK/CLI output and execution behavior.
- Protect data, security, and error-handling boundaries touched by shared-file edits with focused existing tests and required CI.
- Finish the remaining work with the smallest materializable implementation unit.

**Non-Goals:**

- Reimplementing or fully re-certifying retained monitor/database/recovery features.
- New Moritora compatibility work, live database E2E, test-repository branches/MRs, scripts, fixtures, manifests, or evidence tooling.
- A replacement registry, adapter, abstraction, provider mechanism, daemon, Supervisor integration, or future-consumer hook.
- Changes to retained schemas, migrations, data formats, ownership rules, redaction, rollback, interruption, or cleanup semantics.

## Decisions

### D1. Delete machine-only paths before repairing shared seams

Remove `operations/`, the contract/operation command modules, their Click registrations, operation-only context and public exports, provider packaging, provider fixtures, operation tests, machine docs, and machine matrix rows. Then use import/reference failures to identify the smallest shared-file repairs. Do not refactor retained code while deleting.

Alternative: classify and redesign every file changed by graph 1 before editing. Rejected because the current tree already exposes a concentrated machine-only surface and the owner requested remaining work, not a second implementation design.

### D2. Preserve retained implementations byte-for-byte unless deletion breaks a direct seam

Monitor, database, backup, catalogue, migration, and recovery modules stay unchanged unless they directly import a removed symbol or a shared architecture/output assertion must be corrected. Existing focused tests for a touched seam are run; unrelated suites are left to normal required CI.

Alternative: repeat complete retained-scope qualification and live walkthroughs. Rejected because those features are already implemented and the deletion does not change their public behavior, storage schema, or external protocol.

### D3. Restore test-owned CLI inventory without a replacement authority

`tests/unit/test_cli_output_modes.py` again owns `PublicLeafCase`/`PUBLIC_LEAF_CASES`. Direct CLI registration is the production surface. Characterization and command-matrix checks must reject missing or extra public leaves, but production must not import tests or keep another inventory.

Alternative: preserve production descriptors for drift detection. Rejected because this retains the speculative layer under another name.

### D4. Use existing checks; local live E2E is not required

`e2e_required=false`. The remaining change removes public machine-only commands and internal support code; it does not modify the retained snapshot payload, database mutation semantics, persistence, or visible ordinary CLI output. Existing unit/integration/packaging/architecture tests can prove registration, imports, exports, inventory completeness, output/error boundaries, and retained shared seams. Required CI supplies platform and repository-wide regression coverage, followed by the standard four-profile implementation-parent review.

Moritora compatibility and a real database disappearance do not uniquely exercise the deletion and would test unchanged retained behavior. No new test-repository orchestration or evidence artifacts are justified. If implementation changes a retained public payload, storage/migration behavior, or visible ordinary output, this decision becomes invalid and planning must be revised.

### D5. Ponytail full gate

No new subsystem is introduced. Deletion and existing tests are sufficient.

| Candidate work | Need for desired diff | Existing alternative | Decision |
|---|---|---|---|
| Replacement operation registry/adapters | No consumer remains | Direct SDK and JSON CLI | Drop as YAGNI |
| Full monitor/database/recovery requalification | Retained code is already implemented | Target only directly touched seams plus required CI | Drop as YAGNI |
| Moritora and live DB E2E | No unique risk from deletion | Existing monitor/schema/dashboard and database/recovery tests | Drop as YAGNI |
| Test-repository branches/MRs/scripts/manifests | No local E2E remains | Repository tests and CI | Drop as YAGNI |
| New forbidden-symbol/evidence framework | Existing searches and architecture tests suffice | Extend an existing assertion only if needed | Drop as YAGNI |
| New recovery/reconciliation abstraction | No retained behavior changes | Existing implementation | Drop as YAGNI |

## Boundaries that must not be cut

- **Callers and exports:** ordinary CLI registration, root lazy exports, SDK primitives, command context, and non-machine exceptions must remain importable.
- **Tests and fixtures:** delete operation-only fixtures/tests; retain or minimally adjust shared CLI characterization, architecture, documentation, packaging, and output tests.
- **Configuration:** remove only the operation-provider entry-point fixture/metadata; retain package extras, dashboard configuration, and existing CI workflows.
- **Data:** keep catalogue schema, migration `0008_replacement_recovery`, historical rows/events, recovery bytes, and OpenAPI/web snapshot types unchanged.
- **Security and errors:** preserve sanitization, secret-free diagnostics, resource ownership, approval for ordinary mutations, exit mappings, interruption, rollback, and cleanup.

## Risks / Trade-offs

- **A shared file mixes machine and ordinary behavior** → Remove only operation imports/branches and run its focused characterization/output/security tests.
- **A stale export or registration survives** → Check root exports, Click tree, package installation, docs, and architecture inventories after deletion.
- **A retained import is accidentally removed** → Run focused monitor/database/recovery tests only when their files or imports are touched, then rely on required CI for repository-wide regression.
- **No local live E2E misses a real behavior change** → The implementation must stop and request a planning revision if it changes retained payloads, persistence, migrations, or visible ordinary output.

## Migration Plan

1. Delete machine-only production modules, entrypoints, exports, provider metadata, fixtures, tests, docs, and spec references.
2. Restore the test-owned CLI matrix and regenerate its existing command-matrix projection without the removed leaves.
3. Repair only direct shared imports, registrations, output/exception branches, architecture inventories, and documentation/packaging assertions.
4. Run focused affected tests, required repository checks and GitHub CI; publish one exact candidate for the standard parent review.

Rollback is a normal revert of the implementation commit(s). No retained database schema or recovery state is removed.

## Open Questions

None. A discovered dependency that requires changing retained public behavior is a planning blocker, not permission to expand this change.
