## Контекст

`MYL-460` воспроизводит два последовательных дефекта в приватном numbered workflow, добавленном в `MYL-445`:

1. `EnvironmentResource.adopt_command()` сравнивает `remote get-url` как сырые строки. При этом numbered coordinator уже умеет строго приводить `git@host:path` и `ssh://git@host/path` к одной безопасной идентичности. Поэтому корректный независимый clone отвергается до provisioning.
2. `_copy_auxiliary_session()` возвращает `None` для `remote_name` и `backup_id`. Это противоречит main specs: источник архива может быть удалённым, но target restore обязан идти через локальный project runtime/Compose cluster. В результате named-remote adoption имеет remote acquisition actions и локальные PostgreSQL probes, но не получает bounded auxiliary Database Manager для target restore.

Текущий код уже содержит все необходимые механизмы: строгий parser repository identity в `internal/agent_checkout.py`, immutable checkout plan, `AuxiliaryRestoreSession`, local PostgreSQL probes, COPY journal, compensation и selective cleanup. Изменение не должно создавать параллельный restore path или переносить project-specific Binta policy в публичный SDK.

## Цели / не-цели

**Цели:**

- принять только действительно эквивалентные безопасные Git remote формы при adoption;
- восстановить named-remote и retained-backup COPY в локальный target через существующий auxiliary lifecycle;
- сохранить единственный immutable plan, probe pair, journal и cleanup semantics;
- доказать полный numbered lifecycle и отсутствие влияния на reference/source/neighbor assets.

**Не-цели:**

- добавлять public slot CLI, новый transport, новый restore engine или database schema;
- менять формат project manifest, секретные inputs или remote-backup protocol;
- автоматически исправлять чужие remote URL, orphaned slots либо cleanup_failed ownership;
- включать реальные private URL, hostnames, порты, database names или secrets в Git artifacts.

## Решения

### 1. Один строгий normalizer repository identity в Git boundary

Перенести существующую `_canonical_repository_identity()` из numbered-specific `internal/agent_checkout.py` в нейтральный внутренний Git boundary (`internal/git_worktree.py`) с типизированной ошибкой/адаптацией вызывающего слоя. `agent_checkout` и `checkout_api.adopt_command()` используют одну функцию.

Normalizer сохраняет transport, host, explicit port и path; приводит host к lowercase и scp-like Git SSH к `ssh://git@...`; не удаляет `.git`, не объединяет HTTP и SSH и не выполняет network I/O. Secret-bearing/unsafe syntax продолжает отклоняться до mutation. `checkout_api` переводит ошибку разбора в существующий typed repository identity conflict без отражения входной строки.

Альтернатива — локально сравнить только две строковые формы в `checkout_api`. Она отвергнута: дублирует security-sensitive parser и оставляет numbered/adoption с разными правилами.

### 2. Source selection не управляет target auxiliary lifecycle

Изменить `_copy_auxiliary_session(plan)` так, чтобы COPY plan с usable local `source_config/start_config` захватывал существующий `AuxiliaryRestoreSession` для всех source kinds. `remote_name` и `backup_id` влияют только на `acquire_copy_backup()` и remote transfer actions, но не отключают local target runtime.

Execution сохраняет текущий порядок: session attach к immutable command; local auxiliary readiness до COPY preflight; remote acquisition при необходимости; coordinator-owned `exists-before`; restore request после exact endpoint revalidation; `exists-after`; journal `RESTORED`; cleanup auxiliary process. Никаких дополнительных probes или nested source-specific execution contexts не добавляется.

Альтернатива — исправить только Binta wrapper или запустить постоянный target Odoo вручную. Она отвергнута: wrapper уже передаёт корректные typed inputs, ручной listener обходит exact process/socket ownership и не исправляет SDK contract для retained backups.

### 3. Regression coverage следует существующим seams

- В `test_environment_adoption.py` добавить matrix для scp/SSH equivalence и negative mismatches; проверить отсутствие mutation при rejection.
- В checkout/restore tests проверить, что named remote и retained backup включают один auxiliary lifecycle, remote transfer только для named source, а before/after probes потребляются ровно один раз.
- В numbered tests проверить делегирование shared normalizer без копии parser и failure cleanup linkage.
- Реальный Mac E2E выполняется отдельно Tester-ом через test-repo wrapper и выделенный `odcli-N`; planning не запускает его.

## Ponytail gate

Новых подсистем нет. Repository identity использует существующий строгий parser вместо второго adapter; COPY restore использует существующий `AuxiliaryRestoreSession` вместо wrapper-specific runtime или нового restore coordinator. Решение: оставить оба существующих механизма, переместить только общий parser в его естественный boundary и удалить source-kind exclusion.

## Риски / компромиссы

- [Normalizer может случайно объединить разные repositories] → не удалять `.git`, не смешивать transports, сохранять port/path и покрыть mismatch matrix.
- [Auxiliary lifecycle для explicit source изменит plan shape] → использовать те же step IDs/order и расширить plan/architecture assertions, не добавляя второй lifecycle.
- [Restore failure после создания target требует destructive recovery evidence] → сохранить COPY journal stages и существующий fail-closed compensation; не объявлять отсутствие partial resources без доказанного ownership.
- [Mac E2E зависит от приватного стенда] → хранить scripts/fixtures и code results в MR, а команды, hashes, sanitized logs и изображения — только в Multica; секреты не передавать через argv или artifacts.

## План миграции

1. Внести shared normalizer и переключить оба caller-а с focused tests.
2. Расширить auxiliary selection на explicit COPY sources и подтвердить immutable plan/probe parity.
3. Выполнить repository checks и независимый Mac E2E на отдельном numbered slot.
4. При откате вернуть implementation commit; schema/data migration отсутствует. Не удалять доказательный branch/MR или retained recovery records автоматически.

## Открытые вопросы

Нет. Scope, ownership, local/remote boundary и recovery behavior определены main specs и этим delta package.
