## Delivery Contract

- **Task key:** `MYL-521`
- **Change:** `add-project-remove-and-purge`
- **Approved base:** `origin/main` at `9cb436775307c2568c814ed128f07a6ea015867d`
- **Sizing source:** authoritative `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` custom properties on the root planning issue; numeric totals are intentionally not duplicated here
- **Estimate basis:** remaining work for one experienced developer familiar with Python, Click, SQLite/Alembic, PostgreSQL, Docker Compose, Git, and this repository, without AI acceleration; active development hours include investigation, implementation, required tests, review fixes, and attended verification while external queues and unavailable infrastructure are excluded
- **Estimate confidence:** medium and uncalibrated; the cross-provider scope and safety contract are inspectable, while destructive retry integration, legacy ownership states, Compose teardown, and live verification create material but bounded uncertainty
- **Delivery mode:** `dag`

The authoritative headline estimate property exceeds the multi-package threshold. The topology has two genuine parallel frontiers: catalogue/registration and public SDK contracts at stage 1, then runtime/environment and database/cluster cleanup at stage 4. Shared contracts and the coordinator precede both cleanup siblings; finalization follows them. Directly related tests, fixtures, snapshots, generated contracts, documentation, migration metadata, and service files belong to the owning package unless explicitly owned by an active sibling.

## Work-Package Task Coverage

Every OpenSpec task is owned exactly once:

| OpenSpec tasks | Work package |
|---|---|
| `1.1`, `1.2`, `1.3`, `3.2` | `WP-01-registration-foundation` |
| `2.1` | `WP-02-public-project-contract` |
| `2.2`, `2.3` | `WP-03-resource-projection` |
| `2.4`, `3.1`, `5.1` | `WP-04-coordinator-forget-cli` |
| `4.1`, `4.2` | `WP-05-runtime-environment-purge` |
| `4.3`, `4.4` | `WP-06-data-cluster-purge` |
| `4.5`, `4.6`, `5.2` | `WP-07-finalization-output` |
| `5.3`, `6.1`, `6.2`, `6.3` | `WP-08-verification-documentation` |

## Stage Map

Stages are contiguous topological layers and SHALL be materialized as the `stage` field on each WP child issue:

| WP | Stage |
|---|---:|
| `WP-01-registration-foundation` | 1 |
| `WP-02-public-project-contract` | 1 |
| `WP-03-resource-projection` | 2 |
| `WP-04-coordinator-forget-cli` | 3 |
| `WP-05-runtime-environment-purge` | 4 |
| `WP-06-data-cluster-purge` | 4 |
| `WP-07-finalization-output` | 5 |
| `WP-08-verification-documentation` | 6 |

## WP-01-registration-foundation

- **Stage:** 1
- **Depends on:** none
- **OpenSpec task coverage:** `1.1`, `1.2`, `1.3`, `3.2`
- **Deliverable:** one migrated project registration state, registered-only ordinary discovery/monitoring, full-id lifecycle lookup, and successful-init reactivation serialized on the existing project lifecycle lock.
- **Owned responsibility scope:** catalogue SQLAlchemy metadata and Alembic migration/equivalence; project catalogue accessors/transitions; init registration and lifecycle locking; monitor catalogue selection/cache invalidation; related catalogue/init/monitor/HTTP generated-contract tests and fixtures. Critical shared surfaces include `storage/catalog_schema.py`, `storage/catalog_migrations/`, `storage/catalog/backup.py`, `storage/catalog/environment.py`, `internal/project_init.py`, `project_init.py`, and monitor planning/collection modules.
- **Contract surface:** existing rows migrate to `registered`; only `registered` rows enter ordinary discovery; full-id lifecycle lookup sees both states; forget/reactivate state invariants are atomic and reuse existing row timestamps; successful init alone restores registered state; the canonical project lock orders init/removal; no dedicated forgotten timestamp is added.
- **DoD / evidence:** fresh and prior-head catalogues reach one metadata-equivalent head with all rows/relations preserved; downgrade refuses with forgotten rows; registered/forgotten transition matrices and concurrent init/removal pass; global/filtered snapshots hide forgotten rows, reactivation restores one original id, and schema/OpenAPI consumers remain compatible.
- **Parallel safety:** writes catalogue/init/monitor surfaces only. It shares semantic types with WP-02 through the OpenSpec contract, not source files; neither package imports the other's unfinished implementation.

## WP-02-public-project-contract

- **Stage:** 1
- **Depends on:** none
- **OpenSpec task coverage:** `2.1`
- **Deliverable:** frozen public selector/minimal-decision/result/error models, lazy exports, one minimal `ProjectResource`, and `OdooClient.projects` facade wiring with no operational coordinator yet.
- **Owned responsibility scope:** public project lifecycle models/errors/resource facade, package lazy-export maps, client facade wiring, import/public-method characterization and focused type/serialization tests. Critical shared surfaces include `client.py`, `__init__.py`, public model/error modules, and a new project resource entry point.
- **Contract surface:** exact path-or-full-id selector; public decisions expose only stable identity/type, action, reason and sanitized target while ownership/active-use/dependency/fingerprint evidence stays internal; frozen JSON-safe bounded values; `remove_command`/delegating `remove` signatures; no Click/Rich/executor callbacks or secret-bearing fields; lightweight import guarantees remain intact.
- **DoD / evidence:** public values round-trip through project conversion; lazy imports resolve and cache; characterized public methods change only as specified; import smoke and static typing pass; facade construction causes no catalogue/filesystem/process work.
- **Parallel safety:** owns public contract and facade files while WP-01 owns persistence/init/monitor files. Later packages consume the stabilized types without changing them.

## WP-03-resource-projection

- **Stage:** 2
- **Depends on:** `WP-01-registration-foundation`, `WP-02-public-project-contract`
- **OpenSpec task coverage:** `2.2`, `2.3`
- **Deliverable:** a deterministic, bounded, secret-free project-removal projection and fingerprint that maps every known resource to one typed action/reason without mutation.
- **Owned responsibility scope:** extensions to internal local resource inventory, project selection/resolution, ownership/exclusive-use classification, containment/Git-checkout classification, stable fingerprinting and projection tests. Critical shared surfaces include `internal/resource_inventory.py`, `commands/resource.py` only where projection reuse requires it, catalogue projection helpers, internal path/Git identity helpers, and new project-removal planning modules.
- **Contract surface:** one resource identity and action/reason per relationship; registered path and full-id selection; deduplicated shared resources; canonical no-symlink containment; stable fingerprint includes every fact required for execution-time drift detection; no reconciliation or adoption.
- **DoD / evidence:** matrices cover missing roots, unknown/full ids, cross-project/shared/borrowed resources, caller/linked/other-tool-managed checkouts, unknown files, symlink escape, database/cluster/backup identity, redaction, deterministic ordering and bounded truncation; inventory remains read-only.
- **Parallel safety:** depends on stabilized catalogue and public types. It owns projection/planning modules and does not write init/monitor or public facade files.

## WP-04-coordinator-forget-cli

- **Stage:** 3
- **Depends on:** `WP-03-resource-projection`
- **OpenSpec task coverage:** `2.4`, `3.1`, `5.1`
- **Deliverable:** the shared immutable coordinator skeleton, complete non-purge forget behavior, root CLI parsing/interactive token transport, canonical `remove` plus same-object `rm` alias, and sealed adapter interfaces for later purge packages.
- **Owned responsibility scope:** project `remove_command` coordinator and prepared action ledger; project-lock/fingerprint revalidation; forget blockers/transition/idempotence; root `commands/remove.py` registration with its Click `rm` alias and selector/format/confirmation parsing; coordinator/forget/CLI-focused tests. Critical shared surfaces include the new project resource implementation, root CLI registration, execution/action helpers used for composition, and catalogue transition APIs.
- **Contract surface:** captured children flatten into one `ExecutionPlan`; project lock precedes child locks; forget mutates registration only and refuses active blockers; `remove` and `rm` share one Click object/callback/options while stable machine command id remains `remove`; CLI passes one collected token into the already captured command; runtime/environment and data/cluster adapters have frozen input/output protocols and separate modules.
- **DoD / evidence:** plan inspection and execution share steps/fingerprint; every forget blocker and idempotent case is covered; no resource changes in forget/dry-run; CLI selector exclusivity, `remove`/`rm` help/completion/behavior parity and non-mutating parsing pass; `PUBLIC_LEAF_CASES` has only canonical `remove`; adapter contract tests allow WP-05 and WP-06 to proceed without editing coordinator files.
- **Parallel safety:** creates the common orchestration contract before stage-4 siblings. After this WP, the coordinator and root command are reserved from sibling writes; each sibling implements only its assigned adapter modules/tests.

## WP-05-runtime-environment-purge

- **Stage:** 4
- **Depends on:** `WP-04-coordinator-forget-cli`
- **OpenSpec task coverage:** `4.1`, `4.2`
- **Deliverable:** captured purge adapters for exact runtime stop, publication removal, and stable-order environment removal with existing cleanup/retry semantics preserved.
- **Owned responsibility scope:** project/environment runtime and publication cleanup adapters; environment command composition; related runtime/publication/environment fixtures and tests. Critical shared surfaces include new runtime/environment purge adapter modules plus existing instance planning, publication resource, and environment cleanup modules only for directly required reusable hooks.
- **Contract surface:** exact owner identities; stop then unpublish then environment removal; each environment keeps its captured plan, lock, ownership and postconditions; caller-owned code/shared inputs/borrowed backups remain retained; adapter returns typed consumed/retained/failure decisions to the fixed coordinator interface.
- **DoD / evidence:** live/stale/absent runtime and route matrices pass; environments execute by stable UUID; cleanup-failed and already-removed retries retain evidence; unrelated processes/routes/environments are unchanged; adapter contract and focused integration tests pass.
- **Parallel safety:** owns runtime/publication/environment adapter write zones. It does not edit coordinator, public types, database/cluster adapters, catalogue schema, or WP-06 tests; related fixtures use runtime/environment-specific namespaces.

## WP-06-data-cluster-purge

- **Stage:** 4
- **Depends on:** `WP-04-coordinator-forget-cli`
- **OpenSpec task coverage:** `4.3`, `4.4`
- **Deliverable:** captured purge adapters for provenance-authorized database/filestore and backup deletion plus exact unused SDK-owned Compose container/volume destruction.
- **Owned responsibility scope:** database/filestore/backup/cluster purge adapters; any narrow reusable cluster destroy command; ownership revalidation and postconditions; related PostgreSQL/Docker/backup fixtures and tests. Critical shared surfaces include new data/cluster adapter modules, guarded database drop and backup resources, Postgres cluster lifecycle/compose helpers, cluster catalogue claims, and resource-specific locks.
- **Contract surface:** latest exact lifecycle provenance authorizes database deletion; contained filestore follows database result; only exclusively owned unreferenced backups delete; cluster destruction requires active claim, labels, exclusive use and no retained dependency; Docker images/external clusters remain untouched; adapter returns typed decisions through WP-04's fixed interface.
- **DoD / evidence:** exact/missing/malformed/stale/foreign ownership, active bindings/sessions, borrowed/reference backup, already-absent retry, cluster label/reuse/drift and destroy postconditions pass; unrelated project DB/filestore/container/volume and all images remain unchanged.
- **Parallel safety:** owns database/backup/cluster adapter write zones and tests. It does not edit coordinator, runtime/environment adapters, public types, CLI, or WP-05 fixtures; shared point-operation changes remain confined to data/cluster modules.

## WP-07-finalization-output

- **Stage:** 5
- **Depends on:** `WP-05-runtime-environment-purge`, `WP-06-data-cluster-purge`
- **OpenSpec task coverage:** `4.5`, `4.6`, `5.2`
- **Deliverable:** contained filesystem/source finalization, exact confirmation gate, truthful partial/retry behavior, terminal forgotten transition, and complete bounded CLI/architecture inventory integration.
- **Owned responsibility scope:** filesystem/primary-source cleanup adapter; final coordinator integration of both sibling adapters; confirmation validation; structured partial errors/results; root removal Rich/JSON/TOON renderer and `PUBLIC_LEAF_CASES`/architecture/generated command matrix updates; cross-provider integration tests. Critical shared surfaces are the project coordinator/root command now released by WP-04, output envelope/rendering tests, architecture inventories, and generated command matrix.
- **Contract surface:** exact token precedes all mutation; source/path deletion is canonical and fail-closed; final forget is last; completed/retained/remaining decisions derive from one ledger; retry by full id reconstructs current state; `remove` is one canonical bounded SDK-first leaf, `rm` preserves that identifier, and `--yes` is insufficient.
- **DoD / evidence:** confirmation/TTY/machine matrices, dry-run parity, cross-adapter action order, injected failure after every phase, absent-source retry, repeat purge, path/symlink/unknown-file retention, bounded truncation/redaction, format equality, help/exit codes, public inventory and architecture gates all pass.
- **Parallel safety:** starts only after both adapter siblings complete. It is the sole package allowed to edit coordinator/CLI/output shared integration files at this stage, eliminating sibling merge conflicts.

## WP-08-verification-documentation

- **Stage:** 6
- **Depends on:** `WP-07-finalization-output`
- **OpenSpec task coverage:** `5.3`, `6.1`, `6.2`, `6.3`
- **Deliverable:** complete user documentation, consolidated safety/regression matrices, real-Odoo/Compose CI coverage, generated evidence metadata, and all repository acceptance gates green locally where prerequisites exist.
- **Owned responsibility scope:** user/project lifecycle documentation; remaining cross-cutting unit/integration/compatibility tests, including `tests/integration/test_project_removal.py` deterministic fail-after-action coverage using `odoo_instance_sdk.internal.proc.RecordingExecutor(result_factory=...)` for captured process steps and pytest `monkeypatch` of the specific guarded adapter callback for in-process action steps; real-Odoo test module/fixtures/command matrix/evidence metadata/workflow selection; generated API/client contract checks; directly related snapshots and test service files. It does not include Tester-owned GitLab scripts, runtime evidence, a production failure-injection seam, or a test-only CLI option.
- **Contract surface:** docs state exact behavior/exclusions; CI exercises public init → forget → re-init → purge and isolation; cleanup ledger proves no leaks; canonical leaf/evidence metadata names the new operation; no test-only ownership bypass enters production.
- **DoD / evidence:** focused and full test matrices cover every OpenSpec scenario; `pytest tests/integration/test_project_removal.py -k 'partial_failure or retry'` injects each deterministic failure through `RecordingExecutor.result_factory` or a named adapter monkeypatch, asserts the public result/ledger, and exposes neither mechanism through the CLI; migration/equivalence, format, lint, typing, offline, compatibility, architecture, generated-contract and available real-Odoo gates pass; missing opt-in infrastructure is reported honestly; strict OpenSpec validation passes; Tester can execute the E2E contract below without editing product CI.
- **Parallel safety:** final package begins after all implementation packages, so its cross-cutting test/doc write zones have no active sibling. Tester work remains in the separate GitLab repository and evidence channel.

## Estimation Evidence and Assumptions

- This revision re-estimates the complete remaining package from the approved source snapshot after a Ponytail full audit. It counts the existing `Command`/ledger boundary, catalogue migration harness, local resource inventory, environment cleanup, guarded database/backup operations, bounded output pipeline, alias characterization, and real-Odoo fixtures as reusable foundations rather than new subsystems.
- The audit removes the dedicated forgotten timestamp, keeps ownership/active-use/dependency evidence out of the minimal public decision model, and implements `rm` through the existing same-object Click alias mechanism. These choices avoid a separate migration API, duplicated public inventory graph, callback, renderer, SDK method, inventory row, test framework, or live E2E case.
- Evidence inspected: current `projects`/foreign-key schema and migrations; registration and monitor selection paths; immutable command/action model; project/environment runtime, publication and environment cleanup; guarded database/filestore and backup deletion; Postgres Compose identity; local resource inventory; public facade/lazy export and bounded output contracts; `PUBLIC_LEAF_CASES`; unit/integration/real-Odoo CI; and the Mac test repository/stand scripts at the recorded revision.
- The estimate includes design clarification already resolved in the artifacts, migration and compatibility work, implementation of all WP contracts, required tests/fixtures/generated metadata, documentation, normal review repair, and attended verification.
- It assumes the existing point-operation locks, runtime stop, publication removal, environment removal, guarded database drop, backup delete, resource inventory, process boundary, output pipeline and real-Odoo harness remain reusable rather than requiring replacement.
- It assumes ambiguous legacy ownership is retained, not adopted; no remote deletion, Docker image deletion, generic policy engine, parallel catalogue, purge journal, new API endpoint, or background worker is added.
- Main coupled uncertainty is cross-provider failure recovery: catalogue state, filesystem/source removal, PostgreSQL/filestore provenance, backup references and Compose exclusive-use must agree while retaining retry evidence. A contract change that permits force deletion, requires atomic rollback across providers, adds remote cleanup, or introduces automatic policy invalidates the estimate.
- Tests were not run to manufacture estimate evidence; source, tests, configuration, current artifacts and existing CI/E2E design were inspected instead.

## E2E Decision

`e2e_required=true`.

The required behavior is a destructive transaction spanning catalogue registration, monitor visibility, filesystem containment, runtime/publication cleanup, environment lifecycle, PostgreSQL/filestore provenance, backup references, and Docker container/volume identity. Focused unit/integration/contract tests can prove each guard and failure branch, but they cannot by themselves prove that one public CLI invocation deletes the complete disposable project while preserving a second project and shared/external resources on the real Mac/PostgreSQL/Docker boundary. A local live walkthrough is therefore required in addition to repository CI.

Planning does not run the tests. The test repository is the project-description repository `https://gitlab.icode.by/a.maksimchyk/binta-test-repo`, inspected at `97fba95d3e6ba56cde87bf4f478e85c3be47da26`. The reference checkout and persistent stand are read-only inputs and SHALL NOT be used as the purge target.

## Verification Matrix

| Case | OpenSpec requirement/scenario | Gap and local E2E decision | OpenAPI operation/schema | Local test script | Independent Tester steps | CI disposition | Assertions | Artifacts / images |
|---|---|---|---|---|---|---|---|---|
| `E01-forget-reinit` | `project-removal`: safe forget, active-resource refusal, repeated forget; `project-init`: successful/failed reactivation; `environment-monitor`: hidden/reactivated project | Live catalogue + monitor cache + preserved database/files require a real lifecycle; selected | None; monitor `GET /api/v1/snapshot` response remains schema v4 and must omit/reactivate the project without schema change | `tests/e2e/myl521_project_removal_e2e.py --case forget-reinit` | On a fresh isolated clone/catalogue: initialize, record hashes/DB identity, prove active refusal, stop/remove active blockers, forget, inspect CLI and monitor absence, re-init, inspect one original id and unchanged data | Implementer adds focused catalogue/monitor/init tests and a public-boundary node in `tests/integration/real_odoo/test_project_removal.py`; full real-Odoo tier | Forget changes only registration fields; project disappears; files/secrets/DB/cluster remain; repeated forget is inert; re-init restores one same id | Sanitized commands/exits, before/after catalogue projections and hashes in Multica; actual CLI text and PNG renders |
| `E02-confirm-dry-run` | `project-removal`: exact confirmation and immutable projection; `cli-odcli`: machine non-prompt behavior and `remove`/`rm` alias parity | TTY/non-TTY, alias routing and plan/execution parity are observable public-boundary behavior; selected as a focused live CLI case, no second stand or alias-only case | None | Same script, `--case confirm-dry-run` | Capture canonical/alias help and equivalent dry-runs in Rich/JSON/TOON; try absent, shortened, mismatched and `--yes`-only confirmation through both spellings; then inspect that no resource changed and machine `command` stays `remove` | Parametrized CLI/output/unit tests exhaust the callback/option/completion/error parity matrix; CI real-Odoo node checks one `rm` machine refusal and one canonical dry-run | Same Click command behavior; exact token shown; no prompt in machine modes; every refusal exits nonzero with zero mutations; dry-run actions equal later execution actions; no `rm` inventory row or machine id | Base/candidate root help, candidate `remove`/`rm` help, candidate dry-run/error text and corresponding rendered PNGs; JSON assertions reported in Multica only |
| `E03-isolated-purge` | `project-removal`: complete isolated purge, ordering, bounded result; `local-resource-lifecycle`: complete projection and exclusive ownership | Only a real disposable project can prove coordinated filesystem, PostgreSQL, Docker and catalogue postconditions; selected | Monitor snapshot only; no new API | Same script, `--case isolated-purge` | Build a dedicated project clone with own Compose project, database, filestore, backup, env, venv/log/temp and route; run exact purge; independently inspect absence in filesystem/psql/docker/catalogue/monitor | Implementer extends full real-Odoo/Compose CI and its cleanup ledger; not smoke because volume destruction and full init exceed smoke scope | Ordered plan consumed once; owned resources absent; project forgotten last; Docker image remains; repeat by full id is idempotent | Plan/result text, exit codes, psql/docker/catalogue assertions, cleanup ledger, candidate before/after summary PNGs in Multica |
| `E04-cross-project-isolation` | `project-removal`: other project/shared retention, unproven database/volume retention; projection deduplication | Cross-provider exclusive-use proof needs a live control project; selected and shares E03 setup | Monitor snapshot must retain the control project | Same script, `--case cross-project-isolation` | Create a second disposable control clone with distinct DB/port/catalogue relationships plus a deliberately shared/borrowed input; purge only target; inspect control SHA/files/DB/volume/snapshot and retained reasons | Focused ownership matrices plus the same full-tier real-Odoo node | Control project and shared/external/caller-owned resources are unchanged; no remote repo/image deletion; retained reasons are exact | Before/after control hashes and inventories, assertion summary, one sanitized retention table render attached in Multica |
| `E05-missing-source-recovery` | `project-removal`: missing-source full-id selection and recovery | A disposable source can be removed safely by the Tester-owned fixture setup before the product invocation; deterministic mid-command failure and retry stay in product integration tests, so the live case needs no unavailable failure-injection seam | None | Same script, `--case missing-source-recovery` | Let the harness initialize a disposable target, stop its owned runtime, remove only that fixture's captured source root during setup, then invoke `odcli-N remove --project-id <full-id> --purge --confirm-project <basename>:<full-id>` and inspect retained/deleted decisions through public output and read-only stores | Implementer-owned integration tests substitute the in-process executor/action callback to fail after each phase and cover drift, partial result, terminal-forget refusal, and retry; no production flag, environment switch, or public test seam is added | Full-id lookup succeeds with an absent source; the absent source is reported idempotently; eligible remaining resources are handled once; terminal state and bounded output are truthful | Sanitized setup provenance, public command/result text, catalogue/resource assertions, and revision provenance in Multica; no diagnostic JSON committed |

## Scenario Traceability

Every delta-spec scenario has exactly one primary verification disposition below. `E01`–`E05` refer to the live cases above. `Lower-level` means Implementer-owned product-repository coverage under tasks `6.1`/`6.2`; the stated reason explains why no additional local E2E case is warranted.

### `project-removal`

| Scenario | Primary disposition | Coverage or reasoned local-E2E exclusion |
|---|---|---|
| Path selector resolves canonical registration | `E01` | Live initialization supplies the canonical path and registration. |
| Missing source resolves by full id | `E05` | Public full-id purge is executed after fixture-owned source removal. |
| Invalid selector is inert | Lower-level | Pure selector/zero-mutation invariant is exhaustive and deterministic in unit/integration matrices. |
| Mixed ownership plan | `E04` | Target/control/shared inputs exercise delete/retain projection. |
| Dry-run and execution share a snapshot | `E02` | Public dry-run and later execution plans are compared; drift variants remain deterministic integration coverage. |
| Secrets stay absent | `E02` | Live bounded outputs are inspected; exhaustive field/redaction permutations stay in serializer tests. |
| Safe forget | `E01` | Live preserved bytes, rows, identities, and hidden registration are inspected. |
| Active resource blocks forget | `E01` | The public forget refusal is demonstrated before blockers are removed. |
| Repeated forget is idempotent | `E01` | The same full id is forgotten twice with unchanged resources. |
| Exact interactive confirmation | `E02` | Rich-mode exact-token collection is captured on the disposable fixture. |
| Generic or shortened confirmation is refused | `E02` | Absent, generic, shortened, and mismatched tokens are public negative cases. |
| Non-interactive confirmation is mandatory | `E02` | JSON/TOON invocations prove non-prompt refusal without the exact option. |
| Dry-run is inert | `E02` | Store/resource hashes before and after all dry-run formats prove no mutation. |
| Complete isolated purge | `E03` | Full disposable filesystem/PostgreSQL/Docker/catalogue postconditions are inspected. |
| Another project and shared resources are unchanged | `E04` | A live control project and shared input remain byte/identity stable. |
| Other-tool checkout is retained | `E04` | The disposable retention fixture includes an independently owned checkout. |
| Unproven database or volume is retained | `E04` | Ambiguous ownership is represented in the live control fixture and must retain. |
| Resource set changes after preview | Lower-level | A deterministic between-plan-and-lock mutation is required; live timing would be flaky and add no boundary evidence. |
| Concurrent init cannot race final forget | Lower-level | A barrier-controlled concurrency integration test proves lock ordering; a manual race cannot prove the invariant. |
| Failure before terminal forget | Lower-level | Task `6.1` uses `RecordingExecutor.result_factory` or a named guarded-adapter monkeypatch after each phase; no public failure hook is permitted. |
| Retry after missing source | `E05` + lower-level | E05 proves public absent-source recovery; deterministic fail-then-retry ledger semantics use the in-process test double. |
| Repeated successful purge | `E03` | The second public full-id purge is asserted idempotent after the live purge. |
| Machine partial failure | Lower-level | CLI integration invokes the real renderer with the injected structured failure; faulting a live persistent service is unsafe and less deterministic. |
| Bounded large plan | Lower-level | Synthetic high-cardinality projection tests can prove exact truncation limits without provisioning wasteful live resources. |

### `local-resource-lifecycle`

| Scenario | Primary disposition | Coverage or reasoned local-E2E exclusion |
|---|---|---|
| Complete project projection | `E03` | The isolated fixture instantiates every supported resource class. |
| Shared identity is deduplicated | `E04` | One shared input is referenced by target and control and appears once. |
| Planning does not repair evidence | Lower-level | Read-only malformed/stale evidence fixtures prove zero catalogue/filesystem writes deterministically. |
| Symlink escape is retained | `E04` | A disposable contained symlink targets outside the declared root and must retain without touching its target. |
| Borrowed or referenced backup is retained | `E04` | The live shared fixture includes a referenced/borrowed backup. |
| Exact owned Compose volume is deletable | `E03` | Captured labels and post-purge Docker inspection prove exact volume deletion. |

### `project-init`

| Scenario | Primary disposition | Coverage or reasoned local-E2E exclusion |
|---|---|---|
| Successful re-init restores visibility | `E01` | Public re-init restores the same id and monitor visibility. |
| Failed re-init remains forgotten | Lower-level | Deterministic init-postcondition failure is injected in integration; breaking a live init adds no contract evidence. |
| Dry-run does not reactivate | `E01` | A dry-run init is issued while forgotten before the successful re-init. |

### `environment-monitor`

| Scenario | Primary disposition | Coverage or reasoned local-E2E exclusion |
|---|---|---|
| Forgotten project disappears from snapshot | `E01` | Live global and project-filtered snapshots omit the forgotten row. |
| Retained ownership does not imply visibility | `E01` | Preserved database/files remain while the project is absent from the snapshot. |
| Reactivation restores one project | `E01` | Re-init yields one entry with the original id and no duplicate. |

### `cli-odcli`

| Scenario | Primary disposition | Coverage or reasoned local-E2E exclusion |
|---|---|---|
| Help shows full command surface | `E02` | Base/candidate root and remove help are captured and rendered. |
| Short remove alias preserves one command | `E02` | Canonical/alias help, completion, dry-run and refusal paths are compared; product contract tests exhaust success/failure formats and prove one inventory row. |
| JSON envelope | `E02` | Live remove dry-run/result is parsed as the versioned envelope. |
| JSON alias preserves envelope v1 | `E02` | The alias and canonical format are compared on the same dry-run. |
| TOON is semantically equal to JSON | `E02` | Decoded TOON and JSON graphs are compared for the same plan. |
| Conflicting alias is usage error | Lower-level | Click parser matrix proves the deterministic pre-domain usage error. |
| Machine diagnostics stay on stderr | Lower-level | Subprocess integration can assert exact stdout/stderr separation across failures. |
| Structured failure details preserve the failure variant | Lower-level | Renderer integration consumes each typed failure injected by the task `6.1` test mechanisms. |
| Machine mutation requires explicit confirmation | `E02` | Public machine calls without an exact token refuse without prompting. |
| Explicit machine mutation executes | `E03` | The isolated purge uses explicit machine confirmation and executes. |
| Machine remove requires exact project confirmation | `E02` | Exact/absent/mismatched machine tokens are exercised. |
| Explicit machine project purge executes | `E03` | The public machine purge completes on the disposable target. |
| Secrets redacted | `E02` | Live outputs are scanned; serializer tests own exhaustive secret-shaped values. |
| Diagnostic machine formats share one result graph | Lower-level | One injected typed diagnostic is rendered through both serializers and compared exactly. |
| Native command dry-run supports every bounded format | `E02` | Rich, JSON, JSON alias, and TOON dry-runs use the same fixture. |
| Normal native command stays raw | Lower-level | Existing native-command characterization plus focused regression tests prove unchanged raw mode. |
| Normal native command rejects machine options | Lower-level | Parser-only negative cases require no live stand and precede domain execution. |
| Canonical bounded inventory remains single-source | Lower-level | Architecture/generated-contract tests compare the authoritative inventory mechanically. |
| Mutating lifecycle dry-runs are canonical leaves | Lower-level | Generated leaf metadata and command-matrix tests cover all commands without replaying unrelated live mutations. |
| Database drop is a canonical bounded leaf | Lower-level | Existing database command architecture/metadata tests verify this unchanged leaf. |
| Selector exclusivity | `E02` | Public CLI supplies both selectors and proves a pre-mutation usage error. |
| Forget output | `E01` | Rich and machine forget results are captured around monitor inspection. |
| Rich purge confirmation | `E02` | Actual Rich prompt/token behavior is captured with stated TTY limitations. |

## Local Script and Stand Contract

The Tester-owned GitLab MR SHALL add `tests/e2e/myl521_project_removal_e2e.py` plus narrow offline self-tests in `tests/test_myl521_project_removal_e2e.py`. The runner is an assertion harness, not a second implementation. It SHALL exercise `rm` only as an alternate spelling of canonical `remove`, never model it as a second operation. It SHALL accept all environment-specific values as explicit arguments: baseline/candidate product checkouts and SHAs, test-repository checkout and SHA, dedicated candidate and baseline `odcli-N` executables, numbered slot, separate target/control checkout roots, isolated `ODCLI_USER_ROOT` values, Odoo/Python/psql paths, Compose image and approved digest, distinct Compose project names, database names, PostgreSQL/HTTP ports, artifact directory, locale, terminal width, and the bundled `openspec-e2e-testing/scripts/render_terminal.swift` path.

Setup SHALL use fresh clones of the test repository and disposable catalogue roots; it SHALL refuse dirty or revision-mismatched inputs, occupied names/ports, the reference checkout, the persistent source database/volume, or any path outside the declared disposable roots. Each case records baseline/candidate/test revision provenance and exact public commands. Failure-safe cleanup removes only resources whose captured disposable identities match, but preserves the GitLab evidence MR/branch/commits.

The GitLab MR contains only the reusable test scripts, self-tests, fixtures and ordinary code changes. If the tested CLI itself creates a meaningful Git commit, that commit is preserved unchanged and linked separately; otherwise no artificial second commit is required. Runtime JSON, logs, images, hashes, SQL/Docker output, commands, exit codes, provenance, and assertion results SHALL NOT be committed.

## Independent Walkthrough and Image Contract

The independent Tester SHALL perform a fresh manual run distinct from invoking the automated script. It SHALL use its own disposable clone, database, Compose project, catalogue root, ports, and dedicated `odcli-N`; it SHALL not use the reference checkout or any other Tester's resources. Manual actions use only the public CLI/SDK plus read-only `psql`, Docker, filesystem, catalogue/monitor, and Git inspections.

Visible output changes require actual UTF-8 capture on macOS with fixed locale and 120-column width. The Tester SHALL capture:

- approved-base and candidate `odcli --help` plus candidate `odcli remove --help` / `odcli rm --help` parity;
- candidate purge dry-run delete/retain plan;
- candidate missing/mismatched confirmation failures;
- candidate forget result and before/after monitor/project listing;
- candidate completed purge and retained-resource summary.

Baseline and candidate help captures use equivalent clean fixtures. Lifecycle before/after captures use the same candidate fixture at the named state and SHALL NOT be mislabelled as cross-revision comparisons. Render each captured text file to PNG with the bundled `openspec-e2e-testing/scripts/render_terminal.swift`; label it as a text render, preserve the tested output language, and record revision, locale and width. Redirection does not prove TTY-only prompt styling, ANSI, spinner, progress, or interactive cursor behavior; those limitations SHALL be stated rather than inferred.

## CI and Ownership

- **Implementer ownership:** every product-repository task in `tasks.md`, including migration/equivalence tests, unit/CLI/integration matrices, `tests/integration/real_odoo/test_project_removal.py`, fixtures, command-matrix/E2E metadata, and workflow selection required for the full-tier scenario. Tester SHALL not edit these GitHub CI files.
- **Tester ownership:** the GitLab E2E MR scripts/self-tests/fixtures, isolated execution, independent walkthrough, real captures, evidence publication, and findings. Product defects are reported; the Tester does not repair product code.
- Existing CI jobs remain `make lint`, `make types`, `make test`, compatibility, migration/architecture/generated-contract gates, and the real-Odoo full tier. No new test framework is introduced.

## Evidence Publication and Cleanup

The Tester's final Multica comment SHALL link the product PR/commits/files and the GitLab test MR/commit/files; list tested product/test SHAs, commands and exit codes; summarize assertions and cleanup; and attach every required PNG plus any compact sanitized text/log evidence. The Tester SHALL read the posted comment back and verify links and attachment metadata before declaring verification complete.

Cleanup deletes only the disposable stand (catalogue roots, target/control clones, test databases/filestores, routes, environments, Compose containers/volumes and temporary artifacts) after captured-identity checks. It SHALL preserve the evidence MR, branch, commits and linked product history. It SHALL not close/merge the MR, merge the product PR, delete branches, delete Docker images, or touch the reference checkout/persistent stand without a separate instruction.
