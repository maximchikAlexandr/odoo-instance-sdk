## Context

Baseline: `ab71895f7031eabe129fbd5dbfcda71298cd4016` (`origin/main`, 2026-10-10). The issue's earlier observation SHA `cb638fa4d575b0dbb8ca5d4452897568102484ca` is behind current main; the intervening changes add the Multica checkout binding and several audited lifecycle fixes but do not add an operation contract.

The current implementation already has the foundations that this design must reuse:

- `execution.Command[T]` pairs an immutable public `ExecutionPlan` with a private in-memory `PreparedCommand`; the executable callback is deliberately absent from serialization.
- `commands.output.OutputDocument` is the CLI-private envelope v1 shared by Rich/JSON/TOON, and `internal.output_fields.field_schema` already binds concrete result DTOs to bounded leaves.
- `tests/unit/test_cli_output_modes.py::PUBLIC_LEAF_CASES` is a complete classified leaf inventory, but it is test-owned and therefore cannot be the runtime discovery or schema source.
- `CliContext` and `ResolvedContext` exist, but individual paths still reopen catalogues or rediscover runtime facts after ingress.
- `EnvironmentMonitor` already groups catalogue rows once and filters the selected project before manifest/status/Docker work. Its in-process cache can derive CPU percentages, while a fresh CLI process cannot.
- `BackupResource.list/latest` are observational. In contrast, `DatabaseResource.list/exists/current` record `dropped` events, including through methods and commands marked read-only.
- DB replacement has a typed `CopyReplacementFailureContext`, but persists it by embedding JSON between delimiters in `environments.last_error` and later reparses that human string.
- `_discover_providers()` proves a small, bounded Python entry-point pattern for environment facts. It is not a general operation framework and must not be reused as if it already supplied operation identity, schemas or implementations.

The active `refactor-cli-output-boundary` change owns the current envelope, formats, field projection and canonical monitor result graph. This change consumes those contracts instead of replacing them. MYL-409/411 supplies domain operations such as Git/GitLab/module/publication on its own implementation branch; those operations may later register through this boundary, but their domain behavior is not reimplemented here. MYL-423/425 is treated as an already-integrated baseline. MYL-429 telemetry remains separate.

## Goals / Non-Goals

**Goals:**

- Make operation identity, request/result/error schemas, context policy, preview support and transport policy discoverable from one production source.
- Derive JSON wire schemas from the concrete DTO encoder projection, including aliases such as `DepsMissingImport.import_name -> "import"`.
- Execute a finite local machine request without prompt or stdout contamination and support one-process preview/approval/execution for mutations.
- Let installed Python packages contribute namespaced operations without changes to a Go inventory and without making any daemon required.
- Make finite monitoring honest about selection, observation time, completeness and cross-process CPU sampling.
- Separate observational reads from explicit catalogue reconciliation and retain structured DB replacement recovery evidence.
- Preserve the existing SDK-first CLI, private executable snapshot, redaction, ownership/process/Expression gates, locks, rollback and postconditions.

**Non-Goals:**

- A Go daemon, RPC server, remote queue, scheduling, telemetry/exporter/outbox, daemon dispatch command or network API.
- Moving all existing commands into plugins, hot-loading code, plugin install/update/remove lifecycle, dependency solving or isolation of untrusted Python.
- A second execution engine, a serialized executable `Command`, a generic DI container or a replacement for Click/Rich/JSON/TOON.
- Reworking native TTY loops (`run`, interactive `shell`, `psql`), existing JSONL streams or Rich watch into finite documents.
- Global caches, a new general workflow engine or weakening any execution-time precondition.

## Decisions

### D1. Promote one canonical operation inventory into production

Add `odoo_instance_sdk.operations` with frozen `OperationDescriptor`, `OperationTransport`, `OperationParameter`, `OperationError` and `OperationBinding` models. Production `PUBLIC_LEAF_CASES` becomes the canonical tuple of CLI leaf bindings; characterization and E2E tests import it from production and add test vectors separately. Each binding contains a stable namespaced ID (`odcli.<domain>.<verb>`), canonical Click path, aliases, public SDK primitive reference, request/result/error DTO types, context policy, transport, preview/approval policy and exit mapping.

CLI registration attaches an operation ID to the real Click command. A registry build walks the composed Click tree and the same bindings, failing on a missing leaf, an unbound bounded leaf, duplicate path, duplicate ID or incompatible alias. An alias points to the same binding and cannot alter its ID. There is no second command list or registry for schema export.

Alternative: introspect the Click tree and infer everything from callback signatures. Rejected because Click types cannot prove SDK ownership, domain-result schemas, cancellation or preview policy. Alternative: leave `PUBLIC_LEAF_CASES` in tests and copy a runtime table. Rejected because it recreates the drift this change removes.

### D2. Export the actual encoder schema, not Python annotation spelling

Every finite operation receives a frozen, unknown-field-forbidden request DTO and a concrete result DTO. The schema exporter uses the project's `msgspec` schema bridge and verifies representative values through the same `msgspec.to_builtins`/JSON encoding path used by CLI output. Field names, tagged unions, nullability and defaults therefore follow wire metadata, not attribute names. The bundle includes envelope v1, typed operation result/error references, parameter required/default metadata, transport/preview/exit metadata, and a contract version independent from envelope and catalogue versions.

`odcli contract export --format json` emits one deterministic bundle. Consumer code generation is delivery tooling over that bundle: a pinned Go `go-jsonschema` tool lives under repository tooling and produces checked fixtures in verification; it is not imported or invoked by OdCLI at runtime and is not a dependency of a Python plugin installation. Other consumers may use the same standard JSON Schema bundle.

Alternative: hand-write JSON Schema or use Click parameter metadata as the schema. Rejected because both miss DTO aliases and nested result semantics. Alternative: add a custom multi-language generator to the SDK. Rejected as unnecessary maintenance when a standard schema and pinned consumer generator suffice.

### D3. Keep friendly CLI aliases and stable operation identity separate

Existing CLI paths remain user-facing adapters. A new `odcli operation invoke <operation-id>` is the explicitly local machine entrypoint; it resolves an installed operation and never dispatches to a daemon or coordinator. There is no dispatch command in this change. Future remote dispatch must be a separate command and may call the local invoke boundary, preventing `coordinator -> CLI dispatch -> coordinator` recursion.

Finite `document` transport emits the existing envelope v1 once. Bounded session transport is JSONL and emits typed `accepted`, `preview`, `approval_required`, `step`, `result`, `error` and `cancelled` records. Native TTY, existing application JSONL and interactive transports are classified in discovery but rejected by finite invoke with a typed `transport_required` error and a direct CLI-path hint.

Alternative: rename every friendly command to its operation ID. Rejected for compatibility. Alternative: wrap native streams into one JSON result. Rejected because it loses TTY, streaming and boundedness semantics.

### D4. Capture one explicit operation context at ingress

`OperationContext` is a frozen invocation-scoped carrier created once from explicit request selectors plus the captured cwd. It contains stable project/environment identity and provenance, the selected interpreter/package set, one lazily opened catalogue handle owned by the invocation, and already-resolved config/runtime objects where required. Its public projection contains only redacted identifiers and provenance; filesystem paths, secrets and handles are private.

Operation factories accept typed request plus `OperationContext`. Existing `CliContext`/`ResolvedContext` resolvers become the construction boundary instead of being called repeatedly inside one invocation. Execution still revalidates current process, Git/config/runtime, filesystem, DB ownership, locks and postconditions through the existing private prepared steps; context capture is not authorization to skip changing-state checks.

Alternative: global context/cache shared between calls. Rejected because lifetime, invalidation and multi-user isolation are not justified. Alternative: let each SDK resource rediscover context. Rejected because it produces inconsistent snapshots within one call.

### D5. Preserve one private `Command` through preview and decision

For a previewable mutation, local invoke builds one `Command` and retains it in the same process. The JSONL session emits the redacted public plan/fingerprint, then waits for exactly one typed decision (`approve` with the fingerprint or `cancel`) under a configurable bounded timeout capped by the contract. Approval executes that same object; EOF, timeout, mismatched fingerprint and cancel dispose it without execution. `Command` callbacks and prepared inputs are never serialized or reconstructed from the plan.

An execution result may be a successful transport document whose typed payload reports a negative domain outcome (failed test, dirty Git check, validation findings or stopped/unready status). Only invocation/transport failures use `ok=false`; descriptors document the domain status field.

Alternative: run `--dry-run`, then start a second process to execute. Rejected because it cannot prove snapshot identity. Alternative: serialize private callbacks. Rejected by the existing security boundary.

### D6. Discover only explicit installed operation providers

Use one entry-point group, `odoo_instance_sdk.operations`, whose provider returns a finite tuple of complete `OperationBinding` values and factories. Built-ins are loaded through the same validation path after cheap core metadata registration. Provider loading uses the selected interpreter, deterministic `(provider, operation_id)` order, one startup deadline and bounded sanitized failures. Duplicate IDs/paths, unsupported contract versions or incomplete bindings fail registry construction; an explicitly requested missing plugin returns `operation_unavailable` without scanning arbitrary packages.

Provider packages own descriptor, request/result DTO and implementation together. They may depend on public SDK APIs but core never imports provider domain packages. There is no hot reload: installed entry points are evaluated once per process.

Alternative: extend the environment-facts provider to operations. Rejected because its failure-isolated optional summaries do not provide executable contracts. Alternative: a full plugin manager. Rejected by scope and Ponytail.

### D7. Make finite monitor selection and freshness part of the DTO

Add frozen `SnapshotRequest` section selectors and additive snapshot-v3 observation metadata: a shared UTC observation time, requested/completed/unknown sections, per-section freshness (`observed_at`, optional source age), completeness and sanitized reason. Catalogue selection and project filtering remain before expensive Git/storage/Docker/PostgreSQL probes. Unselected sections do not start probes or populate caches. A batch request plans selected projects/environments once and shares the observation boundary.

Runtime metrics add process identity (`root_pid`, `create_time`), raw cumulative CPU seconds and sample time. Percentage remains available only when the same monitor has a matching previous point or the caller explicitly supplies one; a fresh one-shot process returns raw counters with percentage unknown. Platform memory semantics and shared-PID dedup remain unchanged. The real monitor production path, not a standalone assumption, continues to own Docker stats collection.

Alternative: add a global CPU cache. Rejected because persistence/invalidation is unjustified. Alternative: synthesize percentage from one sample. Rejected as dishonest.

### D8. Separate observation from reconciliation

`DatabaseResource.list/exists/current` and their `*_command()` forms become read-only: they return typed observations about current Odoo/psql state and tracked catalogue differences but never append `dropped` events. An explicit `reconcile_databases_command(observation)` rechecks the exact cluster/database identity under the existing catalogue transaction and records idempotent dropped events. Startup, registration, destructive postconditions and repair flows that require reconciliation call that explicit operation; ordinary list/current/exists and monitor paths do not.

Backup `list/latest` remain observational and receive characterization tests proving no backfill/missing-state writes. Existing registration/startup reconciliation is preserved at its named mutation boundary. No broad `repair everything` command is introduced.

Alternative: retain hidden writes for compatibility. Rejected because machine reads would remain unsafe to poll. Alternative: remove all reconciliation. Rejected because durable lifecycle audit is required.

### D9. Store DB replacement recovery separately from human diagnostics

Add nullable `recovery_json` to the existing environment row through the single Alembic lineage. It encodes a versioned, frozen `CopyReplacementRecovery` DTO using the catalogue JSON adapter; `last_error` remains a bounded sanitized human diagnostic. Failed compensation writes state, diagnostic, recovery DTO and event atomically. Retry/repair reads only `recovery_json`, validates exact environment/backup/database/filestore/cluster identities, rechecks live state and clears it only after successful compensation or publication.

Legacy `cleanup_failed` rows containing the old `retained=...` segment are migrated lazily at the explicit replacement repair boundary: parse the bounded known legacy form once, validate it, persist `recovery_json`, and never use arbitrary `last_error` as executable input. Unknown forms fail closed. Existing journals, locks and compensations remain the executor; no state-machine framework is added.

Alternative: add a new workflow/recovery table. Rejected because one nullable structured field is sufficient for the one existing lifecycle. Alternative: keep parsing `last_error`. Rejected because diagnostics are not a durable typed contract.

### D10. Compatibility and rollout are additive until explicit read semantics switch

Envelope v1, Rich/JSON/TOON bytes for existing friendly commands, Click exit codes and SDK method names remain stable. Snapshot schema advances to v3 for additive observation/CPU fields. The contract bundle starts at version 1. Catalogue migration adds only nullable recovery storage. Existing aliases bind to stable IDs without changing help paths.

The database read/reconcile behavior is the intentional semantic change: reads stop writing. Call sites that relied on implicit reconciliation migrate in the same implementation before the old writes are removed. Contract and generated-type fixtures detect accidental breaking schema changes; incompatible operation-contract evolution requires a new contract version.

### D11. Ponytail full gate

| Proposed mechanism | Why it is needed | Existing/minimal alternative checked | Decision |
|---|---|---|---|
| Production operation inventory | Runtime discovery and schema export cannot import tests | Reuse the current complete leaf classification and Click composition | Keep; move authority, do not add a parallel list |
| Wire-schema bundle | Consumers need actual aliases and nested DTO shapes | Reuse `msgspec` and envelope v1 encoding | Keep; no handwritten schemas |
| Local invoke + bounded JSONL session | Automation and same-snapshot approval need a no-prompt transport | Existing friendly CLI and `Command` remain underneath | Keep only two machine transports; no daemon |
| Operation context | One call currently rediscovering cwd/config/catalogue can disagree with itself | Extend existing `CliContext`/`ResolvedContext` | Keep invocation-scoped only; no global cache |
| Operation entry point | Installed Python packages must contribute operations without core/Go enumeration | Environment-facts entry points prove the loading pattern but not the contract | Keep one narrow group; no plugin manager/hot reload |
| Monitor section metadata | One-shot/batch callers need bounded selection and honest unknown/freshness | Extend existing monitor plan/cache and DTO | Keep; no new collector graph |
| Explicit DB reconcile | Current reads mutate audit state | Split existing code into observe then named reconcile | Keep; no generic repair framework |
| Structured replacement recovery | Current executable recovery evidence is hidden in `last_error` | Reuse existing typed context, journal and compensation | Keep one nullable field; no workflow engine/table |
| Custom Go generator | Not required when standard JSON Schema can drive a maintained tool | Pinned external `go-jsonschema` delivery tool | Remove |
| Telemetry/classifier/outbox | Owned by MYL-429 and not required for stable errors/events | Stable error/event DTOs only | Remove |
| Go/RPC daemon and remote dispatch | Deferred GitHub #136 | Explicit local invoke only | Remove |

Expected simplifications are measurable: one authoritative operation inventory instead of test plus runtime copies; zero reads that append database lifecycle events; zero recovery decisions parsed from new `last_error` values; zero Go domain-package lists; one context capture and at most one catalogue owner per finite invoke; no probe for an unselected monitor section.

## Risks / Trade-offs

- **Large initial contract surface** → Register every current leaf but enable local finite invoke only where a typed request/result and transport classification are complete; CI rejects unclassified additions.
- **Plugin import can execute trusted installed Python** → Discovery is limited to explicitly installed entry points in the selected interpreter, bounded and sanitized; untrusted-code sandboxing is explicitly not promised.
- **Schema generators may differ in supported JSON Schema features** → Keep the bundle within the pinned generator's tested subset, verify aliases/unions/nulls with committed fixtures, and treat bundle bytes as authority.
- **Approval timeout may strand resources** → No execution begins before approval; session teardown disposes the in-memory `Command` and invokes existing prepared-command cleanup only for resources actually acquired.
- **Changing database reads can miss required audit updates** → Inventory all current call sites, migrate startup/destructive/postcondition owners to explicit reconcile first, then remove implicit writes and add no-write spies.
- **Snapshot v3 affects strict consumers** → Preserve v2 fields, add explicit schema version and generated consumer fixture tests, and document unknown percentage semantics.
- **Catalogue migration encounters malformed legacy recovery text** → Nullable field defaults to absent; only explicit repair attempts known parsing and fails closed without mutation for unknown forms.
- **Overlap with MYL-409/411 or active output work** → Own only the cross-domain registry/transport/context boundary; consume their public operations and the existing output primitives rather than duplicating them.

## Migration Plan

1. Characterize the current Click tree, envelope bytes, DTO aliases, SDK primitive ownership, native/streaming behavior, database read writes and replacement recovery parsing.
2. Move the canonical leaf binding authority into production and keep all existing CLI behavior unchanged; add registry/schema validation and contract export.
3. Add typed request adapters, one-shot local invoke and operation-context capture for representative bounded reads, then the remaining eligible finite operations.
4. Add the same-process JSONL preview/approval protocol over existing `Command` objects and verify cancellation/timeout/cleanup.
5. Add the narrow provider entry point, conflict/version/missing-plugin handling and an isolated fixture plugin; generate and verify Go consumer fixtures outside runtime installation.
6. Add monitor section selection, snapshot v3 observation metadata and raw CPU counters while retaining existing watch behavior and platform memory semantics.
7. Introduce explicit database reconciliation, migrate every required mutation owner, prove ordinary reads are inert, and characterize backup reads.
8. Add nullable structured replacement recovery storage, write/read migration and legacy repair compatibility; remove new writes and decisions based on `last_error` payloads.
9. Run focused contract, schema, CLI/SDK, plugin, monitor, catalogue migration, database/recovery, redaction/security and packaging checks, followed by repository `make pr` and build gates.

Rollback is commit-wise. Registry/export/invoke changes can be reverted without data migration. Snapshot v3 additions can revert with consumers pinned to v2. The catalogue column is nullable and may remain unused on rollback; downgrade must preserve its bytes rather than erase recovery evidence. The explicit-read switch rolls back only together with its reconciler call-site migration.

## Open Questions

None. Operation IDs, local transport split, approval lifetime, provider boundary, schema source, monitor sampling semantics, reconciliation ownership, structured recovery storage and exclusions are fixed by this design.
