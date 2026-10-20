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
- **Contract surface:** existing rows migrate to `registered`; only `registered` rows enter ordinary discovery; full-id lifecycle lookup sees both states; forget/reactivate state/timestamp invariants are atomic; successful init alone clears forgotten state; the canonical project lock orders init/removal.
- **DoD / evidence:** fresh and prior-head catalogues reach one metadata-equivalent head with all rows/relations preserved; downgrade refuses with forgotten rows; registered/forgotten transition matrices and concurrent init/removal pass; global/filtered snapshots hide forgotten rows, reactivation restores one original id, and schema/OpenAPI consumers remain compatible.
- **Parallel safety:** writes catalogue/init/monitor surfaces only. It shares semantic types with WP-02 through the OpenSpec contract, not source files; neither package imports the other's unfinished implementation.

## WP-02-public-project-contract

- **Stage:** 1
- **Depends on:** none
- **OpenSpec task coverage:** `2.1`
- **Deliverable:** frozen public selector/decision/result/error models, lazy exports, one minimal `ProjectResource`, and `OdooClient.projects` facade wiring with no operational coordinator yet.
- **Owned responsibility scope:** public project lifecycle models/errors/resource facade, package lazy-export maps, client facade wiring, import/public-method characterization and focused type/serialization tests. Critical shared surfaces include `client.py`, `__init__.py`, public model/error modules, and a new project resource entry point.
- **Contract surface:** exact path-or-full-id selector; frozen JSON-safe bounded values; `remove_command`/delegating `remove` signatures; no Click/Rich/executor callbacks or secret-bearing fields; lightweight import guarantees remain intact.
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
- **Depends on:** `WP-01-registration-foundation`, `WP-02-public-project-contract`, `WP-03-resource-projection`
- **OpenSpec task coverage:** `2.4`, `3.1`, `5.1`
- **Deliverable:** the shared immutable coordinator skeleton, complete non-purge forget behavior, root CLI parsing/interactive token transport, and sealed adapter interfaces for later purge packages.
- **Owned responsibility scope:** project `remove_command` coordinator and prepared action ledger; project-lock/fingerprint revalidation; forget blockers/transition/idempotence; root `commands/remove.py` registration and selector/format/confirmation parsing; coordinator/forget/CLI-focused tests. Critical shared surfaces include the new project resource implementation, root CLI registration, execution/action helpers used for composition, and catalogue transition APIs.
- **Contract surface:** captured children flatten into one `ExecutionPlan`; project lock precedes child locks; forget mutates registration only and refuses active blockers; CLI passes one collected token into the already captured command; runtime/environment and data/cluster adapters have frozen input/output protocols and separate modules.
- **DoD / evidence:** plan inspection and execution share steps/fingerprint; every forget blocker and idempotent case is covered; no resource changes in forget/dry-run; CLI selector exclusivity and non-mutating parsing pass; adapter contract tests allow WP-05 and WP-06 to proceed without editing coordinator files.
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
- **Contract surface:** exact token precedes all mutation; source/path deletion is canonical and fail-closed; final forget is last; completed/retained/remaining decisions derive from one ledger; retry by full id reconstructs current state; `remove` is one canonical bounded SDK-first leaf and `--yes` is insufficient.
- **DoD / evidence:** confirmation/TTY/machine matrices, dry-run parity, cross-adapter action order, injected failure after every phase, absent-source retry, repeat purge, path/symlink/unknown-file retention, bounded truncation/redaction, format equality, help/exit codes, public inventory and architecture gates all pass.
- **Parallel safety:** starts only after both adapter siblings complete. It is the sole package allowed to edit coordinator/CLI/output shared integration files at this stage, eliminating sibling merge conflicts.

## WP-08-verification-documentation

- **Stage:** 6
- **Depends on:** `WP-07-finalization-output`
- **OpenSpec task coverage:** `5.3`, `6.1`, `6.2`, `6.3`
- **Deliverable:** complete user documentation, consolidated safety/regression matrices, real-Odoo/Compose CI coverage, generated evidence metadata, and all repository acceptance gates green locally where prerequisites exist.
- **Owned responsibility scope:** user/project lifecycle documentation; remaining cross-cutting unit/integration/compatibility tests; real-Odoo test module/fixtures/command matrix/evidence metadata/workflow selection; generated API/client contract checks; directly related snapshots and test service files. It does not include Tester-owned GitLab scripts or runtime evidence.
- **Contract surface:** docs state exact behavior/exclusions; CI exercises public init → forget → re-init → purge and isolation; cleanup ledger proves no leaks; canonical leaf/evidence metadata names the new operation; no test-only ownership bypass enters production.
- **DoD / evidence:** focused and full test matrices cover every OpenSpec scenario; migration/equivalence, format, lint, typing, offline, compatibility, architecture, generated-contract and available real-Odoo gates pass; missing opt-in infrastructure is reported honestly; strict OpenSpec validation passes; Tester can execute the E2E contract below without editing product CI.
- **Parallel safety:** final package begins after all implementation packages, so its cross-cutting test/doc write zones have no active sibling. Tester work remains in the separate GitLab repository and evidence channel.

## Estimation Evidence and Assumptions

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
| `E02-confirm-dry-run` | `project-removal`: exact confirmation and immutable projection; `cli-odcli`: machine non-prompt behavior | TTY/non-TTY and plan/execution parity are observable public-boundary behavior; selected as a focused live CLI case, no second stand | None | Same script, `--case confirm-dry-run` | Capture dry-run in Rich/JSON/TOON; try absent, shortened, mismatched and `--yes`-only confirmation; then inspect that no resource changed | Parametrized CLI/output/unit tests are sufficient for exhaustive negative matrix; CI real-Odoo node checks one machine refusal and one dry-run | Exact token shown; no prompt in machine modes; every refusal exits nonzero with zero mutations; dry-run actions equal later execution actions | Base/candidate help and candidate dry-run/error text; corresponding rendered PNGs; JSON assertions reported in Multica only |
| `E03-isolated-purge` | `project-removal`: complete isolated purge, ordering, bounded result; `local-resource-lifecycle`: complete projection and exclusive ownership | Only a real disposable project can prove coordinated filesystem, PostgreSQL, Docker and catalogue postconditions; selected | Monitor snapshot only; no new API | Same script, `--case isolated-purge` | Build a dedicated project clone with own Compose project, database, filestore, backup, env, venv/log/temp and route; run exact purge; independently inspect absence in filesystem/psql/docker/catalogue/monitor | Implementer extends full real-Odoo/Compose CI and its cleanup ledger; not smoke because volume destruction and full init exceed smoke scope | Ordered plan consumed once; owned resources absent; project forgotten last; Docker image remains; repeat by full id is idempotent | Plan/result text, exit codes, psql/docker/catalogue assertions, cleanup ledger, candidate before/after summary PNGs in Multica |
| `E04-cross-project-isolation` | `project-removal`: other project/shared retention, unproven database/volume retention; projection deduplication | Cross-provider exclusive-use proof needs a live control project; selected and shares E03 setup | Monitor snapshot must retain the control project | Same script, `--case cross-project-isolation` | Create a second disposable control clone with distinct DB/port/catalogue relationships plus a deliberately shared/borrowed input; purge only target; inspect control SHA/files/DB/volume/snapshot and retained reasons | Focused ownership matrices plus the same full-tier real-Odoo node | Control project and shared/external/caller-owned resources are unchanged; no remote repo/image deletion; retained reasons are exact | Before/after control hashes and inventories, assertion summary, one sanitized retention table render attached in Multica |
| `E05-partial-retry` | `project-removal`: drift refusal, partial failure, missing-source retry | Deterministic injected failures belong in integration tests; one manual filesystem-loss retry closes the real missing-root gap without destructive fault injection into persistent services | None | Same script, `--case missing-source-retry` | On a new fixture, make source deletion the last completed filesystem action, inject a test-owned catalogue finalization failure through the documented test seam, retry by full id, and inspect truthful completed/remaining state | Implementer owns exhaustive failure injection, resource-drift and retry tests; CI full node checks one fail-after-mutation retry | First run stays registered and reports failure; retry resolves absent source by full id, does not repeat unrelated effects, and finishes once | Sanitized failure/result text, action-ledger assertions and revision provenance in Multica; no diagnostic JSON committed |

## Local Script and Stand Contract

The Tester-owned GitLab MR SHALL add `tests/e2e/myl521_project_removal_e2e.py` plus narrow offline self-tests in `tests/test_myl521_project_removal_e2e.py`. The runner is an assertion harness, not a second implementation. It SHALL accept all environment-specific values as explicit arguments: baseline/candidate product checkouts and SHAs, test-repository checkout and SHA, dedicated candidate and baseline `odcli-N` executables, numbered slot, separate target/control checkout roots, isolated `ODCLI_USER_ROOT` values, Odoo/Python/psql paths, Compose image and approved digest, distinct Compose project names, database names, PostgreSQL/HTTP ports, artifact directory, locale, terminal width, and the bundled `openspec-e2e-testing/scripts/render_terminal.swift` path.

Setup SHALL use fresh clones of the test repository and disposable catalogue roots; it SHALL refuse dirty or revision-mismatched inputs, occupied names/ports, the reference checkout, the persistent source database/volume, or any path outside the declared disposable roots. Each case records baseline/candidate/test revision provenance and exact public commands. Failure-safe cleanup removes only resources whose captured disposable identities match, but preserves the GitLab evidence MR/branch/commits.

The GitLab MR contains only the reusable test scripts, self-tests, fixtures and ordinary code changes. If the tested CLI itself creates a meaningful Git commit, that commit is preserved unchanged and linked separately; otherwise no artificial second commit is required. Runtime JSON, logs, images, hashes, SQL/Docker output, commands, exit codes, provenance, and assertion results SHALL NOT be committed.

## Independent Walkthrough and Image Contract

The independent Tester SHALL perform a fresh manual run distinct from invoking the automated script. It SHALL use its own disposable clone, database, Compose project, catalogue root, ports, and dedicated `odcli-N`; it SHALL not use the reference checkout or any other Tester's resources. Manual actions use only the public CLI/SDK plus read-only `psql`, Docker, filesystem, catalogue/monitor, and Git inspections.

Visible output changes require actual UTF-8 capture on macOS with fixed locale and 120-column width. The Tester SHALL capture:

- approved-base and candidate `odcli --help` plus `odcli remove --help` behavior;
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
