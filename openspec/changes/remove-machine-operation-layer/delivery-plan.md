## Delivery contract

- `change_id=remove-machine-operation-layer`
- `graph_revision=2`
- `planning_base_sha=67b30692bd21a81c5e0759937fb08b86c0d30540`
- `implementation_parent=MYL-432`
- `feature_branch=agent/planner/7abc23a82502`
- `estimate_source=MYL-430 custom properties: Estimate, hours; Estimate min, hours; Estimate max, hours`
- `estimate_basis=remaining active developer effort from the graph-1 published head to graph-2 acceptance; tests were not run to estimate`
- `estimate_confidence=medium; current code and consolidated commits are inspectable, while shared-file decoupling and external Moritora availability carry bounded uncertainty`
- `calibration=uncalibrated; no comparable completed scope-removal timing history was supplied`
- `evidence_policy=all graph-2 review, CI, and E2E evidence SHALL identify graph_revision=2 and the exact candidate SHA; graph-1 results are historical only`
- `compatibility_policy=local make compat is forbidden; Ubuntu/macOS compatibility SHALL run only in GitHub CI`

The archived graph-1 OpenSpec remains immutable. The active change and this delivery plan are authoritative for graph revision 2.

## E2E requirement and source mapping

`e2e_required=true` because one material compatibility result depends on a separately deployed Moritora consumer that repository contract tests cannot execute, and one retained mutation boundary is best proven across a real PostgreSQL/catalogue lifecycle. The current project description is the only authority for `TEST_REPO_URL`; the URL SHALL be resolved at execution time and SHALL NOT be committed to this public repository.

| Case | OpenSpec requirement/scenario | Verification gap / decision | Interface | Test-repository script | Independent agent steps | CI disposition | Assertions | Evidence |
|---|---|---|---|---|---|---|---|---|
| G2-E01 | `environment-monitor` / Existing Moritora consumes the additive snapshot | Required local E2E: embedded dashboard/OpenAPI tests cannot prove the current external Moritora decoder and renderer | Candidate `GET /api/v1/snapshot`; supplied current Moritora launch/consumer contract | WP-02 source branch/MR owns `e2e/MYL-432/graph2/check_moritora_snapshot_compat.py` and `e2e/MYL-432/graph2/moritora/**`; Tester consumes the WP-04 integration commit | In fresh context, verify product/build revisions, fetch a genuine candidate snapshot, start the supplied current Moritora build against that endpoint, inspect browser/consumer health, console, and existing project/environment rows without rerunning the automatic script | Local-only external-consumer case; `dashboard-tests`, `web-codegen-check`, HTTP/OpenAPI tests remain CI coverage for the embedded consumer | Candidate and consumer revisions match inputs; top-level schema/existing rows remain readable; additive observation is accepted; no decoder error, crash, blank state, lost row, secret, or private path | Sanitized snapshot, consumer/build provenance, launch command and exit, health/DOM assertions, browser console, cleanup log in Multica only |
| G2-E02 | `database-management` / Polling read remains inert; Confirmed missing database is reconciled | Required local E2E: lower-level tests cover branches, but a real database disappearance plus catalogue transaction proves the retained cross-component boundary | Public JSON CLI reads and direct public Python SDK observation/reconciliation on one dedicated numbered environment | WP-03 source branch/MR owns `e2e/MYL-432/graph2/check_database_reconciliation.py` and `e2e/MYL-432/graph2/database/**`; Tester consumes the WP-04 integration commit | In fresh context, validate recorded `odcli-N` identity and exact candidate, independently repeat list/exists/current and catalogue hashes, build and inspect the public reconciliation plan, run once, inspect event/state, run against a fresh observation again, and verify idempotence without invoking the automatic script | Real lifecycle is local-only; `offline-tests`, catalogue migration checks, and existing real-Odoo tiers cover deterministic negative/recovery paths in CI | Reads change no catalogue bytes/events; plan names only the case database; live absence and identity revalidate; first run appends exactly one dropped event; repeat appends none; foreign/inconclusive/reappeared state fails closed | Commands, exits, pre/plan/post projections, catalogue/event hashes, sanitized diagnostics, numbered create/inspect/remove records in Multica only |

Structured replacement recovery, legacy adoption, backup-read inertness, catalogue migration, and monitor section/CPU edge cases require no additional live case: their failure matrices and historical SQLite inputs are more deterministically exercised by the focused unit/integration and migration suites named in tasks 4.4 and 5.5. A live compensation fault injection would add destructive setup without a distinct public assertion.

## Test scripts, environment, and cleanup

WP-01 SHALL record one immutable `TEST_REPO_BASE_SHA` from `TEST_REPO_URL` for both stage-2 successors. WP-02 SHALL create only branch `wp/MYL-432-WP-02-moritora-e2e` and its source MR, writing only `e2e/MYL-432/graph2/check_moritora_snapshot_compat.py`, `e2e/MYL-432/graph2/moritora/**`, and MR metadata. WP-03 SHALL independently create only branch `wp/MYL-432-WP-03-database-e2e` and its source MR, writing only `e2e/MYL-432/graph2/check_database_reconciliation.py`, `e2e/MYL-432/graph2/database/**`, and MR metadata. Neither sibling may push to, rebase, merge, or update the other's branch/MR, and neither writes a shared graph-2 README, manifest, or integration branch.

After both source commits and MRs are immutable handoff inputs, WP-04 SHALL create `integration/MYL-432-graph2-e2e` from `TEST_REPO_BASE_SHA`, integrate those two exact commits, and open the single integration task MR. WP-04 SHALL verify that each source commit changes only its owned paths, both path sets are present byte-for-byte, no private input is committed, and applying the commits in either order yields the same tree. Only WP-04 may update the integration branch/MR. The parent-gate manifest SHALL record the base SHA, both source branch/MR/commit refs, integration branch/MR/commit ref, file links, and the tested product SHA.

Tester SHALL consume the reviewed WP-04 integration commit and SHALL NOT edit any source or integration branch/MR while executing the live gate. A result commit SHALL exist only if the tested function itself produces a repository-worthy functional artifact; diagnostic snapshots, browser output, catalogue projections, logs, hashes, and assertions are evidence and SHALL NOT be committed.

Both cases SHALL record `TESTED_PRODUCT_SHA`, `TEST_SCENARIO_SHA`, `REFERENCE_SHA`, candidate wheel digest, and graph revision before setup. G2-E01 additionally records an immutable `MORITORA_BUILD_REF` and the supplied launch contract. If that build or reproducible launch contract is absent, G2-E01 is blocked and graph-2 E2E cannot pass. Private hosts, credentials, user identities, and absolute local paths stay in execution inputs/evidence and are sanitized from public Git.

G2-E02 SHALL allocate a distinct numbered identity, checkout, database, interpreter, state root, port, and compose project. It SHALL use the recorded canonical launcher/interpreter and exact Odoo executable; it SHALL NOT use a reference checkout, shared database, or port. Signal/exception cleanup is installed before mutation. Cleanup stops only the exact recorded environment and delegates removal to `numbered_tool.py remove`, then proves launcher, checkout, environment, database, process, port, and owned volumes are absent.

## Independent checks and images

The independent agent SHALL perform the public/manual steps in the matrix from fresh context and SHALL NOT satisfy them by rerunning either automatic script. It SHALL compare every recorded identity and revision before acting and fail closed on mismatch.

`images_required=false`. Graph 2 deliberately removes a machine-only surface and preserves the current visible CLI and Moritora presentation; no changed table, log, progress, summary, or browser layout exists for a meaningful before/after pair. G2-E01 instead records actual DOM/row assertions and browser console output. If implementation changes visible Moritora or CLI output, planning must be revised before execution to add genuine before/after captures.

## CI, review, and publication gates

Implementer-owned graph-2 CI SHALL include strict OpenSpec, format/Ruff, strict typing, focused and full offline tests, Alembic/schema checks, HTTP/OpenAPI export, generated TypeScript check, dashboard tests, package/build, both real-Odoo smoke jobs, and GitHub-only Ubuntu/macOS compatibility. The source-boundary gate from task 6.1 SHALL run in offline/architecture coverage. No new CI framework is introduced.

After all WP results fan into one authoritative implementation-parent candidate and required GitHub CI succeeds on its exact SHA, Manager SHALL run two sequential parent gates. First, four independent profiles SHALL review the full graph-2 range: security, specification/standards, code quality, and tests/coverage. Any Blocker or Required finding returns to a bounded implementation-parent fix iteration and invalidates prior exact-SHA acceptance. Second, only after a clean consolidated review, Manager SHALL dispatch Tester-owned G2-E01/G2-E02 live E2E on the reviewed exact SHA. Reviewers and Tester are parent-gate actors, never WP-child assignees; every WP child is assigned through WP Delivery and ends before these gates.

An E2E finding returns to Manager with case IDs and evidence. Any product fix changes the candidate SHA and therefore requires fresh exact-SHA CI, four-profile review, and affected E2E before acceptance. Graph-1 results and pre-fix graph-2 results cannot satisfy a later candidate.

The final Tester comment in Multica SHALL link the WP-04 integration MR/commit/files and both source MR/commit refs, attach or link all execution-only evidence through the platform, and read the comment back to prove accessibility. It SHALL preserve all three branches, MRs, and commits, remove only run-owned resources, and SHALL NOT merge/close an MR or delete a branch without separate authorization.

## Estimate evidence and topology decision

The exact estimate totals are authoritative only in the MYL-430 custom properties named in the delivery contract. The weighted estimate property is above the multi-WP threshold. Confidence is medium and calibration is unavailable: the consolidated commits, current call sites, tests, and generated surfaces are inspectable, but machine/shared decoupling and availability of the current external Moritora build can expand repair work. The estimate covers scope census, machine removal, mixed test/doc cleanup, retained monitor and database/recovery verification, CI/review repair, and the two E2E cases. It excludes Supervisor integration, a replacement machine interface, external approval wait time, and provider outages.

The topology is `delivery_mode=dag`. Stage 2 contains two genuinely independent WPs after the shared removal foundation: monitor/Moritora work and database/recovery work have disjoint production paths and separate test-repository branches, MRs, and path ownership. WP-04 produces the single integrated product candidate, test-repository integration MR, and parent-gate handoff after their fan-in. Four-profile review and Tester-owned E2E are sequential implementation-parent gates outside the WP DAG.

## Dependency graph

Direct edges only:

```text
WP-01-machine-layer-removal -> WP-02-monitor-consumer-compatibility
WP-01-machine-layer-removal -> WP-03-database-recovery-retention
WP-02-monitor-consumer-compatibility -> WP-04-graph2-integration
WP-03-database-recovery-retention -> WP-04-graph2-integration
```

Authoritative stage mapping:

```text
WP-01-machine-layer-removal -> 1
WP-02-monitor-consumer-compatibility -> 2
WP-03-database-recovery-retention -> 2
WP-04-graph2-integration -> 3
```

Stages are consecutive topological layers, not WIP policy. Every predecessor has a lower stage than its dependent. Task coverage below is complete and non-overlapping.

## WP-01 — Machine-layer removal foundation

- `wp_id=WP-01-machine-layer-removal`
- `stage=1`
- `depends_on=[]`
- `task_coverage=1.1,1.2,1.3,1.4,2.1,2.2,2.3,2.4,3.1,3.2,3.3,3.4`

Deliverable: a reduced production/test/documentation/specification surface with no operation registry, export, invoke, session, provider, or consumer layer and a restored exhaustive test-owned CLI matrix.

Owned responsibility scope:

- Full path/symbol census and remove/retain/shared-decouple classification.
- Resolution and recording of the immutable `TEST_REPO_BASE_SHA` consumed by both stage-2 test-repository source branches.
- Deletion of `operations/`, machine CLI modules and registration, operation-only context/public exports/exceptions/output branches, provider metadata and fixtures, consumer/Go proof, and machine documentation.
- Restoration of test-owned CLI/E2E inventory and removal or rewrite of operation-only/mixed tests, matrices, architecture inventories, repository rules, README/SDK/execution docs, and main/delta contract references.
- Critical shared files include `AGENTS.md`, `README.md`, `docs/execution-boundary.md`, `docs/python-sdk.md`, `src/odoo_instance_sdk/__init__.py`, CLI registration/context/output modules, `tests/unit/test_cli_output_modes.py`, architecture/documentation tests, real-Odoo matrix/generator, and affected main specs.

Contract surface:

- Existing friendly CLI paths, SDK primitives, Rich/JSON/TOON envelopes, dry-run behavior, native transports, exit codes, redaction, and command execution remain.
- Production has no runtime operation table and no imports from tests; the test matrix is exhaustive evidence only.
- Archived graph-1 artifacts remain unchanged and never become graph-2 evidence.

Definition of done and evidence:

- Every graph-1 reference is classified once; forbidden files/symbols and machine leaves are absent.
- One accessible `TEST_REPO_BASE_SHA` is recorded before stage 2; both successor source branches prove that exact base.
- Test-owned CLI characterization remains complete and detects missing/extra leaves, ownership/classification drift, and missing E2E disposition/rationale.
- Mixed tests/docs/specs contain no stale operation claims and no retained behavior is removed by a mechanical revert.

Parallel safety: this predecessor exclusively owns all shared CLI/output/inventory/spec/documentation seams before stage-2 work begins. Successors consume the reduced boundary and do not edit WP-01 critical files except for a verified integration repair routed through WP-04.

## WP-02 — Monitor and consumer compatibility

- `wp_id=WP-02-monitor-consumer-compatibility`
- `stage=2`
- `depends_on=[WP-01-machine-layer-removal]`
- `task_coverage=4.1,4.2,4.3,4.4`

Deliverable: selective truthful snapshot behavior remains independent of the deleted machine layer, and embedded plus external consumer compatibility is executable and fail-closed.

Owned responsibility scope:

- Monitor DTOs, planning, collectors, process metrics, CLI/API monitor paths, snapshot HTTP schema, OpenAPI, generated TypeScript, embedded dashboard consumption, and directly related unit/integration/dashboard tests.
- Test-repository branch `wp/MYL-432-WP-02-moritora-e2e`, its source MR/commit, and only `e2e/MYL-432/graph2/check_moritora_snapshot_compat.py` plus `e2e/MYL-432/graph2/moritora/**`, without private host or credentials.
- Critical files include monitor models/resources/internal process metrics, `openapi.json`, generated web types, `web/src/App.tsx`, monitor/HTTP/codegen/dashboard tests, and monitor-specific docs.

Contract surface:

- Top-level snapshot schema and existing project/environment fields remain compatible; observation metadata is additive and truthful.
- Unselected sections perform no probes; unavailable sections are unknown/incomplete, never fabricated zero/false.
- External Moritora build/launch input is required evidence, not a new production adapter.

Definition of done and evidence:

- Snapshot-v2/v3, process identity/CPU, no-call, HTTP/OpenAPI/codegen, and embedded dashboard checks pass on graph 2.
- The compatibility script rejects revision mismatch and exposes an actionable blocker when the current external build is unavailable or rejects the candidate snapshot; its source MR/commit is published from the recorded common test-repository base and changes only the WP-02-owned paths.

Parallel safety: owns monitor/API/web generated surfaces plus its dedicated test-repository branch/MR and Moritora paths only. It does not edit database resources, catalogue migrations, replacement recovery, the WP-03 source branch/MR or database E2E paths, or the WP-04 integration branch/MR.

## WP-03 — Database and recovery retention

- `wp_id=WP-03-database-recovery-retention`
- `stage=2`
- `depends_on=[WP-01-machine-layer-removal]`
- `task_coverage=5.1,5.2,5.3,5.4,5.5`

Deliverable: inert database/backup reads, explicit exact reconciliation, and structured replacement recovery remain complete and independent after machine-layer deletion.

Owned responsibility scope:

- Database observation DTOs/resources/commands and explicit lifecycle reconciliation call sites.
- Backup observational queries, catalogue transaction/event behavior, structured replacement recovery codec/planning/validation, environment storage, Alembic migration/head, and directly related fixtures/tests/docs.
- Test-repository branch `wp/MYL-432-WP-03-database-e2e`, its source MR/commit, and only `e2e/MYL-432/graph2/check_database_reconciliation.py` plus `e2e/MYL-432/graph2/database/**`.
- Critical files include database backup/restore resource parts, catalogue environment/restore/schema/migration modules, `internal/dbreplace*`, `internal/pg` retained reconciliation paths, migration `0008_replacement_recovery.py`, and database/backup/recovery/migration tests.

Contract surface:

- Reads append no events or backfill state; explicit reconcile revalidates exact absence/identity and appends at most one idempotent event.
- Recovery evidence is secret-free structured state; new failures do not use human `last_error` as executable input.
- One catalogue and one Alembic head preserve existing rows, events, foreign keys, and recovery bytes.

Definition of done and evidence:

- Focused read/reconcile/backup/catalogue/migration/replacement/rollback/redaction suites pass from graph-2 code.
- No retained path imports operation registry/session/provider types, and all necessary startup/destructive/repair owners remain explicit.
- The database E2E source MR/commit is published from the recorded common test-repository base and changes only the WP-03-owned paths.

Parallel safety: owns database/catalogue/recovery files/tests plus its dedicated test-repository branch/MR and database E2E paths only. It does not edit monitor/API/web surfaces, the WP-02 source branch/MR or Moritora E2E paths, or the WP-04 integration branch/MR.

## WP-04 — Graph-2 integration and parent-gate handoff

- `wp_id=WP-04-graph2-integration`
- `stage=3`
- `depends_on=[WP-02-monitor-consumer-compatibility,WP-03-database-recovery-retention]`
- `task_coverage=6.1,6.2,6.3,6.4,6.5`

Deliverable: one clean published graph-2 implementation-parent candidate with forbidden-surface proof, retained-module coverage, all required GitHub CI, and a complete handoff manifest for the subsequent parent review and E2E gates.

Owned responsibility scope:

- Cross-subsystem architecture/forbidden-symbol gates, final docs/generated artifacts, coverage comparison, focused/full repository gates, package/build verification, exact-SHA publication, CI evidence, exclusive test-repository integration branch/MR ownership after fan-in, and an exact-SHA handoff manifest for the parent gates.
- Directly related tests, fixtures, snapshots, docs, support files, and generated code are included after stage-2 fan-in.

Contract surface:

- No machine layer reappears during integration; retained SDK/CLI/monitor/database/recovery behavior remains authoritative.
- Compatibility runs only in GitHub; graph-1 evidence cannot satisfy graph-2 review or CI.

Definition of done and evidence:

- Strict OpenSpec, format/lint/type, Alembic/schema, OpenAPI/codegen, dashboard, focused/full tests, package/build, real-Odoo smoke, GitHub-only compatibility, and `git diff --check` pass on one exact published SHA.
- The implementation parent has one published exact candidate with successful required CI; the test repository has one WP-04-owned integration MR/commit that reproduces both exact source commits from their common base with order-independent tree equality; all G2-E01/G2-E02 file, input-parameter, provenance, and cleanup refs are ready for independent parent-gate dispatch.

Parallel safety: fan-in only after both stage-2 WPs. WP-04 alone writes the product integration surface and `integration/MYL-432-graph2-e2e`; no sibling remains active in those write zones, and the two source branches/MRs stay immutable.

## Implementation-parent acceptance gates (outside the WP DAG)

1. `PARENT-GATE-REVIEW`: after WP-04 publishes the authoritative fully fanned-in candidate and required CI is successful, Manager dispatches the four independent review profiles on that exact implementation-parent SHA/range. The gate passes only with one consolidated result containing no unresolved Blocker or Required finding.
2. `PARENT-GATE-E2E`: only after `PARENT-GATE-REVIEW` passes, Manager dispatches Tester on the same reviewed SHA for G2-E01 and G2-E02. Tester uses the prepared scripts/fixtures, a dedicated numbered environment, fresh independent actions, Multica-only execution evidence with read-back, and exact-owned cleanup while preserving the test MR/branch/commits.

These gates have no `wp_id`, `stage`, `depends_on`, or OpenSpec task coverage. Manager owns their dispatch and any parent-level fix/review/retest loop. They SHALL NOT be materialized as children of WP Delivery, included in a WP definition of done, or satisfied by graph-1 evidence.

## Coverage and frontier audit

Tasks `1.1` through `6.5` are assigned exactly once to the four materializable WP children. Direct edges omit transitive dependencies. Stages 1–3 are consecutive; stage 2 is a valid two-WP parallel frontier with non-overlapping production paths and separate test-repository branches/MRs/path sets, and stage 3 is their only integration writer after full fan-in. Parent review and E2E gates deliberately have no WP task coverage. Any change to product scope, public contracts, estimate classification, WP ownership, direct dependencies, test-repository branch/path ownership, or parent-gate order requires a new planning revision. Operational stage promotion does not.
