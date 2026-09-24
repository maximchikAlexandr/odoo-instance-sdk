## Context

На verified base `9a862fbe8ff83d57c452144badbd178327d5e598` CLI уже имеет один typed `OutputMode`, immutable envelope v1, command-local Rich projections и frozen process inventory. В `src/odoo_instance_sdk/commands/` найдено 32 прямых создания `Table` в 11 модулях. Большинство полагается на Rich defaults, поэтому строки не разделены; `ps` и широкий `env list` явно задают `box=None`, а узкий `env list` возвращает `Text` blocks. `doctor`, `env show`, detached `run`, `deps verify` и `module install-order` строят structured prose.

Rich-проекции некоторых команд создают `Console(record=True)` без in-memory `file`, печатают renderable в текущий stdout, затем возвращают `export_text()` в общий `emit()`. Это делает projection нечистой и объясняет наблюдавшуюся двойную отрисовку `module ls`: terminal write происходит и внутри projection, и в единственной общей emission point.

Данные для `ps` уже представлены `ProcessInventory`: shared resources, main checkout и environments содержат Odoo group, owned cluster, backend groups и bounded external contributions. Поэтому изменение presentation не требует нового collector или публичной модели. Состояние PostgreSQL уже разложено на lifecycle `ClusterSnapshot.state` и typed reasons (`unavailability_reason`, `server_unavailability_reason`); противоречие возникает, когда renderers смешивают эти оси или превращают failure метрики в lifecycle state.

Change `refactor-cli-output-boundary` остаётся отдельным источником базовых output contracts. Этот change расширяет текущую реализацию на main и не изменяет его исторические artifacts.

## Goals / Non-Goals

**Goals:**

- Дать всем bounded structured Rich results одну видимую table geometry без нового renderer framework.
- Сохранить command-local ownership колонок, labels, row mapping и semantic styling.
- Сделать narrow layouts таблицами, которые помещают вторичные facts в `Details` и сохраняют primary identity/status.
- Свести каждый `ps` section к одной общей process table без изменения `ProcessInventory` и collection path.
- Сделать Rich projection pure: построить/serialize renderable без записи в stdout; terminal emission выполняет только `emit()` или явный one-shot/live owner.
- Разделить PostgreSQL lifecycle state и availability/detail reason одинаково во всех human views.
- Защитить JSON/TOON/native stream contracts и зафиксировать presentation matrix тестами.

**Non-Goals:**

- Новый generic renderer interface, registry, DSL, plugin system или публичный table model.
- Изменение CLI envelope v1, typed SDK/FastAPI schemas, collectors, process attribution или database lifecycle.
- Добавление dependency поверх уже установленного Rich.
- Табличная обёртка raw/native streams (`run` foreground, `shell`, `psql`, `logs --follow`, raw `eval`/`exec`) или scalar `env path`.
- Переработка цветов/темы, interactive live lifecycle или машинных serializers.

## Decisions

### D1: Один style-only table factory, данные остаются command-local

Добавить в `commands/output.py` `bordered_table(*headers: str, title: str | None = None) -> Table`, который возвращает стандартный Rich `Table` с `box=box.SQUARE`, `show_lines=True`, `header_style="bold"` и `safe_box=True`. Callers добавляют/настраивают columns и rows обычным Rich API; helper не знает command, typed result, rows или business fields.

Каждая существующая projection продолжает рядом с командой выбирать columns, styles и row values. Прямые `Table(...)` в production `commands/` заменяются вызовом helper; source-contract test разрешает прямой constructor только внутри helper. Это минимальная общая точка для инварианта и не нарушает существующее требование держать concrete Rich renderers рядом с командами.

Альтернатива: renderer base class/registry и декларативные schemas. Отклонена как лишняя архитектура для одного style contract.

### D2: Проекция не пишет в terminal

Добавить `render_rich_text(renderable: RenderableType, *, width: int = 180) -> str` для преобразования готового Rich renderable в строку через `Console(file=StringIO(), color_system=None, width=width)`. Command-local projection строит table/group и возвращает результат utility; она не создаёт console, связанный с `sys.stdout`, и не вызывает общий emitter.

`emit()` остаётся единственной точкой normal bounded output. `_print_env_list_human`, `_print_ps_human` и Live остаются явными owners для renderables, которые не проходят через string projection. Тест `module ls` проверяет один header/row block и одну invocation projection/emission path.

Альтернатива: локально добавлять `StringIO` в каждый renderer. Отклонена: дублирует легко забываемую safety detail и не защищает новые commands.

### D3: Responsive tables сокращают columns, а не форму

`env list` больше не переключается на `Text` blocks. На ширине 80 он использует compact bordered columns с primary name/state и многострочным `Details` для branch, database, Git и provider facts; на 120 добавляет самостоятельные high-value columns; на 180 использует expanded table. Worktree path folds/shortens только в human view, как сейчас.

Остальные tables используют Rich wrapping/folding и command-local column ratios/no-wrap только для коротких identifiers. Значения не отбрасываются: secondary fields переходят в `Details`. Empty collections получают одну explicit empty row или table-local `Status/Details` row, а unavailable values отображаются как `—` вместе с typed reason в `Details`, когда reason существует.

Альтернатива: сохранять free-form compact blocks. Отклонена, потому что нарушает единый визуальный язык и лишает narrow output column/row boundaries.

### D4: `ps` использует один row adapter и одну table на section

Существующие section headings и порядок `shared → main checkout → environments` сохраняются. Для каждой section строится ровно одна table с primary columns `Type`, `State`, `PID / scope`, `Processes`, `CPU`, `Memory`, `Details`.

- Odoo group: root/child scope и aggregate metrics; database, endpoint и branch/commit в `Details`.
- Owned PostgreSQL cluster/container: lifecycle state, confirmed container PID scope, container metrics; endpoint, version, connections и availability reasons в `Details`.
- Backend group: database/process scope, connection count and metrics; attribution reason и connection identity в `Details`.
- External contribution: confirmed scope/metrics; source, stable identity и availability/completeness в `Details`.

Отсутствующий объект не выдумывается. Stopped/unavailable entries остаются явными rows только когда typed inventory уже доказывает owner/entry; произвольные system processes не сканируются. Storage footprint остаётся отдельной section fact и не маскируется под process row.

Альтернатива: отдельная table на каждый product kind. Отклонена — это текущая несовместимая форма, которую issue требует заменить.

### D5: Lifecycle state и availability — разные cells

Human adapters получают lifecycle state только из canonical typed `state`. `stats_failed`, `query_failed`, missing metrics и другие typed reasons показываются в `Details`/availability, но не заменяют `healthy` на `stopped`. `postgres_state_cells(state: PostgresClusterState, *reasons: str | None) -> tuple[str, str]` возвращает `state.value` и стабильную de-duplicated строку непустых reasons для `Details`; formatter используется в `ps`, `env list`, `env show`, `doctor` и `postgres status`, не собирает данные и не меняет exit semantics.

Тесты подают одинаковый frozen cluster input во все affected projections и сравнивают lifecycle label плюс reason. Между независимыми invocations реальный state может измениться; контракт запрещает противоречие для одного source state, а не обещает cross-time snapshot transaction.

Альтернатива: расширить `ClusterSnapshot` новым presentation status. Отклонена: текущая модель уже содержит обе необходимые оси, новая schema создала бы дублирование.

### D6: Structured prose получает небольшие локальные tables

`doctor` группирует checks по global/project/environment section и строит на section одну table `Check | Status | Details`, включая facts/remediation как wrapped details; drift остаётся отдельной table в соответствующей section. `env show` строит `Scope | Field | Value`; detached `run` — `Field | Value`; `deps verify` — `Check | Status | Details`; `module install-order` — `Order | Module`. Repeated multi-target plans/results используют одну row на target с outcome/details.

True scalar/native exceptions остаются как есть. Простая success line без нескольких named fields не превращается в искусственную one-cell table.

### D7: Presentation gate сочетает structural и behavioral tests

Добавить:

- source guard для отсутствия direct `Table(...)` вне style helper;
- unit assertions на factory geometry и pure in-memory rendering;
- parameterized width matrix 80/120/180 для representative env/process/general tables;
- fixtures для empty, unavailable, long и mixed Odoo/PostgreSQL cases;
- command tests для converted prose и single-emission `module ls`;
- parity regression, что JSON/TOON payloads, stdout/stderr, exit codes и native streams не меняются.

Полный `make pr` остаётся repository gate; Docker/real-Odoo prerequisites не нужны для presentation unit coverage.

## Risks / Trade-offs

- **Borders consume terminal width** → Compact schemas на 80/120, `Details` folding и line-length assertions предотвращают horizontal overflow.
- **Механическая замена затронет много command modules** → Один source inventory, command-grouped tasks и representative behavior tests; business/result code не меняется.
- **Shared helper может превратиться в renderer abstraction** → Helper принимает только Rich presentation options и готовые renderables; command/result dispatch запрещён дизайном и source tests.
- **State consistency может скрыть diagnostics** → Reasons не удаляются, а показываются отдельно в `Details`; меняется только ошибочное смешение lifecycle и availability.
- **Unicode borders могут отличаться на legacy terminals** → Используется Rich safe box behavior; supported widths тестируются без ANSI, machine/native modes не затрагиваются.
- **Active OpenSpec change пересекается с output boundary** → Реализация основывается на main snapshot и не редактирует чужой change; при расхождении implementation сначала rebase и повторный inventory прямых constructors/prose projections.

## Migration Plan

1. Добавить table geometry/render-to-text helpers и их focused tests/source guard.
2. Перевести существующие direct Rich tables по command groups без изменения row contents; после каждого group прогнать локальные CLI tests.
3. Заменить compact `env list` и structured prose на responsive local tables.
4. Свести `ps` к универсальной table на section и применить единый PostgreSQL state/reason formatter.
5. Добавить cross-command width/state/single-emission/machine-parity coverage и выполнить repository gates.

Изменение не требует data/schema migration. Rollback выполняется одним revert presentation commit set; machine contracts и persisted state остаются совместимыми.

## Open Questions

Нет. Scope, exceptions, primary `ps` columns, narrow behavior, state semantics и compatibility constraints определены GitHub #81.
