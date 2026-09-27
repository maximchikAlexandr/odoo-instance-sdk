## Why

Текущий честный targeted mutation audit генерирует 298 мутантов, но единый GitHub Actions job не успевает классифицировать их за настроенные 30 минут: наблюдаемый запуск после 34 минут оставил 200 мутантов в состоянии `not checked`. Пока workflow не достигает терминальной классификации, его диагностический отчёт и будущая baseline-политика не могут надёжно обнаруживать регрессии.

## What Changes

- Разделить неизменный пятифайловый mutation scope на GitHub Actions matrix: один канонический target назначается ровно одному shard, а каждый shard запускает существующий bounded smoke и mutmut только для своего target.
- Расширить единый runner-контракт `make mutation`, чтобы он принимал один проверенный target из канонической конфигурации, собирал shard-local terminal classification и падал при любом `not checked` или неполном отчёте.
- Публиковать отдельную диагностику каждого shard даже при ошибке, затем в обязательном aggregate job проверять полноту набора shard-отчётов, суммировать terminal категории и выпускать единый complete summary; любая потеря, дублирование или незавершённость shard SHALL завершать workflow с ошибкой.
- Зафиксировать первый полный current-main результат как version-controlled baseline для `survived` и `timeout`; последующие запуски SHALL падать при увеличении любого из этих counts, а уменьшение SHALL обновляться отдельным осознанным изменением baseline.
- Сохранить существующие пять targets, `mutmut>=3,<4`, честную non-zero failure-семантику, bounded real-mutmut smoke и diagnostic/non-required policy; production SDK/CLI и PR-required gates не меняются.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `mutation-testing`: добавить полное partition/aggregation поведение, terminal-completeness gate и version-controlled survivor/timeout baseline поверх существующего честного targeted audit.

## Impact

- CI и orchestration: `.github/workflows/mutation.yml`, `Makefile`, `scripts/run_mutation.py` и один небольшой stdlib-only aggregate/validation seam под `scripts/`.
- Конфигурация: `pyproject.toml` остаётся единственным каноническим перечнем пяти mutation targets; baseline хранится в одном reviewable repository-файле без нового framework или сервиса.
- Проверка: focused runner, partition, aggregation, baseline и workflow-contract tests; ручной `workflow_dispatch` на current `main` остаётся обязательным acceptance evidence.
- Документация: contributor mutation workflow, shard artifacts, aggregate summary и правило baseline regression.
- Public Python API, production modules, CLI-поведение, mutation target scope и required PR gates не затрагиваются.
