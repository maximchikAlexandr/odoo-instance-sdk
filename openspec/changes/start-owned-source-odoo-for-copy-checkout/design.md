## Контекст

На базовой ревизии `9a862fbe8ff83d57c452144badbd178327d5e598` команда COPY checkout строит один неизменяемый `Command` в `EnvironmentResource.checkout_command()`, но `_run_checkout_snapshot()` вызывает `_preflight_copy_checkout()` до каталожной мутации без активной auxiliary-сессии. Поэтому `settings.py` сразу обращается к source Database Manager и оборачивает недоступность endpoint в `Source Odoo HTTP endpoint unavailable for copy mode`.

Нужный lifecycle уже реализован в `resources/instance/auxiliary_restore.py`: `AuxiliaryRestoreSession` захватывает точную команду запуска, регистрирует собственный process handle, ждёт Database Manager readiness, различает доказанный persisted runtime и неизвестного владельца порта, удаляет временный secret config и завершает только зарегистрированный процесс. CLI database restore подключает его через `_attach_auxiliary_restore_runtime()` из `commands/db.py`, однако helper находится на CLI-слое и недоступен resource-level checkout.

## Цели / Не-цели

**Цели:**

- Сделать COPY checkout самодостаточным при остановленном source Odoo.
- Переиспользовать существующий auxiliary lifecycle и один captured execution plan.
- Не присваивать ownership уже отвечающему Database Manager или доказанному совместимому runtime.
- Гарантировать cleanup собственного процесса и secret config при любом выходе, сохранив первичную ошибку.
- Не ослабить текущие COPY journal, provenance, target-absence, restore postcondition и compensation контракты.

**Не-цели:**

- Новый launcher, process manager, background service, retry subsystem или storage schema.
- Изменение CLI-параметров, shared database mode, формата backup/restore или публичной модели checkout.
- Завершение, перезапуск или принятие ownership произвольного слушателя порта.
- Миграция данных или новая внешняя зависимость.

## Решения

### 1. Перенести command attachment в instance auxiliary-runtime слой

Существующий `_attach_auxiliary_restore_runtime()` переносится из `commands/db.py` рядом с `AuxiliaryRestoreSession` и экспортируется через `resources.instance` как общий внутренний seam. Helper принимает исходный captured `Command`, session и явный step anchor, вставляет `start`/`ready` перед первой операцией, которой нужен Database Manager, а `cleanup` — последним шагом. Database CLI продолжает использовать anchor локального restore, а COPY checkout использует anchor `checkout.catalog`, потому что его preflight выполняется непосредственно перед этой каталожной action.

Альтернатива — скопировать wrapper в environment resource — отклонена: два lifecycle-адаптера быстро разойдутся по порядку шагов, context reset и error semantics. Отдельный command/plan также отклонён, поскольку dry-run перестал бы быть точной проекцией исполняемого checkout.

### 2. Подключать session только при построении COPY checkout command

После `_build_checkout_snapshot()` и `_command_from_snapshot()` checkout проверяет `snapshot.private.db_mode`. Только для COPY он строит source instance из уже захваченных `ProjectConfig`, project runtime, source config и локального endpoint, создаёт `AuxiliaryRestoreSession` и оборачивает исходный command общим attachment helper. Shared mode возвращает исходный command без новых шагов и probes.

Выбор делается во время построения команды, а не внутри `_do_copy_restore()`: так start/readiness/cleanup попадают одновременно в public plan и private prepared steps, а dry-run остаётся полностью инертным. Создание session лишь захватывает параметры и не запускает процесс.

### 3. Сначала доказать доступность endpoint, затем решать вопрос запуска

При первом database-manager обращении активная session выполняет ограниченную проверку `/web/database/list`. Валидный JSON-RPC list response означает внешний responsive runtime: session отмечает reuse, пропускает запланированные start/readiness/cleanup steps и никогда не регистрирует либо не останавливает процесс. Если endpoint ещё не готов, сохраняется текущая строгая проверка persisted runtime по owner, PID, create time и порту; такой runtime также переиспользуется без остановки. Только после отрицательных проверок проверяется свободный порт и запускается captured process.

Альтернатива — считать любой занятый порт пригодным — отклонена из-за риска отправить backup/master-password запросы чужому сервису. Альтернатива — всегда стартовать новый Odoo на другом порту — отклонена, поскольку меняет зафиксированный source endpoint и усложняет конфигурацию.

### 4. Сохранить порядок COPY guards и единственный lifecycle owner

После readiness существующие `_preflight_copy_checkout()` и `_do_copy_restore()` выполняются без изменения их journal и recovery порядка. Active auxiliary session уже перехватывается существующими database list/backup/restore primitives, поэтому первый реальный запрос не может обойти readiness. PostgreSQL existence probes и rollback остаются частью исходного checkout command.

Session хранит единственный зарегистрированный `OdooProcess`; cleanup получает процесс только через точный session id и использует сохранённый process group handle. При external/persisted reuse cleanup лишь помечает неиспользованные lifecycle steps skipped. Никакая ветка не ищет процесс только по порту или executable name.

### 5. Не маскировать первичную ошибку ошибкой cleanup

Attachment helper всегда сбрасывает `ContextVar` во вложенном `finally`. Если основная операция уже завершилась исключением, ошибка cleanup добавляется к ней как диагностическая note, а наружу возвращается исходное исключение. Если основная операция успешна, самостоятельная ошибка cleanup остаётся ошибкой команды. Этот порядок применяется одинаково к database restore и COPY checkout и покрывает `BaseException`, чтобы controlled cancellation также прошла через cleanup.

Альтернатива — текущая семантика Python `finally`, при которой cleanup exception заменяет исходную, — отклонена, потому что скрывает причину backup/restore/cancellation и нарушает требование диагностируемости.

## Риски / Компромиссы

- **[Readiness probe добавляет ещё один HTTP-запрос]** → использовать тот же bounded Database Manager list contract; не выполнять probe в dry-run и не добавлять retry вне существующего readiness timeout.
- **[Project runtime и source endpoint могут разойтись после planning]** → строить session только из captured snapshot/project inputs и сохранять существующую execution-time revalidation до мутации.
- **[Общий attachment helper затрагивает database restore]** → перенести поведение без изменения порядка его шагов и добавить регрессии на plan parity, context reset и error precedence.
- **[Cleanup может сам завершиться ошибкой]** → всегда сбрасывать context; при уже существующей primary error прикреплять cleanup detail, а при успешной основной операции честно завершать command ошибкой.
- **[Persisted runtime отвечает не сразу]** → ждать его через существующий bounded readiness path, не регистрировать и не завершать его; timeout остаётся явной ошибкой без signal.

## План миграции

Миграция данных и конфигурации не требуется. Изменение выпускается как внутреннее переиспользование существующего lifecycle: сначала переносится общий attachment seam с сохранением database restore поведения, затем он подключается к COPY checkout и покрывается focused regression tests. Откат возвращает прежнее требование ручного запуска source Odoo; созданных постоянных данных или новых форматов после отката не остаётся.

## Открытые вопросы

Нет. Ownership, plan topology, error precedence, режимы COPY/shared и границы переиспользования зафиксированы требованиями.
