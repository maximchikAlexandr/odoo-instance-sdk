## Delivery contract

- `change_id=add-typed-machine-operation-contracts`
- `base_sha=ab71895f7031eabe129fbd5dbfcda71298cd4016`
- `delivery_mode=dag`
- `estimate_source=planning issue properties: Estimate, hours; Estimate min, hours; Estimate max, hours`
- `estimate_basis=remaining active developer effort for one experienced developer familiar with Python, Click, msgspec, SQLite/Alembic and the repository; tests were not run to estimate`
- `estimate_confidence=medium; source and tests are inspectable, but complete request DTO migration, provider compatibility and cross-process contract fixtures carry bounded integration uncertainty`
- `calibration=uncalibrated; no comparable completed-run timing history was supplied`
- `launch_policy=normal squad flow; WHEN the authoritative Estimate, hours property is at or below 32 THEN human_review SHALL be approved and the implementation parent MAY start automatically in todo; WHEN it is above 32 THEN human_review SHALL remain pending and the implementation parent SHALL remain backlog`
- `current_launch_outcome=the authoritative Estimate, hours property is above 32; human_review=pending; implementation parent remains backlog`

The authoritative `Estimate, hours` property is above the multi-WP topology threshold. The graph has a real parallel frontier at stage 2: local invocation/session, installed-provider delivery tooling and monitor snapshot work share only the completed stage-1 contracts and own non-conflicting write zones.

## Dependency graph

Direct edges only:

```text
WP-01 -> WP-02
WP-01 -> WP-03
WP-01 -> WP-04
WP-02 -> WP-05
WP-05 -> WP-06
WP-03 -> WP-07
WP-04 -> WP-07
WP-06 -> WP-07
```

Stage mapping:

```text
WP-01-operation-contract-foundation -> 1
WP-02-local-invoke-and-approval -> 2
WP-03-provider-contract -> 2
WP-04-selective-monitor-snapshots -> 2
WP-05-explicit-database-reconciliation -> 3
WP-06-structured-replacement-recovery -> 4
WP-07-integrated-contract-verification -> 5
```

Stages are consecutive topological layers. They describe dependency readiness, not WIP policy. Stage 2 is the validated parallel frontier; its siblings have distinct implementation ownership.

## WP-01 — Operation contract foundation

- `wp_id=WP-01-operation-contract-foundation`
- `stage=1`
- `depends_on=[]`
- `task_coverage=1.1,1.2,1.3,1.4,1.5,1.6,2.1,2.2,2.3,2.4,2.6,5.1,5.3`

Deliverable: one production-owned canonical operation inventory and validated contract bundle foundation that preserves all current CLI behavior, binds aliases to stable IDs, derives actual wire schemas and defines the provider seam consumed by later WPs.

Owned responsibility scope:

- Frozen operation descriptors, binding/transport/error vocabulary, contract versioning and production `PUBLIC_LEAF_CASES`.
- Click-tree/SDK-primitive validation, deterministic registry and schema bundle construction.
- Frozen finite request DTOs, envelope/result/error schema references and compatibility fixtures.
- Metadata-only `contract export` command and provider protocol/registration seam, excluding provider loading.
- Characterization tests, fixtures, packaging exports and directly related documentation/support files.
- Critical shared files: new `src/odoo_instance_sdk/operations/` contract/registry/schema modules, CLI composition metadata, `internal/output_fields.py`, model exports and the canonical CLI contract tests.

Contract surface:

- Existing Click paths, aliases, envelope-v1 bytes, formats, exit codes and native transports remain unchanged.
- One stable ID maps to one implementation/request/result/error contract.
- Schema names and aliases come from runtime msgspec projection; no handwritten command or field list exists.
- Provider bindings plug into a fixed validated seam without importing provider domain code in core.

Definition of done and evidence:

- Current leaf/alias tree and representative wire fixtures are characterized before authority moves.
- Missing/stale/duplicate/incomplete bindings and SDK drift fail deterministic tests.
- Repeated export is byte-stable and performs no domain I/O.
- Existing CLI output and installed-package characterization suites pass unchanged except for importing the production inventory.

Parallel safety: this predecessor owns every shared registry/schema/protocol contract before siblings start. Later WPs consume its stable APIs and SHALL NOT edit its critical contract/registry modules except through a planning revision.

## WP-02 — Local invoke and same-snapshot approval

- `wp_id=WP-02-local-invoke-and-approval`
- `stage=2`
- `depends_on=[WP-01-operation-contract-foundation]`
- `task_coverage=3.1,3.2,3.3,3.4,3.5,3.6,4.1,4.2,4.3,4.4,4.5`

Deliverable: finite operations execute locally by stable ID through one captured context, while previewable mutations retain one private `Command` across a bounded JSONL approval session.

Owned responsibility scope:

- Invocation-scoped operation context over existing CLI/runtime resolution and single catalogue ownership.
- Local lookup/invoke command, finite document adapter, transport rejection and eligible built-in request adapters.
- JSONL session protocol, one-Command retention, decision timeout/cancellation, StepObserver projection and cleanup.
- Directly related CLI/SDK/session/redaction tests, fixtures and documentation.
- Critical shared files: new operation runtime/session modules, `commands/context.py`, the local invoke Click adapter, `commands/output.py` integration points and `execution.py` only where the existing `Command` lifecycle requires a minimal hook.

Contract surface:

- Local invoke never constructs remote dispatch, daemon or RPC clients.
- Public plans remain non-executable; private callbacks/snapshots are never serialized.
- Existing envelope, sanitizer and exit mapping remain authoritative for finite documents.
- Native/interactive/live/streaming commands retain direct friendly CLI behavior.

Definition of done and evidence:

- Explicit selectors and one catalogue/context capture are proven with spies.
- Approved matching fingerprint executes the original object once; mismatch, EOF, timeout, cancel and interrupt execute zero times and clean exact owned work.
- Domain-negative results remain successful transport results.
- Representative existing finite leaves have CLI/stable-ID parity and no prompt/ANSI/stdout pollution.

Parallel safety: owns runtime/context/session and local-invoke adapters only. It consumes WP-01 registry APIs, does not edit provider loader/tooling owned by WP-03 or monitor collectors owned by WP-04.

## WP-03 — Installed providers and neutral consumer boundary

- `wp_id=WP-03-provider-contract`
- `stage=2`
- `depends_on=[WP-01-operation-contract-foundation]`
- `task_coverage=2.5,5.2,5.4,5.5`

Deliverable: selected-interpreter entry-point providers are discovered once under bounded validation, and the authoritative exported bundle remains a neutral boundary for external consumer type generation without becoming a runtime dependency.

Owned responsibility scope:

- Provider entry-point loader, deadline/error isolation and deterministic provider ordering.
- Fixture plugin package and packaging/discovery compatibility tests.
- External consumer generation is intentionally outside repository runtime dependencies; the repository publishes only the authoritative schema bundle.
- Negative provider/version/conflict/absence/slow-load tests and directly related docs.
- Critical shared files: provider-loader module beneath the WP-01 seam and fixture plugin metadata.

Contract surface:

- One entry-point group and one supported operation-contract version.
- Provider descriptor/DTO/factory stay colocated; core has no provider-domain import list.
- External consumers consume exported JSON Schema only; Python installation and operation discovery never invoke consumer tooling.
- No install/update/remove/hot-reload/sandbox lifecycle is introduced.

Definition of done and evidence:

- A fixture provider appears in discovery/export and invokes through the fixed registry seam.
- Missing, incompatible, failing, slow and conflicting providers produce bounded sanitized outcomes before operation execution.
- Exported request/result/error/alias schemas remain available for external generators.
- Core wheel metadata contains no consumer-toolchain or plugin-manager dependency.

Parallel safety: owns provider loading and fixture package. WP-01 owns shared registry/schema definitions; WP-02 owns runtime invocation; WP-04 owns monitor paths, so sibling write zones do not overlap.

## WP-04 — Selective truthful monitor snapshots

- `wp_id=WP-04-selective-monitor-snapshots`
- `stage=2`
- `depends_on=[WP-01-operation-contract-foundation]`
- `task_coverage=6.1,6.2,6.3,6.4,6.5,6.6`

Deliverable: snapshot v3 provides selective finite/batch collection, explicit observation completeness/freshness and honest raw CPU samples without a global cache or second monitor graph.

Owned responsibility scope:

- Snapshot request/observation DTOs and additive v3 model/API/CLI fixtures.
- Monitor planning, section filtering, batch observation boundary and cache behavior.
- Process raw CPU identity/counter/sample fields and compatible prior-sample calculation.
- Existing Git/storage/artifact/Docker/PostgreSQL collector coordination and directly related tests/docs.
- Critical shared files: `resources/monitor/`, `internal/process_metrics.py`, monitor/public model files, process inventory projection and snapshot API/client fixtures.

Contract surface:

- Existing v2 fields keep their meaning; v3 additions distinguish unrequested, incomplete and unknown.
- Unselected sections start no expensive work.
- CPU percentage needs two compatible samples; raw counters remain available to one-shot callers.
- Darwin/non-Darwin memory semantics, PID ownership/dedup and production Docker collection remain unchanged.

Definition of done and evidence:

- No-call spies cover every unselected expensive section.
- Batch uses one catalogue selection pass and shared UTC boundary.
- First sample, compatible previous sample and PID-reuse tests prove CPU semantics.
- SDK, CLI, API and existing Rich/watch fixtures pass with explicit v3 updates.

Parallel safety: owns monitor/process/snapshot files only and consumes WP-01 descriptors for schema export. It does not edit local invocation/session or provider tooling siblings.

## WP-05 — Explicit database reconciliation

- `wp_id=WP-05-explicit-database-reconciliation`
- `stage=3`
- `depends_on=[WP-02-local-invoke-and-approval]`
- `task_coverage=7.1,7.2,7.3,7.4,7.5,7.6`

Deliverable: database list/exists/current and all monitoring/inventory/backup reads are inert, while one typed previewable reconciler retains required idempotent dropped-event behavior at explicit mutation boundaries.

Owned responsibility scope:

- Database observation DTOs and Odoo/psql evidence projection.
- `DatabaseResource` list/exists/current read paths and commands.
- Explicit reconcile command, transaction/lock/revalidation and operation binding.
- Startup/registration/destructive/repair call-site migration and backup-read characterization.
- Directly related database, catalogue-event, CLI/SDK and polling tests/docs.
- Critical shared files: `resources/database/backup_restore_parts/queries.py`, database public/resource models, required lifecycle call sites and catalogue dropped-event adapters.

Contract surface:

- Existing database names/backups and fallback availability behavior remain typed.
- Reads append no events and perform no backfill.
- Reconciliation requires exact proven absence and revalidates before one idempotent event.
- Required startup/destructive audit behavior is preserved through an explicit call.

Definition of done and evidence:

- An inventory enumerates and migrates every current implicit-write owner.
- Stale, reappeared, inconclusive, foreign and mismatched observations mutate nothing.
- Repeated list/exists/current/monitor/inventory/backup polls leave catalogue bytes/events unchanged.
- Approved explicit reconciliation records the same required audit outcome once.

Parallel safety: runs after WP-02 because it exposes a previewable operation through that transport. It owns database observation/reconciliation paths; no sibling is active in those paths, and WP-06 follows it before touching adjacent catalogue recovery files.

## WP-06 — Structured replacement recovery

- `wp_id=WP-06-structured-replacement-recovery`
- `stage=4`
- `depends_on=[WP-05-explicit-database-reconciliation]`
- `task_coverage=8.1,8.2,8.3,8.4,8.5,8.6`

Deliverable: DB replacement recovery evidence is versioned structured catalogue data, atomically maintained and consumed by the existing guarded compensation flow; new execution no longer parses human diagnostics.

Owned responsibility scope:

- Recovery DTO/codec, environment catalogue field and single Alembic migration lineage.
- DB replacement failure persistence, retry/repair validation, compensation/publication clear semantics and events.
- Explicit known-legacy adoption path and fail-closed malformed/contradictory handling.
- Catalogue migration, interruption/retry/rollback, security/redaction and directly related tests/docs.
- Critical shared files: `internal/dbreplace/`, `storage/catalog/environment.py`, catalogue schema/Alembic versions, environment models and migration fixtures.

Contract surface:

- `last_error` remains bounded human text and is not new executable input.
- Recovery identities are secret-free, versioned and exact; existing locks, journals, ownership checks, archive/checksum/permission gates, rollback and postconditions remain authoritative.
- Successful recovery clears only current structured evidence and preserves history.
- One catalogue and one Alembic head remain.

Definition of done and evidence:

- Failure state, diagnostic, recovery and event commit atomically.
- Retry/repair refuses stale or contradictory environment/backup/cluster/database/filestore evidence.
- Known legacy rows adopt only at explicit repair; unknown text mutates nothing.
- Current, legacy and retry migration fixtures plus interruption/compensation tests pass.

Parallel safety: follows WP-05 to avoid overlapping catalogue/database write zones and to consume the completed explicit-operation/session contract. It has no active sibling modifying DB replacement or catalogue environment schema.

## WP-07 — Integrated contract verification and delivery

- `wp_id=WP-07-integrated-contract-verification`
- `stage=5`
- `depends_on=[WP-03-provider-contract,WP-04-selective-monitor-snapshots,WP-06-structured-replacement-recovery]`
- `task_coverage=9.1,9.2,9.3,9.4,9.5,9.6,9.7,9.8,9.9`

Deliverable: one integrated, documented, buildable change proves the contract across bounded read, previewable mutation and native/streaming transport while preserving standalone SDK/CLI and every safety boundary.

Owned responsibility scope:

- Cross-subsystem installed-package E2E contract tests and source-boundary architecture checks.
- Final SDK/CLI/plugin/schema/snapshot/reconcile/recovery/versioning documentation.
- Focused and full repository gates, schema export checks, packaging/build verification and final diff cleanup.
- Directly related shared fixtures/snapshots/docs/support files after fan-in.
- Critical shared files: integration/contract tests, README/docs and packaging metadata only if needed for verified delivery.

Contract surface:

- One inventory and executor, no production test imports, no language-specific domain list, no daemon/RPC/telemetry/plugin lifecycle framework.
- Bounded output is schema-valid, prompt-free and redacted; native/interactive transport remains native.
- SDK remains directly callable without a daemon; installing a Python provider requires no consumer-generator rebuild.
- All process, Expression, ownership, filesystem, archive, database, lock, rollback and postcondition gates remain covered.

Definition of done and evidence:

- Representative installed-package matrix covers schema alias/defaults, domain-negative result, same-snapshot approval, cancellation and privacy.
- Focused operation/schema/session/plugin/monitor/database/catalogue/security/packaging suites pass.
- Formatting, lint, typing, Alembic/schema, architecture/contract, `make pr`, build and schema export gates pass, with external prerequisite skips recorded separately.
- Final diff contains no generated consumer fixture, generated visual HTML or out-of-scope daemon/telemetry/domain-feature work.

Parallel safety: final fan-in only. All predecessors are complete before it may update cross-cutting tests, docs, packaging or generated fixtures, so no sibling write conflict remains.

## E2E test plan

Resolve `TEST_REPO_URL` only from the current project description. Never place its value, hostname, credentials, ports or personal paths in public Git. Local execution is macOS arm64 with CPython 3.12. E01 uses a separately validated numbered interpreter/launcher pair for installed-package proof. E02 and E03 each allocate a different fresh numbered identity through the repository-owned `numbered_tool.py`; they use that identity's already-linked checkout, environment, database and port and release all of them only through canonical numbered removal. Never commit unsanitized create/inspect records. The Linux amd64 Odoo resolution lock belongs only to the Ubuntu `real-odoo-tier` GitHub Actions job; it SHALL NOT be passed to local numbered creation or treated as local E2E evidence.

| Case | Requirement and public boundary | Local script and entry command | Independent agent check | CI disposition | Assertions and artifact destinations |
|---|---|---|---|---|---|
| E01 | Installed provider plus finite, session and native boundaries. OpenAPI: N/A; the public boundary is CLI plus exported JSON Schema. | `e2e/MYL-432/test_operation_boundaries.py`; `"$ODCLI_PYTHON" "$TEST_WORKTREE/e2e/MYL-432/test_operation_boundaries.py" --odcli "$ODCLI_BIN" --project "$TEST_WORKTREE" --artifacts "$ARTIFACT_ROOT/E01"`. | I01 below: a fresh agent runs specified public CLI calls, validates schema/result/privacy fields, and inspects H0/H1/H2 without invoking the automatic script. | **Already covered** by job `package`, `tests/packaging/test_installed_operation_contract.py`; negative session/provider gates are in job `offline-tests`. | Byte-stable export; aliases and required/default metadata; `ok=true` domain-negative result; installed provider; clean bounded output and redaction; native rejection; same-snapshot approval; cancellation. GitLab retains the script, provider and necessary non-evidence fixtures; the deterministic contract export may be added in the optional result commit because JSON generation is itself the tested function. Multica retains both raw exports, health/domain-negative/native outputs, session transcripts, H0/H1/H2, hashes, commands, exit codes and assertions. |
| E02 | Selective, honest snapshot v3. OpenAPI operation `getMonitorSnapshot`; response schemas `Snapshot`, `SnapshotObservation`, `SnapshotSectionObservation`; section selection is the SDK request boundary. | `e2e/MYL-432/prepare_numbered_case.py` followed by `test_snapshot_v3.py`; `"$ODCLI_PYTHON" "$TEST_WORKTREE/e2e/MYL-432/test_snapshot_v3.py" --odcli "$ODCLI_BIN" --project "$PROJECT" --environment "$ENV_ID" --artifacts "$ARTIFACT_ROOT/E02" --retain-for-agent`. | I02 below: a fresh agent uses `env show` and a direct SDK one-liner, then inspects process identity; it never imports or calls the script. | **Sufficient lower-level check** in job `offline-tests`, `tests/unit/test_monitor_snapshot_v3.py`; OpenAPI projection is checked by job `dashboard-tests`, `tests/unit/test_openapi_export.py`. | Requested/completed/unknown sets, one UTC boundary, no unselected probes, unknown unavailable values, honest first CPU sample and redaction. GitLab retains the producer, test script and necessary fixtures. Multica retains private case inputs, create/inspect records, minimal/expanded/agent snapshots, process identity, logs, commands, exit codes and assertions; none of those diagnostic JSON files enters the MR. |
| E03 | Observational database reads and explicit reconciliation. OpenAPI: N/A; the public boundary is SDK resource/command. | `e2e/MYL-432/prepare_numbered_case.py` followed by `test_database_reconciliation.py --prepare-only`; `"$ODCLI_PYTHON" "$TEST_WORKTREE/e2e/MYL-432/test_database_reconciliation.py" --odcli "$ODCLI_BIN" --project "$PROJECT" --environment "$ENV_ID" --artifacts "$ARTIFACT_ROOT/E03" --prepare-only`. | I03 below: a fresh agent uses public `db list`, performs the one public SDK reconciliation between independent before/after inspections, and queries test-owned PostgreSQL/catalogue state; it never imports or calls the script. | **Sufficient lower-level check** in job `offline-tests`, `tests/unit/resources/test_database_resource.py`. | Repeated reads are byte/event-inert; exact plan target; execution revalidation; one `dropped` event; idempotent repeat. GitLab retains the producer, test script and necessary fixtures. Multica retains private case inputs, create/inspect/checkpoint records, before/plan/first/second/after diagnostics, logs, commands, exit codes and assertions; none of those diagnostic JSON files enters the MR. |

### Requirement and scenario traceability

Paths below are relative to `openspec/changes/archive/2026-10-10-add-typed-machine-operation-contracts/specs/`. Every listed scenario is routed to one E2E case or to an exact sufficient `offline-tests`/`package` check; no changed scenario is silently excluded.

| Spec requirement | Scenarios | Route |
|---|---|---|
| `machine-operation-contracts/spec.md` — Stable versioned operation identity | Alias preserves identity; Duplicate identity fails closed | E01 export asserts one ID for aliases; `offline-tests`: `tests/unit/test_operation_contracts.py::test_builtin_inventory_is_complete_and_aliases_share_identity`, `::test_registry_rejects_duplicate_ids_paths_and_incomplete_provider_bindings`. |
| Same file — Single production inventory | New unclassified leaf is rejected; Stale inventory entry is rejected | Sufficient lower-level: `offline-tests`, `tests/unit/test_operation_contracts.py::test_composed_click_tree_rejects_unknown_leaves_and_alias_drift` and `tests/unit/test_integrated_contract_boundaries.py::test_production_inventory_is_single_and_tests_use_separate_vectors`. |
| Same file — Complete operation descriptor | Descriptor covers a bounded mutation; Domain failure stays in result schema | E01 asserts `remote.add` descriptor transport/approval/errors and invokes `binta_test.domain_negative`, requiring envelope `ok=true` plus typed negative `status`; `package`: `tests/packaging/test_installed_operation_contract.py::test_installed_package_invokes_sdk_operation`. |
| Same file — Wire schemas follow actual DTO projection | Aliased field is exported; Schema and encoded fixture agree | E01 asserts `DepsMissingImport` exports `import` but not `import_name`, and validates provider/default/null/tagged fixtures; `offline-tests`: `tests/unit/test_operation_contracts.py::test_schema_and_runtime_projection_use_wire_aliases`, `::test_nested_tagged_default_null_and_alias_wire_fixture_is_real_json`. |
| Same file — Deterministic contract bundle and consumer generation | Repeated export is byte-stable; External types come from exported bundle | E01 compares raw export bytes and confirms no generator invocation; `offline-tests`: `tests/unit/test_operation_contracts.py::test_contract_export_is_byte_stable_and_includes_schema_policy`. |
| Same file — Local invocation is distinct from dispatch | Local invoke has no coordinator recursion; Native operation requires its transport | E01 invokes installed finite provider and rejects `odcli.run` with `transport_required` and no child; `offline-tests`: `tests/unit/test_operation_invoke_session.py::test_local_invoke_decodes_typed_request_and_preserves_private_context`. |
| Same file — One captured operation context | Explicit selector stays authoritative; Context does not replace revalidation | E01 supplies an explicit project and verifies captured provenance; execution-time drift remains a sufficient lower-level gate in `offline-tests`: `tests/unit/test_operation_contracts.py::test_init_adapter_preserves_explicit_project_path` and `tests/unit/test_operation_invoke_session.py::test_approval_session_rejects_stale_decision_without_execution`. |
| Same file — Bounded local machine output | Finite read is one document; Output limit is enforced | E01 parses exactly one prompt/ANSI-free envelope; limit overflow is sufficient lower-level coverage in `offline-tests`: `tests/unit/test_operation_invoke_session.py::test_session_limits_always_emit_typed_terminal_bounded_output_error`. |
| Same file — Installed Python operation providers | Installed provider becomes discoverable; Missing requested plugin is bounded; Incompatible provider fails closed | E01 installs and invokes its provider in `ODCLI_PYTHON`; missing/incompatible cases are sufficient lower-level coverage in `offline-tests`: `tests/unit/test_operation_provider_loader.py::test_incompatible_provider_is_reported_without_calling_operation_factory`, `tests/unit/test_operation_invoke_session.py::test_cli_provider_discovery_failure_is_bounded_for_invoke_and_session`. |
| Same file — No plugin lifecycle framework | Installed set is stable for one process | Sufficient lower-level: `offline-tests`, `tests/unit/test_operation_provider_loader.py::test_default_discovery_is_cached_once` and `tests/unit/test_integrated_contract_boundaries.py::test_production_has_no_test_imports_or_operation_lifecycle_framework`. |
| Same file — Representative end-to-end contract proof | Representative matrix passes | E01 plus E02/E03 assert alias, required/default flags, domain-negative semantics, privacy/redaction, bounded output, snapshot parity, cancellation and native preservation; `package` adds installed-package read/session/native proof. |
| `cli-odcli/spec.md` — CLI paths bind to stable operation IDs | Existing alias shares contract | E01 export assertion plus `offline-tests`: `tests/unit/test_cli_aliases.py::test_aliases_share_click_object_callback_and_public_contract`. |
| Same file — Operation contract commands are read-only | Contract export has no domain I/O | E01 exports outside an initialized project and compares catalogue/process state; `package`: `tests/packaging/test_installed_operation_contract.py::test_installed_package_exports_canonical_contract`. |
| Same file — Local operation invoke preserves output boundary | Machine invoke has clean stdout; Approval-required document call is rejected | E01 asserts finite/domain-negative output and invokes `odcli.remote.add` through document mode expecting `session_transport_required` with no preview/mutation; `offline-tests`: `tests/unit/test_operation_invoke_session.py::test_document_invoke_rejects_mutating_eval_without_running_it`. |
| Same file — Native and streaming CLI transports remain native | Interactive transport is unchanged | E01 finite-invoke rejection of `odcli.run`; `package`: `tests/packaging/test_installed_operation_contract.py::test_installed_package_keeps_native_cli_boundary`. |
| `command-execution/spec.md` — Same-snapshot preview approval and execution | Approved fingerprint executes captured command; Mismatched fingerprint is rejected | E01 proves approved H0→H1 mutation with emitted fingerprint; stale mismatch is sufficient lower-level coverage in `offline-tests`: `tests/unit/test_operation_invoke_session.py::test_approval_session_rejects_stale_decision_without_execution`. |
| Same file — Bounded decision and cancellation lifecycle | Approval times out; Caller cancels preview; Execution precondition still fails safely | E01 proves cancellation leaves H2==H1; timeout and stale-precondition behavior are sufficient lower-level coverage in `offline-tests`: `tests/unit/test_operation_invoke_session.py::test_cancel_and_timeout_are_non_execution_terminal_outcomes`, `::test_approval_session_rejects_stale_decision_without_execution`. |
| `environment-monitor/spec.md` — Finite snapshot section selection | Git and storage are unselected; Batch uses one selection pass | E02 minimal/expanded live snapshots; `offline-tests`: `tests/unit/test_monitor_snapshot_v3.py::test_unselected_sections_do_not_probe_or_cache`, `::test_snapshot_batch_sections_share_one_observation_boundary`. |
| Same file — Snapshot v3 observation truth | One probe is unavailable; Unrequested differs from unknown | E02 asserts public requested/completed/unknown output; `offline-tests`: `tests/unit/test_monitor_snapshot_v3.py::test_snapshot_observation_marks_unavailable_runtime`, `::test_snapshot_observation_marks_unavailable_docker`. |
| Same file — Honest process CPU samples | First finite sample has raw counter; Compatible previous sample yields percentage; PID reuse invalidates history | E02 proves live first sample; prior-sample/PID-reuse branches are sufficient lower-level coverage in `offline-tests`: `tests/unit/test_monitor_snapshot_v3.py::test_cpu_sample_carries_identity_and_rejects_pid_reuse`, `tests/unit/test_process_metrics.py::test_cpu_two_sample`, `tests/unit/test_monitor_required_regressions.py::test_cpu_identity_changes_prune_old_point_and_read_runtime_once_per_poll`. |
| Same file — Existing resource safety semantics remain intact | Shared PID remains counted once | Sufficient lower-level: `offline-tests`, `tests/unit/test_process_inventory.py::test_shared_external_process_shown_once`, `::test_no_double_counting_of_shared_backend_group`. |
| `database-management/spec.md:3` — modified database listing/existence requirement | Scenarios at lines 17, 22, 27, 32, 37 and 42: multiple removed databases; listing unavailable; missing tracked database observed; `list()` populates backup for each database; `list()` without a cluster key; empty list with tracked restores is observational | E03 proves missing tracked observation and inert repeated reads. Other branches are sufficient lower-level checks in `offline-tests`, `tests/unit/resources/test_database_resource.py`, including `TestList::test_missing_catalogue_names_are_observed_without_reconciliation`, `::test_with_cluster_key_populates_backup`, `TestExists::test_odoo_down_no_cluster_key`, `TestPlannedExistsProbe::test_unplanned_psql_fallback_when_odoo_is_unavailable` and `TestList::test_repeated_read_polling_does_not_publish_events`. |
| Same file — Explicit database reconciliation operation | Confirmed missing database is reconciled; Database reappears before execution; Inconclusive probe does not reconcile | E03 proves confirmed missing execution/idempotency; `offline-tests`: `tests/unit/resources/test_database_resource.py::test_explicit_reconciliation_revalidates_and_records_once`, `::test_reconciliation_rejects_reappeared_database`, `::test_reconciliation_rejects_invalid_observation_before_mutation`. |
| Same file — Required lifecycle owners call reconciliation explicitly | Startup reconciliation remains available; Polling read remains inert | E03 proves inert polling; startup ownership is sufficient lower-level coverage in `offline-tests`: `tests/unit/resources/test_database_resource.py::test_with_tracked_database_calls_explicit_reconciliation`. |
| `backup-catalog/spec.md` — Backup reads remain observational | Missing backup file is only observed; Repeated latest is inert | Sufficient lower-level because no new public E2E state is added: `offline-tests`, `tests/unit/resources/test_backup_resource.py::test_list_skips_missing_file`, `::test_repeated_backup_reads_leave_catalogue_bytes_unchanged`. |
| Same file — Structured durable replacement recovery | Failed compensation persists structure atomically; Retry consumes only validated recovery; Successful recovery clears evidence | Sufficient fault-path coverage in `offline-tests`: `tests/unit/internal/test_database_replacement.py::test_replacement_incomplete_compensation_persists_sanitized_cleanup_context`, `::test_replacement_cleanup_failed_published_topology_is_retryable`, `::test_replacement_success_publishes_new_backup_and_removes_rollback_filestore`; destructive live fault injection is excluded because it adds no public-boundary assertion beyond these exact state-machine tests. |
| Same file — Legacy replacement evidence migrates only at explicit repair | Known legacy evidence is adopted; Unknown legacy text is rejected | Sufficient lower-level: `offline-tests`, `tests/unit/internal/test_database_recovery.py::test_legacy_recovery_is_available_only_as_explicit_repair_input`, `::test_legacy_recovery_rejects_secret_bearing_text`. |
| Same file — Catalogue migration preserves compatibility | Existing catalogue migrates; Migration retry is idempotent | Sufficient lower-level: `offline-tests`, `tests/unit/storage/test_catalog_migration.py::test_prior_catalog_revisions_upgrade_to_current_schema`, `::test_reopen_stamped_catalog_is_idempotent`; no live E2E because exact historical SQLite fixtures are the authoritative input. |

### State lifecycle and public calls

E01 installs the candidate wheel and provider into `ODCLI_PYTHON`, creates `.odcli/project.toml`, and confirms that `myl432-approved` and `myl432-cancelled` do not exist. Its provider exposes `binta_test.health` and `binta_test.domain_negative`; the latter returns a typed negative `status` without a transport failure. It invokes:

- `"$ODCLI_BIN" --project "$TEST_WORKTREE" contract export --format json` twice;
- `"$ODCLI_BIN" --project "$TEST_WORKTREE" operation invoke binta_test.health --request '{}'`;
- `"$ODCLI_BIN" --project "$TEST_WORKTREE" operation invoke binta_test.domain_negative --request '{}'`;
- `"$ODCLI_BIN" --project "$TEST_WORKTREE" operation invoke odcli.remote.add --request '{"name":"myl432-document","base_url":"https://myl432.invalid","database":"myl432_document","git_branch":"main"}'`;
- `"$ODCLI_BIN" --project "$TEST_WORKTREE" operation invoke odcli.run --request '{}'`;
- `"$ODCLI_BIN" --project "$TEST_WORKTREE" operation session odcli.remote.add --request '{"name":"myl432-approved","base_url":"https://myl432.invalid","database":"myl432_approved","git_branch":"main"}'`, followed by `{"decision":"approve","fingerprint":"<emitted fingerprint>"}`;
- a separate session with `--request '{"name":"myl432-cancelled","base_url":"https://myl432.invalid","database":"myl432_cancelled","git_branch":"main"}'`, followed by `{"decision":"cancel","fingerprint":"<emitted fingerprint>"}`.

Export assertions include the `DepsMissingImport` alias, `env.create.ticket.required == true`, non-true `dry_run` defaults, and `remote.add` required fields. The domain-negative envelope must have `ok=true`; all outputs must omit the canary secret, HOME, `TEST_WORKTREE`, prompts and ANSI. Document-mode mutation and native invoke exit non-zero with `session_transport_required` and `transport_required`, respectively, without a child or manifest change. Capture manifest bytes/SHA256 as H0 before preview, H1 after approval and H2 after cancellation. Approval exits zero and changes only the approved remote; cancellation exits zero, leaves H2 byte-identical to H1 and never creates the cancelled remote. Cleanup runs `"$ODCLI_BIN" --project "$TEST_WORKTREE" remote remove myl432-approved --format json`, restores and verifies the original manifest bytes, uninstalls the provider from the dedicated interpreter, and removes only case-owned files.

`e2e/MYL-432/prepare_numbered_case.py` is the deterministic producer for E02/E03; no pre-existing fixture JSON is assumed. Its checked input contract requires `case`, `sdk_sha`, `reference_sha`, `base_ref`, `remote_name`, `target_database`, `odoo_bin`, `python`, `hash_lock`, `hash_lock_sha256`, `http_port`, `compose_file`, and `compose_project`, and rejects unknown fields. The private input instance is execution evidence delivered through Multica, not a GitLab fixture. The executor clones `TEST_REPO_URL` into a disposable absolute `REFERENCE_CHECKOUT`, sets `REFERENCE_SHA=$(git -C "$REFERENCE_CHECKOUT" rev-parse HEAD)`, sets `PROJECT="$REFERENCE_CHECKOUT"`, and obtains `REMOTE_NAME` from `"$CANONICAL_ODCLI" --project "$PROJECT" remote list --format json`; the chosen configured remote must be explicit and unique. It resolves `TESTED_PRODUCT_SHA` from PR HEAD, checks out that exact revision as `SDK_CHECKOUT`, selects a reviewed hash-locked Odoo 19 dependency resolution for macOS arm64 and CPython 3.12 from the test repository, computes its SHA256, and allocates distinct E02/E03 database names, ports, checkout parents and compose projects. The test-code commit SHALL contain that lock and its source-revision/architecture audit; if no compatible audited lock exists, E02/E03 setup SHALL stop before numbered creation.

Mac preflight SHALL confirm `platform.system() == "Darwin"`, `platform.machine() == "arm64"`, the selected Python reports 3.12, and the Odoo source `odoo-bin --version` reports 19. The lock SHALL be resolved from that pinned Odoo source's requirements for `aarch64-apple-darwin` and CPython 3.12, include hashes, and be audited for native build prerequisites and reviewed overrides. On a disposable local interpreter, `uv pip sync --require-hashes --dry-run --python "$CASE_PYTHON" "$MAC_ODOO_LOCK"` SHALL succeed; the executor SHALL then install that same lock into an isolated disposable interpreter, run `uv pip check` and an Odoo version/import probe, and compare the installed lock digest with the private case input before invoking `numbered_tool.py`. Record commands, exit codes, architecture, Python/Odoo versions, lock digest and audit provenance in Multica. A successful resolution alone is not proof that native dependencies install or Odoo starts. The Linux lock remains exercised only by `.github/workflows/real-odoo-tier.yml` on its Ubuntu runner; a workflow name or skipped job is not execution evidence.

Before reading project state, the producer assigns `CANONICAL_ODCLI=$(command -v odcli)` and requires it to be the executable trusted user installation resolved by `fix_tool.py`. It assigns `CANONICAL_PYTHON` from that launcher's absolute shebang and requires `fix_tool._canonical_interpreter()` and `_canonical_source()` to return and validate the same executable. The schema's `python` remains the environment-creation selector. The schema's absolute executable `odoo_bin`, resolved once with `command -v` when necessary, is the sole source for `ODOO_EXECUTABLE`; require `ODOO_EXECUTABLE == case_input.odoo_bin == environment_input.odoo_bin` before create and remove. The producer records these equality/provenance checks in its private setup output and fails closed on a missing, relative, non-executable or mismatched path. Its entry command is `"$CANONICAL_PYTHON" "$TEST_WORKTREE/e2e/MYL-432/prepare_numbered_case.py" --case "$CASE_ID" --sdk-checkout "$SDK_CHECKOUT" --reference-checkout "$REFERENCE_CHECKOUT" --project "$PROJECT" --remote "$REMOTE_NAME" --checkout-parent "$CHECKOUT_PARENT" --output "$CASE_ROOT"`. Missing paths, ambiguous remotes, occupied resources or SHA/digest drift fail setup.

The producer writes the private `environment-E0N.json` accepted by `numbered_tool.py` with `db_mode="copy"`, `remote_name`, `base_ref`, `target_database`, `odoo_bin`, `python`, `create_venv=true`, `hash_lock`, `hash_lock_sha256`, and `http_port`, then runs:

```text
"$CANONICAL_PYTHON" "$SDK_CHECKOUT/scripts/odcli_agent_tools/numbered_tool.py" create --project "$PROJECT" --reference-checkout "$REFERENCE_CHECKOUT" --checkout-parent "$CHECKOUT_PARENT" --reference-sha "$REFERENCE_SHA" --sdk-sha "$TESTED_PRODUCT_SHA" --environment-json "$CASE_ROOT/environment-E0N.json" --odoo-executable "$ODOO_EXECUTABLE" >"$CASE_ROOT/create.json"
"$CANONICAL_PYTHON" "$SDK_CHECKOUT/scripts/odcli_agent_tools/numbered_tool.py" inspect "$IDENTIFIER" >"$CASE_ROOT/inspect.json"
```

`IDENTIFIER` comes from `create.json`. Create is authoritative for `ODCLI_BIN=create.launcher`, `ODCLI_USER_ROOT=create.state_root`, `PROJECT=create.checkout`, `REFERENCE_SHA=create.checkout_sha`, `ENV_ID=create.environment_id`, `ODCLI_PYTHON=create.interpreter`, `HTTP_PORT=create.http_port`, and `TARGET_DB=create.database`. Inspect is authoritative for candidate identity and health: require `inspect.sha == TESTED_PRODUCT_SHA`, `inspect.healthy == true`, and equality of create/inspect `identifier`, `launcher`, `checkout`, `checkout_sha`, `environment_id`, and `environment_path`, plus `create.state_root == inspect.state` and `create.interpreter == inspect.interpreter_path`. Port and database are never read from inspect. The public read-back `"$ODCLI_BIN" --project "$PROJECT" env show "$ENV_ID" --format json` must confirm the linked environment, database, port and `PROJECT_ID` before either case proceeds.

E02 runs `"$ODCLI_BIN" --project "$PROJECT" --env "$ENV_ID" run --detach --wait-ready --readiness-timeout 60 --format json`, calls `EnvironmentMonitor().snapshot(request=SnapshotRequest(sections=("catalogue", "runtime"), project_ids=(PROJECT_ID,), environment_ids=(ENV_ID,)))`, and repeats with `sections=("catalogue", "runtime", "postgresql", "docker")`. E03 validates the linked copy as present, saves a sanitized catalogue projection, and removes only its physical test database with `docker compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT" exec -T postgres dropdb -U odoo "$TARGET_DB"`; the producer-generated case input binds both compose selectors and the target to E03. The preparation script then performs only repeated observational reads, records their unchanged catalogue/event hashes, writes `checkpoint.json`, and exits without reconciliation while retaining the numbered identity for I03.

Every script installs signal/exception cleanup before its first mutation. A fresh identity makes reset an allocation precondition rather than a public `env checkout`/`env rm` cycle. After the fresh-agent checkpoint, or after any failure, cleanup stops only the linked environment and delegates environment, checkout, interpreter, launcher and manifest removal to the numbered service:

```text
"$ODCLI_BIN" --project "$PROJECT" --env "$ENV_ID" stop --format json
"$CANONICAL_PYTHON" "$SDK_CHECKOUT/scripts/odcli_agent_tools/numbered_tool.py" remove "$IDENTIFIER" --checkout-parent "$CHECKOUT_PARENT" --odoo-executable "$ODOO_EXECUTABLE"
```

Cleanup must report `removed=true`, then prove the recorded launcher, manifest, checkout, environment and database are absent and the case port/process/compose resources are released. It never calls public `env rm` for a numbered environment and never deletes a foreign or unverified resource.

### Independent fresh-agent walkthroughs

- **I01:** Check out `TEST_SCENARIO_SHA`, verify `PRODUCT_WHEEL` SHA256, and use the recorded `ODCLI_BIN`/`ODCLI_PYTHON` pair. Run contract export and `binta_test.domain_negative` directly, then inspect `contract-a.json`, `domain-negative.json`, H0/H1/H2 and process inventory with `shasum -a 256` and `git diff --no-index`. Expect alias/default/required assertions, `ok=true` with negative status, H0→H1 only for `myl432-approved`, H1==H2 and no child. Any extra manifest delta, prompt/ANSI/canary/path, non-typed error or hash mismatch fails I01.
- **I02:** While `--retain-for-agent` keeps only the recorded test-owned environment, parse `ODCLI_USER_ROOT=create.state_root`, `PROJECT=create.checkout`, `ENV_ID=create.environment_id`, `PROJECT_ID` from the validated public read-back, and `ODCLI_PYTHON=create.interpreter`; require `ODCLI_USER_ROOT == inspect.state`, `PROJECT == inspect.checkout`, `ENV_ID == inspect.environment_id`, and `ODCLI_PYTHON == inspect.interpreter_path`. Run `"$ODCLI_BIN" --project "$PROJECT" env show "$ENV_ID" --format json`. Then run `env ODCLI_USER_ROOT="$ODCLI_USER_ROOT" PROJECT_ID="$PROJECT_ID" ENV_ID="$ENV_ID" "$ODCLI_PYTHON" -c 'import os,sys,msgspec; from odoo_instance_sdk.models import SnapshotRequest; from odoo_instance_sdk.resources.monitor import EnvironmentMonitor; value=EnvironmentMonitor().snapshot(request=SnapshotRequest(sections=("catalogue","runtime"), project_ids=(os.environ["PROJECT_ID"],), environment_ids=(os.environ["ENV_ID"],))); open(sys.argv[1],"wb").write(msgspec.json.encode(value))' "$ARTIFACT_ROOT/E02/agent-snapshot.json"`. Inspect the recorded PID/create-time with `ps`. Expect the exact environment, only requested sections, one UTC boundary and raw first-sample counters with percentage unknown. An equality failure, unrequested section, fresh zero, identity mismatch, private path or unavailable owned process fails I02.
- **I03:** Require `checkpoint.json` to say `reconciled=false`. Parse `ODCLI_USER_ROOT=create.state_root`, `ENV_ID=create.environment_id`, `TARGET_DB=create.database`, and `ODCLI_PYTHON=create.interpreter`; require equality with `inspect.state`, `inspect.environment_id`, the validated public environment read-back and `inspect.interpreter_path`, respectively. Run `"$ODCLI_BIN" --project "$PROJECT" --env "$ENV_ID" db list --format json`, then run the fresh-agent SDK process as `env ODCLI_USER_ROOT="$ODCLI_USER_ROOT" ENV_ID="$ENV_ID" TARGET_DB="$TARGET_DB" ODOO_EXECUTABLE="$ODOO_EXECUTABLE" "$ODCLI_PYTHON" -c '<documented I03 SDK inspection/reconciliation program>'`. That program constructs `OdooClient(OdooClientConfig(executable=os.environ["ODOO_EXECUTABLE"]))`, loads `client.environments.get(os.environ["ENV_ID"])`, derives `instance=client.instance.from_environment(environment)`, and rejects any selected database other than `TARGET_DB`. Call `observe/list/exists/current`, hash the sanitized catalogue projection, and query the event count into `agent-before.json`; require its hashes/counts to match the checkpoint. In that same process, build `command=instance.databases.reconcile_databases_command(observation)`, save its exact plan as `agent-plan.json`, execute it once into `agent-first.json`, take a fresh observation, execute its reconciliation command into `agent-second.json`, and save new catalogue/event/PostgreSQL inspections as `agent-after.json`. Do not rerun `test_database_reconciliation.py`. Query physical absence with `docker compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT" exec -T postgres psql -U odoo -d postgres -v target_db="$TARGET_DB" -Atc "SELECT 1 FROM pg_database WHERE datname = :'target_db'"`. Expect pre-reconcile reads to be inert, the plan to name only the case target, the first execution to add exactly one `dropped` event, the second to be idempotent, and PostgreSQL output to remain empty; an environment-binding/equality failure, mutation during reads, a foreign target, reappearance, inconclusive probe or duplicate event fails I03.

The fresh agent records commands, exit statuses and observations but does not invoke any `test_*.py`. It always releases the recorded retained identity through the documented cleanup; missing cleanup proof fails the case.

### Output captures and evidence publication

Plan exactly **two before/after image pairs**, both for E01's changed CLI output:

1. `MYL-432-E01-invoke-before.txt/.png` and `MYL-432-E01-invoke-after.txt/.png`;
2. `MYL-432-E01-session-before.txt/.png` and `MYL-432-E01-session-after.txt/.png`.

Resolve both revisions immediately before capture with `gh pr view "$PR_NUMBER" --json baseRefOid,headRefOid`: `BASELINE_PRODUCT_SHA=baseRefOid`, `TESTED_PRODUCT_SHA=headRefOid`; require the latter to equal the wheel/slot revision and the former to be its ancestor. Build both wheels in clean checkouts and install them into separate empty venvs with the same provider tree from `TEST_SCENARIO_SHA`. Run the identical invoke and session argv after restoring identical manifest bytes, with `LC_ALL=C.UTF-8`, `COLUMNS=120` and JSON/JSONL output mode. The baseline's real “command unavailable” exit/output is a valid visible before state; a wheel/provider installation failure, different fixture/request, dirty reset or candidate mismatch makes the pair incomparable and blocks image completion.

The scenario commit includes `e2e/MYL-432/render_terminal.swift`, a minimal macOS AppKit renderer with the checked interface `swift render_terminal.swift <title> <input.txt> <output.png>`, plus a fixture self-check. Render only the actual captured UTF-8 files; do not reference an external skill path. Redirection is not a TTY, so the text/PNG pairs deliberately exclude ANSI styling and carriage-return progress and prove bounded textual output only. E02 and E03 change SDK/state semantics, not human CLI/table/log/progress output, so no images are useful for them.

Use one task MR in the test repository. Its scenario commit is `test(e2e): add MYL-432 scenarios` and contains only reusable scripts, provider, renderer and necessary fixtures. A second result commit is created only after the tested function actually produces a repository-worthy functional result. For these cases the only eligible JSON is the byte-stable E01 contract export, because generating that contract is itself the public function under test; E02/E03 snapshots and all diagnostic JSON are evidence, not GitLab content. If no eligible result is produced, do not create an empty or evidence-only second commit. Provide parameterized MR, scenario/result commit, diff and file links only for commits and files that exist.

The final Multica comment records `BASELINE_PRODUCT_SHA`, `TESTED_PRODUCT_SHA`, wheel digests, scenario/result commit SHAs, commands, exit codes and assertions; links the MR and retained GitLab files; and attaches the four PNGs, their captured text, session transcripts/logs and sanitized diagnostic JSON. Read the comment back and require every link and attachment to be accessible to the acting user. None of these evidence outputs, provenance values or PNGs is committed to GitLab. Cleanup removes only recorded run-owned slots, checkouts, databases, ports, processes, volumes and temporary files. It preserves the evidence MR, branch and commits for later access and never closes the MR or deletes its branch automatically. No video, full CLI sweep or destructive recovery fault injection is required. GitHub CI coverage remains Implementer-owned in WP-07 tasks 9.1, 9.4 and 9.5; Tester only inspects it and reports gaps, and no new CI framework or CI test is required by these cases.

## Coverage and topology audit

Every OpenSpec task `1.1` through `9.9` is assigned exactly once. Direct dependencies contain no transitive edge. Each predecessor has a lower stage than its dependent, stage numbers are consecutive, and the stage-2 frontier contains three independent WPs with non-conflicting ownership. Product scope, public/shared contract, estimate classification or edge changes require a new planning revision; operational promotion does not.
