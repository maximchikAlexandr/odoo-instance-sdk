## Context

Checkout заранее разрешает Python mode и сохраняет его в `_CheckoutPlan`: reused mode хранит конкретный interpreter в `python_path`, owned mode хранит venv path и создаёт interpreter через `_owned_python_executable(plan.venv)`. `_checkout_steps()` затем строит один immutable набор process steps. Сейчас install/sync step содержит `--python`, а непосредственно предшествующий compile step — нет, поэтому эти два шага могут разрешить зависимости для разных Python versions.

Изменение ограничено legacy dependency-input branch checkout. Hash-lock branch уже минует compilation, а `env sync` не входит в scope GitHub #82.

## Goals / Non-Goals

**Goals:**

- Гарантировать, что checkout compile и последующий install/sync используют один resolved Python executable.
- Сохранить inspectable immutable command snapshot и существующий process boundary.
- Покрыть reused и owned Python modes одной параметризованной регрессией.

**Non-Goals:**

- Не менять public SDK/CLI contracts, project manifest, catalog schema или dependency set.
- Не вводить resolver abstraction, config option или environment-variable workaround.
- Не менять hash-lock branch и поведение отдельной операции `env sync`.

## Decisions

### 1. Вычислять checkout dependency Python один раз внутри `_checkout_steps()`

Для dependency-input branch `_checkout_steps()` SHALL определить `dependency_python`: `_owned_python_executable(plan.venv)` для owned mode и `plan.python_path` для reused mode. Этот exact value SHALL передаваться как `--python` и в `uv pip compile`, и в последующий `uv pip sync`/`uv pip install`.

Это сохраняет исправление в единственной точке, где строятся оба связанных шага, и исключает повторное разрешение interpreter. Вариант с `UV_PYTHON` отклонён: скрытое process environment не выражает контракт в inspectable argv. Общий resolver/builder отклонён как ненужная абстракция для одной локальной развилки.

### 2. Сохранить существующие ветви installation

Owned mode SHALL продолжать выполнять `uv pip sync`, reused mode — `uv pip install -r`; добавляется только target constraint к compile argv. Hash-lock mode SHALL по-прежнему содержать единственный `uv pip sync --require-hashes` dependency step без compile.

Это минимизирует изменение и не затрагивает ownership/cleanup semantics. Унификация install и sync отклонена, потому что она изменила бы сохранение посторонних tools в reused venv.

### 3. Проверять равенство target interpreter в публично наблюдаемом command path

Параметризованный unit test SHALL выполнить checkout для reused и owned modes через существующий fake process boundary, извлечь единственные compile и install/sync calls и проверить, что значение после `--python` совпадает. Тест SHALL также сохранить существующую проверку различия `install`/`sync` по ownership mode.

Проверка внутренней локальной переменной отклонена: она не доказывает содержимое реально исполняемого immutable argv.

## Risks / Trade-offs

- [Risk] Порядок аргументов `uv pip compile` может сделать слишком точный тест хрупким → проверять наличие `--python` и его значение, а не полный argv.
- [Risk] Исправление checkout оставляет аналогичный вопрос для отдельного `env sync` вне scope → явно не менять его здесь; отдельное изменение возможно только по отдельному требованию.
- [Trade-off] Добавление одного argv pair меняет dry-run projection → это ожидаемый результат: preview должен показывать тот же Python constraint, который будет исполнен.

## Migration Plan

Data migration не требуется. Изменение поставляется как backward-compatible argv correction; rollback — возврат единственного implementation commit, без преобразования catalog или generated lock files.

## Open Questions

Нет.
