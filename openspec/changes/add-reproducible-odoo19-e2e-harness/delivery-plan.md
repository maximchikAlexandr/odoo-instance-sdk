# Delivery plan

## Compilation invariants

- Planning revision: MYL-149 at base `0ff164636617c03a51277055af45cef009277368`.
- Work packages are implementation ownership units, not live status or assignee records.
- `depends_on` contains only direct graph edges. Stage is the topological level; WIP limits are operational and are not encoded here.
- Responsibility scopes name owned concerns and critical shared files. They are not exhaustive file allowlists: directly related tests, fixtures, snapshots, documentation, generated evidence, and service files follow the owning concern unless an active sibling owns them explicitly.
- Task IDs T01–T26 refer to `tasks.md`; every task appears exactly once below.

## DAG

```text
Stage 0: WP-01
           |
Stage 1: WP-02
          / | \
Stage 2: WP-03  WP-04  WP-05
          \ | /
Stage 3: WP-06
```

## WP-01 — Canonical verification contracts

- Stage: 0
- `depends_on`: none
- Task coverage: T01, T02, T03, T04
- Independent deliverable: one test-only contract surface that augments the existing canonical CLI inventory with E2E disposition/evidence, validates complete coverage, defines immutable pins/budgets/platforms, and generates the traceability projection.
- Owned responsibility scope: canonical `PublicLeafCase` metadata in `tests/unit/test_cli_output_modes.py`; E2E contract/pin/matrix modules; generator/check command; pytest marker declarations; focused unit tests. Critical shared files are `tests/unit/test_cli_output_modes.py` and `pyproject.toml`.
- Contract surface: `PublicLeafCase.e2e_disposition`, `e2e_evidence`, and `e2e_rationale`; a frozen pin manifest containing every value in `ci-design.md`; a deterministic matrix projection; normalized platform and cold/warm budget classification results. Production modules SHALL NOT import this surface.
- Definition of done / evidence: existing inventory-completeness test still passes; a new leaf without E2E metadata fails; the generated projection matches `command-matrix.md`; malformed/mutable pins, unsupported platforms, and absent prerequisites fail unit tests; `pytest --collect-only` recognizes both new markers without warnings.
- Parallel-safety rationale: foundation is deliberately serial because it owns the only shared inventory and marker configuration. Downstream packages consume its frozen test contract and do not edit these files.

## WP-02 — Disposable Odoo/PostgreSQL fixture foundation

- Stage: 1
- `depends_on`: WP-01
- Task coverage: T05, T06, T07, T08, T09
- Independent deliverable: self-provisioning pinned Compose/reference fixtures, `base`-only addon, genuine ZIP generator, readiness gates, secret handling, reverse-order cleanup ledger, and zero-leak audit usable by smoke and full tests.
- Owned responsibility scope: `tests/integration/real_odoo/conftest.py`; Compose and pin-consumption fixtures; harness lifecycle/readiness/cleanup helpers; `tests/fixtures/addons/odcli_e2e_probe/**`; fixture-specific tests and snapshots. Critical shared file is `tests/integration/real_odoo/conftest.py`; it exposes fixtures but owns no production subprocess runner.
- Contract surface: session fixtures `source_server`, `source_backup`, and immutable pins; function fixtures `target_runtime`, `resource_ledger`, and `failure_evidence`; resource records keyed by run id; readiness results for `pg_isready`, Odoo HTTP, and pre-restore database-manager availability; `audit_no_leaks(run_id)`; bounded sanitized log collection.
- Definition of done / evidence: the addon manifest depends exactly on `base`; generated ZIP has dump plus filestore and recorded SHA/size; two distinct run ids can coexist without shared names/ports/XDG/catalog state; success and injected fixture failure both execute cleanup; audit reports no process/container/network/volume/port/database/filestore/worktree/catalog/runtime-root residue; secret canary is absent from logs and manifests.
- Parallel-safety rationale: this predecessor owns all shared fixtures and resource contracts before scenario siblings begin. It does not write scenario or workflow files owned by WP-03/04/05.

## WP-03 — Source-backed full critical path

- Stage: 2
- `depends_on`: WP-02
- Task coverage: T10, T11, T12, T13, T14
- Independent deliverable: one serial `e2e_full` critical-path test that proves the OdCLI-managed target checkout/venv/config/process and complete backup/restore/module/inspection/cleanup workflow.
- Owned responsibility scope: critical-path scenario module(s), assertions, and critical-path-only snapshots/evidence under `tests/integration/real_odoo`; it may add directly related test data that is not part of the shared addon. Critical shared fixture files from WP-02 are read-only to this package.
- Contract surface: consumes WP-02 fixtures and WP-01 evidence IDs E2E-CP-01..15; emits machine results and JUnit properties for manifest identity, source SHA, owned Python, readiness, backup identity, restored record/attachment, idempotency, and empty cleanup audit.
- Definition of done / evidence: the target executable resolves inside the pinned OdCLI worktree; owned Python is 3.12.13; `env checkout --create-venv` and `env sync` use uv 0.10.8; real target Odoo reaches HTTP readiness; probe install/update/test succeeds; `db refresh --restore` restores data and attachment verified through XML-RPC; every critical matrix row has evidence; repeated safe operations remain idempotent; cleanup audit is empty.
- Parallel-safety rationale: writes only critical-path scenario files. It may run in parallel with WP-04 and WP-05 after WP-02 because those siblings own failure scenarios and CI/docs respectively; all shared fixtures are read-only.

## WP-04 — Focused failures, recovery, and non-critical leaves

- Stage: 2
- `depends_on`: WP-02
- Task coverage: T15, T16, T17, T18, T19
- Independent deliverable: focused `e2e_full` cases for authentication/network/archive/restore failures, remaining applicable CLI leaves, controlled interruption/timeout, partial publication, secret redaction, retained debug files, and leak-free recovery.
- Owned responsibility scope: failure-injection proxies/fixtures local to focused scenarios; focused, recovery, and security test modules; their bounded snapshots and test data. It does not modify the shared lifecycle fixture or critical-path module. If a new reusable fixture contract is genuinely required, that is a WP-02 contract revision rather than a sibling edit.
- Contract surface: consumes WP-02 `failure_evidence` and resource ledger; evidence IDs E2E-FC-01..13, E2E-REC-01..03, E2E-SEC-01..03; emits stable public error code, non-zero exit, publication/retention state, primary-versus-cleanup error fields, canary scan, and final audit.
- Definition of done / evidence: every required negative case fails at its specified boundary; no secret/canary occurs in argv/output/artifacts/fingerprint; truncated/incompatible archives never publish unowned state; SIGINT returns public interruption semantics; timeout and partial failure preserve the primary error; retained debug mode keeps files only; all focused matrix rows and recovery/security IDs have executable evidence and empty live-resource audits.
- Parallel-safety rationale: owns separate focused/recovery files and treats WP-02 and WP-01 contracts as read-only, so it is write-disjoint from WP-03 and WP-05.

## WP-05 — CI tiers, caches, budgets, and operator documentation

- Stage: 2
- `depends_on`: WP-02
- Task coverage: T20, T21, T22, T23
- Independent deliverable: immutable-SHA PR smoke and scheduled/manual full workflows, content-addressed source/uv caches, measurable budgets, bounded redacted evidence packaging, and one documented local entry point for each tier.
- Owned responsibility scope: new/updated `.github/workflows/*real-odoo*`; CI bootstrap/evidence packaging scripts; real-Odoo operator documentation; workflow-focused tests. It reads test selectors and contracts from WP-01/02 and does not edit critical/focused scenario modules. Critical shared files are `.github/workflows/ci.yml` if smoke is added there and any shared Makefile target chosen for the documented entry point.
- Contract surface: job names and selectors from `ci-design.md`; pin/cache/resource/timing JSON schema; JUnit properties; cold/warm classification; 2 MiB text/success and 50 MiB failure limits; 7-day retention; pre-upload secret-canary gate; bootstrap exit contract that cannot convert missing prerequisites into skips.
- Definition of done / evidence: Actions are immutable SHAs; runner/pin/platform manifest is emitted; PR smoke and full select only their declared markers; cache keys contain all required identities and exclude mutable state; cache miss/hit exercises cold/warm budgets; artifact oversize/canary tests fail closed; local commands match workflow commands; workflow lint and bootstrap tests pass.
- Parallel-safety rationale: owns workflows, packaging, and docs only. It is write-disjoint from WP-03 critical scenarios and WP-04 failure scenarios and consumes the completed fixture selectors without changing them.

## WP-06 — Integrated verification and acceptance evidence

- Stage: 3
- `depends_on`: WP-03, WP-04, WP-05
- Task coverage: T24, T25, T26
- Independent deliverable: complete local/static and E2E acceptance evidence proving contract/matrix/spec coverage, cold/warm budgets, and zero leaks, with no product-scope expansion.
- Owned responsibility scope: verification commands and generated `.artifacts/real-odoo-e2e/**` evidence; final matrix/spec consistency checks; directly related expectation regeneration. Source fixes remain with the WP that owns the failed responsibility rather than becoming cross-scope cleanup in this package.
- Contract surface: consumes all WP evidence; produces final pin manifest, generated matrix, JUnit, phase/bundle measurements, cleanup audit, command/exit-code ledger, and out-of-scope assertion.
- Definition of done / evidence: formatting/lint/type/unit/strict OpenSpec/inventory checks pass; smoke passes twice; full passes once cold and once warm; each run's budget and bundle cap passes; all audits are empty; every T01–T26 and spec scenario maps to exactly one executable evidence item; diff contains no production runner/API, Enterprise input, mutable backup, hidden prerequisite, or unapproved contract change.
- Parallel-safety rationale: this is the sole convergence package and starts only after all stage-2 deliverables. It performs integrated reads and evidence generation, avoiding concurrent edits to sibling-owned source.

## Coverage proof

| Work package | Tasks | Count |
| --- | --- | ---: |
| WP-01 | T01–T04 | 4 |
| WP-02 | T05–T09 | 5 |
| WP-03 | T10–T14 | 5 |
| WP-04 | T15–T19 | 5 |
| WP-05 | T20–T23 | 4 |
| WP-06 | T24–T26 | 3 |
| **Total** | **T01–T26 exactly once** | **26** |
