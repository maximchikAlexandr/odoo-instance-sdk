## Why

Структурированный human-readable вывод CLI сейчас использует несовместимые формы: часть Rich-таблиц не имеет границ между строками и ячейками, `ps` и широкий `env list` явно безрамочные, узкий `env list` превращается в текстовые блоки, а ряд много-полевых результатов остаётся плотным prose. Из-за этого большие результаты трудно сканировать, один и тот же PostgreSQL cluster может выглядеть противоречиво в соседних командах, а `module ls` способен отрисовать один результат дважды.

## What Changes

- Вводится единый визуальный контракт для bounded structured Rich output: внешняя рамка, вертикальные разделители колонок, горизонтальные разделители строк, различимый header и безопасное wrapping на поддерживаемых ширинах.
- Все существующие структурированные human-таблицы в audited command surface переводятся на этот контракт без изменения typed results, JSON/TOON envelope v1, stdout/stderr, exit codes или native streaming.
- Узкие structured views остаются компактными bordered tables; вторичные значения переносятся в `Details`, а не в свободный текст.
- `odcli ps` сохраняет секции main checkout/environment/shared resources, но внутри каждой секции использует одну универсальную таблицу с общими primary columns для Odoo, owned PostgreSQL, backend groups и внешних contributions.
- `doctor`, `env show`, detached `run`, `deps verify` и `module install-order` получают локальные табличные Rich-проекции; multi-target plans/results используют тот же контракт для повторяющихся records.
- Human views `ps`, `env list`, `env show`, `doctor` и `postgres status` проецируют состояние одного PostgreSQL snapshot/source без противоречивых lifecycle labels и без домыслов при unavailable data.
- Каждая команда отрисовывает один logical result ровно один раз; регрессия двойного вывода `module ls` покрывается тестом.
- Presentation tests фиксируют ширины 80, 120 и 180 колонок, пустые данные, unavailable metrics, длинные значения и смешанные Odoo/PostgreSQL rows.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `cli-odcli`: устанавливает единый bordered-table contract для structured Rich output, narrow-table behavior, табличные проекции ранее текстовых результатов, одноразовую emission и согласованные PostgreSQL state labels при сохранении machine/native contracts.
- `process-inventory`: определяет одну универсальную process table на существующую checkout/environment/shared section и правила отображения Odoo, owned cluster, backend groups и external contributions в общих колонках.

## Impact

- Основные implementation areas: `commands/output.py` как минимальный style-only table helper; локальные Rich projections в `commands/ps.py`, `commands/env/`, `commands/backup.py`, `commands/db.py`, `commands/pg.py`, `commands/module.py`, `commands/resource.py`, `commands/git.py`, `commands/translations.py`, `commands/test.py` и `commands/cli_parts/callbacks.py`.
- Процессные typed models и collectors остаются источником данных; публичная SDK/FastAPI модель, schema version и collection topology не меняются без доказанной необходимости.
- Расширяются CLI presentation/contract tests и существующие command-local тесты; production dependencies не добавляются.
- Незавершённый change `refactor-cli-output-boundary` остаётся отдельным: новый change использует уже существующий `OutputMode`/command-local Rich boundary и не переписывает его архитектуру.
