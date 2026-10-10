## Контекст

Сейчас `internal/paths.py` жёстко вычисляет `Path.home() / ".odcli"`; все каталог, окружения, бэкапы, locks, pgAdmin и update evidence сходятся в этот root. `internal/storage_migration.py` также всегда мигрирует platformdirs-источники в `~/.odcli`, а self-update определяет uv-tool по текущему executable. Существующий `.agents/.../fix_tool.py` показывает рабочий локальный паттерн отдельных `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`, shim и lifecycle lock, но намеренно сохраняет общий `~/.odcli` и привязан к issue/PR. Для несовместимых ревизий этот паттерн переиспользовать напрямую нельзя.

## Цели / Не-цели

**Цели:**

- один источник истины для global user root при сохранении canonical default;
- воспроизводимый numbered slot из exact SHA, проверенный до публикации launcher;
- полная изоляция SDK-owned user state и безопасное удаление ровно одного slot;
- явная политика migration и update;
- доказуемая совместная работа двух несовместимых catalogue revisions.

**Не-цели:**

- менять `HOME`, XDG, Git/SSH/uv settings;
- изолировать project-local `.odcli`, Odoo, PostgreSQL, Docker, filestore или ports;
- заменять `odcli-fix-<issue>` либо привязывать slot number к issue/PR;
- вводить daemon, registry database, dependency или общий version solver.

## Решения

### D1. Один ранний selector global root

`internal.paths.get_user_root()` станет единственной точкой выбора. По умолчанию он возвращает существующий `~/.odcli`; trusted shim передаёт абсолютный `ODCLI_USER_ROOT=~/.odcli-N` и `ODCLI_SLOT_ID=N`, не меняя `HOME`. Остальные providers продолжают строить child paths от `get_user_root()`. Прямые `Path.home() / ".odcli"` для global state переводятся на provider; project-local `.odcli` не затрагивается.

Альтернатива — менять `HOME` в shim. Она отклонена: вместе с OdCLI переключились бы Git, SSH, uv и другие пользовательские настройки. Альтернатива с отдельной env-переменной для каждого каталога отклонена как источник рассинхронизации.

### D2. Canonical CLI владеет lifecycle slot

Добавляется группа `odcli slot` с `install`, `list`, `remove`. Детерминированные manager paths: launcher `~/.local/bin/odcli-N`, manager root `~/.local/share/odcli-slots/N`, uv layout внутри него, manifest с exact SHA и ожидаемыми путями, lock рядом с manifest; state остаётся `~/.odcli-N`. Slot number имеет единственную canonical decimal форму.

Install/replace сначала работает во временном sibling layout, использует fixed HTTPS Git origin и full SHA, затем читает installed `direct_url.json`/distribution metadata и сверяет repo+commit. Только после этого manifest и launcher публикуются через atomic replace. Shim — небольшой Python launcher без shell: shared-lock, фиксированные environment selectors, exec/subprocess exact argv. Replace/remove берут exclusive nonblocking lock и fail closed при активном child.

Отдельная registry SQLite отклонена: filesystem manifest и deterministic paths уже дают нужную идентичность. Встраивание slot в `uv tool` default registry отклонено: package name одинаков, а ownership removal становится неявным.

### D3. Replace сохраняет state, remove удаляет его

`--replace` меняет только verified tool+launcher+manifest и сохраняет `~/.odcli-N`: это и есть явное переназначение slot на новый SHA, после которого новая ревизия мигрирует только этот root. `remove` проверяет manifest, launcher content, отсутствие symlink/path escape и exact deterministic targets, затем удаляет только выбранные tool artifacts и slot state. Неожиданный файл или identity mismatch блокирует удаление вместо рекурсивного угадывания ownership.

Альтернатива с автоматическим backup/copy state при replace отклонена: она переносит абсолютные пути и migration history, усложняет ownership и не требуется. Альтернатива очищать state при каждом replace отклонена как неожиданная потеря данных; для чистого состояния оператор сначала выполняет explicit remove, затем install.

### D4. Legacy migration зависит от canonical selection

`ensure_storage_migrated()` сохраняет существующее поведение только когда selector не задан и root canonical. Для explicit noncanonical root он не вызывает platformdirs discovery/copy/rewrite/cleanup и возвращает отдельный no-adoption result; normal path providers лениво создают пустой slot root. Catalogue migrations конкретной revision затем применяются только к selected root.

Отдельный fork storage migration для slots отклонён: slot не имеет legacy source и не должен его приобретать.

### D5. Slot update и management запрещены из numbered context

Self-update проверяет `ODCLI_SLOT_ID` до plan construction и возвращает typed actionable failure с canonical replace command. `odcli-N slot ...` также запрещён: lifecycle другого slot всегда меняет canonical manager. Ordinary `odcli update` ничего не знает о numbered slots и не очищает их.

Альтернатива разрешить `odcli-N update --ref` отклонена: updater рассчитан на current uv tool identity, а implicit branch/ref update разрушает exact-SHA воспроизводимость и может затронуть не тот launcher.

### D6. Verification следует границе состояния

Unit tests инвентаризуют все global providers и доказывают root propagation, canonical default и migration bypass. Packaging E2E создаёт два uv layouts с различными local exact SHAs/fixture revisions, запускает несовместимые catalogue migrations одновременно, сравнивает sentinels/digests canonical и соседнего root, проверяет direct-update rejection, replace и selective remove. Отдельные tests подтверждают, что `HOME` не меняется и внешние project/Odoo/PostgreSQL ресурсы не объявляются изолированными.

## Ponytail Gate

| Новая подсистема | Необходимость | Существующая альтернатива | Решение |
| --- | --- | --- | --- |
| Central root selector | Нужен, иначе providers расходятся между roots | Текущий `internal.paths` | Оставить как минимальное расширение существующего provider |
| Slot lifecycle manager | Нужен для exact-SHA install/replace/remove | Паттерны `fix_tool.py` и uv env vars | Оставить внутри CLI, переиспользовать паттерны без PR-specific policy |
| Manifest + lock | Нужны для ownership и защиты running slot | JSON/shim/lock pattern fix-tool | Оставить один JSON и один lock, без database/service |
| Отдельный updater | Не нужен | Canonical manager replace | Убрать; direct slot update запрещён |
| State copier/backup | Не нужен и опасен для absolute paths/history | Explicit remove+install | Убрать |
| Slot registry database/daemon | Не нужен | Deterministic paths + bounded manifests | Убрать |

## Риски / Компромиссы

- **[Неполный inventory global paths]** → статический test/grep gate перечисляет прямые home-based global writes; implementation переводит их на central provider до acceptance.
- **[Crash между tool install и publish]** → temporary sibling layout не считается slot; следующая management operation распознаёт и безопасно очищает только собственный temp по manifest/имени.
- **[Повреждённый manifest или изменённый shim]** → list показывает unhealthy, replace/remove fail closed; автоматического рекурсивного repair нет.
- **[Одна project copy использует общие PostgreSQL/Docker/ports]** → help/docs явно требуют отдельные project copies либо разнесённые external resources; manager не обещает их изоляцию.
- **[Revision не понимает selector]** → post-install provenance недостаточна; manager выполняет bounded capability probe новой revision с isolated temporary root до публикации.

## План миграции и отката

1. Выпустить additive selector и canonical slot manager; существующий canonical root и update остаются default.
2. Установка первого slot создаёт только manager artifacts; state появляется при первом вызове numbered command.
3. Existing canonical/legacy data не перемещаются и не копируются.
4. Откат production code оставляет slots как обычные локальные artifacts; до отката оператор удаляет их новой командой. Если code уже откатан, deterministic paths и manifest дают ручной bounded cleanup без удаления canonical state.

## Открытые вопросы

Нет. Формат number, path ownership, replace semantics, migration policy, update policy и external-resource boundary зафиксированы нормативно.
