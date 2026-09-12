## Delivery Contract

- Task key: `MYL-136`
- Change: `developer-workflow-cli-contracts`
- Approved base: `origin/main@0ff164636617c03a51277055af45cef009277368`
- Delivery branch: `feat/odcli-developer-workflow`
- `graph_revision`: `MYL-136-GR3`
- Source snapshots: GH#34 `2026-09-09T15:08:24Z`; GH#43 `2026-09-05T16:11:43Z`; GH#54 `2026-09-03T13:13:24Z`; GH#62 `2026-09-10T15:57:28Z`; GH#64 `2026-09-10T15:47:27Z`; GH#65 `2026-09-10T09:25:34Z`.
- Revision rule: any source-body change, base-SHA change, product-scope change, contract dependency change, owned-scope change, or DAG edge change requires a revised OpenSpec and an incremented `graph_revision` before implementation. Stage/assignee/status movement does not.

## DAG

```text
WP-01 ─┬─> WP-03 ─┐
       ├─> WP-04 ─┴─> WP-06 ─> WP-07 ─┐
       └─> WP-05 ──────────────────────┴─> WP-08
WP-02 ─────> WP-04
```

Direct edge set: `WP-01→WP-03`, `WP-01→WP-04`, `WP-01→WP-05`, `WP-02→WP-04`, `WP-03→WP-06`, `WP-04→WP-06`, `WP-06→WP-07`, `WP-05→WP-08`, `WP-07→WP-08`.

Stages/topological levels: level 0 = WP-01 and WP-02; level 1 = WP-03, WP-04, and WP-05; level 2 = WP-06; level 3 = WP-07; level 4 = WP-08. Operational WIP limits are not encoded in the DAG.

## WP-01 — Shared CLI, Plan, and Configuration Foundations

- Stage / level: Foundation / 0
- Tasks covered exactly once: `1.1`–`1.6`
- Direct `depends_on`: none
- Deliverable: stable shared format/projection, Rich-plan, Ticket Allocation, remediation, optional-tool, config, and command-registration contracts ready for domain consumers.
- Owned mutable scope: `src/odoo_instance_sdk/commands/output.py`, `src/odoo_instance_sdk/commands/context.py`, the Ticket Allocation block in `src/odoo_instance_sdk/commands/env.py`, shared registration/extraction in `src/odoo_instance_sdk/cli.py`, `src/odoo_instance_sdk/config.py`, `src/odoo_instance_sdk/project.py`, new `src/odoo_instance_sdk/internal/executables.py`, remediation foundations in `src/odoo_instance_sdk/internal/doctor.py`, newly extracted `src/odoo_instance_sdk/commands/module.py` and `src/odoo_instance_sdk/commands/translations.py`, `tests/fixtures/architecture_inventory.py`, `tests/unit/resources/test_odcli_lifecycle.py`, `tests/unit/test_cli_output_modes.py`, `tests/unit/test_cli_boundary_contract.py`, `tests/unit/test_architecture_inventory.py`, removal of `tests/unit/test_cli_jira_checkout.py`, replacement `tests/unit/test_cli_ticket_checkout.py`, and new `tests/unit/test_project_config_git.py`.
- Contract surface: `output_options`, `resolve_output_mode`, schema-derived recursive envelope projector with no manual command/field registry, `_rich_plan_projection`, `PUBLIC_LEAF_CASES`, the full existing Ticket Allocation behavior and neutral provenance, project manifest parsing/serialization for Git settings, remediation/executable models, and module/translation command registration hooks whose callbacks live in the extracted modules.
- DoD / evidence: focused output/projection/plan/config tests pass; every eligible leaf derives nested accepted fields from its concrete typed result and rejects unknown paths before execution; `--json` and any manual field registry are absent; machine parity and redaction hold; the renamed Ticket Allocation regression suite retains all checkout/allocation/lifecycle cases while emitting only neutral identifiers; architecture inventory and lifecycle expectations reflect the real module/translation extraction; extracted commands retain existing behavior; no domain features are implemented here.
- Parallel safety: independent of WP-02 because it does not mutate path providers, catalogue schema, resource files, or storage tests. It is the sole owner of shared CLI/output seams, preventing level-1 siblings from conflicting there.

## WP-02 — Unified Storage and Backup Ownership

- Stage / level: Foundation / 0
- Tasks covered exactly once: `2.1`–`2.4`
- Direct `depends_on`: none
- Deliverable: one journaled `~/.odcli/` migration and direct project-owned backup schema/query contract.
- Owned mutable scope: `src/odoo_instance_sdk/internal/paths.py`, new `src/odoo_instance_sdk/internal/storage_migration.py`, `src/odoo_instance_sdk/storage/backup_catalog.py`, the download-ownership path in `src/odoo_instance_sdk/resources/database.py`, `src/odoo_instance_sdk/internal/pgadmin.py`, `src/odoo_instance_sdk/internal/pgadmin_files.py`, `tests/unit/storage/test_catalog_migration.py`, `tests/unit/storage/test_backup_catalog.py`, `tests/unit/test_catalog_runtime_record.py`, and new `tests/unit/test_storage_migration.py` and `tests/unit/test_backup_project_ownership.py`.
- Contract surface: global path provider, migration journal/lock, sequential catalogue v15-to-v16 ownership migration with preserved foreign-key relations, `start_download(project_id=...)`, and direct project-scoped/global backup queries that keep ambiguous legacy ownership null.
- DoD / evidence: fresh/full-legacy/interrupted/conflict migration fixtures pass; a populated v15 catalogue migrates to v16 with the same backup UUIDs, event/restore/environment relations, modes, and deterministic ownership; two-project provenance stays unowned, excluded from both project scopes, and visible globally until explicit safe relink; local/remote download, cross-project, global, and unowned cases pass; repository-local `.odcli/` stays untouched.
- Parallel safety: disjoint from WP-01's shared CLI/config files. All later storage consumers depend directly or indirectly on this package.

## WP-03 — Module Developer Workflow

- Stage / level: Domain A / 1
- Tasks covered exactly once: `3.1`–`3.5`
- Direct `depends_on`: `WP-01`
- Deliverable: complete public/CLI module discovery, dependency planning, changed update, dry-run visibility, and conflict classification.
- Owned mutable scope: new `src/odoo_instance_sdk/resources/module.py`, module models in `src/odoo_instance_sdk/models.py`, module attachment in `src/odoo_instance_sdk/resources/instance.py`, module exports in `src/odoo_instance_sdk/resources/__init__.py` and `src/odoo_instance_sdk/__init__.py`, `src/odoo_instance_sdk/commands/module.py` after WP-01 extraction, and new `tests/unit/test_module_resource.py`, `tests/unit/test_cli_module_workflow.py`, and `tests/integration/test_module_update.py`.
- Contract surface: `OdooInstance.modules`, `ModuleResource`, frozen module/selection models, module command group, existing changed-selector and exclusive-update adapters.
- DoD / evidence: safe parsing, precedence/shadow/missing/cycle, both owner contexts, stale/no-op/not-installed, exact Rich/machine plan, integration update, and conflict-classification tests pass.
- Parallel safety: WP-03 is the sole level-1 owner of `models.py`, `resources/instance.py`, and public resource/package exports. WP-04 and WP-05 own none of those paths. WP-06 and WP-07 are serialized successors before they may extend the same seams.

## WP-04 — Environment Inspection and CLI Presentation

- Stage / level: Domain B / 1
- Tasks covered exactly once: `4.1`–`4.6`
- Direct `depends_on`: `WP-01`, `WP-02`
- Deliverable: focused environment/runtime inspection, adaptive/contentful Rich leaves, canonical aliases, and repaired generated Compose config.
- Owned mutable scope: `src/odoo_instance_sdk/resources/monitor.py`, `src/odoo_instance_sdk/internal/doctor.py`, `src/odoo_instance_sdk/internal/generated_config.py`, `src/odoo_instance_sdk/commands/env.py` excluding the WP-01 Ticket Allocation block, the validation renderer in `src/odoo_instance_sdk/commands/backup.py`, init/alias code in `src/odoo_instance_sdk/cli.py` only through WP-01 hooks, `tests/unit/test_cli_env_list_grouping.py`, `tests/unit/test_monitor_snapshot.py`, `tests/unit/test_cli_aliases.py`, `tests/unit/test_cli_init.py`, `tests/unit/test_cli_init_postgres.py`, and new `tests/unit/test_cli_env_show.py`, `tests/unit/test_doctor_runtime.py`, and `tests/unit/test_cli_rich_presentation.py`.
- Contract surface: one-snapshot selector, runtime diagnosis/remediations, Rich env/backup views, Click alias metadata, generated-config reconciliation.
- DoD / evidence: one-snapshot/no-start tests; project/environment diagnosis; real-leaf 80/120/180 audit; absolute machine paths; all alias/help/completion parity; missing/stale/current/dry-run init tests.
- Parallel safety: shared output/CLI hooks were frozen by WP-01 and paths by WP-02. Its exact paths do not overlap WP-03 or WP-05; WP-07's later doctor extension is serialized through WP-06.

## WP-05 — Translation Validation

- Stage / level: Domain C / 1
- Tasks covered exactly once: `5.1`–`5.3`
- Direct `depends_on`: `WP-01`
- Deliverable: optional captured `msgfmt` validation with atomic export safety and typed output parity.
- Owned mutable scope: `src/odoo_instance_sdk/commands/translations.py`, new translation result types colocated in that module, and new `tests/unit/test_translation_msgfmt.py` and `tests/unit/test_translation_architecture.py`.
- Contract surface: translation export plan/result and the shared optional-executable/process-step contracts consumed read-only.
- DoD / evidence: available/unavailable/success/warning/failure/dry-run cases pass; destination preservation and stdin/environment capture proven; package metadata unchanged.
- Parallel safety: translation files and dedicated tests are disjoint from WP-03 and WP-04 and from the later WP-07; shared architecture rules are already owned by WP-01.

## WP-06 — Stopped-Project Restore Lifecycle

- Stage / level: Lifecycle / 2
- Tasks covered exactly once: `6.1`–`6.3`
- Direct `depends_on`: `WP-03`, `WP-04`
- Deliverable: safe restore of a stopped project through a bounded owned auxiliary Database Manager with preserved recovery guarantees.
- Owned mutable scope: restore portions of `src/odoo_instance_sdk/resources/database.py`, helper-runtime additions in `src/odoo_instance_sdk/resources/instance.py`, the restore adapter in `src/odoo_instance_sdk/commands/db.py`, new `tests/unit/test_stopped_project_restore.py`, and `tests/integration/test_database_lifecycle.py`.
- Contract surface: existing restore command/compensation pipeline, project runtime binding, process ownership/readiness/cleanup, exact recovery diagnostics.
- DoD / evidence: stopped-project integration passes; foreign listener/startup/preflight failures mutate nothing unsafe; filestore/database/default postconditions, cleanup, retained retry, and compensation pass.
- Parallel safety: WP-04 already carries WP-02 transitively, so no direct WP-02 edge remains. Waiting for both WP-03 and WP-04 freezes module/public-export and runtime-diagnostic seams before WP-06 alone extends `resources/instance.py`; no level-2 sibling writes exist.

## WP-07 — Odoo Git Workflow

- Stage / level: Domain D / 3
- Tasks covered exactly once: `7.1`–`7.5`
- Direct `depends_on`: `WP-06`
- Deliverable: public and CLI commit/check/absorb/sync workflow with exact message and safe same-branch publication contracts.
- Owned mutable scope: new `src/odoo_instance_sdk/resources/git.py`, new `src/odoo_instance_sdk/commands/git.py`, Git models in `src/odoo_instance_sdk/models.py`, Git attachment in `src/odoo_instance_sdk/resources/instance.py`, Git exports in `src/odoo_instance_sdk/resources/__init__.py` and `src/odoo_instance_sdk/__init__.py`, the `git-absorb` capability extension in `src/odoo_instance_sdk/internal/doctor.py`, and new `tests/unit/test_git_resource.py`, `tests/unit/test_cli_git.py`, `tests/unit/test_git_architecture.py`, and `tests/integration/test_git_sync.py`.
- Contract surface: `OdooInstance.git`, frozen Git results/commands, project Git config consumed read-only, shared process/output/optional-tool/Ticket contracts, Git executable.
- DoD / evidence: tag/scope/ticket/message matrix, malformed history, missing/late-installed absorb, unmapped hunks, new/fast-forward/exact-lease/stale-lease/conflict/dirty/protected/upstream/HTTPS cases pass without live forge access.
- Parallel safety: WP-07 is serialized after WP-06, which already follows WP-03 and WP-04. It can therefore reuse WP-03's module mapping and extend `models.py`, `resources/instance.py`, public exports, and WP-04's doctor path without concurrent writes; WP-05 is path-disjoint.

## WP-08 — Documentation and Verification

- Stage / level: Integration / 4
- Tasks covered exactly once: `8.1`–`8.3`
- Direct `depends_on`: `WP-05`, `WP-07`
- Deliverable: integrated, documented, fully verified implementation on the exact planned lineage.
- Owned mutable scope: `README.md`, `CHANGELOG.md`, `docs/python-sdk.md`, and `tests/unit/test_documentation_contract.py`; verification produces command evidence without mutating other paths, and any integration defect is returned to the predecessor that owns its file.
- Contract surface: all six source contracts, repository architecture inventories, build/test/type/lint/docs/codegen/package gates.
- DoD / evidence: all commands in task 8.2 exit `0`; trace audit maps every delta requirement to passing tests and every task to one WP; branch descends from the approved SHA; worktree is clean; no prohibited abstraction/dependency/integration is present.
- Parallel safety: terminal integration package; starts only after every product deliverable and performs no concurrent feature writes.

## Coverage Audit

`tasks.md` contains 35 tasks: WP-01 covers 1.1–1.6 (6), WP-02 covers 2.1–2.4 (4), WP-03 covers 3.1–3.5 (5), WP-04 covers 4.1–4.6 (6), WP-05 covers 5.1–5.3 (3), WP-06 covers 6.1–6.3 (3), WP-07 covers 7.1–7.5 (5), and WP-08 covers 8.1–8.3 (3). Total coverage is 35 tasks, with no duplicate or uncovered task IDs.

## Requirement and scenario trace map

This closeout map is the review evidence index for all 32 delta requirements and 43 scenarios. Each row points to the passing public/unit/integration contract suite; the focused R1 regressions are included in the referenced suites.

| ID | Requirement | Passing test evidence |
|---|---|---|
| R01 | Nested process plans remain visible | `tests/unit/test_cli_output_modes.py`, `tests/unit/test_cli_module_workflow.py` |
| R02 | Git operations use the shared execution boundary | `tests/unit/test_git_architecture.py`, `tests/unit/test_cli_git.py` |
| R03 | Frozen Odoo commit context and message | `tests/unit/test_git_resource.py` |
| R04 | Commit history validation | `tests/unit/test_git_resource.py` |
| R05 | Optional external absorb adapter | `tests/unit/test_git_resource.py` |
| R06 | Safe same-branch synchronization | `tests/integration/test_git_sync.py` |
| R07 | Project ownership captured at download start | `tests/unit/test_backup_project_ownership.py`, `tests/unit/test_catalog_runtime_record.py` |
| R08 | Effective runtime diagnosis | `tests/unit/test_doctor_runtime.py`, `tests/unit/test_monitor_snapshot.py` |
| R09 | Generated Compose config is independently idempotent | `tests/unit/test_cli_init.py`, `tests/unit/test_cli_init_postgres.py` |
| R10 | Git commit settings | `tests/unit/project/test_project_config_git.py` |
| R11 | pgAdmin follows unified storage migration | `tests/unit/test_storage_migration.py`, `tests/unit/internal/test_pgadmin.py` |
| R12 | Focused selection from one snapshot | `tests/unit/test_cli_env_show.py`, `tests/unit/test_monitor_snapshot.py` |
| R13 | Direct project ownership of backups | `tests/unit/test_backup_project_ownership.py`, `tests/unit/storage/test_backup_catalog.py` |
| R14 | Unified catalogue migration | `tests/unit/storage/test_catalog_migration.py`, `tests/unit/test_catalog_runtime_record.py` |
| R15 | Single format selector and typed field projection | `tests/unit/test_cli_output_modes.py`, `tests/unit/test_cli_boundary_contract.py` |
| R16 | Structured doctor remediations | `tests/unit/test_doctor_runtime.py`, `tests/unit/test_cli_boundary_contract.py` |
| R17 | Focused environment details command | `tests/unit/test_cli_env_show.py` |
| R18 | Human-oriented bounded presentation | `tests/unit/test_cli_rich_presentation.py`, `tests/unit/test_cli_output_modes.py` |
| R19 | Canonical short resource commands | `tests/unit/test_cli_aliases.py` |
| R20 | Tracker-neutral Ticket Allocation CLI | `tests/unit/test_cli_ticket_checkout.py`, `tests/unit/resources/test_odcli_lifecycle.py` |
| R21 | Optional msgfmt validation step | `tests/unit/test_translation_msgfmt.py` |
| R22 | Validation result and publication safety | `tests/unit/test_translation_msgfmt.py` |
| R23 | Tracker-neutral ticket allocation | `tests/unit/test_cli_ticket_checkout.py` |
| R24 | Unified environment storage location | `tests/unit/test_storage_migration.py`, `tests/unit/resources/test_odcli_lifecycle.py` |
| R25 | Single global user-storage root | `tests/unit/test_storage_migration.py` |
| R26 | Locked conflict-safe storage migration | `tests/unit/test_storage_migration.py` |
| R27 | Restore while project Odoo is stopped | `tests/unit/test_stopped_project_restore.py`, `tests/integration/test_database_lifecycle.py` |
| R28 | Manifest-backed module catalogue | `tests/unit/test_module_resource.py` |
| R29 | Deterministic dependency plan | `tests/unit/test_module_resource.py` |
| R30 | Changed-module update selection | `tests/unit/test_cli_module_workflow.py`, `tests/integration/test_module_update.py` |
| R31 | Concurrent module operation classification | `tests/unit/test_cli_module_workflow.py` |
| R32 | Optional host tools are not package dependencies | `tests/unit/test_translation_architecture.py`, `tests/unit/test_git_architecture.py` |

| Scenario ID | Scenario | Passing test evidence |
|---|---|---|
| S01 | Module update preview contains process steps | `tests/unit/test_cli_module_workflow.py` |
| S02 | Inspect Git plan | `tests/unit/test_cli_git.py` |
| S03 | Build a configured ticket commit | `tests/unit/test_git_resource.py` |
| S04 | Infer prefix deterministically | `tests/unit/test_git_resource.py` |
| S05 | Validate feature history | `tests/unit/test_git_resource.py` |
| S06 | Absorb executable appears after installation | `tests/unit/test_git_resource.py` |
| S07 | Publish rebased feature branch | `tests/integration/test_git_sync.py` |
| S08 | Rebase conflicts | `tests/integration/test_git_sync.py` |
| S09 | Start project download | `tests/unit/test_backup_project_ownership.py` |
| S10 | Diagnose project without environment | `tests/unit/test_doctor_runtime.py` |
| S11 | Repair missing generated config | `tests/unit/test_cli_init.py` |
| S12 | Initialize Git commit policy | `tests/unit/project/test_project_config_git.py` |
| S13 | Migrate pgAdmin state | `tests/unit/test_storage_migration.py` |
| S14 | Select explicit environment | `tests/unit/test_cli_env_show.py` |
| S15 | Downloaded backup is immediately project-visible | `tests/unit/test_backup_project_ownership.py` |
| S16 | Ambiguous legacy ownership stays unowned | `tests/unit/test_backup_project_ownership.py` |
| S17 | Migrate existing catalogue | `tests/unit/storage/test_catalog_migration.py` |
| S18 | Project repeated fields | `tests/unit/test_cli_output_modes.py` |
| S19 | Reject invalid projection | `tests/unit/test_cli_output_modes.py` |
| S20 | Typed schema is the field authority | `tests/unit/test_cli_output_modes.py` |
| S21 | Known dependency drift remediation | `tests/unit/test_doctor_runtime.py` |
| S22 | Show stopped current environment | `tests/unit/test_cli_env_show.py` |
| S23 | Presentation contract audits real leaves | `tests/unit/test_cli_rich_presentation.py` |
| S24 | Alias help and behavior parity | `tests/unit/test_cli_aliases.py` |
| S25 | Allocate a repeated ticket | `tests/unit/test_cli_ticket_checkout.py` |
| S26 | Validator is available | `tests/unit/test_translation_msgfmt.py` |
| S27 | Validator is unavailable | `tests/unit/test_translation_msgfmt.py` |
| S28 | Validation succeeds with statistics | `tests/unit/test_translation_msgfmt.py` |
| S29 | Validation fails | `tests/unit/test_translation_msgfmt.py` |
| S30 | Allocate without tracker integration | `tests/unit/test_cli_ticket_checkout.py` |
| S31 | Create environment after migration | `tests/unit/resources/test_odcli_lifecycle.py` |
| S32 | Fresh installation | `tests/unit/test_storage_migration.py` |
| S33 | Conflicting destination | `tests/unit/test_storage_migration.py` |
| S34 | Retry interrupted migration | `tests/unit/test_storage_migration.py` |
| S35 | Restore valid ZIP on a free project port | `tests/unit/test_stopped_project_restore.py` |
| S36 | Listener is unrelated | `tests/unit/test_stopped_project_restore.py` |
| S37 | Retry retained artifact | `tests/unit/test_stopped_project_restore.py` |
| S38 | Resolve module information safely | `tests/unit/test_module_resource.py` |
| S39 | Plan transitive installation | `tests/unit/test_module_resource.py` |
| S40 | Reject incomplete graph | `tests/unit/test_module_resource.py` |
| S41 | Preview changed update | `tests/integration/test_module_update.py` |
| S42 | Another module operation is active | `tests/unit/test_cli_module_workflow.py` |
| S43 | Build package metadata | `tests/unit/test_translation_architecture.py`, `tests/unit/test_git_architecture.py` |
