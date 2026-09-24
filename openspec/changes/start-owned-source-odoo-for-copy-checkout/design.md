## Контекст

На ревизии реализации `46efd7d882fa122120e149896d560340cc96777b` COPY checkout уже подключает `AuxiliaryRestoreSession` к единому неизменяемому `Command`, запускает собственный source Odoo при свободном порте и сохраняет ownership-safe cleanup. Реализация также перестала доверять одному лишь JSON-ответу неизвестного listener и принимает persisted runtime только после проверки catalog row, PID, create time, executable, argv, cwd и config path.

Две границы остаются незакрытыми. Во-первых, проверенный процесс не связан с live listening socket: сохранённый PID может быть корректным, пока тот же порт уже обслуживает другой локальный процесс. Во-вторых, source backup отправляет `admin_passwd` без request-adjacent revalidation; `ensure_started()` вызывается для list/restore, но `_download_backup_part()` сразу открывает password-bearing POST. Отдельная проверка свободного порта также выполняется как скрытый in-process effect и отсутствует в public/private plan. Эта ревизия планирования заменяет прежний responsive-unrecorded reuse на строгий recorded-runtime trust contract и делает обе проверки наблюдаемыми actions.

## Цели / Не-цели

**Цели:**

- Сделать COPY checkout самодостаточным при остановленном source Odoo.
- Переиспользовать существующий auxiliary lifecycle и один captured execution plan.
- Никогда не доверять responsive, но unrecorded listener; переиспользовать только recorded runtime с доказанной связью exact process identity и listening socket.
- Перед каждым auxiliary HTTP request с master password повторно доказывать ту же связь и не отправлять секрет при неуспехе проверки.
- Отражать identity probe, port/ownership precondition и privileged-request revalidation отдельными actions в одинаковых public/private plans.
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

### 3. Доверять только recorded runtime, связанный с live socket

Session сначала проверяет persisted runtime по owner, PID, create time, executable, argv, cwd, config path, endpoint и порту. После этого она обязана доказать по локальной таблице TCP listeners, что configured listening socket принадлежит тому же exact PID. Отсутствующая информация, несовпадающий PID, несколько неоднозначных listeners, недостаточные права на socket inspection или любая ошибка проверки означают отсутствие proof и приводят к fail-closed.

Если такой recorded runtime доказан, session ждёт bounded `/web/database/list` readiness, не регистрирует его и не останавливает. Если recorded proof отсутствует, отдельный port/ownership action проверяет configured endpoint: свободный порт разрешает captured spawn; любой занятый порт, включая корректно отвечающий unrecorded Database Manager, отклоняется до checkout mutation и без HTTP probe, signal либо secret transmission.

Альтернатива — доверять форме JSON-RPC ответа — отклонена: она не аутентифицирует listener. Альтернатива — автоматически переносить auxiliary runtime на другой порт — отклонена, поскольку меняет captured source endpoint. Authenticated endpoint protocol не вводится в этом change; поэтому недоказуемый listener всегда отклоняется.

### 4. Повторно доказывать endpoint непосредственно перед секретным запросом

`AuxiliaryRestoreSession` предоставляет одну строгую request-authorization operation для Database Manager requests своего exact source instance. Непосредственно перед открытием каждого auxiliary HTTP request, содержащего `master_pwd`, backup/restore boundary повторяет catalog/PID/create-time/process/socket proof. Проверка выполняется до построения или отправки password-bearing request; при расхождении запрос не вызывается, `admin_passwd` не покидает процесс, а операция завершается `DatabaseManagerUnavailableError`.

Проверка применяется только когда request instance совпадает с instance активной auxiliary session. Поэтому target restore внутри COPY не ошибочно проверяется против source runtime. Для owned runtime session использует зарегистрированный exact process handle и тот же socket-to-PID proof; для recorded reuse — повторяет полный persisted proof. Каждый планируемый password-bearing auxiliary request получает собственный revalidation action, расположенный внутри соответствующей database operation перед HTTP effect. Текущий scope содержит один source backup в COPY и один restore request в standalone auxiliary restore; повторные запросы не переиспользуют уже consumed proof.

Request-adjacent OS socket proof уменьшает, но без аутентифицированного transport protocol не устраняет теоретическое окно TOCTOU между inspection и connect. В рамках локального runtime contract оно принимается только при exact PID/create-time/socket proof и немедленном запросе; расширение протокола аутентификации остаётся вне scope.

### 5. Сделать все preconditions честными plan actions

Attachment добавляет отдельные `PreparedAction` для recorded identity probe, port/ownership decision и каждого privileged-request revalidation. Они имеют уникальные step id, одинаково присутствуют в public plan и private prepared steps и consume/fail/complete либо skip вокруг точного in-process effect. Port action выполняется до spawn decision; request action — после входа в соответствующую database operation и непосредственно перед HTTP effect. Dry-run только показывает sanitized actions и ничего не проверяет в ОС.

### 6. Сохранить порядок COPY guards и единственный lifecycle owner

После readiness существующие `_preflight_copy_checkout()` и `_do_copy_restore()` выполняются без изменения их journal и recovery порядка. Active auxiliary session уже перехватывается существующими database list/backup/restore primitives, поэтому первый реальный запрос не может обойти readiness. PostgreSQL existence probes и rollback остаются частью исходного checkout command.

Session хранит единственный зарегистрированный `OdooProcess`; cleanup получает процесс только через точный session id и использует сохранённый process group handle. При external/persisted reuse cleanup лишь помечает неиспользованные lifecycle steps skipped. Никакая ветка не ищет процесс только по порту или executable name.

### 7. Не маскировать первичную ошибку ошибкой cleanup

Attachment helper всегда сбрасывает `ContextVar` во вложенном `finally`. Если основная операция уже завершилась исключением, ошибка cleanup добавляется к ней как диагностическая note, а наружу возвращается исходное исключение. Если основная операция успешна, самостоятельная ошибка cleanup остаётся ошибкой команды. Этот порядок применяется одинаково к database restore и COPY checkout и покрывает `BaseException`, чтобы controlled cancellation также прошла через cleanup.

Альтернатива — текущая семантика Python `finally`, при которой cleanup exception заменяет исходную, — отклонена, потому что скрывает причину backup/restore/cancellation и нарушает требование диагностируемости.

## Риски / Компромиссы

- **[Socket inspection может быть недоступен на платформе или из-за прав]** → трактовать отсутствие доказательства как fail-closed; не откатываться к response-shape trust.
- **[Listener меняется между proof и HTTP connect]** → выполнять proof непосредственно перед password-bearing request; более сильная криптографическая identity требует отдельного protocol change и не симулируется здесь.
- **[Project runtime и source endpoint могут разойтись после planning]** → строить session только из captured snapshot/project inputs и сохранять существующую execution-time revalidation до мутации.
- **[Дополнительные actions могут разойтись между public/private plans]** → строить обе проекции из одного набора `PreparedAction` и проверять exact ids/order в тестах.
- **[Общий attachment helper затрагивает database restore]** → перенести поведение без изменения порядка его шагов и добавить регрессии на plan parity, context reset и error precedence.
- **[Cleanup может сам завершиться ошибкой]** → всегда сбрасывать context; при уже существующей primary error прикреплять cleanup detail, а при успешной основной операции честно завершать command ошибкой.
- **[Persisted runtime отвечает не сразу]** → ждать его через существующий bounded readiness path, не регистрировать и не завершать его; timeout остаётся явной ошибкой без signal.

## План миграции

Миграция данных и конфигурации не требуется. На текущей реализации сохраняются уже выполненные attachment, COPY integration и cleanup changes; доработка добавляет socket ownership proof, отдельные plan actions и request-adjacent authorization, затем обновляет focused security/plan/integration tests. Откат этой ревизии недопустим как runtime fallback: response-shaped unrecorded reuse не возвращается. Полный rollback feature по-прежнему возвращает требование ручного запуска source Odoo и не оставляет новых постоянных форматов.

## Открытые вопросы

Нет. Fail-closed policy, exact socket/process proof, request scope, action topology, error precedence и границы COPY/shared зафиксированы требованиями.
