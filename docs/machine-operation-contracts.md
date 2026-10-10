# Machine-operation contracts

`odoo_instance_sdk.operations.PUBLIC_LEAF_CASES` is the production-owned
inventory for CLI leaves, aliases, stable namespaced operation IDs, SDK
primitive ownership, transport class, request/result/error DTOs, and exit
policy. Characterization and real-Odoo evidence tables add invocation data but
are not a second runtime registry.

## Export and local invocation

Contract metadata is read-only and works outside an initialized project:

```bash
odcli contract export --format json
odcli operation invoke odcli.contract.export
```

The export is a deterministic contract-version-1 JSON Schema bundle. Schemas
come from the same `msgspec` projection used for runtime values, so aliases,
tagged unions, defaults, nullability, and unknown-field rejection are wire
contracts. The finite invoke command emits exactly one envelope-v1 document;
it does not prompt, construct a daemon/RPC client, or add diagnostics to
stdout. Domain-negative results remain successful transport documents with a
typed status field.

Previewable mutations use one bounded JSONL session:

```bash
odcli operation session odcli.env.create
```

The session retains one private `Command`, emits only its redacted plan and
fingerprint, and executes only after one matching approval. Cancel, EOF,
timeout, stale approval, interruption, and output-limit failures are typed
non-execution outcomes. Native TTY, interactive, and streaming leaves keep
their direct CLI behavior; finite invoke rejects them with the required
transport instead of buffering them into a fabricated document.

## Installed providers and consumer types

Trusted installed Python packages may expose a finite tuple of complete
bindings through the `odoo_instance_sdk.operations` entry-point group. A
provider ships its descriptor, concrete DTOs, and factory together. Discovery
is once-per-process, deterministic, deadline-bounded, version-checked, and
sanitized. There is no plugin install/update lifecycle, hot reload, dependency
resolver, remote download, or untrusted-code sandbox in this contract.

Consumer types are delivery artifacts generated from the exported bundle. The
pinned Go fixture under `tools/go-consumer/` consumes that schema and compiles
without adding a Go runtime dependency to the Python package or a Go domain
operation list to the SDK. Normal installation and local invocation never run
the Go generator.

## Monitor and database semantics

Finite monitor requests select catalogue, runtime, Git, storage, artifact,
PostgreSQL, and Docker sections before probes run. Snapshot-v3 preserves the
v2 fields and adds one UTC observation boundary, requested/completed/unknown
sections, per-section freshness/completeness, and bounded reasons for partial
data. Process metrics publish confirmed PID/create-time identity, raw
cumulative CPU seconds, and sample time; a percentage is present only when a
compatible previous sample exists. Unrequested sections are not probes and
are distinct from requested-but-unknown sections.

Database and backup reads are observational. `list`, `exists`, `current`,
monitor, inventory, and backup polling do not append catalogue events or
backfill state. Lifecycle owners that need audit changes call the explicit
typed `reconcile_databases_command` after proving absence; execution
revalidates cluster identity, tracked evidence, and live absence under the
existing transaction and lock. Replacement repair stores bounded,
secret-free `CopyReplacementRecovery` in nullable `recovery_json`; new
`last_error` diagnostics are never executable input. Known legacy evidence is
adopted only by explicit repair and fails closed when malformed or
contradictory.

## Compatibility and versioning

Envelope-v1 bytes, friendly CLI paths, aliases, SDK method names, exit codes,
native transports, and existing safety gates remain stable. Snapshot-v3 is
additive over v2. The operation-contract version is independent from envelope
and catalogue versions; removing an operation or changing a public schema,
parameter, alias, transport, or exit policy requires an explicit contract
version change. Repeated exports with the same installed operation set are
byte-identical, and the catalogue migration adds only nullable recovery
storage through the existing single Alembic lineage.
