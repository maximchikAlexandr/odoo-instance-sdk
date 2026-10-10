## Контекст

`internal/paths.py` сейчас жёстко сводит SDK-owned global state в `~/.odcli`; storage migration, catalogue, environments, backups, locks, pgAdmin, reports и update evidence следуют туда. Существующий skill-local `fix_tool.py` уже создаёт отдельный uv layout и `odcli-fix-<issue>`, но хранит registry в `~/.odcli/fix-tools`, не передаёт root selector и предупреждает, что hot fix разделяет canonical state. Следовательно, разные executable не дают изоляции данных. Новая revision распространяет один root-selection contract и на numbered slots, и на hot-fix launchers.

## Цели / Не-цели

**Цели:**

- canonical `odcli`, каждый `odcli-N` и каждый `odcli-fix-ISSUE` имеют взаимоисключающий SDK-owned root;
- exact-SHA provenance и selector capability проверяются до публикации alternate launcher;
- новый alternate root всегда пуст, без копирования absolute paths и migration history;
- numbered removal и hot-fix retirement удаляют только доказанно принадлежащий им root/tool/launcher/metadata;
- несовместимые SQLite migrations и concurrent writes не пересекают roots.

**Не-цели:**

- менять `HOME`, XDG, Git/SSH/uv settings;
- объединять numbered slots и issue-bound hot fixes в один пользовательский workflow;
- изолировать repository-local `.odcli`, project checkouts, Odoo, PostgreSQL, Docker, filestore или ports;
- добавлять daemon, registry database, dependency, generic plugin manager или automatic state copier.

## Решения

### D1. Один ранний selector global root

`internal.paths.get_user_root()` остаётся единственной точкой выбора. Без selector возвращается `~/.odcli`; trusted numbered shim передаёт абсолютный `ODCLI_USER_ROOT=~/.odcli-N`, hot-fix shim — `ODCLI_USER_ROOT=~/.odcli-fix-ISSUE`. Отдельный typed launcher-kind/id selector позволяет self-update выдать правильную remediation. `HOME` не меняется. Все прямые global `Path.home() / ".odcli"` writes переводятся на provider либо классифицируются как manager/executable paths; project-local `.odcli` остаётся на месте.

Подмена `HOME` отклонена, потому что переключит Git, SSH и uv. Набор env vars для каждого каталога отклонён как источник split-brain paths.

### D2. Общий маленький identity/path contract, разные managers

Typed helper валидирует canonical positive decimal identity и вычисляет deterministic state/manager/tool/bin/launcher/manifest/lock paths. Numbered manager остаётся public `odcli slot`; hot-fix manager остаётся skill-local и сохраняет PR/issue/reviewer policy. Они переиспользуют только identity/path, verified uv provenance, lifecycle lock и shim rendering primitives, но не получают generic registry/service abstraction.

Это минимальная общая граница: duplicating root validation создаёт риск расхождения, а объединение user workflows стирает разные ownership и retirement rules.

### D3. Детерминированные layouts

- canonical state: `~/.odcli`;
- numbered state: `~/.odcli-N`, manager: `~/.local/share/odcli-slots/N`;
- hot-fix state: `~/.odcli-fix-ISSUE`, manager/tool metadata: `~/.local/share/odcli-fix-tools/ISSUE`;
- launchers: `~/.local/bin/odcli-N` и `~/.local/bin/odcli-fix-ISSUE`.

Manifest и lock находятся вне state root, поэтому cleanup может проверить ownership до удаления state. Existing hot-fix registry больше не читает и не пишет canonical `~/.odcli/fix-tools`. Legacy manifests не переносятся автоматически: неизвестная старая shared-state installation остаётся fail-closed и требует явного удаления старым workflow либо ручной эскалации; молчаливое adoption могло бы удалить или переименовать чужое состояние.

### D4. Staged exact-SHA publication и capability probe

Оба installers сначала создают temporary sibling uv layout, проверяют fixed repository и exact SHA по installed metadata, затем запускают bounded capability probe с disposable empty root и sentinels вокруг canonical/neighbor roots. Только revision, доказавшая selector confinement, может получить launcher. Numbered replace публикуется атомарно. Hot-fix install дополнительно сохраняет existing compatibility review, PR head и linked issue gates; review теперь оценивает shared external resources, а не schema compatibility изолированных catalogues.

Копирование canonical catalogue для probe или первого запуска отклонено: оно переносит absolute paths/history и уничтожает смысл isolation.

### D5. Lifecycle locks и точный cleanup

Каждый shim держит shared lock полный child lifetime и делегирует argv без shell. Replace/remove/retire берут exclusive nonblocking lock. Перед удалением manager сверяет deterministic targets, manifest schema, recorded SHA, launcher bytes, regular-file/symlink identity и containment. Numbered remove удаляет один slot. Hot-fix retirement дополнительно требует merged PR в default branch, closed linked issue, installed canonical ancestry и safe branch/worktree deletion; затем удаляет только eligible hot fix и `~/.odcli-fix-ISSUE`.

State сохраняется при numbered replace, поскольку это явная revision reassignment. Hot-fix revision не заменяется: новый SHA требует пересмотра и переустановки после явного удаления/retirement, иначе review evidence перестаёт быть exact.

### D6. Legacy migration только canonical

`ensure_storage_migrated()` вызывает platformdirs discovery/copy/rewrite/cleanup только без explicit selector и для canonical root. Alternate root не инспектирует legacy или canonical paths; обычные providers лениво создают его пустым. Catalogue migrations выбранной revision применяются только внутри selected root.

### D7. Direct self-update запрещён для alternate launchers

Self-update проверяет typed launcher context до plan construction. `odcli-N update` направляет к canonical slot replace. `odcli-fix-ISSUE update` направляет к skill-managed canonical update/reconcile и не предлагает replacement. Ordinary `odcli update` не обнаруживает и не меняет alternate roots. Только отдельная skill reconciliation после успешного canonical update может selectively retire eligible hot fixes.

### D8. Acceptance доказывает отрицательные границы

Process/packaging fixtures создают populated canonical и legacy roots, два numbered roots и два hot-fix roots с разными schema fixtures. Concurrent migrations/writes проверяются по filesystem access spy, SQLite revisions и before/after digests. Отдельно проверяются first-run no-adoption, direct updates, canonical update isolation, numbered removal, eligible hot-fix retirement, active lock и corrupt launcher/manifest. External resources в этих fixtures разделены; документация запрещает считать их изолированными автоматически.

## Ponytail Gate

| Новая или изменяемая часть | Необходимость | Существующая альтернатива | Решение |
| --- | --- | --- | --- |
| Central root selector | Нужен для полного path confinement | `internal.paths` | Оставить как минимальное расширение provider |
| Alternate identity/path helper | Нужен двум launcher видам для одинаковой валидации/containment | Разрозненные `_paths()` и literals | Оставить компактный typed helper |
| Numbered lifecycle manager | Нужен для user-managed exact-SHA slots | Existing uv/fix-tool patterns | Оставить public CLI без registry service |
| Hot-fix manager rewrite | Нужен, потому что существующий workflow пишет canonical state | Existing `fix_tool.py`/reviewer/skill | Изменить на isolated root, не создавать второй manager |
| Manifest + lifecycle lock | Нужны для ownership и running protection | Existing fix-tool JSON/flock pattern | Переиспользовать один manifest и lock на launcher |
| Отдельный updater | Не нужен | Canonical replace или skill reconciliation | Убрать; direct alternate update запрещён |
| State copier/migrator | Не нужен и опасен | Fresh empty root | Убрать |
| Registry DB/daemon/generic plugin layer | Не нужен | Deterministic paths + bounded manifests | Убрать |

## Риски / Компромиссы

- **[Revision не понимает selector]** → pre-publication disposable-root capability probe блокирует launcher.
- **[Неполный inventory global paths]** → static provider inventory и cross-root filesystem spy входят в acceptance.
- **[Существующий old-style hot fix разделяет canonical state]** → не adopt/migrate автоматически; detect and fail closed с explicit remediation.
- **[Crash между install и publish]** → temporary sibling не считается installed launcher и удаляется только по manager-owned identity.
- **[Повреждённый manifest/shim или symlink]** → list/cleanup fail closed; guessing и recursive repair отсутствуют.
- **[Общие PostgreSQL/Docker/ports конфликтуют]** → compatibility review и docs требуют separate project copies или explicit disjoint resources.
- **[Hot-fix PR merged, но process активен]** → exclusive lock не берётся, все artifacts сохраняются до следующей reconciliation.

## План миграции и отката

1. Выпустить additive selector и alternate identity helpers; canonical default не меняется.
2. Перевести numbered manager и existing hot-fix skill на deterministic isolated layouts.
3. Existing old-style hot-fix manifests не переносить и не удалять автоматически; показать fail-closed remediation.
4. Новые roots появляются только при первом invocation и не получают данных из canonical/legacy roots.
5. При rollback удалить новые alternate launchers штатными managers до отката; deterministic manifests остаются bounded evidence для ручной эскалации, но canonical state не затрагивается.

## Открытые вопросы

Нет. Root naming, old-style behavior, compatibility boundary, update policy, lifecycle ownership и cleanup зафиксированы нормативно.
