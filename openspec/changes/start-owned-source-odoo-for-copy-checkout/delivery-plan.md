## Основание

- Planning issue: `MYL-241` / GitHub #83.
- OpenSpec change: `start-owned-source-odoo-for-copy-checkout`.
- Planning revision base: implementation review SHA `46efd7d882fa122120e149896d560340cc96777b`; original feature diff base remains `origin/main` at `9a862fbe8ff83d57c452144badbd178327d5e598`.
- Числовые optimistic, weighted и pessimistic totals хранятся только в свойствах planning issue `Estimate min, hours`, `Estimate, hours` и `Estimate max, hours` и подтверждены read-back.
- Оценка относится к оставшейся доработке от planning revision base одним опытным разработчиком, знакомым с Python/odcli и репозиторием, без AI-ускорения. Это active developer effort; unattended CI, очередь review и внешние ожидания исключены.
- Уверенность: средняя. Текущая implementation и review findings дают точную границу, но OS-level socket ownership inspection, честное размещение request-adjacent actions и regression isolation несут ограниченный platform/plan risk.
- Калибровка: qualitative, без исторического набора сопоставимых фактических трудозатрат. Основание — текущие `AuxiliaryRestoreSession`, persisted runtime identity helpers, Database Manager backup/restore boundaries, immutable checkout command и существующие focused test suites. Тесты для оценки не запускались.

## Топология исполнения

Режим: `single_wp_no_dag`.

Итоговое оценочное свойство planning issue находится в диапазоне, для которого protocol требует один all-covering work package. Потенциально разделимые production и test edits остаются в одной единице исполнения, потому что они меняют один lifecycle contract и должны проверяться совместно.

## WP-01 — Self-contained COPY source Database Manager lifecycle

- Покрытие tasks: T01, T02, T03, T04, T05, T06, T07, T08, T09 — каждый task покрыт здесь ровно один раз.
- Самостоятельный deliverable: COPY checkout использует существующий bounded auxiliary runtime, отклоняет любой unrecorded listener, переиспользует recorded runtime только при exact process-to-socket proof, повторяет proof непосредственно перед каждым auxiliary password-bearing request, показывает все preconditions в едином immutable plan и очищает только собственный process/secret config; shared mode и COPY recovery guarantees сохранены.
- Owned responsibility scope: `src/odoo_instance_sdk/resources/instance/auxiliary_restore.py`; Database Manager privileged request boundaries в `src/odoo_instance_sdk/resources/database/backup_restore_parts/backup.py` и `queries.py`; COPY command composition/step declaration в `src/odoo_instance_sdk/resources/environment/checkout.py` и `checkout_artifacts.py`; непосредственно связанные instance, backup/restore, environment и public lifecycle tests. Связанные fixtures, typed builders, snapshots, typing fixes и test helpers входят в scope этого WP; существующие export/caller files меняются только при прямой необходимости сохранить attachment contract.
- Critical shared files: `resources/instance/auxiliary_restore.py`, `resources/database/backup_restore_parts/backup.py`, `resources/database/backup_restore_parts/queries.py`, `resources/environment/checkout.py`, `resources/environment/checkout_artifacts.py`, `tests/unit/test_stopped_project_restore.py`, `tests/unit/resources/test_environment_checkout.py`, `tests/integration/test_database_lifecycle.py`.
- Contract surface: один active `AuxiliaryRestoreSession`; persisted-runtime reuse только после exact catalog/PID/create-time/process/socket proof; unrecorded occupied listener fail-closed без trust probe/secret/signal; free-port-only spawn; exact registered process handle; bounded readiness; source-instance-scoped revalidation immediately before each `master_pwd` request; distinct honest plan actions; nested cleanup/context reset; primary-error preservation; COPY-only attachment; public/private plan parity и inert dry-run.
- Definition of done / evidence: все scenarios delta spec имеют focused executable coverage; recorded and owned runtime paths prove exact listener binding; mismatch, ambiguity and inspection failure reject before HTTP/secret; identity drift immediately before source backup or standalone auxiliary restore prevents request invocation; stopped source COPY still starts and cleans owned runtime; timeout, early exit, preflight, backup, restore and cancellation preserve ownership-safe cleanup; cleanup failure does not hide primary failure; public/private plan ids/order match and dry-run is inert; target restore and shared mode retain behavior. Успешны focused pytest, Ruff, strict mypy, full unit suite, strict OpenSpec validation и `git diff --check`.
- Parallel-safety rationale: WP является единственной write-зоной и единственной единицей Implementer/WP Verifier. Параллельные siblings не создаются, поэтому общие lifecycle и checkout файлы не могут получить конфликтующие изменения.

## Coverage proof

`WP-01` покрывает полный диапазон `T01–T09` из `tasks.md` однократно; непокрытых или дублированных tasks нет.
