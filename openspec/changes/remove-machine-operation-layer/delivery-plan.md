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
| G2-E01 | `environment-monitor` / Existing Moritora consumes the additive snapshot | Required local E2E: embedded dashboard/OpenAPI tests cannot prove the current external Moritora decoder and renderer | Candidate `GET /api/v1/snapshot`; supplied current Moritora launch/consumer contract | `e2e/MYL-432/graph2/check_moritora_snapshot_compat.py` plus only the minimal launch fixture supplied by the test repository | In fresh context, verify product/build revisions, fetch a genuine candidate snapshot, start the supplied current Moritora build against that endpoint, inspect browser/consumer health, console, and existing project/environment rows without rerunning the automatic script | Local-only external-consumer case; `dashboard-tests`, `web-codegen-check`, HTTP/OpenAPI tests remain CI coverage for the embedded consumer | Candidate and consumer revisions match inputs; top-level schema/existing rows remain readable; additive observation is accepted; no decoder error, crash, blank state, lost row, secret, or private path | Sanitized snapshot, consumer/build provenance, launch command and exit, health/DOM assertions, browser console, cleanup log in Multica only |
| G2-E02 | `database-management` / Polling read remains inert; Confirmed missing database is reconciled | Required local E2E: lower-level tests cover branches, but a real database disappearance plus catalogue transaction proves the retained cross-component boundary | Public JSON CLI reads and direct public Python SDK observation/reconciliation on one dedicated numbered environment | `e2e/MYL-432/graph2/check_database_reconciliation.py` using existing `scripts/odcli_agent_tools/numbered_tool.py` | In fresh context, validate recorded `odcli-N` identity and exact candidate, independently repeat list/exists/current and catalogue hashes, build and inspect the public reconciliation plan, run once, inspect event/state, run against a fresh observation again, and verify idempotence without invoking the automatic script | Real lifecycle is local-only; `offline-tests`, catalogue migration checks, and existing real-Odoo tiers cover deterministic negative/recovery paths in CI | Reads change no catalogue bytes/events; plan names only the case database; live absence and identity revalidate; first run appends exactly one dropped event; repeat appends none; foreign/inconclusive/reappeared state fails closed | Commands, exits, pre/plan/post projections, catalogue/event hashes, sanitized diagnostics, numbered create/inspect/remove records in Multica only |

Structured replacement recovery, legacy adoption, backup-read inertness, catalogue migration, and monitor section/CPU edge cases require no additional live case: their failure matrices and historical SQLite inputs are more deterministically exercised by the focused unit/integration and migration suites named in tasks 4.4 and 5.5. A live compensation fault injection would add destructive setup without a distinct public assertion.

## Test scripts, environment, and cleanup

The Tester SHALL create one task MR in `TEST_REPO_URL`. Its scenario commit SHALL contain only the two reusable scripts, minimal non-secret fixtures, and instructions. A result commit SHALL exist only if the tested function itself produces a repository-worthy functional artifact; diagnostic snapshots, browser output, catalogue projections, logs, hashes, and assertions are evidence and SHALL NOT be committed.

Both cases SHALL record `TESTED_PRODUCT_SHA`, `TEST_SCENARIO_SHA`, `REFERENCE_SHA`, candidate wheel digest, and graph revision before setup. G2-E01 additionally records an immutable `MORITORA_BUILD_REF` and the supplied launch contract. If that build or reproducible launch contract is absent, G2-E01 is blocked and graph-2 E2E cannot pass. Private hosts, credentials, user identities, and absolute local paths stay in execution inputs/evidence and are sanitized from public Git.

G2-E02 SHALL allocate a distinct numbered identity, checkout, database, interpreter, state root, port, and compose project. It SHALL use the recorded canonical launcher/interpreter and exact Odoo executable; it SHALL NOT use a reference checkout, shared database, or port. Signal/exception cleanup is installed before mutation. Cleanup stops only the exact recorded environment and delegates removal to `numbered_tool.py remove`, then proves launcher, checkout, environment, database, process, port, and owned volumes are absent.

## Independent checks and images

The independent agent SHALL perform the public/manual steps in the matrix from fresh context and SHALL NOT satisfy them by rerunning either automatic script. It SHALL compare every recorded identity and revision before acting and fail closed on mismatch.

`images_required=false`. Graph 2 deliberately removes a machine-only surface and preserves the current visible CLI and Moritora presentation; no changed table, log, progress, summary, or browser layout exists for a meaningful before/after pair. G2-E01 instead records actual DOM/row assertions and browser console output. If implementation changes visible Moritora or CLI output, planning must be revised before execution to add genuine before/after captures.

## CI, review, and publication gates

Implementer-owned graph-2 CI SHALL include strict OpenSpec, format/Ruff, strict typing, focused and full offline tests, Alembic/schema checks, HTTP/OpenAPI export, generated TypeScript check, dashboard tests, package/build, both real-Odoo smoke jobs, and GitHub-only Ubuntu/macOS compatibility. The source-boundary gate from task 6.1 SHALL run in offline/architecture coverage. No new CI framework is introduced.

After required GitHub CI succeeds on the exact graph-2 SHA, four independent profiles SHALL review the full graph-2 range: security, specification/standards, code quality, and tests/coverage. Any Blocker or Required finding returns to a bounded graph-2 fix iteration and invalidates prior exact-SHA acceptance. Live E2E dispatch occurs only after the consolidated clean review.

The final Tester comment in Multica SHALL link the test MR and every existing scenario/result commit/file, attach or link all execution-only evidence through the platform, and read the comment back to prove accessibility. It SHALL preserve the MR, branch, and commits, remove only run-owned resources, and SHALL NOT merge/close the MR or delete its branch without separate authorization.

## Estimate evidence and topology decision

The exact estimate totals are authoritative only in the MYL-430 custom properties named in the delivery contract. The weighted estimate property is above the multi-WP threshold. Confidence is medium and calibration is unavailable: the consolidated commits, current call sites, tests, and generated surfaces are inspectable, but machine/shared decoupling and availability of the current external Moritora build can expand repair work. The estimate covers scope census, machine removal, mixed test/doc cleanup, retained monitor and database/recovery verification, CI/review repair, and the two E2E cases. It excludes Supervisor integration, a replacement machine interface, external approval wait time, and provider outages.

The topology is `delivery_mode=dag`. Stage 2 contains two genuinely independent WPs after the shared removal foundation: monitor/Moritora work and database/recovery work have disjoint production/test ownership. The final integration and E2E gates follow their fan-in.

## Dependency graph

Direct edges only:

```text
WP-01-machine-layer-removal -> WP-02-monitor-consumer-compatibility
WP-01-machine-layer-removal -> WP-03-database-recovery-retention
WP-02-monitor-consumer-compatibility -> WP-04-graph2-integration
WP-03-database-recovery-retention -> WP-04-graph2-integration
WP-04-graph2-integration -> WP-05-graph2-live-e2e
```

Authoritative stage mapping:

```text
WP-01-machine-layer-removal -> 1
WP-02-monitor-consumer-compatibility -> 2
WP-03-database-recovery-retention -> 2
WP-04-graph2-integration -> 3
WP-05-graph2-live-e2e -> 4
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
- Deletion of `operations/`, machine CLI modules and registration, operation-only context/public exports/exceptions/output branches, provider metadata and fixtures, consumer/Go proof, and machine documentation.
- Restoration of test-owned CLI/E2E inventory and removal or rewrite of operation-only/mixed tests, matrices, architecture inventories, repository rules, README/SDK/execution docs, and main/delta contract references.
- Critical shared files include `AGENTS.md`, `README.md`, `docs/execution-boundary.md`, `docs/python-sdk.md`, `src/odoo_instance_sdk/__init__.py`, CLI registration/context/output modules, `tests/unit/test_cli_output_modes.py`, architecture/documentation tests, real-Odoo matrix/generator, and affected main specs.

Contract surface:

- Existing friendly CLI paths, SDK primitives, Rich/JSON/TOON envelopes, dry-run behavior, native transports, exit codes, redaction, and command execution remain.
- Production has no runtime operation table and no imports from tests; the test matrix is exhaustive evidence only.
- Archived graph-1 artifacts remain unchanged and never become graph-2 evidence.

Definition of done and evidence:

- Every graph-1 reference is classified once; forbidden files/symbols and machine leaves are absent.
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
- Reusable external Moritora compatibility script/fixture contract without private host or credentials.
- Critical files include monitor models/resources/internal process metrics, `openapi.json`, generated web types, `web/src/App.tsx`, monitor/HTTP/codegen/dashboard tests, and monitor-specific docs.

Contract surface:

- Top-level snapshot schema and existing project/environment fields remain compatible; observation metadata is additive and truthful.
- Unselected sections perform no probes; unavailable sections are unknown/incomplete, never fabricated zero/false.
- External Moritora build/launch input is required evidence, not a new production adapter.

Definition of done and evidence:

- Snapshot-v2/v3, process identity/CPU, no-call, HTTP/OpenAPI/codegen, and embedded dashboard checks pass on graph 2.
- The compatibility script rejects revision mismatch and exposes an actionable blocker when the current external build is unavailable or rejects the candidate snapshot.

Parallel safety: owns monitor/API/web generated surfaces only. It does not edit database resources, catalogue migrations, or replacement recovery owned by WP-03.

## WP-03 — Database and recovery retention

- `wp_id=WP-03-database-recovery-retention`
- `stage=2`
- `depends_on=[WP-01-machine-layer-removal]`
- `task_coverage=5.1,5.2,5.3,5.4,5.5`

Deliverable: inert database/backup reads, explicit exact reconciliation, and structured replacement recovery remain complete and independent after machine-layer deletion.

Owned responsibility scope:

- Database observation DTOs/resources/commands and explicit lifecycle reconciliation call sites.
- Backup observational queries, catalogue transaction/event behavior, structured replacement recovery codec/planning/validation, environment storage, Alembic migration/head, and directly related fixtures/tests/docs.
- Critical files include database backup/restore resource parts, catalogue environment/restore/schema/migration modules, `internal/dbreplace*`, `internal/pg` retained reconciliation paths, migration `0008_replacement_recovery.py`, and database/backup/recovery/migration tests.

Contract surface:

- Reads append no events or backfill state; explicit reconcile revalidates exact absence/identity and appends at most one idempotent event.
- Recovery evidence is secret-free structured state; new failures do not use human `last_error` as executable input.
- One catalogue and one Alembic head preserve existing rows, events, foreign keys, and recovery bytes.

Definition of done and evidence:

- Focused read/reconcile/backup/catalogue/migration/replacement/rollback/redaction suites pass from graph-2 code.
- No retained path imports operation registry/session/provider types, and all necessary startup/destructive/repair owners remain explicit.

Parallel safety: owns database/catalogue/recovery files and tests only. It does not edit monitor/API/web surfaces owned by WP-02.

## WP-04 — Graph-2 integration, CI, and independent review

- `wp_id=WP-04-graph2-integration`
- `stage=3`
- `depends_on=[WP-02-monitor-consumer-compatibility,WP-03-database-recovery-retention]`
- `task_coverage=6.1,6.2,6.3,6.4,6.5`

Deliverable: one clean published graph-2 candidate with forbidden-surface proof, retained-module coverage, all required GitHub CI, and a clean four-profile review.

Owned responsibility scope:

- Cross-subsystem architecture/forbidden-symbol gates, final docs/generated artifacts, coverage comparison, focused/full repository gates, package/build verification, exact-SHA publication, CI evidence, and bounded fixes from four-profile review.
- Directly related tests, fixtures, snapshots, docs, support files, and generated code are included after stage-2 fan-in.

Contract surface:

- No machine layer reappears during integration; retained SDK/CLI/monitor/database/recovery behavior remains authoritative.
- Compatibility runs only in GitHub; graph-1 evidence cannot satisfy graph-2 review or CI.

Definition of done and evidence:

- Strict OpenSpec, format/lint/type, Alembic/schema, OpenAPI/codegen, dashboard, focused/full tests, package/build, real-Odoo smoke, GitHub-only compatibility, and `git diff --check` pass on one exact published SHA.
- Security, specification/standards, code-quality, and test profiles return no unresolved Blocker/Required findings on that SHA/range.

Parallel safety: fan-in only after both stage-2 WPs. It may resolve cross-cutting conflicts because no sibling remains active in their write zones.

## WP-05 — Graph-2 live E2E and evidence publication

- `wp_id=WP-05-graph2-live-e2e`
- `stage=4`
- `depends_on=[WP-04-graph2-integration]`
- `task_coverage=7.1,7.2,7.3,7.4`

Deliverable: independent current-Moritora snapshot compatibility and real database reconciliation evidence for the accepted exact candidate, with fail-safe cleanup and durable Multica/GitLab references.

Owned responsibility scope:

- Test-repository MR scripts/fixtures for G2-E01/G2-E02, isolated numbered environment execution, independent fresh-agent walkthroughs, evidence publication/read-back, and exact-owned cleanup.
- No production repository code or graph-2 candidate mutation is permitted in this WP.

Contract surface:

- Current external Moritora build provenance and launch contract are mandatory; missing inputs block rather than waive G2-E01.
- G2-E02 operates only on recorded `odcli-N` resources and one exact test database; automatic and independent actions remain separate.
- Execution evidence stays in Multica; GitLab stores reusable scenario code and only genuine functional results.

Definition of done and evidence:

- Both cases satisfy every assertion in the E2E matrix on the WP-04 exact SHA.
- Final comment read-back proves revisions, links, attachments/outputs, commands, exits, assertions, and cleanup evidence are accessible.
- Cleanup removes all run-owned resources while preserving the test MR, branch, and commits.

Parallel safety: final external verification only; it starts after code/review/CI acceptance and has no implementation sibling.

## Coverage and frontier audit

Tasks `1.1` through `7.4` are assigned exactly once. Direct edges omit transitive dependencies. Stage 2 is a valid two-WP parallel frontier with non-overlapping production write zones; all other stages are intentional fan-in or external verification. Any change to product scope, public contracts, estimate classification, WP ownership, or direct dependencies requires a new planning revision. Operational stage promotion does not.
