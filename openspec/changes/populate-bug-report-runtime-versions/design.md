## Контекст

`bug_report_init_command()` сейчас планирует только один мутирующий `ActionStep`, а `_report_template()` вызывает две константные функции, возвращающие `unknown`. При этом репозиторий уже умеет находить ближайший/зарегистрированный проект через `internal.context.resolve_project`, строить неизменяемые `PreparedStep` и безопасно получать PostgreSQL `server_version` через `internal.pg.server`. Odoo-проект хранит `odoo_bin`, Python runtime и рабочий каталог в `ProjectConfig`.

Изменение затрагивает публичный SDK command, CLI dry-run, запуск дочерних процессов и потенциально чувствительные PostgreSQL credentials. Поэтому discovery должен быть частью инспектируемого command plan, а не скрытым subprocess внутри draft action.

## Цели / Не-цели

**Цели:**

- Заполнять версии Odoo и PostgreSQL для текущего managed project, когда конкретное значение можно получить read-only probe.
- Планировать каждый дочерний процесс заранее через `internal.proc`, ограничивать время и объём вывода и сохранять dry-run полностью без эффектов.
- Изолировать сбои: ошибка одного provider не мешает второму provider и не отменяет создание draft.
- Никогда не переносить credentials, process stderr или произвольный probe output в `report.md`, публичный plan либо diagnostics результата.
- Сохранить существующий публичный вызов `bug_report_init_command(title=..., kind=...)` и формат результата.

**Не-цели:**

- Запускать или перезапускать Odoo/PostgreSQL, устанавливать runtime либо чинить конфигурацию проекта.
- Добавлять CLI-флаги выбора проекта или версии.
- Менять submit/review workflow, draft schema либо уже созданные drafts.
- Гарантировать версию вне managed project или при недоступном runtime.

## Решения

### 1. Один project snapshot и явные optional probes

На этапе построения `bug_report_init_command()` код SHALL вызвать существующий `resolve_project(None, cwd=Path.cwd())` ровно один раз. Успешный `ProjectConfig` становится единственным snapshot для обоих providers. Отсутствие/ошибка project context SHALL дать пустой набор probe steps и значения `unknown`, не ошибку команды.

Команда SHALL содержать, в порядке исполнения, optional read-only Odoo `PreparedStep`, ноль или более уже типизированных PostgreSQL server-summary `PreparedStep`, затем существующий mutating draft `PreparedAction`. Callback SHALL выполнить доступные probes независимо, учесть каждый неисполненный optional step через `RunContext.skip()`, затем создать draft с двумя итоговыми строками.

Так план остаётся полным и fingerprinted, а `--dry-run` показывает probes, но не выполняет их и не создаёт файлы. Альтернатива — вызывать `subprocess` внутри `_create_draft()` — отвергнута, потому что скрывает процесс от immutable plan. Альтернатива — читать версии только из строк manifest/image — отвергнута, потому что это заявленное намерение, а не обязательно фактический runtime.

### 2. Odoo version через конфигурированный runtime

Odoo provider SHALL переиспользовать `ProjectConfig.odoo_bin`, `ProjectConfig.python`, `runtime_cwd`, `defer_project_runtime()` и `resolve_project_runtime()` для построения точного argv `<runtime-prefix> <odoo-bin> --version`. Numeric uv selector SHALL сохраняться как deferred `uv run --no-project --python ...` prefix; provider SHALL NOT запускать отдельный resolver при планировании.

Probe SHALL быть `read_only=True`, `shell=False` и с timeout 5 секунд. Output длиннее 16 KiB SHALL быть отвергнут до разбора. Успехом считается return code 0 и одна безопасная нормализованная версия, извлечённая из стандартного `Odoo Server <version>` output. Нормализованное значение SHALL состоять только из ASCII letters/digits и `._+-`, начинаться с цифры и иметь максимум 64 символа. Любой другой exit, timeout, отсутствующий executable, переполнение либо malformed output SHALL дать `unknown` только для Odoo.

### 3. PostgreSQL version через существующий server-summary contract

PostgreSQL provider SHALL построить `PostgresCluster.from_project(project.repository_root)`, затем переиспользовать `build_server_summary_plan()` и `collect_server_summary()` с единым deadline не более 10 секунд. Это даёт фактический `PostgresServerInfo.server_version` для уже доступного compose или external runtime, использует существующую последовательность maintenance database candidates и не стартует сервис.

Из результата SHALL приниматься только `server.server_version`, прошедший ту же однострочную нормализацию и ограничение 64 символов. Credentials SHALL оставаться только в secret-aware private prepared steps существующего PostgreSQL boundary; в draft попадает исключительно нормализованная версия. Missing credentials/tool, stopped/unreachable server, timeout, SQL/decode failure или malformed value SHALL дать `unknown` только для PostgreSQL.

Альтернатива — выводить tag контейнерного image — отвергнута: tag может быть mutable, digest не содержит версии, а фактический сервер может не совпадать с tag.

### 4. Независимое best-effort выполнение и узкий результат

Callback SHALL хранить две локальные строки `odoo_version` и `postgres_version`, обе с начальным значением `unknown`, без нового публичного типа или provider hierarchy. Он SHALL ловить ожидаемые probe/config/process/decode ошибки вокруг каждого provider отдельно; `KeyboardInterrupt`, `SystemExit` и ошибка самого draft write SHALL продолжать распространяться. Raw stdout/stderr и тексты исключений SHALL NOT записываться в draft или возвращаться в `BugReportInitResult`.

`_report_template()` SHALL принимать уже разрешённые значения. Константные `_odoo_version()` / `_postgres_version()` SHALL быть удалены, чтобы не осталось второго пути discovery.

### 5. Проверка на SDK и публичной CLI границе

Тесты SHALL использовать временный managed-project manifest и deterministic injected/recorded process executor. Параметризованная матрица SHALL покрыть: обе версии доступны; Odoo failure при успешном PostgreSQL; PostgreSQL failure при успешном Odoo; обе недоступны; unsafe/multiline/oversized output; отсутствие managed project.

Отдельный публичный `CliRunner` regression SHALL вызвать `bug-report init` из configured project и проверить обе строки в созданном `report.md`. Dry-run regression SHALL проверить, что probes присутствуют в plan, но executor не вызван и draft не создан. Тесты SHALL дополнительно доказать отсутствие password/secret marker в plan, result и report.

## Риски / Компромиссы

- **Probe увеличивает обычный init latency при недоступном runtime** → короткие индивидуальные bounds и общий PostgreSQL deadline; draft всё равно создаётся после fallback.
- **Формат `odoo-bin --version` меняется** → строгий parser принимает только известный префикс и безопасный token, иначе `unknown`.
- **PostgreSQL credentials доступны только частично** → переиспользуется существующий server-summary eligibility/failure classification, без нового credential lookup.
- **Несколько PostgreSQL maintenance candidates создают несколько steps** → они используют один deadline, а callback обязан skip-нуть остаток после успеха/окончательного fallback.
- **Best-effort ошибки могут быть незаметны пользователю** → это сознательная совместимость: публичный результат не расширяется, а `unknown` остаётся явным сигналом недоступности.

## План миграции

Миграция данных не требуется. Изменение поставляется как совместимое обновление SDK/CLI; существующие drafts не переписываются. Откат — revert одного OpenSpec implementation commit/PR: формат drafts и публичные модели остаются прежними.

## Открытые вопросы

Нет. Источники, precedence, bounds, fallback и redaction contract определены выше.
