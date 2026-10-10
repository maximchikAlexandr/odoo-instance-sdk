## Контекст

`internal/paths.py` сейчас жёстко сводит SDK-owned global state в `~/.odcli`. Существующий standalone `.agents/skills/odcli-autonomous-work/scripts/fix_tool.py` уже содержит минимальный lifecycle exact-SHA tool: проверяет hot-fix policy, ставит uv tool, пишет manifest/shim с `flock`, а после merge gates удаляет tool. Но он хранит registry в canonical root, не выбирает отдельный user root и не входит в wheel. Предыдущая revision ошибочно планировала вторую копию этих mechanics для numbered manager.

Текущие execution boundaries подтверждены: product CLI поставляется только из `src/odoo_instance_sdk`; skill script запускается системным Python и не может быть импортируемой product dependency. Значит, повторное использование возможно только если existing lifecycle mechanics один раз перейти в package-owned module, а skill script станет тонким policy/bootstrap caller этого module.

## Цели / Не-цели

**Цели:**

- один implementation install/provenance/capability/manifest/lock/shim/list/remove для `odcli-N` и `odcli-fix-ISSUE`;
- canonical `odcli`, каждый numbered launcher и каждый hot-fix launcher имеют взаимоисключающий SDK-owned root;
- numbered surface отличается только identity, replace policy и CLI rendering;
- hot-fix surface отличается только PR/reviewer/issue/merge/branch policy;
- новый alternate root всегда пуст, а cleanup удаляет только доказанно принадлежащие launcher artifacts.

**Не-цели:**

- generic plugin framework, registry database, daemon, dependency или automatic state copier;
- два lifecycle managers, два capability probes, два shim renderers или две cleanup implementations;
- менять `HOME`, XDG, Git/SSH/uv settings;
- изолировать repository-local `.odcli`, project checkout, Odoo, PostgreSQL, Docker, filestore или ports.

## Решения

### D1. Один ранний selector global root

`internal.paths.get_user_root()` остаётся единственной точкой выбора. Без trusted selector возвращается `~/.odcli`; shared launcher передаёт абсолютный `ODCLI_USER_ROOT` и typed launcher kind/id. Numbered identity даёт `~/.odcli-N`, hot-fix identity — `~/.odcli-fix-ISSUE`. `HOME` не меняется. Все SDK-owned global paths следуют provider; repository-local `.odcli` остаётся project-local.

Подмена `HOME` отклонена, потому что перенаправит Git, SSH и uv. Много отдельных env vars отклонено как источник split-brain paths.

### D2. Existing fix-tool lifecycle становится единственным shared engine

Existing `fix_tool.py` mechanics переносятся, а не переписываются рядом, в узкий `internal/alternate_tool.py` (или эквивалентный package-owned module). Он владеет ровно общей частью:

- canonical positive decimal identity и deterministic state/tool/launcher/manifest/lock paths;
- temporary uv install из fixed repository, exact-SHA installed provenance и root-selector capability probe;
- один manifest format, один shim renderer, full-lifetime shared lock и atomic publication;
- bounded list/inspect и fail-closed exclusive-lock removal с containment, symlink, launcher-content и neighbor checks.

Module принимает только launcher kind/id, requested SHA и фиксированные typed manifest fields: common lifecycle identity плюс hot-fix PR/review/branch fields только для hot-fix kind. Он не решает PR eligibility, merge ancestry или numbered replace policy. Numbered CLI импортирует этот API напрямую.

Standalone `fix_tool.py` сохраняет существующий entrypoint и stdlib bootstrap. Он находит canonical `odcli`, проверяет абсолютный executable shebang/interpreter и запускает `python -m odoo_instance_sdk.internal.alternate_tool` из canonical uv environment. Так skill использует тот же installed module без копирования package code и без добавления private/public Click surface. Невозможность доказать canonical interpreter блокирует mutation.

Альтернативы отклонены: импорт `.agents` из wheel невозможен; копирование mechanics в numbered manager создаёт подтверждённый дубль; новый generic service/console script расширяет public/runtime surface без необходимости.

### D3. Два тонких policy adapters

Canonical `odcli slot` валидирует numbered intent: unused vs explicit replace, canonical-only invocation и Rich/JSON/TOON output. Затем он вызывает shared install/list/remove. Он не реализует uv, provenance, manifest, shim или cleanup.

`fix_tool.py` валидирует только hot-fix intent: review bound to exact SHA, PR head and linked issue, external-resource compatibility, merge/default-branch/issue/ancestry/branch eligibility. Install/inspect/remove делегируются shared engine. Compatibility reviewer больше не сравнивает isolated catalogue schemas; selector capability доказывает shared engine, reviewer оценивает только действительно общие external resources.

Один discriminated shared manifest хранит lifecycle identity и только заранее определённые kind-specific fields. Shared engine проверяет structural fields; hot-fix adapter интерпретирует только фиксированные PR/review/branch fields. Arbitrary metadata/extensions отсутствуют. Unknown old shared-state manifest не принимается и не мигрируется.

### D4. Детерминированные layouts

- canonical state: `~/.odcli`;
- numbered state: `~/.odcli-N`, manager/tool: `~/.local/share/odcli-slots/N`;
- hot-fix state: `~/.odcli-fix-ISSUE`, manager/tool: `~/.local/share/odcli-fix-tools/ISSUE`;
- launchers: `~/.local/bin/odcli-N` и `~/.local/bin/odcli-fix-ISSUE`.

Manifest и lock живут в manager path вне state root. Existing `~/.odcli/fix-tools` больше не читается и не пишется новым workflow. Old-style manifests остаются fail-closed с explicit remediation; silent adoption могло бы удалить чужое состояние.

### D5. Один staged publication и один cleanup algorithm

Shared install создаёт temporary sibling uv layout, проверяет fixed repository/exact SHA, затем запускает bounded capability probe с disposable empty root и sentinels вокруг canonical/neighbor roots. Только после этого один renderer публикует launcher и manifest. Numbered replace использует тот же staged transition; hot-fix install добавляет policy gates до вызова engine.

Каждый rendered shim держит shared lock полный child lifetime, передаёт только root/kind/id/executable selectors и делегирует argv без shell. Shared remove берёт exclusive nonblocking lock и проверяет deterministic targets, manifest, SHA, launcher bytes, regular-file/symlink identity и containment. Numbered policy разрешает explicit replace/remove. Hot-fix policy вызывает remove только после retirement gates. Сам алгоритм удаления один.

### D6. Legacy migration только canonical

`ensure_storage_migrated()` выполняет platformdirs discovery/copy/rewrite/cleanup только без explicit selector. Alternate root не инспектирует canonical/legacy paths и создаётся пустым обычными providers. Catalogue migrations выбранной revision применяются только внутри selected root.

### D7. Direct self-update запрещён для alternate launchers

Self-update проверяет launcher context до plan construction. `odcli-N update` направляет к canonical slot replace; `odcli-fix-ISSUE update` — к skill-managed canonical update/reconcile. Ordinary `odcli update` не обнаруживает и не меняет alternate roots. После успешного canonical update skill wrapper может проверить hot-fix policy и делегировать eligible removal shared engine.

### D8. Acceptance доказывает reuse и отрицательные границы

Unit tests импортируют shared engine один раз и проверяют обе identities через одинаковые install/render/lock/remove cases; adapter tests доказывают только policy differences. Architecture test запрещает uv-install, shim-rendering, flock/remove implementations вне shared module. Process/packaging acceptance запускает canonical, два numbered и два hot-fix roots с incompatible SQLite migrations и before/after digests.

## Ponytail Gate

| Часть | Что уже есть | Решение |
| --- | --- | --- |
| uv install/provenance | `fix_tool.py` | Перенести один раз в shared engine; удалить из wrapper |
| manifest/shim/flock/remove | `fix_tool.py` | Перенести один раз; обе identities используют один API/renderer/cleanup |
| user-root selector | `internal.paths` | Минимально расширить один provider |
| numbered commands | отсутствуют | Добавить тонкий adapter к shared engine |
| hot-fix PR/reviewer/retirement policy | `fix_tool.py`/`reviewer.py` | Оставить тонким skill-specific слоем |
| второй manager/capability probe/renderer | не нужен | Удалить из plan |
| registry DB/daemon/plugin abstraction | не нужен | Не создавать; deterministic paths + manifest достаточно |
| state copier/migrator | опасен | Не создавать; alternate root starts empty |

## Риски / Компромиссы

- **[Standalone skill не находит canonical package interpreter]** → validate resolved `odcli` entrypoint/shebang and fail before mutation; packaging acceptance covers uv installation.
- **[Shared engine получает policy logic]** → manifest допускает только фиксированные kind-specific fields, но engine не интерпретирует PR/replace eligibility; adapter tests enforce boundary.
- **[Revision не понимает selector]** → единственный pre-publication capability probe блокирует launcher.
- **[Неполный inventory global paths]** → provider inventory и cross-root filesystem spy входят в acceptance.
- **[Old hot fix использует shared state]** → no adoption/migration; fail closed with explicit remediation.
- **[Общие PostgreSQL/Docker/ports конфликтуют]** → reviewer/docs требуют separate project copies или disjoint resources.

## План миграции и отката

1. Выпустить selector и shared lifecycle module, полученный refactor existing fix-tool mechanics.
2. Перевести `fix_tool.py` на policy/bootstrap delegation и isolated hot-fix root.
3. Добавить numbered CLI как второй thin adapter к уже проверенному engine.
4. Не переносить и не удалять old shared-state manifests автоматически.
5. При rollback удалить новые alternate launchers штатными adapters до отката; canonical state не затрагивается.

## Открытые вопросы

Нет. Shared-code placement, bootstrap boundary, policy split, layouts, migration, update и cleanup ownership зафиксированы.
