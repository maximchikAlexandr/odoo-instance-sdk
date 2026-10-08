## Основание планирования

- Planning issue: `MYL-396`; исходное требование: GitHub issue `#127`.
- OpenSpec change: `fix-ps-postgresql-attribution-plan`.
- Approved source snapshot: `origin/main` at `c1e57b79f39e529a50c25818134c06309384ee23`.
- Единственный источник числовой оценки: подтверждённые read-back properties корневой planning issue `Estimate, hours`, `Estimate min, hours` и `Estimate max, hours`.
- Основа оценки: remaining scope до всех acceptance scenarios для одного опытного разработчика, знакомого с Python, msgspec, PostgreSQL transport и command ledger этого репозитория, без AI-ускорения. Unattended CI, очереди review и внешние ожидания исключены; тесты в ходе оценки не запускались.
- Уверенность: средняя. Путь дефекта, canonical PostgreSQL builder/transport, process ledger, recording executor и test seams доступны в inspected snapshot. Основная неопределённость связана с согласованием captured catalogue inputs с snapshot ownership и с полной классификацией process/response failures.
- Калибровка: uncalibrated; сопоставимые исторические трудозатраты не предоставлены.

Режим реализации: `single_wp_no_dag`. Authoritative `Estimate, hours` property находится в пределах обязательного single-package threshold, а production seam и его boundary tests имеют общую write-зону.

## WP-01 — Предварительно спланированная PostgreSQL attribution для `ps`

- **Task coverage:** `1.1`, `1.2`, `1.3`, `2.1`, `2.2`, `2.3`, `3.1`, `3.2`, `3.3`, `3.4` из `tasks.md`, каждый task покрыт ровно один раз.
- **Самостоятельный deliverable:** `EnvironmentMonitor.processes_command()` возвращает один immutable inspectable command, содержащий exact captured `pg_stat_activity` steps даже при construction-time отсутствии `psql`; успешная attribution потребляет каждый требуемый step один раз, а spawn/timeout и другие bounded PostgreSQL failures деградируют только затронутую backend group и сохраняют успешный `ProcessInventory`.
- **Owned responsibility scope:** command construction and catalogue credential capture в `src/odoo_instance_sdk/resources/monitor/collection_parts/collect.py`; backend specification consumption, parsing и typed degradation в `src/odoo_instance_sdk/internal/process_inventory.py`; при необходимости узкое переиспользование canonical `src/odoo_instance_sdk/internal/pg/{builder,transport}.py`; напрямую связанные unit/public-boundary/CLI output tests, fixtures, documentation и служебные verification files. Critical shared files: `collect.py`, `process_inventory.py`, `tests/unit/test_process_inventory.py`, `tests/unit/test_cli_output_modes.py`.
- **Contract surface:** неизменные публичные `EnvironmentMonitor.processes_command()` / `processes()` signatures; existing `ProcessInventory` schema and ownership rules; one canonical snapshot; exact private/public `PsqlSpecification` with canonical `_require_binary=False` deferred-capability behavior; concrete `ProcessSpawnError` / `ProcessTimeoutError` degradation; `RunContext` equality and single-consumption ledger; existing `BackendUnavailabilityReason`; common Rich/JSON/TOON projection.
- **Definition of done / evidence:** все покрытые task checkboxes выполнены; strict delta scenarios доказаны recording-executor cases для success и bounded failures; отдельный public-boundary regression доказывает, что resolver возвращает отсутствие executable, construction создаёт inspectable literal-`psql` step без `FileNotFoundError`, executor spawn failure даёт только `psql_missing`, timeout даёт `timeout`, а `UnplannedStepError` / `DuplicateStepError` не перехватываются; plan/fingerprint/error text не раскрывают password; нет второго snapshot/backend sample или process-boundary bypass; focused tests, architecture/process-boundary tests, Ruff format/lint, strict mypy, `git diff --check` и strict OpenSpec validation завершаются успешно; final diff не содержит unrelated production scope или новой generic abstraction.
- **Parallel-safety rationale:** credential capture, exact plan assembly, runner consumption, classification и principal regressions меняют один связный command/inventory seam и общие test fixtures. Разделение создало бы конфликтующие write-зоны без независимо поставляемого результата, поэтому весь scope принадлежит одному WP.

## Coverage proof

| Work package | OpenSpec tasks | Coverage |
| --- | --- | --- |
| `WP-01` | `1.1`–`1.3`, `2.1`–`2.3`, `3.1`–`3.4` | Все tasks покрыты ровно один раз |
