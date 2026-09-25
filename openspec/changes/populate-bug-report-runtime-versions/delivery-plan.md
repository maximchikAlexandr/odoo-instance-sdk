## Основание поставки

- Task key: `MYL-282`.
- OpenSpec change: `populate-bug-report-runtime-versions`.
- Approved base: `origin/main` на зафиксированном planning snapshot.
- Режим реализации: `single_wp_no_dag`, выбран по authoritative property `Estimate, hours`, значение которого находится в диапазоне обязательного single-WP режима.
- Числовые estimate totals являются authoritative только в properties planning issue: `Estimate, hours`, `Estimate min, hours`, `Estimate max, hours`.
- Основа оценки: полный остающийся scope для одного опытного разработчика, знакомого с Python/Click и архитектурой репозитория, без AI acceleration. Тесты для получения оценки не запускались.
- Уверенность: средняя. OpenSpec, production path, process boundary, project/runtime resolution и тестовые аналоги изучены; основная неопределённость связана с аккуратным встраиванием optional PostgreSQL steps и их ledger accounting.
- Калибровка: uncalibrated — сопоставимые фактические замеры выполнения такого изменения отсутствуют.

## WP-MYL-282-01 — Project-aware runtime versions in bug-report drafts

### Покрытие OpenSpec tasks

WP однократно покрывает все tasks: `1.1`, `1.2`, `1.3`, `2.1`, `2.2`, `3.1`, `3.2`, `3.3`, `4.1`.

### Самостоятельный deliverable

Совместимое изменение `bug_report_init_command()`, которое в managed project планирует и выполняет независимые bounded read-only probes, записывает безопасные конкретные версии Odoo/PostgreSQL при успехе и сохраняет независимый `unknown` fallback при любой недоступности. Публичная CLI regression, SDK/process-contract проверки и repository gates входят в тот же deliverable.

### Owned responsibility scope

- Root-cause flow в `src/odoo_instance_sdk/bug_report.py`: process-free project snapshot, probe construction, execution/fallback, template inputs и immutable plan composition.
- Переиспользование существующих контрактов `internal.context`, `internal.project_runtime`, `internal.pg.server`, `resources.postgres` и `internal.proc` без второго parser/runner/provider hierarchy.
- Критические shared files: `src/odoo_instance_sdk/bug_report.py`, `src/odoo_instance_sdk/internal/context.py`, `tests/unit/test_bug_report.py`, `tests/unit/internal/test_context.py`, а при доказанной необходимости — непосредственно связанные process/output contract tests.
- В scope также входят необходимые рядом расположенные fixtures, parametrized cases, docs/contract adjustments и служебные test files, если они не расширяют продуктовый scope.
- Production behavior submit/review, публичные модели результата, CLI arguments и draft schema вне version values не входят в изменение.

### Contract surface

- `bug_report_init_command(title=..., kind=...)` сохраняет публичную сигнатуру, result model и dry-run contract.
- Все child processes заранее представлены в immutable public plan и имеют соответствующие private prepared snapshots; `shell=False`, read-only flags, time bounds и ledger accounting обязательны.
- Construction snapshot использует только filesystem/read-only catalog, существующие `internal.repo_key.git_common_dir()` и manifest reader; command-backed `resolve_project()` и `git_worktree.rev_parse_*` на этом пути запрещены.
- Odoo source: текущий managed-project runtime выполняет `odoo_bin --version`; принимается только строгий нормализованный token.
- PostgreSQL source: существующий server-summary plan/collector возвращает фактический `server_version`; сервис не запускается.
- Каждый provider деградирует в `unknown` независимо. Secrets, raw stdout/stderr и exception text не пересекают private execution boundary.

### Definition of Done и evidence

- Все OpenSpec tasks отмечены выполненными только после соответствующего production/test результата.
- Parametrized tests доказывают success, independent fallback, non-zero, timeout, oversized/multiline/unsafe output и отсутствие managed project.
- Command-plan tests доказывают immutable capture, redaction, read-only/non-shell execution, time bounds и полное consume/skip accounting; отдельный spawn trap падает при любом `execute()`/`spawn()` во время command construction.
- Публичный `CliRunner` regression создаёт draft с обеими версиями из deterministic managed-project providers; dry-run под тем же spawn trap не запускает ни process resolution, ни probes, ничего не создаёт и возвращает полный applicable preview.
- Проходят focused bug-report tests, CLI output-boundary tests, Ruff, mypy и reproducible core gate из `CONTRIBUTING.md`; команды и exit codes фиксируются в implementation handoff.
- Diff остаётся минимальным: без новой зависимости, второго project parser, нового process runner и speculative abstraction.

### Обоснование последовательного исполнения

Все части меняют один command plan и его общий ledger/fallback contract; tests зависят от окончательной формы тех же prepared steps. Разделение на параллельные write-зоны создало бы конфликт в `bug_report.py` и тестовых fixtures без самостоятельных deliverables, поэтому один WP является естественной и policy-required единицей исполнения.
