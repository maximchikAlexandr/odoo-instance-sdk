## 1. Characterize and establish the canonical operation inventory

- [x] 1.1 Add characterization fixtures for the complete Click leaf/alias tree, current `PUBLIC_LEAF_CASES`, SDK primitive ownership, transport classes, envelope-v1 bytes, exit codes and native/interactive behavior.
- [x] 1.2 Add wire-projection fixtures for nested DTOs, tagged unions, defaults/nulls and aliases including `DepsMissingImport.import_name -> import`.
- [x] 1.3 Add frozen operation descriptor/binding/transport/error models and move the canonical `PUBLIC_LEAF_CASES` authority from tests into production without changing existing command behavior.
- [x] 1.4 Bind real Click leaves and aliases to stable namespaced operation IDs and validate one ID/path/implementation mapping against the composed tree.
- [x] 1.5 Update existing characterization/E2E matrices to import production inventory and keep invocation arguments/evidence as test-only data.
- [x] 1.6 Prove registry construction rejects missing/stale leaves, duplicate IDs/paths, incomplete metadata and SDK primitive drift.

## 2. Export actual wire contracts and consumer types

- [x] 2.1 Define frozen unknown-field-forbidden request DTOs for finite operations and connect existing concrete result/error DTOs without replacing them with arbitrary JSON.
- [x] 2.2 Implement deterministic JSON Schema generation through the existing msgspec/runtime projection so wire aliases, tagged unions, defaults and nullability match encoded values.
- [x] 2.3 Include envelope-v1, operation metadata, parameter required/default values, transport/preview/cancellation/exit policy and domain-status fields in contract bundle version 1.
- [x] 2.4 Implement metadata-only `odcli contract export --format json` with deterministic ordering and no project/catalogue/Git/Docker/PostgreSQL/domain I/O.
- [x] 2.5 Add pinned Go JSON-Schema consumer tooling outside runtime dependencies and committed generation/compile fixtures covering requests, results, errors, aliases and extensible plugin payloads.
- [x] 2.6 Add compatibility fixtures that reject accidental breaking bundle changes unless the operation-contract version changes explicitly.

## 3. Capture one context and invoke finite operations locally

- [ ] 3.1 Add frozen invocation-scoped `OperationContext` over existing CLI/runtime resolution with explicit selectors, cwd/provenance, selected interpreter set, private resolved objects and one catalogue owner.
- [ ] 3.2 Refactor representative bounded read and mutation factories to accept typed request plus `OperationContext`, eliminating repeated cwd/project/environment/config/catalogue discovery inside one call.
- [ ] 3.3 Implement validated operation registry lookup and `odcli operation invoke <operation-id>` as an explicitly local entrypoint with no daemon/RPC/dispatch construction.
- [ ] 3.4 Implement finite document invocation through existing envelope-v1, sanitization and exit mapping with bounded output and no prompt/ANSI/stdout diagnostics.
- [ ] 3.5 Reject native TTY, interactive, Rich-live and unbounded JSONL operations from finite invoke with typed supported-transport guidance while preserving their friendly CLI paths.
- [ ] 3.6 Migrate the remaining eligible finite built-ins to typed requests/context and prove each friendly CLI adapter and stable operation ID delegate to the same SDK primitive.

## 4. Preserve one private snapshot through approval

- [ ] 4.1 Define bounded JSONL session request/event DTOs and ordering for accepted, preview, approval-required, step, result, error and cancelled records.
- [ ] 4.2 Retain exactly one private `Command` after preview and require one approve decision carrying the emitted fingerprint before executing that same object.
- [ ] 4.3 Implement bounded decision timeout, input/record/byte limits, EOF, invalid-sequence, mismatched-fingerprint, cancel and interrupt semantics without reconstructing executable inputs.
- [ ] 4.4 Preserve existing StepObserver events, process/Expression gates, execution-time revalidation, locks, rollback, postconditions and exact-child cleanup through the session adapter.
- [ ] 4.5 Add tests for approval parity, stale decision, cancel, timeout, cleanup, output privacy and transport-success/domain-negative result separation.

## 5. Add the narrow installed-operation provider boundary

- [x] 5.1 Define the `odoo_instance_sdk.operations` entry-point provider protocol returning finite complete bindings and factories for one supported contract version.
- [x] 5.2 Discover selected-interpreter providers once in deterministic order under a startup deadline and return bounded sanitized missing/load/version errors.
- [x] 5.3 Reject duplicate IDs/paths and incomplete provider DTO/factory bindings before any provider operation executes; keep core free of provider-domain imports.
- [x] 5.4 Add an isolated fixture plugin proving discovery, schema export and local invocation without core or generated-Go inventory changes.
- [x] 5.5 Add negative tests for absent, incompatible, slow, failing and conflicting providers plus packaging tests proving no Go/plugin lifecycle/runtime dependency was introduced.

## 6. Make finite monitor snapshots selective and honest

- [x] 6.1 Add frozen snapshot section request and snapshot-v3 observation/completeness/freshness DTOs while preserving every version-2 field.
- [x] 6.2 Refactor monitor planning so project/environment/section selection precedes Git, storage, artifact, Docker and PostgreSQL probes and unselected sections do not populate caches.
- [x] 6.3 Implement batch observation with one catalogue selection pass, one shared UTC observation boundary and typed requested/completed/unknown section results.
- [x] 6.4 Extend process metrics with confirmed PID/create-time, raw cumulative CPU seconds and sample time; calculate percentage only from a compatible prior point.
- [x] 6.5 Preserve Darwin physical footprint, non-Darwin RSS, recursive ownership, shared-PID dedup and the real production Docker collector path.
- [x] 6.6 Add first-sample, caller-supplied previous sample, PID reuse, partial probe, section-no-call, cache and multi-project batch tests across SDK/CLI/API fixtures.

## 7. Separate database observation from reconciliation

- [ ] 7.1 Characterize every current `DatabaseResource.list/exists/current` catalogue write and all startup/registration/destructive/repair call sites that rely on it; characterize backup list/latest as inert.
- [ ] 7.2 Add typed database observation models carrying names, exact evidence source, tracked/missing names and inconclusive state without catalogue mutation.
- [ ] 7.3 Refactor list/exists/current and their commands to return/consume observational results and remove implicit dropped-event writes, including Odoo-unavailable psql fallback paths.
- [ ] 7.4 Implement previewable `reconcile_databases_command` with exact cluster/database observation, live absence revalidation, transaction/lock use and idempotent dropped events.
- [ ] 7.5 Migrate required startup, registration, destructive postcondition and repair owners to call explicit reconciliation at their named mutation boundary.
- [ ] 7.6 Add stale/reappeared/inconclusive/foreign identity tests and repeated polling spies proving list/exists/current/monitor/inventory/backup reads remain write-free.

## 8. Persist structured DB replacement recovery

- [ ] 8.1 Add versioned frozen `CopyReplacementRecovery` and a nullable `recovery_json` column through the single Alembic lineage with current/legacy/retry migration fixtures.
- [ ] 8.2 Persist cleanup-failed environment state, sanitized diagnostic, structured recovery and event atomically; stop writing new executable evidence inside `last_error`.
- [ ] 8.3 Make replacement retry/repair validate and consume only structured recovery plus exact live environment/backup/cluster/database/filestore evidence.
- [ ] 8.4 Clear structured recovery only with successful compensated or published postconditions while preserving event and transaction journals.
- [ ] 8.5 Implement one explicit guarded legacy adoption path for the known bounded `retained=...` form and reject malformed, contradictory or secret-bearing text without mutation.
- [ ] 8.6 Add interruption/retry/rollback/publication/legacy/unknown-form tests proving locks, checksums, permissions, ownership, cleanup and redaction remain intact.

## 9. Cross-contract verification and documentation

- [ ] 9.1 Add representative installed-package end-to-end proofs for a bounded read, previewable mutation and existing streaming/interactive transport, including schema alias, negative domain result, no prompt/stdout pollution and privacy checks.
- [ ] 9.2 Add source-boundary tests proving one inventory, one executor, no test imports in production, no Go domain package list, no daemon/RPC/telemetry/plugin lifecycle framework and no weakened process/Expression gates.
- [ ] 9.3 Update SDK/CLI/plugin/contract export and schema-generation documentation, snapshot-v3 semantics, explicit reconcile/repair guidance and compatibility/versioning notes.
- [ ] 9.4 Run focused operation/schema/session/plugin/monitor/database/catalogue/redaction/security/packaging suites and record external prerequisite skips separately from regressions.
- [ ] 9.5 Run repository formatting, lint, typing, Alembic/schema, architecture/contract, full `make pr`, build and generated-consumer compile gates; inspect the final diff for planning scope and preserved standalone SDK/CLI behavior.
