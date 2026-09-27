## Основание

- Planning issue: `MYL-271`.
- OpenSpec change: `add-backup-checkout-and-detached-readiness`.
- Rebase baseline: `origin/main` at `3d688b26b463d273e80fa46500226158d7d9fab1`; planning series replayed directly on that commit without a merge commit.
- Authoritative optimistic, weighted и pessimistic totals хранятся только в properties корневой planning issue `Estimate min, hours`, `Estimate, hours` и `Estimate max, hours`; все три значения подтверждены read-back. `Estimate, hours` находится выше порога single-WP.
- Оценка покрывает полный remaining scope до всех acceptance scenarios одним опытным разработчиком, знакомым с Python, Click, msgspec, SQLite/Alembic и этим репозиторием, без AI-ускорения. Unattended CI, очереди review и внешние ожидания исключены.
- Уверенность: средняя. Все затронутые публичные входы, lifecycle/catalog/config boundaries и test matrices доступны, но COPY recovery, destructive revalidation, listener ownership на разных ОС и композиция post-success maintenance создают связанные риски.
- Калибровка: uncalibrated — сопоставимые фактические трудозатраты не предоставлены. Основание: текущие `ProjectConfig`/project-init commands, database preparation, exact backup deletion, Alembic/Core catalog, COPY journal/recovery, detached runtime identity/listener proof, bounded output и canonical leaf inventory. Тесты для оценки не запускались.

## Топология исполнения

Режим: `dag` (multi-WP).

```text
WP-01 persisted configuration and catalog foundation ─┬→ WP-02 named preparation ─┐
                                                       └→ WP-05 retention core ────┴→ WP-03 explicit COPY → WP-06 auto-prune composition ─┐
WP-04 detached readiness ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴→ WP-07 delivery gate
```

Topological level 1 содержит независимые `WP-01` и `WP-04`. После `WP-01` level 2 содержит независимые `WP-02` и `WP-05`. `WP-03` напрямую ждёт оба этих контракта, потому что использует named acquisition и те же catalog projections/locks. `WP-06` напрямую следует только за `WP-03`: retention contract уже достигается транзитивно через `WP-03`. `WP-07` напрямую соединяет готовую detached readiness из `WP-04` с полным source/COPY/retention потоком из `WP-06` и единолично владеет общими CLI callbacks, leaf inventory, документацией и интеграционными repairs.

## WP-01 — Persisted source, policy and catalog foundation

- **Task coverage:** `1.1`, `1.2`, `1.3`, `3.1`, `5.1`.
- **Deliverable:** typed named-source manifest/configuration and retention-policy SDK contracts plus one verified linear catalog migration for historical source, pin state and COPY ownership.
- **depends_on:** none.
- **Stage / topological level:** 1.
- **Owned responsibility scope:** `project.py`, public project-init/configuration functions, manifest locking/writing and init inputs; narrow `user.toml` retention reader/updater; catalog Core metadata, Alembic revision, migration gates, backup/environment projection fields and their focused tests. Critical shared files include `src/odoo_instance_sdk/project.py`, `project_init.py`, `internal/project_manifest.py`, `resources/backup.py`, `storage/catalog_schema.py`, `storage/catalog_migrate.py`, `storage/catalog_migrations/`, catalog projection helpers and directly related init/config/migration tests. CLI init registration required by task `1.3` is owned here; all other CLI registration remains `WP-07`.
- **Contract surface:** deterministic `[remote_instances.NAME]`; one list/configure/remove SDK surface with manifest-fingerprint revalidation; typed retention policy preserving unrelated `user.toml`; nullable conservative migration fields; audited pin event type; single Alembic head and metadata equivalence.
- **DoD / evidence:** round-trip/validation/drift/preview tests; init preservation and credential-key-only reporting; user settings preservation and permission tests; upgrade/fresh-schema/conservative-default/single-head/schema-equivalence tests; Ruff and mypy for touched files.
- **Parallel-safety rationale:** this WP owns shared persisted schemas and configuration contracts before any dependent package. Its production write zone does not overlap `WP-04` runtime files.

## WP-02 — Named credential, preparation and diagnostic path

- **Task coverage:** `1.4`, `1.5`, `2.1`, `2.2`, `2.3`.
- **Deliverable:** exact named-source selection from SDK through secure credential resolution, download/restore preparation and truthful diagnostics, with legacy origin variables inert.
- **depends_on:** `WP-01`.
- **Stage / topological level:** 2.
- **Owned responsibility scope:** project dotenv and child-environment filtering, shared redaction inputs, test-instance trust removal, database preparation source/snapshot/materialization, doctor checks and focused preparation/security tests. Critical shared files include `internal/project_env.py`, `internal/process_env.py`, `internal/dbprep/`, `internal/test_instance_trust.py`, `internal/doctor/`, database preparation models/resources and their tests. CLI callbacks/registration are read-only and belong to `WP-07`.
- **Contract surface:** one derived password key per validated name; canonical worktree root; process precedence; redirect-disabled transport; legacy no-op origin values; explicit source identity and stale-plan/coalescing keys; no hidden source/default selection; offline doctor without authentication claims.
- **DoD / evidence:** named/legacy selection matrix, two-secret isolation, no-secret boundary assertions, origin-variable regression, download-only/restore/default-switch compatibility, profile drift/coalescing, and offline diagnostics all pass; no new secret store/provider/dependency exists.
- **Parallel-safety rationale:** this WP writes preparation/doctor/security modules only. `WP-05` owns backup retention/catalog operations and `WP-04` owns runtime readiness, so level-2 siblings are disjoint.

## WP-03 — Explicit remote and retained-backup COPY

- **Task coverage:** `3.2`, `3.3`, `3.4`, `3.5`.
- **Deliverable:** one COPY pipeline accepting local source, exact named remote or exact retained UUID and preserving isolation, provenance, journal ownership and recovery guarantees.
- **depends_on:** `WP-02`, `WP-05`.
- **Stage / topological level:** 3.
- **Owned responsibility scope:** environment checkout option/planning/execution/artifacts, COPY journal use, exact backup validation/restore handoff and directly related environment/database lifecycle tests. Critical shared files include `resources/environment/checkout_planning.py`, `resources/environment/checkout.py`, `resources/environment/checkout_artifacts.py`, environment cleanup/settings, database preparation handoff models and checkout/recovery tests. Persisted schema definitions are read-only after `WP-01`; retention query/delete primitives are read-only after `WP-05`.
- **Contract surface:** mutually exclusive source inputs; named base resolution to local commit; no hidden Git/network/default fallback; download-only named acquisition; offline exact UUID; owned/borrowed/unknown cleanup; shared checksum/archive/disk/cluster/version/lock gates; retained actionable recovery evidence.
- **DoD / evidence:** validation matrix, exact provenance, no-intermediate/default mutation, HTTP call counts, filestore isolation, neutralization/postconditions, ownership-safe rollback/removal, corruption/disk/busy/collision/version and retry-from-retained-UUID scenarios pass.
- **Parallel-safety rationale:** this WP starts only after both source preparation and retention/catalog query contracts are frozen, eliminating concurrent writes to shared backup/catalog seams.

## WP-04 — Exact detached readiness and owned cleanup

- **Task coverage:** `4.1`, `4.2`, `4.3`.
- **Deliverable:** environment-bound detached launch optionally returns only after exact owned runtime readiness and performs bounded ownership-safe cleanup on failure.
- **depends_on:** none.
- **Stage / topological level:** 1.
- **Owned responsibility scope:** shared runtime/listener identity helper, auxiliary restore consumer adaptation, detached planning/execution and focused lifecycle tests. Critical shared files include `resources/instance/auxiliary_restore_identity.py`, the extracted shared identity module, `resources/instance/auxiliary_restore.py`, `resources/instance/planning.py`, runtime identity helpers, `tests/unit/test_run_detached.py` and auxiliary restore identity tests. CLI callback edits belong to `WP-07`.
- **Contract surface:** exact PID/create-time/argv/cwd/config/socket proof, with the injected effective `--logfile` retained in expected argv; health plus expected environment/database binding; finite timeout; immutable wait/cleanup actions; terminate only owned process group; conditional runtime-row clear; retained logfile/tail and surviving-process identity.
- **DoD / evidence:** disabled compatibility, success, early exit, timeout, wrong listener/binding, effective-logfile argv mismatch, unavailable inspection, inert preview, confirmed cleanup and cleanup-failure evidence pass on supported platform seams; auxiliary restore keeps the same proof.
- **Parallel-safety rationale:** runtime lifecycle files are disjoint from `WP-01` persisted configuration/catalog foundation and all later retention/COPY packages.

## WP-05 — Pinning, protected deletion and manual prune

- **Task coverage:** `5.2`, `5.3`, `5.4`.
- **Deliverable:** idempotent audited pinning, universally protected exact deletion, and immutable project-scoped manual prune with execution-time revalidation.
- **depends_on:** `WP-01`.
- **Stage / topological level:** 2.
- **Owned responsibility scope:** backup resource models/commands, catalog retention queries and pin events, lifecycle-lock/delete composition and focused backup/catalog tests. Critical shared files include `resources/backup.py`, `models/backup.py`, `storage/catalog/backup.py`, backup locks/deletion helpers and directly related unit/integration tests. Environment checkout production files and CLI registration are excluded.
- **Contract surface:** deterministic named/legacy source grouping; pin/busy/live-environment/recovery/newest protection shared by delete and prune; captured policy/cutoff/file identity/bytes; no force; no plan widening; partial truthful outcomes and preserved audit.
- **DoD / evidence:** age/tie/group/protection/unknown/external matrix, inert preview, changed policy/pin/reference/file/latest races, concurrent restore, partial failure, bytes, idempotency and audit assertions pass.
- **Parallel-safety rationale:** this package owns backup/catalog retention modules while `WP-02` owns source preparation and `WP-04` owns runtime readiness. `WP-03` explicitly waits for it before using shared catalog seams.

## WP-06 — One post-success maintenance phase

- **Task coverage:** `5.5`.
- **Deliverable:** project-aware backup, refresh, restore and checkout commands attach exactly one captured sequential auto-prune phase without changing primary success semantics.
- **depends_on:** `WP-03`.
- **Stage / topological level:** 4.
- **Owned responsibility scope:** one narrow private command composer and integration points in public backup/preparation/restore/checkout command construction, plus direct composition tests. Critical shared files are the four public command builders, the retention composer and focused nested-operation/output tests. CLI callbacks remain excluded.
- **Contract surface:** outermost-only attachment; pre-primary captured candidates; revalidation and input/output UUID exclusions; no maintenance on failure/cancellation/read-only/dry-run; structured warning after primary success; explicit prune remains failing.
- **DoD / evidence:** every eligible public entry attaches once, nested paths do not duplicate, skip cases are inert, exclusions survive retries, and maintenance failure preserves successful primary identity and exit semantics.
- **Parallel-safety rationale:** this is the only package allowed to edit integration points across predecessor-owned domains, and it begins after both COPY and retention contracts complete.

## WP-07 — CLI, documentation and integrated delivery gate

- **Task coverage:** `6.1`, `6.2`, `6.3`, `6.4`.
- **Deliverable:** complete SDK-first CLI/documentation projection and one integrated evidence set for two named sources, retained UUID COPY, ready launch and safe pruning.
- **depends_on:** `WP-04`, `WP-06`.
- **Stage / topological level:** 5.
- **Owned responsibility scope:** all remaining Click registration/callbacks/rendering, `PUBLIC_LEAF_CASES`, aliases/help/output/error fixtures, user/SDK documentation, real-Odoo/fake-boundary integration scenarios and final cross-domain repairs after predecessors finish. Critical shared files include CLI registration/callback modules, `commands/backup.py`, `commands/db.py`, `commands/env/`, canonical leaf/output tests, README/docs and integration fixtures.
- **Contract surface:** thin one-delegation leaves; stable Rich/JSON/TOON/error/confirmation/dry-run behavior; source/provenance fields; readiness option validation; retention confirmations/warnings; no credential arguments, orchestration layer or new output registry.
- **DoD / evidence:** two differently authenticated sources remain isolated; staging COPY and offline UUID reuse preserve lab/default; exact runtime readiness/stop/remove works; prune keeps protected/newest archives; plans/results/logs/child env contain no secrets. Focused suites, available current real-Odoo contract cases, Ruff check/format, strict mypy, architecture/leaf/schema gates, `git diff --check` and strict OpenSpec validation pass.
- **Parallel-safety rationale:** this join package starts only after all production contracts are frozen and exclusively owns shared CLI/integration files, so no sibling edits the same callbacks, inventory or final repair zones.

## Task coverage proof

| Work package | OpenSpec tasks covered exactly once |
| --- | --- |
| WP-01 | 1.1, 1.2, 1.3, 3.1, 5.1 |
| WP-02 | 1.4, 1.5, 2.1, 2.2, 2.3 |
| WP-03 | 3.2, 3.3, 3.4, 3.5 |
| WP-04 | 4.1, 4.2, 4.3 |
| WP-05 | 5.2, 5.3, 5.4 |
| WP-06 | 5.5 |
| WP-07 | 6.1, 6.2, 6.3, 6.4 |

Every checkbox in `tasks.md` is assigned once. The level-1 and level-2 frontiers each contain two independently executable packages with non-overlapping write zones; therefore the graph has real parallelism and does not collapse into a linear chain. Operational WIP is not encoded in the DAG.
