## Контекст

На базовой ревизии `3d688b26b463d273e80fa46500226158d7d9fab1` `odcli db restore` принимает обязательный `BACKUP_UUID`, преобразует его во внутренний `_CatalogueRestoreSource` и передаёт в публичный `EnvironmentResource.refresh_database_command()`. Общий preparation command уже захватывает process/action plan, резервирует новый target, запускает локальный Odoo Database Manager, восстанавливает базу и filestore, проверяет postcondition, опционально сбрасывает пароль администратора и атомарно переключает project default.

Проверка Odoo ZIP, защита от подмены пути, приватный verified snapshot и безопасная распаковка filestore уже реализованы для retained-backup replacement в `internal/dbprep/source.py`, но принимают каталогизированный `Backup`. Обычный restore, в свою очередь, требует catalog identity и записывает `backup_id` в `restores`/`database_events`. Поэтому локальный файл нельзя безопасно провести через существующий flow ни через CLI, ни через публичную typed SDK boundary без fake catalog import.

## Цели / Не-цели

**Цели:**

- Добавить `--file PATH` к существующему `db restore`, потребовав ровно один источник.
- Представить файл одной публичной frozen-моделью `LocalArchiveRestoreSource(path: str)` и сохранить `EnvironmentResource.refresh_database_command()` единственным SDK entry point.
- До первой database/filestore мутации проверить Odoo ZIP и связать исполнение с одной захваченной identity/digest snapshot-моделью.
- Потреблять только приватный mode-0600 snapshot и гарантированно очищать staging, не удаляя исходный файл или подтверждённую базу.
- Сохранить общий plan, progress, postcondition, failure retention, optional reset, default switch, redaction и ownership-safe audit.
- Записать provenance без backup catalogue row и без пути пользователя.

**Не-цели:**

- Новый CLI command/group, новый public restore method или отдельный pipeline.
- Импорт/retention локального архива, remote URL, native dump, format conversion или архив без поддерживаемого Odoo ZIP layout.
- `--file --replace`: replacement остаётся операцией над retained UUID.
- Новая зависимость, автоматическая очистка подтверждённой partial database или изменение существующего UUID flow.

## Решения

### 1. Расширить существующую source union одной публичной моделью

В `models.backup` добавляется `LocalArchiveRestoreSource(path: str)`, экспортируемая тем же lazy/public механизмом, что остальные typed models. `EnvironmentResource.refresh_database[_command]()` и coordinator принимают её рядом с текущими remote/catalogue вариантами. Внутри `_coerce_restore_source()` она преобразуется в приватную captured-модель; публичный тип не содержит checksum, snapshot path или mutable execution state.

CLI делает positional `BACKUP_UUID` необязательным, добавляет `--file` и до project/environment resolution проверяет cardinality. UUID сохраняет текущую ветку. `--file` создаёт `LocalArchiveRestoreSource`; сочетание с UUID, отсутствие обоих источников и `--file --replace` являются Click usage errors. Существующая строка `PUBLIC_LEAF_CASES` и её SDK primitive не меняются.

Альтернатива — новый `db restore-file` или метод `DatabaseResource.restore_file()` — отклонена: она дублирует command/output/confirmation boundary и расширяет public surface без необходимости. Передача raw `Path`/`str` также отклонена, потому что она неоднозначна с текущим UUID coercion и не является typed SDK contract.

### 2. Разделить read-only capture и execution-time snapshot materialization

При построении command локальная ветка открывает source с `O_NOFOLLOW`, подтверждает regular/readable file, фиксирует `(device, inode, size, mtime_ns)`, вычисляет SHA-256 и запускает существующий bounded ZIP validator. Capture требует `dump.sql`, согласованный database name из manifest, безопасные filestore members и действующие size/ratio limits. Он выбирает уникальные private snapshot/dump paths под project-owned `.odcli` storage, но ничего не создаёт; поэтому dry-run остаётся немутирующим.

В начале исполнения под project preparation lock отдельный честный `ActionStep` повторно открывает исходный path без symlink following, сравнивает identity, копирует bytes через один file descriptor в exclusive mode-0600 snapshot и одновременно сверяет size/SHA-256. Любое отличие удаляет незавершённый snapshot и завершает command до создания базы. Весь downstream restore открывает только snapshot; исходный path больше не читается.

Общие функции `capture_selected_backup_restore()` выделяются вокруг нейтрального archive evidence, чтобы catalogue replacement и local-file restore использовали одну ZIP/filestore проверку и snapshot copy, а не две реализации. Catalogue-specific metadata checks остаются в `_catalogue_backup_preflight()`.

Альтернатива — скопировать файл при построении command — отклонена: `--dry-run` начал бы мутировать. Повторно открыть исходный path непосредственно при HTTP restore — отклонено из-за swap window после preview/confirmation.

### 3. Переиспользовать текущий preparation command и внутренний restore transport

`_restore_preflight()` извлекает source database из captured manifest, применяет существующие project/cluster/target guards и возвращает preflight с local archive evidence вместо `Backup`. `_preparation_action_steps()` добавляет честные read-only capture/validate и mutating private-snapshot/cleanup actions; process steps и auxiliary runtime attachment остаются единым captured plan.

В `DatabaseResource` общая приватная restore implementation принимает либо каталогизированный `Backup`, либо уже проверенный local snapshot evidence. Обе ветки используют одинаковые master-password, target-absence, auxiliary session, multipart restore, postcondition, neutralization и data-directory guards. Catalogue ветка сохраняет `catalog.verify_identity()` и backup lock; local ветка не создаёт `Backup`, не вызывает catalogue identity lookup и загружает snapshot filename с нейтральным именем. Нового публичного метода нет.

`DatabasePreparationResult.backup` для local archive остаётся `None`; source kind и SHA-256 входят только в безопасный typed failure/audit context, без source/snapshot path. Confirmation для local source называет тип источника и target, а не путь.

Альтернатива — direct `psql` restore по replacement primitives — отклонена: обычный flow уже имеет Odoo Database Manager semantics, neutralization, auxiliary lifecycle и postconditions; перенос создаст второй standalone pipeline.

### 4. Сделать restore provenance независимым от retention

Alembic migration перестраивает `restores` и `database_events`: `backup_id` становится nullable, добавляются `source_kind` и `source_sha256`, а CHECK constraints разрешают только две полные формы:

- `catalogue`: non-null `backup_id`, null `source_sha256`;
- `local_archive`: null `backup_id`, lowercase 64-hex `source_sha256`.

Существующие rows детерминированно backfill-ятся как `catalogue`. `record_restore()` атомарно пишет одинаковую source evidence в restore и restored-event rows. Путь не входит в schema. Catalogue readers сохраняют inner join там, где нужен именно retained backup; source-neutral inventory/ownership readers получают nullable backup id, source kind и digest без попытки материализовать `Backup`.

`db rm` по-прежнему требует exact active `cluster_id` и contained verified `data_directory`. Эти proofs, а не наличие backup row само по себе, дают destructive authority; поэтому корректный local-archive restore может безопасно удалить свою базу/filestore, а external/null provenance остаётся fail-closed. Downgrade отказывается выполнять lossful rollback при наличии local-archive rows.

Альтернатива — deterministic fake backup UUID/row — отклонена как запрещённый скрытый import/retention contract. Полностью пропустить audit — отклонено: это ломает tracked inventory и ownership-safe cleanup после успешного restore.

### 5. Очистка не отменяет failure-retention

Snapshot и derived dump paths принадлежат одному command и удаляются в `finally` при success, ordinary exception и cancellation. Незавершённая filestore staging directory удаляется только до её подтверждённой публикации существующими guarded stages. Caller archive никогда не удаляется и не изменяется. Если база уже подтверждена, более поздняя ошибка reset/default-switch сохраняет базу и source-neutral restore binding по текущему failure-retention contract; cleanup staging не маскирует первичную ошибку.

### 6. Проверить contract вертикально, без второй матрицы

Один parametrized CLI test покрывает UUID-only, file-only, both и neither, включая early usage errors и `--file --replace`. Focused command tests доказывают redacted inert dry-run, public typed source delegation, no path leakage и неизменность `PUBLIC_LEAF_CASES`. Pipeline tests используют реальный ZIP fixture и доказывают snapshot-only consumption, path swap failure, invalid/missing/unreadable/non-regular cases, dump+filestore restore, cleanup и отсутствие backup row. Migration/catalog tests проверяют upgrade, constraints, atomic paired writes, readers и guarded `db rm`. Существующие UUID restore/replacement tests остаются regression gate.

## Риски / Компромиссы

- **[Большой файл дважды читается при capture и snapshot copy]** → это намеренная цена immutable preview/execution evidence; чтение потоковое и bounded, дополнительная retained copy не сохраняется.
- **[Свободного места для private snapshot недостаточно]** → проверить доступное место до copy и завершить до database mutation с typed sanitized error.
- **[SQLite table rebuild может повредить исторический audit]** → migration делает deterministic backfill, проверяет row counts/constraints и покрывается upgrade fixture; downgrade fail-closed при local rows.
- **[Nullable backup id затронет catalogue readers]** → разделить catalogue-only joins и source-neutral binding projections, обновить strict typed rows и characterization tests.
- **[Cleanup ошибка может скрыть исходную restore ошибку]** → сохранить primary exception, добавить sanitized cleanup note и оставить artifact path вне output.
- **[Digest может считаться чувствительной identity]** → хранить только SHA-256 без path/filename; наружу выводить digest лишь там, где spec требует sanitized source evidence.
- **[Odoo ZIP без filestore]** → текущий supported scope требует dump и filestore; расширение на database-only archive оформляется отдельным change.

## План миграции

1. Добавить Alembic migration и source-neutral catalogue models/readers/writers; проверить upgrade на current fixture и совместимость существующих catalogue rows.
2. Добавить public source model и общий archive capture/snapshot primitive без подключения CLI.
3. Подключить local source к единому preparation/restore flow, audit и cleanup.
4. Добавить CLI selection и focused vertical tests, затем обновить SDK/CLI docs и checked matrix только при фактическом изменении projection.

Rollback к прежней версии допустим только пока в каталоге нет `local_archive` provenance. При наличии таких rows downgrade SHALL завершиться ошибкой, чтобы не потерять audit/ownership evidence; для отката сначала требуется явное удаление соответствующих восстановленных ресурсов и их provenance поддерживаемой новой версией.

## Открытые вопросы

Нет. Source cardinality, `--replace` compatibility, supported ZIP layout, snapshot timing, result shape, provenance schema, redaction и cleanup policy зафиксированы требованиями.
