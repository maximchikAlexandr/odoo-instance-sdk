## Delivery Contract

- Task key: `MYL-445`.
- OpenSpec change: `add-numbered-odcli-slots`.
- Approved base: `origin/main` at `4a9369c3cab0f9a34bf3d3b2411f3ec0ac5bf5e6`.
- Delivery mode: `dag`.
- Estimate source: подтверждённые read-back properties корневой planning issue `Estimate, hours`, `Estimate min, hours`, `Estimate max, hours`; числовые totals намеренно не дублируются.
- Estimate basis: remaining active developer effort для одного опытного разработчика, знакомого с Python, Click, uv tool layouts, SQLite migrations и pytest этого репозитория, без AI-ускорения. Unattended CI, approval queues, meetings и external blocking исключены; тесты для получения оценки не запускались.
- Confidence: medium. Central path provider, storage migration, self-update, fix-tool analogue и packaging seams доступны в snapshot; неопределённость сосредоточена в полном inventory прямых global paths, crash-safe uv publication и двухревизионном packaging fixture.
- Calibration: evidence-based, uncalibrated; сопоставимые исторические actuals не предоставлены.
- Topology rule: authoritative estimate property превышает threshold. После foundation существуют два естественных независимых фронта с непересекающимися production write-зонами: lifecycle manager/CLI и runtime root/migration/update policy. Они соединяются только в integrated acceptance.

Полное соответствие stage: `WP-MYL-445-01 -> 1`, `WP-MYL-445-02 -> 2`, `WP-MYL-445-03 -> 2`, `WP-MYL-445-04 -> 3`. Stages являются последовательными топологическими слоями без пропусков; operational WIP в DAG не кодируется.

## WP-MYL-445-01 — Root и slot identity foundation

- **Stage:** `1`.
- **Tasks:** `1.1`, `1.2`, `1.3`.
- **Depends on:** нет.
- **Самостоятельный deliverable:** validated slot identity/path contract и единый canonical-default global-root selector со статическим/тестовым доказательством, что все SDK-owned global providers используют его, а repository-local `.odcli` и real `HOME` не меняются.
- **Owned responsibility scope:** central paths и новые компактные slot value/manifest types; напрямую связанные unit tests, fixtures, typing и docs, необходимые для foundation. Critical shared files: `src/odoo_instance_sdk/internal/paths.py`, новый узкий internal slot-domain module, provider inventory tests.
- **Contract surface:** positive canonical decimal `N`; deterministic manager/tool/launcher/state paths; absent selector означает `~/.odcli`; trusted absolute selector не меняет `HOME`; никаких daemon, registry database, dependency или второго path hierarchy.
- **DoD / evidence:** canonical и numbered provider matrices проходят; прямые global `Path.home()/.odcli` writes инвентаризированы и либо переведены на provider, либо доказано относятся к project-local/executable discovery; invalid number/path cases fail before filesystem mutation; Ruff/mypy focused gates проходят.
- **Parallel safety:** foundation единолично владеет shared types/path contract; successors только потребляют его после завершения.

## WP-MYL-445-02 — Exact-SHA slot lifecycle и canonical CLI

- **Stage:** `2`.
- **Tasks:** `2.1`, `2.2`, `2.3`, `2.4`, `3.2`.
- **Depends on:** `WP-MYL-445-01`.
- **Самостоятельный deliverable:** canonical `odcli slot install/list/remove` создаёт, проверяет, публикует, переназначает и безопасно удаляет numbered uv tools; generated launcher держит lifecycle lock и точно делегирует ordinary CLI.
- **Owned responsibility scope:** slot lifecycle command builder/coordinator, uv exact-SHA/provenance probe, manifest/launcher rendering, CLI registration/output projection/public-leaf inventory и directly related manager tests/fixtures. Critical shared files: новый `internal/slot_*` lifecycle module, новый `commands/slot.py`, `commands/cli_parts/registration.py`, `tests/unit/test_cli_output_modes.py`, manager-focused tests.
- **Contract surface:** fixed credential-free repository; full lowercase SHA; staged install and verified repo+commit+selector capability before atomic publish; one JSON manifest and one lock; bounded list; fail-closed exact-target remove; unchanged argv boundaries and exit status; no shell and no execution of slot code during list.
- **DoD / evidence:** install mismatch and invalid identity publish nothing; implicit overwrite is rejected; explicit idle replace is recoverable; running lock blocks replace/remove; modified/symlinked/path-escaping artifacts block deletion; removing one synthetic slot preserves canonical and neighbor sentinels; Rich/JSON/TOON and public-leaf tests pass.
- **Parallel safety:** после WP-01 пишет lifecycle/CLI modules и manager test fixtures; не изменяет central path/storage migration/self-update modules, принадлежащие параллельному WP-03.

## WP-MYL-445-03 — Runtime isolation, migration и update policy

- **Stage:** `2`.
- **Tasks:** `3.1`, `3.3`, `3.4`.
- **Depends on:** `WP-MYL-445-01`.
- **Самостоятельный deliverable:** explicit numbered root никогда не принимает canonical/legacy state, а numbered management/update modes fail до planning/effects; canonical legacy migration и canonical self-update сохраняют прежний контракт.
- **Owned responsibility scope:** startup storage-migration selection, propagation selected root through remaining global consumers, self-update/maintenance/recovery guards, numbered-context management guard contract и directly related path/migration/update tests. Critical shared files: `internal/storage_migration.py`, `internal/self_update*.py`, `commands/update.py`, focused storage/self-update tests.
- **Contract surface:** platformdirs discovery/copy/rewrite/cleanup только для canonical default; numbered root starts empty; every global lock/journal/catalogue write descends from selected root; `odcli-N update` directs to canonical exact-SHA replace before ref resolution, lock, snapshot, uv or migration; ordinary update neither discovers nor manages slots.
- **DoD / evidence:** populated canonical and legacy fixtures remain byte-identical after first numbered startup; canonical migration regressions remain green; update check/dry-run/mutate/maintenance/recovery variants all reject numbered context before effects; cross-root spy/inventory proves no canonical or neighbor access.
- **Parallel safety:** после WP-01 пишет path consumers, migration и update modules/tests; не изменяет slot lifecycle/CLI registration modules или manager fixtures WP-02.

## WP-MYL-445-04 — Integrated dual-revision acceptance и publication gate

- **Stage:** `3`.
- **Tasks:** `4.1`, `4.2`, `4.3`, `4.4`.
- **Depends on:** `WP-MYL-445-02`, `WP-MYL-445-03`.
- **Самостоятельный deliverable:** один publication-ready SHA с документацией и end-to-end доказательством двух exact revisions, несовместимых SQLite migrations, safe replace/remove и неизменности canonical/neighbor state.
- **Owned responsibility scope:** packaging acceptance fixtures, cross-component repair исключительно для verified integration findings, user documentation/help и final quality evidence. Critical shared files: `tests/packaging/`, `README.md`, architecture/output inventories и любые directly related snapshots/fixtures.
- **Contract surface:** два slot uv layouts и два selected roots; canonical `odcli`/`~/.odcli` и `odcli-fix-<issue>` semantics unchanged; external Odoo/PostgreSQL/database/filestore/Docker/port resources не считаются изолированными; scope/design change возвращается в planning revision.
- **DoD / evidence:** packaging test устанавливает разные exact SHAs/fixtures, одновременно выполняет несовместимые catalogue migrations и доказывает root confinement; replace сохраняет только selected state и меняет verified executable; remove одного slot сохраняет canonical и neighbor byte-for-byte; corrupted identity fails closed; docs/help перечисляют isolated/non-isolated resources; focused/packaging/standard tests, Ruff format/check, strict mypy, public-leaf/output/architecture inventories, `git diff --check` и strict OpenSpec validation проходят с записанными command/exit-code evidence.
- **Parallel safety:** serial join после обоих stage-2 siblings; вправе делать минимальные cross-domain repairs только по фактическим integration failures.

## Topology and Coverage Audit

Прямые edges:

- `WP-MYL-445-01 -> WP-MYL-445-02`
- `WP-MYL-445-01 -> WP-MYL-445-03`
- `WP-MYL-445-02 -> WP-MYL-445-04`
- `WP-MYL-445-03 -> WP-MYL-445-04`

Execution frontiers: stage 1 — foundation; stage 2 — `WP-MYL-445-02` и `WP-MYL-445-03` одновременно; stage 3 — integrated join. Stage 2 имеет две независимые production write-зоны и поэтому удовлетворяет требованию реальной параллельности.

Task coverage полное и однократное:

| WP | OpenSpec tasks |
| --- | --- |
| `WP-MYL-445-01` | `1.1`, `1.2`, `1.3` |
| `WP-MYL-445-02` | `2.1`, `2.2`, `2.3`, `2.4`, `3.2` |
| `WP-MYL-445-03` | `3.1`, `3.3`, `3.4` |
| `WP-MYL-445-04` | `4.1`, `4.2`, `4.3`, `4.4` |

## Estimation Evidence and Assumptions

- Inspected implementation: `internal/paths.py`, `internal/storage_migration.py`, self-update command/coordinator/recovery path, CLI lazy registration, packaging self-update fixtures и autonomous fix-tool exact-SHA/shim/lock analogue.
- Estimate включает implementation, unit/process/packaging verification, documentation, repository gates и likely review repairs; deployment, live Odoo/PostgreSQL provisioning и isolation external resources исключены.
- Оценка станет недействительной, если scope расширится до cross-platform manager beyond supported uv layouts, automatic state cloning/backup, arbitrary repository origins/refs, external-resource orchestration или backward support revisions без root-selector capability.
