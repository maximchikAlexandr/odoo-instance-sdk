## Delivery Contract

- Task key: `MYL-445`.
- OpenSpec change: `add-numbered-odcli-slots`.
- Approved base: `origin/main` at `4a9369c3cab0f9a34bf3d3b2411f3ec0ac5bf5e6`.
- Delivery mode: `dag`.
- Estimate source: подтверждённые read-back properties корневой planning issue `Estimate, hours`, `Estimate min, hours`, `Estimate max, hours`; числовые totals намеренно не дублируются.
- Estimate basis: remaining active developer effort для одного опытного разработчика, знакомого с Python, Click, uv tool layouts, SQLite migrations, skill scripts и pytest этого репозитория, без AI-ускорения. Unattended CI, approval queues, meetings и external blocking исключены; тесты для оценки не запускались.
- Confidence: medium. Central paths, storage migration, self-update, exact-SHA fix-tool и packaging seams доступны; неопределённость сосредоточена в fail-closed legacy hot-fix handling, selector capability probe и multi-root concurrent acceptance.
- Calibration: evidence-based, uncalibrated; сопоставимые historical actuals отсутствуют.
- Topology rule: authoritative estimate property превышает threshold. После единого identity/root foundation существуют три независимых фронта с раздельными mutable write scopes: numbered manager/CLI, SDK runtime migration/update, existing hot-fix skill. Они соединяются только в integrated acceptance.

Stage mapping: `WP-MYL-445-01 -> 1`, `WP-MYL-445-02 -> 2`, `WP-MYL-445-03 -> 2`, `WP-MYL-445-04 -> 2`, `WP-MYL-445-05 -> 3`. Stages последовательны без пропусков; operational WIP в DAG не кодируется.

## WP-MYL-445-01 — Alternate identity и root foundation

- **Stage:** `1`.
- **Tasks:** `1.1`, `1.2`, `1.3`.
- **Depends on:** нет.
- **Самостоятельный deliverable:** validated numbered/hot-fix identity/path-only contract и canonical-default global-root selector с доказательством, что все SDK-owned global providers используют выбранный root, а real `HOME` и repository-local `.odcli` неизменны.
- **Owned responsibility scope:** central paths, compact alternate-launcher identity/manifest path types и provider inventory tests. Critical shared files: `src/odoo_instance_sdk/internal/paths.py`, новый узкий `internal/alternate_tool.py` или эквивалентный module, focused provider tests.
- **Contract surface:** canonical positive decimal identities; deterministic state/manager/tool/launcher/manifest/lock paths; absent selector означает `~/.odcli`; trusted explicit selector не меняет `HOME`; никаких shared provenance/capability, lock-acquisition или shim-rendering primitives, daemon, registry DB, dependency либо generic manager framework.
- **DoD / evidence:** canonical/numbered/hot-fix provider matrices проходят; direct global home writes инвентаризированы; invalid identity/path cases fail before filesystem mutation; project-local paths не redirected; focused Ruff/mypy/tests проходят.
- **Parallel safety:** единолично владеет shared identity/path contract; stage-2 successors только потребляют его после завершения.

## WP-MYL-445-02 — Numbered exact-SHA lifecycle и CLI

- **Stage:** `2`.
- **Tasks:** `2.1`, `2.2`, `2.3`, `2.4`, `2.5`.
- **Depends on:** `WP-MYL-445-01`.
- **Самостоятельный deliverable:** canonical `odcli slot install/list/remove` безопасно управляет numbered uv tools, а `odcli-N` держит lock, точно делегирует ordinary CLI и отвергает manager surface до discovery/effects.
- **Owned responsibility scope:** numbered lifecycle coordinator, его собственные uv exact-SHA provenance/capability probe, lock acquisition и manifest/shim rendering, CLI registration/output/public-leaf inventory и numbered-focused tests. Critical shared files: новый numbered manager module, `commands/slot.py`, `commands/cli_parts/registration.py`, `tests/unit/test_cli_output_modes.py`.
- **Contract surface:** fixed repository/full SHA; staged verified atomic publish; one manifest/lock; bounded list; fail-closed replace/remove; exact argv/exit; no shell; no manager reads from numbered context.
- **DoD / evidence:** mismatch publishes nothing; replace requires explicit idle ownership; running/corrupt/symlink/path-escape removal fails closed; removing one slot preserves canonical, hot-fix and numbered neighbors; output/public-leaf tests pass.
- **Parallel safety:** пишет только numbered manager/CLI surfaces и fixtures; provenance/capability, lock и shim logic принадлежат этому manager и не импортируются hot-fix workflow; не изменяет central paths/storage/self-update или `.agents/skills/odcli-autonomous-work`, принадлежащие siblings.

## WP-MYL-445-03 — Runtime confinement, migration и update policy

- **Stage:** `2`.
- **Tasks:** `3.1`, `3.2`, `3.3`, `3.4`.
- **Depends on:** `WP-MYL-445-01`.
- **Самостоятельный deliverable:** explicit alternate roots не принимают legacy/canonical state, все global consumers остаются confined, numbered/hot-fix direct update fail до planning/effects, canonical migration/update сохраняют прежний контракт.
- **Owned responsibility scope:** storage-migration selection, remaining global consumers, self-update/maintenance/recovery context guards и directly related path/migration/update tests. Critical shared files: `internal/storage_migration.py`, `internal/self_update*.py`, `commands/update.py`, focused tests.
- **Contract surface:** platformdirs adoption только canonical; alternate roots start empty; launcher-specific update remediation; canonical update не discovers/manages alternate roots; no access to manager metadata.
- **DoD / evidence:** populated canonical/legacy/neighbor fixtures remain unchanged on alternate first run; canonical migration regressions pass; every update mode rejects both alternate kinds before effects; filesystem spy proves zero cross-root access.
- **Parallel safety:** пишет только SDK path consumers, migration, self-update modules/tests; не изменяет numbered manager/registration или hot-fix skill/scripts/tests.

## WP-MYL-445-04 — Existing hot-fix workflow isolation

- **Stage:** `2`.
- **Tasks:** `4.1`, `4.2`, `4.3`, `4.4`.
- **Depends on:** `WP-MYL-445-01`.
- **Самостоятельный deliverable:** existing `odcli-fix-ISSUE` workflow публикует exact reviewed revision с `~/.odcli-fix-ISSUE`, не пишет canonical registry/state и selectively retires только доказанно eligible idle hot fix.
- **Owned responsibility scope:** `.agents/skills/odcli-autonomous-work/SKILL.md`, `compatibility-prompt.md`, `scripts/fix_tool.py`, собственные skill-local provenance/capability checks, lock acquisition и shim rendering, необходимые reviewer inputs и `tests/unit/test_odcli_autonomous_skill.py` plus directly related fixtures. Critical shared files ограничены этой skill-owned зоной.
- **Contract surface:** fixed SHA/repo/PR/issue/reviewer gates; manager metadata outside state/canonical; selector capability before publish; external-resource compatibility only; full-lifetime lock; existing merge/ancestry/issue/branch gates; exact selective cleanup; old-style shared-state manifests fail closed without adoption.
- **DoD / evidence:** two hot fixes use distinct roots; warning names SHA/root; direct update rejected through shared SDK guard; eligible retirement removes one root/tool/launcher/manifest and preserves all neighbors; active/corrupt/legacy cases retain artifacts and report remediation; skill tests pass.
- **Parallel safety:** пишет только existing skill scripts/docs/tests and consumes WP-01 identity/path contract; provenance/capability, lock и shim logic остаются внутри skill и не импортируют numbered manager; не изменяет SDK runtime/self-update or numbered CLI files owned by WP-02/WP-03.

## WP-MYL-445-05 — Integrated multi-root acceptance и publication gate

- **Stage:** `3`.
- **Tasks:** `5.1`, `5.2`, `5.3`, `5.4`.
- **Depends on:** `WP-MYL-445-02`, `WP-MYL-445-03`, `WP-MYL-445-04`.
- **Самостоятельный deliverable:** publication-ready SHA с documentation и end-to-end доказательством canonical, двух numbered и двух hot-fix revisions, incompatible migrations, update isolation и selective cleanup.
- **Owned responsibility scope:** cross-component packaging/process fixtures, documentation/help, final inventories/evidence и минимальные repairs только по observed integration findings. Critical shared files: `tests/packaging/`, `README.md`, architecture/output inventories и directly related fixtures/snapshots.
- **Contract surface:** five independent selected roots; canonical behavior unchanged; external project/Odoo/PostgreSQL/database/filestore/Docker/port resources explicitly disjoint; no scope expansion during repair.
- **DoD / evidence:** concurrent incompatible migrations/writes stay confined by access spy, SQLite revision and digest evidence; alternate first-run never adopts legacy/canonical; canonical update preserves alternate roots; numbered remove and one eligible hot-fix retirement preserve every neighbor; docs cover old-style remediation and external-resource boundary; focused/skill/packaging/repository/Ruff/mypy/inventory/diff/strict OpenSpec gates pass.
- **Parallel safety:** serial join after all stage-2 siblings; shared integration fixtures and cross-domain repairs have one owner.

## Topology and Coverage Audit

Direct edges only:

- `WP-MYL-445-01 -> WP-MYL-445-02`
- `WP-MYL-445-01 -> WP-MYL-445-03`
- `WP-MYL-445-01 -> WP-MYL-445-04`
- `WP-MYL-445-02 -> WP-MYL-445-05`
- `WP-MYL-445-03 -> WP-MYL-445-05`
- `WP-MYL-445-04 -> WP-MYL-445-05`

Execution frontiers: stage 1 — foundation; stage 2 — WP-02, WP-03, WP-04 concurrently; stage 3 — integrated join. Stage-2 siblings имеют непересекающиеся production и primary test write-зоны.

Task coverage complete and one-time:

| WP | OpenSpec tasks |
| --- | --- |
| `WP-MYL-445-01` | `1.1`, `1.2`, `1.3` |
| `WP-MYL-445-02` | `2.1`, `2.2`, `2.3`, `2.4`, `2.5` |
| `WP-MYL-445-03` | `3.1`, `3.2`, `3.3`, `3.4` |
| `WP-MYL-445-04` | `4.1`, `4.2`, `4.3`, `4.4` |
| `WP-MYL-445-05` | `5.1`, `5.2`, `5.3`, `5.4` |

## Estimation Evidence and Assumptions

- Inspected: central paths/storage migration, self-update command/coordinator/recovery, CLI registration, packaging self-update fixtures, complete autonomous-work skill, `fix_tool.py`, compatibility prompt и skill tests.
- Estimate includes implementation, focused/skill/process/packaging tests, documentation, repository gates and likely review repairs; deployment and live shared external-resource orchestration excluded.
- Estimate invalidates if scope expands to automatic migration of old shared-state hot fixes, arbitrary repositories/refs, cross-platform manager layouts beyond existing support, external-resource orchestration, or revisions without selector capability.
