## Delivery Contract

- Task key: `MYL-445`.
- OpenSpec change: `add-numbered-odcli-slots`.
- Product merge base: `origin/main` at `4a9369c3cab0f9a34bf3d3b2411f3ec0ac5bf5e6`.
- Planning revision base: current PR #140 head `92406fa537eacf048479162197fe30faab718e6c`; this revision changes planning artifacts only.
- SDK implementation branch: `feat/MYL-445-numbered-odcli-slots`; GitLab implementation requires a separate MR in `binta-test-repo`.
- Delivery mode: `dag`.
- Estimate source: confirmed read-back properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` on the root planning issue; numeric totals are intentionally not duplicated here.
- Estimate basis: remaining active developer effort from the two inspected snapshots to the revised acceptance criteria for one experienced developer familiar with Python, Click, uv tools, POSIX `flock`, Git, Odoo configuration/database tooling, and pytest, without AI acceleration. Tests were not run for estimation; unattended CI, approvals, meetings, and external waiting are excluded.
- Confidence: medium. Current shared lifecycle/root confinement and SDK-owned hash-locked environment lifecycle are implemented and inspectable; uncertainty is concentrated in their coordinator composition, fail-closed multi-resource rollback, concrete GitLab environment drift, and cross-repository acceptance.
- Calibration: evidence-based and uncalibrated; no comparable historical actuals were available.
- Topology rule: authoritative estimate exceeds the DAG threshold. After one SDK-owned private contract, the SDK public-surface/skill revision and GitLab wrapper revision have disjoint mutable write scopes and form a real parallel frontier; integrated acceptance is their serial fan-in.

Stage mapping: `WP-MYL-445-01 -> 1`, `WP-MYL-445-02 -> 2`, `WP-MYL-445-03 -> 2`, `WP-MYL-445-04 -> 3`. Stages are consecutive; stage is a topological layer, not WIP policy.

## WP-MYL-445-01 — Shared private lifecycle and checkout foundation

- **Stage:** `1`.
- **Tasks:** `1.1`, `1.2`, `1.3`, `1.4`, `1.5`.
- **Depends on:** none.
- **Standalone deliverable:** one fixed SDK-private contract that preserves the existing alternate-tool engine, adds bounded checkout mechanics, composes the existing SDK-owned environment lifecycle, and atomically binds the smallest wholly free numbered identity to its checkout and distinct verified Python environment without exposing a product command.
- **Owned responsibility scope:** SDK internal alternate identity/manifest and user-root contract, new private checkout coordinator, composition with existing owned-environment checkout/hash-lock/cleanup modules, allocation/reservation/rollback, process/config validation, package inclusion, focused foundation tests, and the read-only boundary inventory of GitLab `agent_checkout.py`. Critical files: `src/odoo_instance_sdk/internal/alternate_tool.py`, new `src/odoo_instance_sdk/internal/agent_checkout.py` or the equivalent single private module, existing `resources/environment/**` and `internal/dependency_sync.py` only for necessary composition fixes, `internal/paths.py` only when the fixed contract requires it, `tests/unit/test_alternate_tool.py`, new private-coordinator tests, directly related owned-environment tests, packaging and architecture fixtures.
- **Contract surface:** one installer/provenance/probe/manifest/shim/lifecycle-lock/tool-state cleanup; one existing SDK-owned environment create/hash-lock/verify/cleanup path; one global allocation lock; four-dimension occupancy check with the owned environment contained beneath numbered state; fixed numbered-only checkout/environment linkage; explicit validated environment inputs; no shared venv, Click/console registration, arbitrary hooks/extensions, generic providers, daemon, registry DB, Binta defaults, or secret output.
- **DoD / evidence:** concurrent allocation yields distinct identities; each orphan dimension is occupied and untouched; every numbered manifest links a distinct environment ID/path/lock digest/interpreter under its selected root; shared or drifted environments are rejected; trusted reference/origin, marker/hash, symlink/containment, dirty/unpushed, restore/drop and rollback cases fail closed; hot-fix manifests have no checkout/environment fields; packaging imports the private modules; architecture guard finds no duplicated lifecycle or Python-environment mechanics.
- **Parallel safety:** sole owner of shared package-private contracts. Stage-2 successors start only after its API/manifest/environment contract is fixed and do not edit these critical modules or foundation tests.

## WP-MYL-445-02 — SDK service wrapper and public-slot removal

- **Stage:** `2`.
- **Tasks:** `2.1`, `2.2`, `2.3`, `2.4`.
- **Depends on:** `WP-MYL-445-01`.
- **Standalone deliverable:** the SDK repository exposes numbered management only through checked-in private service/skill tooling, has no public `odcli slot` leaf, and retains alternate root/update/hot-fix guarantees with revised documentation and tests.
- **Owned responsibility scope:** SDK `.agents/skills/odcli-autonomous-work/**`, trusted canonical-interpreter wrapper, deletion of `commands/slot.py`, removal from command registration/output/public inventories, self-update remediation wording, SDK README/execution-boundary docs, and related CLI/skill/migration/update tests. It may adapt architecture fixtures owned as consumers but SHALL NOT change the foundation implementation or fixed manifest/environment contract.
- **Contract surface:** private create/list/inspect/remove wrapper with one bounded machine result; no secret output; zero Click or console-script management surface; alternate self-update rejects before effects and points to service tooling; canonical update remains unaware of alternates.
- **DoD / evidence:** root help and public-leaf/matrix counts contain no slot entries; slot module/test and slot-only output plumbing are absent; service wrapper proves canonical interpreter/repository before private invocation; retained migration/update/hot-fix tests pass; docs state checkout/owned-venv/database/filestore/port boundaries, allowed shared read-only source/PostgreSQL boundary, and the PR #140 keep/delete result.
- **Parallel safety:** writes SDK skill/public CLI/docs and their direct tests only. It does not touch GitLab files owned by WP-03 or shared private modules/foundation tests owned by WP-01.

## WP-MYL-445-03 — GitLab thin wrapper and Binta configuration

- **Stage:** `2`.
- **Tasks:** `3.1`, `3.2`, `3.3`, `3.4`.
- **Depends on:** `WP-MYL-445-01`.
- **Standalone deliverable:** a separate GitLab MR where `binta-test-repo/scripts/agent_checkout.py` supplies concrete environment/configuration to the fixed SDK coordinator and no longer duplicates reusable clone/allocation/marker/removal mechanics.
- **Owned responsibility scope:** GitLab `scripts/agent_checkout.py`, wrapper-focused tests, `README.md`, `docs/stand.md`, and directly related fixtures/configuration docs. It owns concrete reference checkout, checkout parent, backup, port bounds, database prefix, PostgreSQL endpoint, base Python, audited dependency lock/digest, Odoo bin, source private `.env`/config, Binta scenarios, and exact-MR evidence. `seed_odoo.py`, DB diagnostics, admin-password setup, secrets and persistent-stand assets remain GitLab-owned and unchanged except for a necessary reference update.
- **Contract surface:** fixed environment keys consumed by the SDK coordinator; no lifecycle algorithm, arbitrary callback/plugin schema, secret serialization, SDK path guesses, or code copy. Missing SDK revision/access/environment values block rather than fall back.
- **DoD / evidence:** wrapper create/list/remove and drift/failure tests pass; output and manifests exclude secret values; Binta-owned assets do not enter the SDK; real stand evidence covers unique checkout/owned-venv/database/filestore/port, distinct resolved interpreters/site-packages, allowed shared read-only source/PostgreSQL, and safe cleanup; MR records its exact revision and expected SDK contract.
- **Parallel safety:** all mutable production/test/docs scope is in the GitLab repository, disjoint from WP-02's SDK work. It consumes but does not edit WP-01's frozen SDK contract.

## WP-MYL-445-04 — Cross-repository acceptance and publication gate

- **Stage:** `3`.
- **Tasks:** `4.1`, `4.2`, `4.3`, `4.4`, `4.5`.
- **Depends on:** `WP-MYL-445-02`, `WP-MYL-445-03`.
- **Standalone deliverable:** publication-ready SDK and GitLab exact revisions with end-to-end proof of one alternate lifecycle, no public slot surface, isolated roots and project resources, selective cleanup, complete PR #140 disposition, and cross-repository ownership.
- **Owned responsibility scope:** cross-component SDK/GitLab acceptance fixtures, before/after digest evidence, final architecture/packaging/docs audits, exact-SHA cross-reference, and minimal repairs only for observed integration failures. Critical surfaces include SDK process acceptance/architecture inventory and GitLab wrapper stand evidence; repairs stay in the original WP owner's responsibility area but are coordinated serially here.
- **Contract surface:** canonical plus two numbered plus two hot-fix roots; two numbered linked checkouts with distinct SDK-owned Python environment/database/filestore/port; shared PostgreSQL server, read-only Odoo source, and source backup preserved; no public slot registration; no duplicate lifecycle/checkout/environment mechanics; exact SDK/GitLab revision binding.
- **DoD / evidence:** incompatible catalogue processes remain root-confined; numbered resources remain checkout-confined and each runtime proves a distinct `sys.executable` and site-package root; dirty/unpushed/running/config/lock/interpreter-drift/orphan cases preserve everything; removing one numbered and one eligible hot fix preserves all neighbors/reference/persistent assets; every PR #140 path matches keep/adapt/delete map; focused/full tests, packaging, output/inventory, Ruff, strict mypy, repository checks, diff check, strict OpenSpec and docs gates pass.
- **Parallel safety:** serial fan-in after both stage-2 siblings. One owner coordinates shared fixtures and cross-repository repair evidence, so parallel siblings never write the same mutable surface.

## Topology and Coverage Audit

Direct edges only:

- `WP-MYL-445-01 -> WP-MYL-445-02`
- `WP-MYL-445-01 -> WP-MYL-445-03`
- `WP-MYL-445-02 -> WP-MYL-445-04`
- `WP-MYL-445-03 -> WP-MYL-445-04`

Execution frontiers: stage 1 fixes the shared SDK-private contract; stage 2 independently revises the SDK service/public surface and the GitLab wrapper/configuration; stage 3 joins them for acceptance. No transitive edge is repeated.

Task coverage is complete and one-time:

| WP | OpenSpec tasks |
| --- | --- |
| `WP-MYL-445-01` | `1.1`, `1.2`, `1.3`, `1.4`, `1.5` |
| `WP-MYL-445-02` | `2.1`, `2.2`, `2.3`, `2.4` |
| `WP-MYL-445-03` | `3.1`, `3.2`, `3.3`, `3.4` |
| `WP-MYL-445-04` | `4.1`, `4.2`, `4.3`, `4.4`, `4.5` |

## Estimation Evidence and Assumptions

- Inspected SDK/OpenSpec snapshot: PR #140 head `92406fa537eacf048479162197fe30faab718e6c`, complete revised package, shared engine, public slot adapter, path/migration/update guards, existing owned-environment create/hash-lock/cleanup path, autonomous skill, packaging/architecture/output tests and PR diff.
- Inspected GitLab snapshot: `d21521c985263a097e6bcf042d132db4d80e137c`, full `scripts/agent_checkout.py`, its README/stand callers, private-config behavior, cleanup guards and Binta-only helper boundaries.
- Reusable work counted once: current shared alternate lifecycle, owned-environment lifecycle, root confinement, migration/update guards, hot-fix policy and their tests are retained/adapted rather than reimplemented.
- Estimate covers remaining implementation in both repositories, focused/concurrency/process/resource tests, separate GitLab MR work, documentation, repository gates and likely review repairs. Deployment, human approval time, unattended CI and unrelated stand maintenance are excluded.
- Estimate invalidates if scope expands to arbitrary repositories/config schemas, Windows support, automatic orphan/legacy adoption, shared-service orchestration, a public numbered API, migration of Binta-only seed/diagnostic/password logic, or additional external environments.
