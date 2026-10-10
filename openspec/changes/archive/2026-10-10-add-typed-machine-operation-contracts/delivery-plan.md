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
WP-03-provider-and-go-consumer -> 2
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

## WP-03 — Installed providers and Go consumer proof

- `wp_id=WP-03-provider-and-go-consumer`
- `stage=2`
- `depends_on=[WP-01-operation-contract-foundation]`
- `task_coverage=2.5,5.2,5.4,5.5`

Deliverable: selected-interpreter entry-point providers are discovered once under bounded validation, and the authoritative exported bundle demonstrably generates compiling Go consumer types without becoming a runtime dependency.

Owned responsibility scope:

- Provider entry-point loader, deadline/error isolation and deterministic provider ordering.
- Fixture plugin package and packaging/discovery compatibility tests.
- Pinned Go JSON-Schema delivery tooling, generated compile fixtures and invocation scripts/configuration outside runtime dependencies.
- Negative provider/version/conflict/absence/slow-load tests and directly related docs.
- Critical shared files: provider-loader module beneath the WP-01 seam, fixture plugin metadata, repository tooling/go module and generated-consumer test fixtures.

Contract surface:

- One entry-point group and one supported operation-contract version.
- Provider descriptor/DTO/factory stay colocated; core has no provider-domain import list.
- Go consumes exported JSON Schema only; Python installation and operation discovery never invoke Go.
- No install/update/remove/hot-reload/sandbox lifecycle is introduced.

Definition of done and evidence:

- A fixture provider appears in discovery/export and invokes through the fixed registry seam.
- Missing, incompatible, failing, slow and conflicting providers produce bounded sanitized outcomes before operation execution.
- Generated Go request/result/error/alias fixtures compile.
- Core wheel metadata contains no Go runtime or plugin-manager dependency.

Parallel safety: owns provider loading, fixture package and consumer tooling. WP-01 owns shared registry/schema definitions; WP-02 owns runtime invocation; WP-04 owns monitor paths, so sibling write zones do not overlap.

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
- `depends_on=[WP-03-provider-and-go-consumer,WP-04-selective-monitor-snapshots,WP-06-structured-replacement-recovery]`
- `task_coverage=9.1,9.2,9.3,9.4,9.5`

Deliverable: one integrated, documented, buildable change proves the contract across bounded read, previewable mutation and native/streaming transport while preserving standalone SDK/CLI and every safety boundary.

Owned responsibility scope:

- Cross-subsystem installed-package E2E contract tests and source-boundary architecture checks.
- Final SDK/CLI/plugin/schema/snapshot/reconcile/recovery/versioning documentation.
- Focused and full repository gates, generated consumer compile checks, packaging/build verification and final diff cleanup.
- Directly related shared fixtures/snapshots/docs/support files after fan-in.
- Critical shared files: integration/contract tests, README/docs, packaging metadata only if needed for verified delivery, and final generated fixtures.

Contract surface:

- One inventory and executor, no production test imports, no Go domain list, no daemon/RPC/telemetry/plugin lifecycle framework.
- Bounded output is schema-valid, prompt-free and redacted; native/interactive transport remains native.
- SDK remains directly callable without a daemon; installing a Python provider requires no Go rebuild.
- All process, Expression, ownership, filesystem, archive, database, lock, rollback and postcondition gates remain covered.

Definition of done and evidence:

- Representative installed-package matrix covers schema alias/defaults, domain-negative result, same-snapshot approval, cancellation and privacy.
- Focused operation/schema/session/plugin/monitor/database/catalogue/security/packaging suites pass.
- Formatting, lint, typing, Alembic/schema, architecture/contract, `make pr`, build and Go consumer compile gates pass, with external prerequisite skips recorded separately.
- Final diff contains no generated visual HTML and no out-of-scope daemon/telemetry/domain-feature work.

Parallel safety: final fan-in only. All predecessors are complete before it may update cross-cutting tests, docs, packaging or generated fixtures, so no sibling write conflict remains.

## Coverage and topology audit

Every OpenSpec task `1.1` through `9.5` is assigned exactly once. Direct dependencies contain no transitive edge. Each predecessor has a lower stage than its dependent, stage numbers are consecutive, and the stage-2 frontier contains three independent WPs with non-conflicting ownership. Product scope, public/shared contract, estimate classification or edge changes require a new planning revision; operational promotion does not.
