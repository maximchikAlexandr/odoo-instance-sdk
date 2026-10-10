## Delivery Contract

- Task key: `MYL-445`.
- OpenSpec change: `add-numbered-odcli-slots`.
- Approved base: `origin/main` at `4a9369c3cab0f9a34bf3d3b2411f3ec0ac5bf5e6`.
- Delivery mode: `dag`.
- Estimate source: подтверждённые read-back properties корневой planning issue `Estimate, hours`, `Estimate min, hours`, `Estimate max, hours`; числовые totals намеренно не дублируются.
- Estimate basis: remaining active developer effort для одного опытного разработчика, знакомого с Python, Click, uv tool layouts, POSIX `flock`, SQLite migrations, skill scripts и pytest этого репозитория, без AI-ускорения. Unattended CI, approval queues, meetings и external blocking исключены; тесты для оценки не запускались.
- Confidence: medium. Existing `fix_tool.py` даёт работающую основу uv/manifest/shim/lock/remove, package/CLI/path seams доступны; основная неопределённость — безопасный canonical-interpreter bootstrap, selector capability probe и multi-root packaging acceptance.
- Calibration: evidence-based, uncalibrated; сопоставимые historical actuals отсутствуют.
- Topology rule: новая authoritative estimate property превышает threshold. После единого shared lifecycle foundation существуют два независимых фронта: thin numbered CLI adapter и SDK runtime confinement/update. Они соединяются только в integrated acceptance.

Stage mapping: `WP-MYL-445-01 -> 1`, `WP-MYL-445-02 -> 2`, `WP-MYL-445-03 -> 2`, `WP-MYL-445-04 -> 3`. Stages последовательны без пропусков; operational WIP в DAG не кодируется.

## WP-MYL-445-01 — Shared lifecycle и thin hot-fix policy

- **Stage:** `1`.
- **Tasks:** `1.1`, `1.2`, `1.3`, `1.4`, `1.5`.
- **Depends on:** нет.
- **Самостоятельный deliverable:** existing hot-fix lifecycle refactored into one package-owned alternate-tool engine; `odcli-fix-ISSUE` uses an isolated root through that engine while its skill wrapper contains only bootstrap and PR/reviewer/retirement policy.
- **Owned responsibility scope:** central user-root/identity paths, shared alternate-tool lifecycle implementation and tests, existing autonomous-work skill wrapper/reviewer/docs/tests, canonical-interpreter packaging bootstrap. Critical shared files: `src/odoo_instance_sdk/internal/paths.py`, new narrow `internal/alternate_tool.py` or equivalent, `.agents/skills/odcli-autonomous-work/**`, `tests/unit/test_odcli_autonomous_skill.py`, directly related shared-engine/packaging tests.
- **Contract surface:** one deterministic identity/path model; one uv install/provenance/capability probe; one discriminated manifest/shim/lock/list/remove implementation; manifest permits only fixed common fields and hot-fix PR/review/branch fields, with no arbitrary extension map; old shared-state manifests fail closed; no public/private Click manager surface, registry DB, daemon, dependency or plugin framework.
- **DoD / evidence:** shared-engine tests exercise numbered and hot-fix identities through the same functions; hot-fix install/reconcile retains exact PR/issue/reviewer/merge/branch gates but delegates mechanics; canonical interpreter is proven before mutation; package-installed bootstrap works; duplicate-mechanics architecture guard fixture is ready; focused Ruff/mypy/tests pass.
- **Parallel safety:** единственный owner shared engine, root selector и skill refactor. Stage-2 successors consume its stable APIs after completion and do not modify these files.

## WP-MYL-445-02 — Thin numbered command adapter

- **Stage:** `2`.
- **Tasks:** `2.1`, `2.2`, `2.3`, `2.4`.
- **Depends on:** `WP-MYL-445-01`.
- **Самостоятельный deliverable:** canonical `odcli slot install/list/remove` adds only numbered identity/replace policy and bounded output over the shared engine; `odcli-N` delegates ordinary commands and cannot manage slots.
- **Owned responsibility scope:** numbered Click adapter, command registration, typed result/output/public-leaf inventory and numbered policy tests. Critical files: new `commands/slot.py`, `commands/cli_parts/registration.py`, command models only if required, `tests/unit/test_cli_output_modes.py`, directly related adapter fixtures.
- **Contract surface:** positive canonical number; exact SHA; unused vs explicit replace intent; bounded list projection; canonical-only manager invocation; Rich/JSON/TOON parity; no uv/provenance/probe/manifest/shim/lock/remove implementation outside shared engine.
- **DoD / evidence:** adapter delegates every lifecycle effect; invalid/existing/running/corrupt cases fail through typed output; exact argv/exit survives; management rejects numbered context before engine discovery; public-leaf and machine parity tests pass.
- **Parallel safety:** пишет только command/registration/output/policy tests after WP-01; не изменяет shared engine/skill files или runtime migration/self-update files owned by WP-03.

## WP-MYL-445-03 — Runtime confinement, migration и update guards

- **Stage:** `2`.
- **Tasks:** `3.1`, `3.2`, `3.3`, `3.4`.
- **Depends on:** `WP-MYL-445-01`.
- **Самостоятельный deliverable:** every selected root confines SDK-owned state; only canonical startup adopts legacy storage; direct numbered/hot-fix update fails before planning/effects and canonical update ignores alternates.
- **Owned responsibility scope:** storage-migration selection, remaining global path consumers, self-update/maintenance/recovery context guards and focused path/migration/update tests. Critical files: `internal/storage_migration.py`, `internal/self_update*.py`, `commands/update.py`, related consumer/tests.
- **Contract surface:** canonical-only platformdirs adoption; explicit alternate roots start empty; launcher-specific remediation; canonical update never discovers/manages alternate tools; no lifecycle mechanics or manager metadata interpretation.
- **DoD / evidence:** provider inventory is complete; canonical migration regressions pass; populated canonical/legacy/neighbors remain unchanged on alternate first run; all alternate update modes reject before effects; filesystem spy proves zero cross-root access.
- **Parallel safety:** writes runtime consumers/migration/update only; не изменяет shared engine/skill files or numbered command/registration/output files owned by WP-02.

## WP-MYL-445-04 — Integrated reuse/isolation acceptance и publication gate

- **Stage:** `3`.
- **Tasks:** `4.1`, `4.2`, `4.3`, `4.4`.
- **Depends on:** `WP-MYL-445-02`, `WP-MYL-445-03`.
- **Самостоятельный deliverable:** publication-ready SHA with documentation and end-to-end proof that canonical, two numbered and two hot-fix revisions use one lifecycle implementation, isolated roots and selective cleanup.
- **Owned responsibility scope:** cross-component process/packaging fixtures, duplicate-mechanics architecture guard, documentation/help, final inventories/evidence and minimal repairs only for observed integration findings. Critical files: `tests/packaging/`, architecture inventory/guard, `README.md`, directly related fixtures/snapshots.
- **Contract surface:** one engine/two thin policies; five independent roots; canonical behavior unchanged; shared external project/Odoo/PostgreSQL/database/filestore/Docker/port resources explicitly disjoint; no scope expansion during repair.
- **DoD / evidence:** architecture guard finds lifecycle mechanics only in shared module; concurrent incompatible migrations stay confined by access spy, SQLite revision and digest evidence; canonical update preserves alternates; shared removal of one numbered or eligible hot fix preserves every neighbor; docs explain reuse, old-style remediation and external-resource boundary; focused/skill/packaging/repository/Ruff/mypy/inventory/diff/strict OpenSpec gates pass.
- **Parallel safety:** serial fan-in after both stage-2 siblings; shared integration fixtures and cross-domain repairs have one owner.

## Topology and Coverage Audit

Direct edges only:

- `WP-MYL-445-01 -> WP-MYL-445-02`
- `WP-MYL-445-01 -> WP-MYL-445-03`
- `WP-MYL-445-02 -> WP-MYL-445-04`
- `WP-MYL-445-03 -> WP-MYL-445-04`

Execution frontiers: stage 1 — shared lifecycle/hot-fix baseline; stage 2 — numbered adapter and runtime confinement concurrently; stage 3 — integrated join. Stage-2 siblings have non-overlapping production and primary-test write zones.

Task coverage complete and one-time:

| WP | OpenSpec tasks |
| --- | --- |
| `WP-MYL-445-01` | `1.1`, `1.2`, `1.3`, `1.4`, `1.5` |
| `WP-MYL-445-02` | `2.1`, `2.2`, `2.3`, `2.4` |
| `WP-MYL-445-03` | `3.1`, `3.2`, `3.3`, `3.4` |
| `WP-MYL-445-04` | `4.1`, `4.2`, `4.3`, `4.4` |

## Estimation Evidence and Assumptions

- Inspected: complete OpenSpec package; existing 254-line `fix_tool.py`; its sole skill caller and unit tests; reviewer compatibility path; `pyproject.toml` wheel/script boundary; central paths/storage migration; self-update provenance/maintenance paths; CLI registration/output/public-leaf tests; packaging and architecture inventories.
- Existing reusable work counted once: exact-SHA uv install, PR/issue linkage, manifest/shim generation, `flock`, reconciliation gates and uv uninstall in `fix_tool.py` are refactored, not reimplemented.
- Estimate includes implementation, focused/shared-engine/skill/process/packaging tests, documentation, repository gates and likely review repairs; deployment and live shared external-resource orchestration excluded.
- Estimate invalidates if scope expands to automatic migration of old shared-state fixes, arbitrary repositories/refs, Windows lifecycle support despite current POSIX `fcntl`, external-resource orchestration, or a new service/plugin API.
