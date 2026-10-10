## Why

OdCLI уже имеет типизированные SDK-примитивы, единый CLI envelope v1 и приватные исполняемые snapshots, но фактический машинный контракт остаётся распределён между Click-регистрацией, production DTO и тестовым `PUBLIC_LEAF_CASES`. Скриптам и будущим Python-пакетам нужен один экспортируемый контракт и локальный безpromptовый вызов, который сохраняет существующие проверки, не зависит от будущего Go-демона и не превращает JSON-проекции в вручную поддерживаемый второй API.

## What Changes

- Ввести стабильные namespaced operation IDs и единственный production inventory, связывающий существующие публичные SDK-примитивы, параметры, фактические wire-схемы результата/ошибки, exit semantics и transport policy; CLI aliases не создают новые операции.
- Экспортировать версионированный JSON Schema bundle и генерировать потребительские типы, включая Go, из того же bundle без зависимости установки Python-пакета от Go toolchain.
- Добавить локальный machine invoke для finite операций и bounded JSONL session для preview/approval/execution одного захваченного `Command`; native TTY, JSONL streams и interactive transports остаются отдельными явно классифицированными режимами.
- Захватывать один explicit operation context на вызов и передавать его SDK-примитиву, чтобы повторно не определять cwd, Git, configuration и catalogue; execution-time safety revalidation сохраняется.
- Расширить конечные monitor collectors выбором секций, общим временем наблюдения и явными freshness/completeness/unknown; публиковать raw cumulative CPU counters вместе с process identity, не имитируя межпроцессную CPU history.
- Сделать database/backup reads наблюдающими и вынести изменение audit/catalogue state в явные reconcile/repair operations; структурировать durable DB-replacement recovery evidence отдельно от `last_error` без нового workflow engine.
- Ввести минимальный Python entry-point contract, в котором descriptor, DTO и реализация операции поставляются одним пакетом; конфликт IDs, несовместимая версия или отсутствующий plugin завершаются bounded typed error.
- Сохранить envelope v1, Rich/JSON/TOON, текущие exit codes, standalone SDK/CLI, redaction, private executable snapshots, process/Expression gates, locks, ownership checks, rollback и postconditions. Телеметрия, Go/RPC-демон, hot reload и полный перенос существующих команд в плагины не входят в change.

## Capabilities

### New Capabilities

- `machine-operation-contracts`: Стабильная operation identity, production discovery, фактические wire schemas, schema/type export, local finite/JSONL invocation и минимальная Python plugin boundary.

### Modified Capabilities

- `cli-odcli`: CLI aliases связываются с operation ID; machine transports остаются без prompt/stdout pollution и отличают transport success от отрицательного domain outcome.
- `command-execution`: preview, approval, cancellation и execution используют один приватный snapshot в одном bounded процессе.
- `environment-monitor`: finite section selection, batch observation metadata и process CPU counters становятся честным типизированным результатом.
- `database-management`: read operations перестают скрыто записывать dropped/reconciliation state; запись выполняет явная typed operation.
- `backup-catalog`: read/latest остаются чистыми, а durable replacement recovery получает структурированное хранение и явный repair lifecycle.

## Impact

- Затрагиваются CLI composition/output/context, публичные operation DTO и `Command`, monitor/process collectors, database resource reconciliation, backup catalogue schema/migrations и DB-replacement recovery.
- Потребуются contract/schema fixtures, CLI/SDK/plugin compatibility tests, representative end-to-end cases для bounded read, previewable mutation и existing streaming/interactive transports, а также обновление документации.
- Активный `refactor-cli-output-boundary` остаётся источником envelope/format/typed-field contracts; change не дублирует его renderer или inventory graph. MYL-409/411 domain operations используют этот общий contract после интеграции, но Git/GitLab, module context, Caddy и panel не реализуются здесь. MYL-423/425 fixes считаются baseline и не перепланируются. MYL-429 telemetry не является prerequisite и не включается.
