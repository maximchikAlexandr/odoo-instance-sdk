## Context

После merge PR #103 base `f7c3f7c9093529d6744c30745e220efb9aea8f80` имеет честный `make mutation`: runner сначала выполняет bounded real-mutmut smoke, затем один `mutmut run --max-children 32`, после успеха собирает `mutmut results --all=true` и сохраняет stage-labelled `.artifacts/mutation/results.txt`. Workflow запускает этот контракт одним Ubuntu job с `timeout-minutes: 30` и всегда загружает диагностический artifact.

Полный scope по-прежнему ограничен пятью файлами в `[tool.mutmut].only_mutate`, но все 298 мутантов делят один runner budget. Наблюдаемый clean-main запуск за 34 минуты классифицировал только 98 мутантов; 200 остались `not checked`. Отдельный полный запуск того же SHA дал 246 killed и 52 survived, что является доступной current-main baseline evidence, но не доказывает укладывание GitHub-hosted workflow в timeout.

Изменение затрагивает только developer/CI tooling. Production SDK/CLI, public API и required PR checks остаются вне scope. `pyproject.toml` остаётся единственным каноническим перечнем mutation targets; новый workflow не должен поддерживать второй ручной список.

## Goals / Non-Goals

**Goals:**

- назначить каждый из пяти канонических targets ровно одному параллельному shard и завершить каждый shard в ограниченном CI budget;
- сохранить один entrypoint `make mutation`, bounded real-mutmut smoke, точные non-zero child statuses и failure-safe diagnostics;
- доказуемо отклонять потерянный, дублированный, незавершённый или непарсабельный shard до публикации aggregate summary;
- публиковать единый итог killed/survived/timeout/suspicious/not-checked и требовать `not checked == 0`;
- fail closed при росте survived или timeout относительно version-controlled current-main baseline.

**Non-Goals:**

- менять пять mutation targets, подключать второй framework или мутировать весь package;
- требовать нулевой survivor/timeout score либо превращать workflow в required PR gate;
- менять production modules, SDK/CLI semantics или обычный `make pr`;
- создавать сервис, базу данных, reusable workflow framework или динамический scheduler для mutation jobs.

## Decisions

### 1. Матрица строится из канонического `pyproject.toml`

Небольшой stdlib-only helper читает `[tool.mutmut].only_mutate`, валидирует непустой уникальный список ожидаемых repository-relative Python paths и выдаёт GitHub matrix JSON с `target` и стабильным filesystem-safe `shard` id. Prepare job передаёт этот JSON через job output; workflow YAML не содержит второго списка targets.

Каждый matrix job вызывает `make mutation MUTATION_TARGET=<exact-path>`. Runner перечитывает тот же TOML и до запуска child process отклоняет отсутствующий либо неоднозначный target. Для mutmut exact path преобразуется в module-prefix filter, который применяется и к `mutmut run`, и к `mutmut results`; unfiltered `make mutation` остаётся локальным full-scope режимом.

Альтернатива — вручную продублировать пять entries в YAML — короче на несколько строк, но допускает silent drift и нарушает требование exact-once partition. Автоматическое балансирование по историческим durations отклонено: измерений по targets нет, а пять независимых files уже являются минимальной устойчивой границей.

### 2. Один shard владеет одним target и сохраняет прежний runner contract

Каждый shard устанавливает те же frozen `mutation` и `test` groups, выполняет существующий bounded integration smoke, запускает выбранный target с существующим `--max-children 32`, затем получает только выбранную result projection. Per-shard timeout устанавливается в 45 минут: он остаётся bounded, но даёт запас относительно shared-run наблюдения, пока параллелизм между файлами устраняет общий 30-minute bottleneck.

Runner сохраняет `.artifacts/mutation/results.txt`, stage labels и первый исходный non-zero status. После успешного result command он проверяет, что отчёт содержит числовые `total`, `killed`, `survived`, `timeout`, `suspicious` и `not checked`, что сумма terminal и unchecked категорий равна total, total положителен и `not checked` равен нулю. Неполная классификация становится отдельной non-zero runner failure, а не успешной диагностикой.

Альтернатива — только увеличить timeout единого job — не создаёт completeness contract и продолжает ставить все targets под один failure/compute budget. Несколько последовательных per-file запусков внутри одного job отклонены, потому что не устраняют общий deadline bottleneck.

### 3. Diagnostics публикуются per shard, aggregate job всегда проверяет полный набор

Каждый matrix job использует `if: always()` upload с уникальным artifact `mutation-results-<shard>` и `if-no-files-found: error`. Matrix `fail-fast` отключён, чтобы отказ одного target не отменял диагностику остальных.

Aggregate job имеет `needs` на prepare и matrix, выполняется с `if: always()`, скачивает все matching artifacts и запускает тот же stdlib helper в aggregate mode. Helper требует ровно один отчёт для каждого canonical target, запрещает неизвестные/повторные shard ids, повторяет арифметическую completeness validation и создаёт `.artifacts/mutation/summary.txt` с target rows и общими counts. Отсутствующий или failed shard остаётся workflow failure даже если aggregate смог обработать остальные отчёты. Summary загружается с fail-on-missing semantics.

Альтернатива — положиться только на matrix conclusion — не доказывает exact target coverage и не создаёт единый baseline-comparable result. Слияние mutmut cache/database отклонено: aggregate нуждается только в стабильном текстовом contract, а объединение внутренних state files связывает workflow с private storage layout mutmut.

### 4. Baseline сравнивается на aggregate totals

Repository хранит один reviewable JSON baseline с source SHA/evidence и числовыми `survived`/`timeout`. Начальные значения берутся из полного current-main запуска на `f7c3f7c…`, уже зафиксированного в issue evidence. Aggregate helper падает, если суммарный survived или timeout превышает baseline; killed и suspicious всегда публикуются, но не имеют отдельного blocking threshold. `not checked` никогда не baseline: успешный audit требует нуля.

Уменьшение survived/timeout не переписывает baseline автоматически. Отдельный reviewable commit после подтверждённого полного запуска снижает соответствующее значение; увеличение возможно только через явно одобренное изменение policy/evidence, а не как побочный эффект workflow.

Per-target baseline отклонён: имеющееся полное evidence содержит aggregate counts, но не достоверное распределение по файлам. Автоматическое обновление artifact-derived baseline отклонено как самоподтверждающийся gate.

### 5. Contract tests проверяют topology, парсер и failure edges без полного audit

Focused tests вызывают helper на временных TOML/reports и проверяют unique exact-once matrix, стабильные shard ids, missing/duplicate/unknown reports, арифметику, `not checked`, baseline regression и deterministic summary. Runner tests проверяют target validation/filtering и сохранение exact child exit. CI contract test семантически проверяет prepare → matrix → aggregate topology, disabled fail-fast, bounded timeout, always-upload и отсутствие literal target duplication в YAML. Полный real-mutmut workload остаётся только scheduled/manual acceptance evidence.

## Risks / Trade-offs

- **[Один target всё ещё превышает 45 минут]** → manual `workflow_dispatch` является обязательным acceptance gate; изменение не принимается, пока каждый shard не завершится в configured timeout.
- **[Формат `mutmut results` меняется в допустимом `<4` диапазоне]** → frozen lock и parser fixtures фиксируют текущий вывод; непарсабельный отчёт fail closed вместо ложного успеха.
- **[Пять jobs расходуют больше суммарных runner-minutes]** → scope остаётся пятью файлами, jobs выполняются параллельно и запускаются только weekly/manual; сложное duration-balancing отсутствует до появления измерений.
- **[Matrix job failure лишает aggregate части отчётов]** → `fail-fast: false`, per-shard `if: always()` upload и aggregate `if: always()` сохраняют доступные diagnostics и явно называют missing shards.
- **[Aggregate baseline скрывает перераспределение survivors между targets]** → это ограничение исходного evidence; exact per-target rows публикуются в summary, а per-target gate вводится только после подтверждённой per-target baseline revision.

## Migration Plan

1. Добавить stdlib helper и focused tests для canonical matrix, result parsing, aggregation и baseline comparison.
2. Расширить runner/Make target проверенным `MUTATION_TARGET`, symmetric run/results filtering и terminal-completeness failure; сохранить unfiltered local mode.
3. Добавить current-main baseline file с evidence SHA и contract tests.
4. Перестроить workflow в prepare, five-way matrix и always-run aggregate; сохранить scheduled/manual triggers и failure-safe uploads.
5. Обновить contributor documentation и выполнить focused lint/type/unit/OpenSpec checks.
6. Запустить `workflow_dispatch` на implementation head, подтвердить пять завершённых shard reports, aggregate totals, нулевой `not checked` и отсутствие timeout job; сохранить URL и artifact evidence в handoff.

Rollback выполняется одним revert planning/implementation change: production state и migrations отсутствуют. Старый единый workflow может быть восстановлен без преобразования данных; generated artifacts и mutmut state остаются disposable.

## Open Questions

Нет. Значения baseline и source SHA заданы issue evidence; runtime acceptance подтверждается обязательным manual workflow run.
