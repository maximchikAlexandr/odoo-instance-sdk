# Delivery plan

## Readiness contract

This is the mandatory task-4.2 blocker revision. MYL-272 is integrated by PR #110 merge `11ff3403f2108adc901154ebeb9ee509add46ef5` and present in selected base `c1e57b79f39e529a50c25818134c06309384ee23`; complete `multica-py` #93 is integrated by PR #95 merge `c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed` with package version `0.1.0` and native checkout CLI floor `0.5.3`. The implementation feature head at research time is `8f63f6d07ac2e9947dbf190590293266e2dd29ae`. `research.md` records the observed APIs, compatibility consequence, and public authoritative Issue/Project-resource repair.

`WP-04` remains blocked and candidate `c15b84d9bc87a63513d9da04f8718dc3cdd3e0cc` remains unaccepted until the new exact SHA of this complete revision is independently approved by Plan Verifier. Manager/WP Delivery SHALL NOT change production code or OpenSpec outside this planning flow. After approval, Plan Verifier updates the graph contract before Manager resumes `WP-04`; `WP-05` remains parked.

## Estimate and delivery mode

The authoritative estimate totals are stored only in this planning issue's `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` properties and were verified by read-back. The revised estimate covers remaining active developer effort from the inspected feature head for one experienced developer familiar with Python SDK/CLI, immutable command plans, Git worktrees, Odoo COPY restore, packaging, and the repository's test gates. Accepted predecessor WPs are excluded; the public context-contract repair, candidate reconciliation, live acceptance and final publication gates are included. External dependency waiting and human approval queues are excluded.

Confidence is **medium** and calibration is **uncalibrated**. Evidence includes the complete OpenSpec package, accepted predecessor implementation, the unaccepted candidate diff, actual platform Project-resource output, exact typed `multica-py` Project/Issue/TaskRun/resource/daemon contracts, and existing integration/publication gates. The principal uncertainty is disposable live daemon/Odoo acceptance and any cross-package repair it exposes. Tests were not run to manufacture estimate timing.

The authoritative `Estimate, hours` property exceeds the multi-WP threshold. Delivery mode is `dag`: five atomic WPs with one genuine parallel frontier. Each OpenSpec task appears exactly once.

## Pre-start gate

Before resuming `WP-04`, the implementation parent SHALL record the selected base, exact `multica-py` revision/version above, this blocker-revision exact SHA, its independent approval, and the revised graph contract. `WP-04` remains blocked until those facts agree; its current candidate is not accepted by this planning revision.

If observed APIs change product scope, compatibility, estimate threshold, task coverage, contract dependencies, or write-zone ownership, return to planning and publish a new delivery-plan revision. Operational status/assignee changes do not revise this topology.

## WP-01 — Dependency contracts and adoption foundation

- **Tasks:** `1.1`, `1.2`, `2.1`, `2.2`.
- **Depends on:** none inside the DAG; the pre-start gate applies.
- **Stage / level:** 1.
- **Deliverable:** the observed core and Multica public contracts are consumed with compatibility tests, and core can safely adopt a caller-owned checkout through the existing COPY pipeline with persisted ownership/project/checkout/artifact evidence.
- **Owned responsibility scope:** dependency compatibility adapters/tests; environment catalog schema and migration; checkout planning/artifacts/adoption entrypoints; focused adoption, migration, provenance, concurrency, and retry tests. Directly coupled fixtures/docs belong here.
- **Critical shared files:** environment models/catalog/migrations and checkout planning/artifact modules. Later WPs treat these as frozen predecessor contracts.
- **Contract surface:** observed `repositories.checkout_command()/checkout()` and `daemon.status_command()/status()` types; observed `remote_name`/`backup_id` COPY selectors; additive ownership/project/checkout/artifact evidence; inspectable adoption command/result; first-adoption validation; matching-ready UUID retry; conflict/recovery behavior; no raw Multica command or decoder.
- **DoD / evidence:** observed dependency versions match planning research; fresh/upgrade schema equivalence and one head; linked-worktree/clone adoption; wrong/dirty/source/base/secret/concurrency/retry matrices; plan/execution parity; existing SDK-owned checkout regression coverage; focused Ruff/mypy/tests pass.
- **Parallel safety:** foundation is deliberately serial because both downstream WPs consume its persisted and public contracts.

## WP-02 — Core lifecycle and owned-only cleanup

- **Tasks:** `2.3`, `2.4`.
- **Depends on:** `WP-01`.
- **Stage / level:** 2.
- **Deliverable:** adopted environments work through existing lookup, configuration, sync, runtime, diagnostics, and removal surfaces while caller-owned code remains undeletable.
- **Owned responsibility scope:** core environment lookup/cwd/config/sync/runtime/diagnostic/cleanup modules and focused lifecycle/removal tests, fixtures, and docs. It does not modify extension package code.
- **Critical shared files:** core environment cleanup and runtime/context resolution; WP-03 must not write these while the level-2 frontier is active.
- **Contract surface:** explicit project/checkout/artifact identity; repository-local rebasing; missing/replaced-code diagnostics; startup refusal; safe database/filestore/artifact cleanup; unchanged SDK-owned behavior.
- **DoD / evidence:** independent-clone lookup by project/UUID/cwd; rebased paths and isolated data; dirty/missing/replaced/symlink/active-runtime/unknown-ownership removal matrix; partial-cleanup recovery; no Git removal/reset/prune for caller-owned code; focused Ruff/mypy/tests pass.
- **Parallel safety:** core lifecycle/cleanup write zone is disjoint from WP-03's extension/package/output-contract zone; both only read WP-01 contracts.

## WP-03 — Typed Multica integration package

- **Tasks:** `3.1`, `3.2`, `3.3`, `3.4`.
- **Depends on:** `WP-01`.
- **Stage / level:** 2.
- **Deliverable:** independently installable `odcli-multica` composes typed native checkout/context with exact core adoption and emits bounded sanitized output without its own orchestration or persistence.
- **Owned responsibility scope:** workspace member/package metadata, `odcli_multica` SDK/CLI, package-local tests/fixtures/docs, and the narrow documented public core output symbols consumed by the extension. It does not edit core lifecycle/cleanup modules owned by WP-02.
- **Critical shared files:** root workspace/lock/release configuration and existing core bounded-output public surface. No sibling writes those files at level 2.
- **Contract surface:** typed checkout/Project resources/issue/run/daemon status; authoritative Issue-to-Project and Project-to-repository identity; optional consistent TaskRun project snapshots; frozen verified context; inspectable prepare command and delegating convenience operation; compatible dependency bounds; standalone/combined tools; Rich/JSON/TOON/error/redaction contracts.
- **DoD / evidence:** no raw command/private import/local decoder; absent/conflicting TaskRun snapshots, complete/incomplete/ambiguous Project resources, identity/containment/run-pagination/forwarded-host/cancellation matrix; no implicit bind/start/task mutation; preparation retry/recovery; no-sources wheel plus isolated standalone/combined installs; output parity and package architecture tests; focused Ruff/mypy/tests pass.
- **Parallel safety:** writes only extension, packaging, and bounded-output-publicization areas; core lifecycle/cleanup is exclusively WP-02.

## WP-04 — Integrated fake and live acceptance

- **Tasks:** `4.1`, `4.2`.
- **Depends on:** `WP-02`, `WP-03`.
- **Stage / level:** 3.
- **Deliverable:** end-to-end evidence proves one native checkout flows through context/adoption/runtime/cleanup at fake boundaries and in an approved disposable native-daemon/Odoo fixture.
- **Owned responsibility scope:** cross-package integration/acceptance tests, disposable fixture wiring, sanitized evidence, and the narrow planning-approved context repair in `odcli_multica` plus directly coupled tests required to consume public Issue/Project resources. Other predecessor public contracts may only change through coordinated repair and planning escalation when scope changes.
- **Critical shared files:** integration test harnesses and fixture configuration; it is the sole writer after fan-in.
- **Contract surface:** public Issue/Project resource authority with optional consistent TaskRun project snapshots; exact run/workspace/runtime/path and daemon proof; checkout → context → COPY adoption → start/status → stop/remove; two tasks per project; same-host evidence; permissions; stable UUID; one database/isolated filestore; no borrowed-code deletion or binding files.
- **DoD / evidence:** task 4.2 explicitly proves `TaskRun omits duplicated project snapshot`, `Matching local run`, `Project repository evidence is unavailable`, and `Wrong host or incomplete evidence`; fake-boundary matrix passes; explicitly approved live fixture passes without customer data; exact dependency versions and sanitized runtime evidence retained; failure/cleanup paths are demonstrated; focused Ruff/mypy/tests pass.
- **Parallel safety:** join WP; no sibling is active at this level.

## WP-05 — Final quality and publication gate

- **Tasks:** `4.3`.
- **Depends on:** `WP-04`.
- **Stage / level:** 4.
- **Deliverable:** one publication-ready integrated SHA with complete repository, package, compatibility, OpenSpec, and release evidence.
- **Owned responsibility scope:** final cross-domain fixes, installed-wheel and all-member checks, release metadata/docs, and evidence collation. No new product scope is allowed.
- **Critical shared files:** any file may be repaired only to close verified integration/quality findings; scope changes return to planning.
- **Contract surface:** unchanged approved behavior, exact verified dependencies or equivalent uniquely versioned releases, independent package release, and complete architecture/output/typing/mutation gates.
- **DoD / evidence:** core-only, extension-only, combined installed-wheel, compatibility, and available all-member smoke pass; focused/full tests as required; Ruff format/check, strict mypy, schema/architecture/command/mutation gates, `git diff --check`, and strict OpenSpec validation pass; exact SHA is pushed and remote equality verified.
- **Parallel safety:** final serial join and publication owner.

## Topology and coverage audit

Direct edges only:

- `WP-01 → WP-02`
- `WP-01 → WP-03`
- `WP-02 → WP-04`
- `WP-03 → WP-04`
- `WP-04 → WP-05`

Topological levels: level 1 — `WP-01`; level 2 — `WP-02`, `WP-03`; level 3 — `WP-04`; level 4 — `WP-05`. The level-2 frontier has two independent WPs with disjoint write zones. Operational WIP is not encoded as a dependency.

Task coverage is complete and non-overlapping:

| WP | OpenSpec tasks |
|---|---|
| WP-01 | `1.1`, `1.2`, `2.1`, `2.2` |
| WP-02 | `2.3`, `2.4` |
| WP-03 | `3.1`, `3.2`, `3.3`, `3.4` |
| WP-04 | `4.1`, `4.2` |
| WP-05 | `4.3` |
