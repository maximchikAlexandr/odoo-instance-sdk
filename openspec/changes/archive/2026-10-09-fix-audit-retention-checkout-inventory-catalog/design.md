## Context

Все четыре дефекта находятся на границах уже существующих механизмов. `_patch_backup_section()` различает только обычный следующий TOML-заголовок; `adopt_command()` вручную собирает public/execution projections в обход `normalize_checkout_stage()`; `_collect_one_provider()` ожидает завершения child до чтения одностороннего `Pipe`; а `_repair_known_v16_catalog()` и `_is_legacy_provenance_schema()` независимо реконструируют исторические формы одной canonical schema. Публичные типы и основной Alembic head уже подходят для исправления.

Базовая ревизия проектирования: `ab71895f7031eabe129fbd5dbfcda71298cd4016` (`origin/main`). Изменение сохраняет существующие данные, сроки выполнения provider и внешние контракты.

## Goals / Non-Goals

**Goals:**

- Исправить четыре root cause без новых зависимостей и параллельных реализаций существующих контрактов.
- Сохранить byte-oriented TOML patching для чужого содержимого, но гарантировать корректные границы `[backup]`.
- Сделать normalized provenance единственным источником public и execution projections для обычного checkout и adoption.
- Читать provider payload одновременно с работой child в пределах одного абсолютного deadline.
- Описывать известные исторические формы каталога централизованно и применять их в обоих migration paths.
- Доказать каждый фикс тестом на наблюдаемом результате, а не только на внутреннем helper.

**Non-Goals:**

- Не добавлять TOML writer/parser dependency, новый settings layer или переформатирование всего `user.toml`.
- Не менять adoption validation, ownership semantics, публичные SDK-типы или CLI.
- Не вводить общий RPC/provider framework, streaming API или новый worker pool.
- Не добавлять Alembic revision, не менять текущую schema и не принимать неизвестные исторические каталоги.

## Decisions

### D1. Распознавать любой TOML table header как границу `[backup]`

Существующий line-preserving patcher остаётся. Его boundary matcher SHALL распознавать и `[table]`, и `[[array-of-tables]]` с допустимыми пробелами/комментарием. Он меняет только два owned assignment внутри первого `[backup]`; последующие строки остаются на прежних позициях. Регрессия записывает файл с `[backup]` перед `[[profile]]`, читает итог через `tomllib` и проверяет, что `retention_days`/`auto_prune` принадлежат `backup`, а `profile` сохранён.

Альтернатива — разобрать и сериализовать весь TOML сторонней библиотекой — отклонена: она добавляет dependency и может переписать комментарии/форматирование, что противоречит текущему контракту сохранения чужого содержимого.

### D2. Использовать один pure projection path для checkout и adoption

Из существующей нормализации checkout SHALL быть выделен минимальный внутренний helper, который получает `_CheckoutPlanningState`, накладывает captured private provenance (`source_*`, `base_revision`, selected backup), затем строит `_public_checkout_plan()` и `_execution_plan()`. Обычный stage и `adopt_command()` SHALL вызывать этот helper; capture snapshot также остаётся единым. Adoption-specific проверки canonical path, repository identity, caller ownership и fingerprint выполняются до общего projection path и не ослабляются.

Это гарантирует, что `plan.provenance.resolved_base_revision` равен уже захваченному `private.base_revision`, а public и execution projections основаны на одном normalized provenance. Альтернатива — присвоить поле только в `adopt_command()` — отклонена, потому что оставляет две сборки snapshot и следующий provenance field снова сможет разойтись.

### D3. Дренировать provider channel до `join` под одним deadline

Родитель SHALL ожидать как readability receive-end, так и process sentinel, используя оставшееся время до одного monotonic deadline. Если данные готовы, `recv()` SHALL дренировать сериализованный payload, пока child может завершить `send`; затем parent присоединяет child только на оставшийся budget. Если child завершился без валидного сообщения, возникла transport/provider error или deadline истёк, результат этого provider остаётся пустым. Живой child после deadline SHALL быть killed и bounded-joined по текущему cleanup contract.

Тестовый provider возвращает валидную frozen summary с text существенно больше типичного pipe buffer; inventory SHALL содержать эту summary и завершиться в установленный deadline. Альтернатива — увеличить pipe/socket buffer или payload limit — отклонена: это не устраняет порядок `join`-before-read и зависит от платформы.

### D4. Строить известные historical fingerprints из одной таблицы различий

Поверх canonical `_reference_fingerprint()` SHALL существовать один внутренний преобразователь с явно именованными историческими вариантами: repairable v16 и pre-source-neutral provenance. Табличные column omissions, required-bit normalization и ожидаемые indexes SHALL быть заданы один раз для общих частей; variant-specific различия (включая отсутствующий restore `state` и repairable index gap v16) SHALL оставаться явными. `_repair_known_v16_catalog()` и `_is_legacy_provenance_schema()` сравнивают actual только с соответствующим результатом этого преобразователя, сохраняя прежние foreign-key/view проверки и Alembic stamp/upgrade flow.

Fixture/assertion SHALL добавить одно исторически отсутствующее поле в общий exclusion contract и доказать, что оба варианта учитывают его; v16 fixture по-прежнему проверяет сохранение строк и восстановление индекса. Альтернатива — синхронно обновить два локальных списка — отклонена как источник исходного drift.

### D5. Ponytail full gate: новые подсистемы не нужны

Новый TOML service не нужен — остаётся существующий patcher; новый checkout planner не нужен — переиспользуется planning state/stages; новый IPC abstraction не нужен — остаётся `multiprocessing.Pipe` и стандартные wait primitives; новый migration framework не нужен — остаются `_reference_fingerprint()` и Alembic. Решение для всех четырёх: убрать дублирование или исправить границу в существующей подсистеме. Новые public classes, registries, dependencies и extensibility hooks запрещены этим change.

## Risks / Trade-offs

- **[TOML header matcher принимает сложный header как границу, но не валидирует весь TOML]** → итоговый public test обязательно перечитывает файл через `tomllib`; malformed input продолжает отклоняться существующим read path.
- **[Общий checkout helper может изменить fingerprint execution plan]** → сравнить ordinary checkout characterization и adoption public/private projections; изменения допускаются только там, где normalized provenance исправляет потерянный base revision.
- **[IPC race между message и process sentinel]** → при готовности обоих сначала читать channel, затем проверять exit; использовать один deadline и idempotent close/kill cleanup.
- **[Слишком широкое historical matching может stamp неизвестную schema]** → варианты остаются exact fingerprints; лишняя/пропущенная колонка, index, FK или view вне явно описанной истории SHALL fail closed.

## Migration Plan

1. Добавить focused regressions, воспроизводящие каждый дефект на текущей базе.
2. Исправить TOML boundary и checkout projection path; прогнать соответствующие unit suites.
3. Исправить provider IPC и проверить large-payload плюс timeout/error isolation.
4. Централизовать historical fingerprints и проверить v16 fixture, legacy provenance upgrade, schema equivalence и single-head gate.
5. Выполнить полный обязательный repository check set. Persisted schema и data migration отсутствуют; rollback — обычный revert code/tests/specs, без преобразования пользовательских данных.

## Open Questions

Нет. Scope, observable behavior и compatibility boundaries определены issue и существующими main specs.
