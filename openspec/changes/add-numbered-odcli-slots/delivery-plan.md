## Delivery Contract

- Task key: `MYL-445`.
- OpenSpec change: `add-numbered-odcli-slots`.
- Product merge base: `origin/main` at `4a9369c3cab0f9a34bf3d3b2411f3ec0ac5bf5e6`.
- Planning revision base: current PR #140 head `92406fa537eacf048479162197fe30faab718e6c`; this revision changes planning artifacts only.
- SDK implementation branch: `feat/MYL-445-numbered-odcli-slots`; GitLab implementation requires a separate MR in `binta-test-repo`.
- Delivery mode: `dag`.
- Estimate source: confirmed read-back properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` on the root planning issue; numeric totals are intentionally not duplicated here.
- Estimate basis: remaining active developer effort from the two inspected snapshots to the revised acceptance criteria for one experienced developer familiar with Python, Click, uv tools, POSIX `flock`, Git, Odoo configuration/database tooling, and pytest, without AI acceleration. Tests were not run for estimation; unattended CI, approvals, meetings, and external waiting are excluded.
- Confidence: medium-high. Alternate lifecycle, caller-owned `adopt_command()`/`adopt()` and environment `remove()` are implemented and directly tested; uncertainty is concentrated in allocation/linkage ordering, external-checkout deletion recovery, concrete GitLab inputs and cross-repository acceptance.
- Calibration: evidence-based and uncalibrated; no comparable historical actuals were available.
- Topology rule: authoritative estimate exceeds the DAG threshold. After one SDK-owned private contract, the SDK public-surface/skill revision and GitLab wrapper revision have disjoint mutable write scopes and form a real parallel frontier; integrated acceptance is their serial fan-in.

Stage mapping: `WP-MYL-445-01 -> 1`, `WP-MYL-445-02 -> 2`, `WP-MYL-445-03 -> 2`, `WP-MYL-445-04 -> 3`. Stages are consecutive; stage is a topological layer, not WIP policy.

## WP-MYL-445-01 — Shared private lifecycle and checkout foundation

- **Stage:** `1`.
- **Tasks:** `1.1`, `1.2`, `1.3`, `1.4`, `1.5`.
- **Depends on:** none.
- **Standalone deliverable:** one fixed SDK-private contract that preserves both existing lifecycles and adds only atomic numbered allocation plus a marked external checkout bound to the caller-owned environment returned by `adopt()`.
- **Owned responsibility scope:** SDK alternate identity/manifest contract, new allocation/external-checkout coordinator, reservation and attempt-owned checkout rollback, clone/origin/marker/dirty-unpushed/containment guards, invocation of existing environment adoption/removal, fixed linkage, post-removal checkout deletion, package inclusion, focused foundation tests, and the GitLab boundary inventory. Critical files: `internal/alternate_tool.py`, one small private coordinator module, `resources/environment/checkout_api.py` or cleanup only if a focused test proves a minimal typed API gap, coordinator/adoption/removal/architecture tests, packaging fixtures.
- **Contract surface:** one alternate-tool lifecycle; existing environment `adopt_command()`/`adopt()` and `remove()` as the sole config/port/COPY DB/catalogue/owned-venv lifecycle; one global allocation lock; four-dimension occupancy; fixed numbered checkout/environment linkage; environment-remove-before-checkout-delete ordering; no shared venv, coordinator config rewrite/port allocator/restore/DB-drop/catalogue/cleanup, Click/console registration, hooks, providers, daemon, registry DB, Binta defaults, or secret output.
- **DoD / evidence:** concurrent allocation yields distinct identities; orphans remain occupied; manifest links returned environment evidence; adoption failure retains retry evidence and removes only attempt-owned checkout state; external Git guards run before environment removal; successful remove cleans environment resources once and preserves caller-owned checkout until coordinator deletes it; a deletion failure remains retryable; architecture guard rejects every duplicated environment mechanic.
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
- **Standalone deliverable:** a separate GitLab MR where `binta-test-repo/scripts/agent_checkout.py` supplies concrete existing environment-API inputs and no longer duplicates allocation, environment or external-checkout lifecycle mechanics.
- **Owned responsibility scope:** GitLab `scripts/agent_checkout.py`, wrapper-focused tests, `README.md`, `docs/stand.md`, and directly related fixtures/configuration docs. It owns the concrete project manifest/reference, checkout parent, COPY source/target, optional requested port, PostgreSQL endpoint, base Python, audited lock/digest, Odoo bin, private config input, Binta scenarios, and exact-MR evidence. Seed, diagnostics, password, secrets and persistent-stand assets remain GitLab-owned.
- **Contract surface:** `ProjectConfig` plus `EnvironmentCheckoutOptions` values consumed by existing adoption; no config rewrite, port allocation, restore/drop, catalogue, venv or cleanup algorithm, callback/plugin schema, secret serialization, SDK path guesses, or code copy. Missing values block rather than fall back.
- **DoD / evidence:** wrapper create/list/remove and drift/failure tests pass; calls resolve to existing adopt/remove paths; output/manifests exclude secrets; Binta assets do not enter SDK; real stand evidence covers unique checkout/owned-venv/database/filestore/port, distinct interpreters/site-packages, allowed shared source/PostgreSQL, one-pass cleanup and external-checkout preservation/deletion; MR records exact revision and expected SDK contract.
- **Parallel safety:** all mutable production/test/docs scope is in the GitLab repository, disjoint from WP-02's SDK work. It consumes but does not edit WP-01's frozen SDK contract.

## WP-MYL-445-04 — Cross-repository acceptance and publication gate

- **Stage:** `3`.
- **Tasks:** `4.1`, `4.2`, `4.3`, `4.4`, `4.5`.
- **Depends on:** `WP-MYL-445-02`, `WP-MYL-445-03`.
- **Standalone deliverable:** publication-ready SDK and GitLab exact revisions with end-to-end proof of one alternate lifecycle, no public slot surface, isolated roots and project resources, selective cleanup, complete PR #140 disposition, and cross-repository ownership.
- **Owned responsibility scope:** cross-component SDK/GitLab acceptance fixtures, before/after digest evidence, final architecture/packaging/docs audits, exact-SHA cross-reference, and minimal repairs only for observed integration failures. Critical surfaces include SDK process acceptance/architecture inventory and GitLab wrapper stand evidence; repairs stay in the original WP owner's responsibility area but are coordinated serially here.
- **Contract surface:** canonical plus two numbered plus two hot-fix roots; two numbered caller-owned checkouts with distinct adopted environment/DB/filestore/port; shared PostgreSQL server, read-only Odoo source and source backup preserved; environment cleanup occurs only through `remove()` before external checkout deletion; no public slot registration or duplicate lifecycle mechanics; exact SDK/GitLab revision binding.
- **DoD / evidence:** incompatible catalogue processes remain root-confined; numbered resources and interpreters are distinct; external Git and environment failures preserve linkage; one successful removal proves DB/backup/config/lock/venv cleanup exactly once, caller-owned checkout preservation by SDK, then coordinator checkout deletion; every neighbor/reference/persistent asset remains; PR #140 map and all repository gates pass.
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

- Inspected SDK/OpenSpec snapshot: PR #140 head `92406fa537eacf048479162197fe30faab718e6c`, shared alternate engine, public slot adapter, caller-owned environment adoption tests, COPY cleanup journal/drop/backup/config/lock/venv removal with caller-owned checkout preservation, root/update guards, autonomous skill and PR diff.
- Inspected GitLab snapshot: `d21521c985263a097e6bcf042d132db4d80e137c`, full `scripts/agent_checkout.py`, its README/stand callers, private-config behavior, cleanup guards and Binta-only helper boundaries.
- Reusable work counted once: alternate lifecycle and complete environment adopt/remove lifecycle, root confinement, migration/update guards, hot-fix policy and their tests are retained rather than reimplemented.
- Estimate covers remaining implementation in both repositories, focused/concurrency/process/resource tests, separate GitLab MR work, documentation, repository gates and likely review repairs. Deployment, human approval time, unattended CI and unrelated stand maintenance are excluded.
- Estimate invalidates if scope expands to arbitrary repositories/config schemas, Windows support, automatic orphan/legacy adoption, shared-service orchestration, a public numbered API, migration of Binta-only seed/diagnostic/password logic, or additional external environments.
