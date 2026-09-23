## Основание

- Planning issue: `MYL-241` / GitHub #83.
- OpenSpec change: `start-owned-source-odoo-for-copy-checkout`.
- Base: `origin/main` at `9a862fbe8ff83d57c452144badbd178327d5e598`.
- Числовые optimistic, weighted и pessimistic totals хранятся только в свойствах planning issue `Estimate min, hours`, `Estimate, hours` и `Estimate max, hours` и подтверждены read-back.
- Оценка относится к оставшейся полной реализации одним опытным разработчиком, знакомым с Python/odcli и репозиторием, без AI-ускорения. Это active developer effort; unattended CI, очередь review и внешние ожидания исключены.
- Уверенность: средняя. Scope и существующие extension points инспектируемы, но остаются ограниченные риски вокруг безопасной проверки уже отвечающего внешнего Database Manager, сохранения единого prepared/public plan и error precedence при двойном отказе основной операции и cleanup.
- Калибровка: qualitative, без исторического набора сопоставимых фактических трудозатрат. Основание — текущие `AuxiliaryRestoreSession`, `_attach_auxiliary_restore_runtime`, immutable checkout command, COPY journal/rollback и существующие focused test suites. Тесты для оценки не запускались.

## Топология исполнения

Режим: `single_wp_no_dag`.

Итоговое оценочное свойство planning issue находится в диапазоне, для которого protocol требует один all-covering work package. Потенциально разделимые production и test edits остаются в одной единице исполнения, потому что они меняют один lifecycle contract и должны проверяться совместно.

## WP-01 — Self-contained COPY source Database Manager lifecycle

- Покрытие tasks: T01, T02, T03, T04, T05, T06, T07, T08, T09 — каждый task покрыт здесь ровно один раз.
- Самостоятельный deliverable: COPY checkout использует существующий bounded auxiliary runtime для запуска остановленного source Odoo, безопасно переиспользует responsive или доказанный recorded runtime, показывает возможный lifecycle в едином immutable plan и очищает только собственный процесс/secret config при любом выходе; shared mode и существующие COPY recovery guarantees сохранены.
- Owned responsibility scope: `src/odoo_instance_sdk/resources/instance/auxiliary_restore.py` и его export boundary; существующие callers в `src/odoo_instance_sdk/commands/db.py`; COPY command composition в `src/odoo_instance_sdk/resources/environment/checkout.py` и непосредственно связанные environment/instance tests. Связанные fixtures, typing fixes, snapshots и test helpers входят в scope этого WP. `settings.py` меняется только если интеграция не может сохранить его существующий COPY preflight/journal/rollback contract без локальной адаптации.
- Critical shared files: `resources/instance/auxiliary_restore.py`, `resources/instance/__init__.py`, `commands/db.py`, `resources/environment/checkout.py`, `tests/unit/test_stopped_project_restore.py`, `tests/unit/resources/test_environment_checkout.py`.
- Contract surface: общий auxiliary command attachment с явным insertion anchor; один active `AuxiliaryRestoreSession`; response-based reuse без ownership; persisted-runtime reuse только после PID/create-time/port proof; free-port-only spawn; точный registered process handle; bounded `/web/database/list` readiness; nested cleanup/context reset; primary-error preservation; COPY-only attachment; public/private plan parity и inert dry-run.
- Definition of done / evidence: все восемь scenarios delta spec имеют focused executable coverage; database restore сохраняет порядок captured steps; stopped source COPY успешно стартует и очищает owned runtime; responsive external и compatible recorded runtime не получают signal/stop; неизвестный occupied listener отклоняется до mutation; timeout, early exit, preflight, backup, restore и cancellation очищают ownership-safe state; cleanup failure не скрывает primary failure; COPY dry-run содержит sanitized lifecycle и ничего не запускает; shared command/plan остаётся прежним. Успешны focused pytest, Ruff, strict mypy, full unit suite, strict OpenSpec validation и `git diff --check`.
- Parallel-safety rationale: WP является единственной write-зоной и единственной единицей Implementer/WP Verifier. Параллельные siblings не создаются, поэтому общие lifecycle и checkout файлы не могут получить конфликтующие изменения.

## Coverage proof

`WP-01` покрывает полный диапазон `T01–T09` из `tasks.md` однократно; непокрытых или дублированных tasks нет.
