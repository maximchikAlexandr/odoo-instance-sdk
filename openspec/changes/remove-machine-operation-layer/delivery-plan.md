## Delivery contract

- `change_id=remove-machine-operation-layer`
- `graph_revision=3`
- `planning_base_sha=0e0ccedd415af31bd9c4ecae67045fdbf1c54cad`
- `implementation_parent=MYL-432`
- `feature_branch=agent/planner/7abc23a82502`
- `delivery_mode=single_wp_no_dag`
- `estimate_source=MYL-430 custom properties: Estimate, hours; Estimate min, hours; Estimate max, hours`
- `estimate_basis=remaining active developer effort from the published graph-3 planning base to the reduced acceptance criteria; retained implementation and external waiting are not re-counted`
- `estimate_confidence=medium-high; machine-only paths and direct shared seams are inspectable, with bounded uncertainty in mixed CLI inventory/output files`
- `calibration=uncalibrated; no comparable completed deletion-only timing history was supplied`
- `compatibility_policy=local make compat is forbidden; compatibility runs only in GitHub CI`

The archived graph-1 package remains immutable. This active change is authoritative for graph revision 3.

## E2E decision and verification mapping

`e2e_required=false`. The remaining diff deletes machine-only production/CLI/provider/consumer surfaces and directly related artifacts. It does not change the retained monitor snapshot payload, database reconciliation behavior, persistence/migration state, or visible ordinary CLI/UI output. No unique cross-component failure remains that requires a new local Moritora or database lifecycle walkthrough; existing targeted tests and required CI exercise every affected boundary more directly.

| Case | Affected requirement / surface | Local E2E decision | Existing or focused replacement | Assertions | Images / artifacts |
|---|---|---|---|---|---|
| G3-V01 | Remove registry/export/invoke/session/provider | Lower-level checks sufficient | Operation tests removed; existing CLI surface, architecture, documentation, packaging, and import checks updated | Removed modules, entrypoints, exports, commands, provider metadata, fixtures, and stale docs are absent | No images; ordinary test/CI results only |
| G3-V02 | Restore test-owned CLI traceability | Lower-level checks sufficient | `tests/unit/test_cli_output_modes.py`, CLI characterization, real-Odoo contract/matrix generator checks | Registered leaves equal one test-owned inventory; removed leaves are absent; every row keeps disposition/rationale | Generated matrix only when produced by the existing repository check |
| G3-V03 | Preserve shared CLI/SDK boundaries | Lower-level checks sufficient | Focused CLI output, context, security, lazy export, alias, and integrated-boundary tests | Ordinary output modes, dry-run/native transport, errors, redaction, ownership, approval, interruption, rollback, and cleanup remain | No images because visible ordinary output is unchanged |
| G3-V04 | Preserve existing monitor/database/recovery work | Lower-level checks sufficient | Run only monitor/database/recovery tests directly affected by removed imports or shared assertions; required CI supplies repository regression | Retained payloads, schema/migration bytes, reconciliation and recovery behavior are unchanged | No new scripts, fixtures, MR, diagnostics, or captures |

The E2E decision must be revised before implementation continues if the reduced diff changes a retained public payload, persistence/migration format, real external integration, or visible ordinary CLI/UI output.

## CI and parent review gates

The Implementer SHALL use existing repository checks and CI. No new CI workflow or evidence framework is planned. Focused checks cover removed imports/registration/exports, exhaustive CLI characterization, packaging, docs/architecture, shared output/security boundaries, and only directly coupled monitor/database/recovery seams. Required GitHub CI remains authoritative for repository-wide tests, package/dashboard/generated-file checks, real-Odoo smoke where already required, and Ubuntu/macOS compatibility. Local `make compat` remains forbidden.

After the single WP publishes one integrated candidate and required CI succeeds, Manager SHALL dispatch the standard four-profile review on the implementation parent. Review is an acceptance gate outside the WP, not new implementation effort. Any product fix changes the candidate SHA and requires applicable CI and review again. No Tester gate follows while `e2e_required=false`.

## Estimate evidence and topology decision

Exact estimate totals exist only in the MYL-430 custom properties. The estimate covers remaining deletion, direct shared-seam repair, focused affected tests, required validation/CI preparation, and likely bounded review fixes. It excludes already implemented retained features, broad requalification, external wait time, Moritora/DB live E2E, test-repository orchestration, and speculative replacement mechanisms.

The authoritative weighted estimate is at or below the topology threshold. Therefore the only valid topology is `single_wp_no_dag`: exactly one all-covering WP, no `stage`, no `depends_on`, no parallel frontier, and no DAG orchestration.

## WP-01 — Remove the machine layer and publish the reduced candidate

- `wp_id=WP-01-remove-machine-layer`
- `task_coverage=1.1,1.2,1.3,2.1,2.2,2.3,3.1,3.2,3.3`

Deliverable: one published graph-3 candidate that removes the complete machine-operation/session/provider/consumer surface, preserves existing useful monitor/database/recovery behavior, and passes focused affected checks plus required CI.

Owned responsibility scope:

- Machine-only production modules, CLI entrypoints/registration, operation context/exports/exceptions, provider packaging, consumer proof, fixtures/tests/docs/spec references, and generated command-matrix rows.
- Directly coupled shared CLI context/output, root exports, architecture/documentation inventories, packaging and characterization tests.
- Existing related tests, fixtures, snapshots, docs, and generated artifacts only where the deletion directly requires an update.

Critical shared files include `AGENTS.md`, `README.md`, `docs/execution-boundary.md`, `docs/python-sdk.md`, `src/odoo_instance_sdk/__init__.py`, CLI registration/context/output and exceptions, `tests/unit/test_cli_output_modes.py`, CLI/architecture/documentation/packaging tests, real-Odoo matrix/contracts, and affected main specs.

Contract surface:

- Production exposes no operation registry, stable IDs, contract export, invoke/session, provider API, or second CLI table.
- Ordinary SDK/CLI commands retain existing Rich/JSON/TOON, dry-run, native transport, exit, security, ownership, approval, interruption, rollback, and cleanup behavior.
- Monitor/database/backup/catalogue/recovery implementations, schema/migration bytes, OpenAPI, and generated web types remain unchanged unless a direct removed import requires the smallest repair.
- Archived graph-1 artifacts and old evidence remain historical only.

Definition of done and evidence:

- Removed paths/symbols/entrypoints/exports/fixtures/docs/spec claims and three machine CLI leaves are absent; no replacement abstraction or inventory is added.
- The test-owned CLI matrix is exhaustive and its existing generated projection is current.
- Focused directly affected tests and required repository checks pass; required GitHub CI is successful on one exact published candidate.
- Diff review confirms no unrelated retained monitor/database/recovery implementation, data schema, migration, OpenAPI/web generated surface, CI workflow, or new test-repository artifact changed.

Parallel safety: not applicable. This is the sole all-covering WP and has no sibling, stage, dependency, frontier, or DAG.

## Coverage audit

Tasks `1.1–3.3` are covered exactly once by `WP-01-remove-machine-layer`. Standard parent review follows successful CI outside the WP. There is no E2E gate. Any product-scope expansion or retained-behavior change requires a new planning revision.
