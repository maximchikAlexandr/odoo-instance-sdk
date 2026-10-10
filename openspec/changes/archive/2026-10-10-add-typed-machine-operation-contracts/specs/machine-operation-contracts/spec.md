## ADDED Requirements

### Requirement: Stable versioned operation identity

Every discoverable operation SHALL have one stable namespaced operation ID, a contract version and exactly one canonical binding to its implementation. A CLI rename or alias SHALL retain the same operation ID. IDs SHALL NOT be derived at runtime from display labels or package import paths.

#### Scenario: Alias preserves identity

- **WHEN** canonical and alias CLI paths for one operation are discovered
- **THEN** both paths reference the same operation ID, request/result schemas and implementation binding

#### Scenario: Duplicate identity fails closed

- **WHEN** two installed bindings declare the same operation ID or the same CLI path for different operations
- **THEN** registry construction fails with a bounded sanitized conflict error before either implementation can run

### Requirement: Single production inventory

Production `PUBLIC_LEAF_CASES` SHALL be the only canonical CLI leaf and operation binding inventory. Contract export, runtime discovery, Click-tree validation and characterization tests SHALL consume that inventory; production code SHALL NOT import tests and SHALL NOT maintain a second command/operation table.

#### Scenario: New unclassified leaf is rejected

- **WHEN** the composed Click tree contains a new leaf absent from `PUBLIC_LEAF_CASES`
- **THEN** the contract gate fails and no contract bundle is published

#### Scenario: Stale inventory entry is rejected

- **WHEN** `PUBLIC_LEAF_CASES` references a missing Click path or public SDK primitive
- **THEN** registry validation fails with the exact stale binding identified

### Requirement: Complete operation descriptor

Each operation descriptor SHALL publish stable ID, contract version, provider, canonical CLI path and aliases, request and result schema references, stable error variants, parameter required/default metadata, context policy, preview/approval capability, cancellation policy, exit mapping and exactly one transport class: finite document, bounded JSONL session, native TTY, JSONL stream or interactive. Domain-negative results SHALL identify their typed status field and SHALL NOT be described as transport errors.

#### Scenario: Descriptor covers a bounded mutation

- **WHEN** a consumer discovers a previewable database mutation
- **THEN** its descriptor identifies its request/result/error schemas, approval requirement, finite/session transports and exit semantics without executing the operation

#### Scenario: Domain failure stays in result schema

- **WHEN** a test, Git check or validation operation completes transport successfully with a negative domain outcome
- **THEN** the envelope remains `ok=true` and the typed result status expresses the negative outcome documented by the descriptor

### Requirement: Wire schemas follow actual DTO projection

Request, result and error schemas SHALL be generated from concrete frozen DTOs through the same `msgspec` metadata and JSON-safe projection used by runtime output. Wire aliases, tagged unions, defaults, nullability, collection order and unknown-field rejection SHALL match encoded values rather than Python attribute spelling. Untyped arbitrary JSON SHALL NOT replace concrete extensible payload DTOs.

#### Scenario: Aliased field is exported

- **WHEN** the schema for `DepsMissingImport` is exported
- **THEN** it contains the wire field `import` and does not expose `import_name`

#### Scenario: Schema and encoded fixture agree

- **WHEN** a representative DTO containing nested, repeated, optional and tagged values is encoded and validated against the exported schema
- **THEN** validation succeeds without a schema-only rename or conversion table

### Requirement: Deterministic contract bundle and consumer generation

`odcli contract export --format json` SHALL emit one deterministic versioned JSON Schema bundle for all successfully registered operations without importing unselected domain implementations or performing domain I/O. Consumer type generation, including Go, SHALL consume that exact bundle through pinned delivery tooling and SHALL NOT be required to install or discover a Python operation plugin.

#### Scenario: Repeated export is byte-stable

- **WHEN** the installed operation set and contract versions are unchanged
- **THEN** two exports have identical UTF-8 bytes and deterministic operation/schema ordering

#### Scenario: Go types come from exported bundle

- **WHEN** the pinned Go consumer generator runs in verification
- **THEN** its generated types compile against representative request, result, error and alias fixtures from the exported bundle
- **AND** normal OdCLI or plugin installation never invokes Go tooling

### Requirement: Local invocation is distinct from dispatch

`odcli operation invoke <operation-id>` SHALL execute only the operation installed in the selected local Python interpreter. It SHALL NOT contact, start or re-enter a daemon/coordinator. This change SHALL NOT add a remote dispatch command; any future dispatch adapter SHALL be distinct from local invoke.

#### Scenario: Local invoke has no coordinator recursion

- **WHEN** a finite operation is invoked by stable ID
- **THEN** the selected local factory is called directly once and no dispatch, daemon or RPC client is constructed

#### Scenario: Native operation requires its transport

- **WHEN** finite local invoke targets a native TTY, unbounded JSONL stream or interactive-only operation
- **THEN** it fails before execution with `transport_required` and reports the supported direct CLI transport

### Requirement: One captured operation context

Each invocation SHALL capture cwd, explicit project/environment selectors, provenance, selected interpreter/package set, resolved configuration and catalogue ownership once into a frozen operation context. Operation factories SHALL consume that context and SHALL NOT independently re-resolve cwd, project, environment or open a second catalogue during the same invocation. The public context projection SHALL exclude secrets, handles and absolute internal paths.

#### Scenario: Explicit selector stays authoritative

- **WHEN** an invocation supplies an explicit project or environment and cwd points elsewhere
- **THEN** every operation phase uses the captured explicit identity and provenance without cwd fallback

#### Scenario: Context does not replace revalidation

- **WHEN** Git/config/runtime/process/filesystem/database state changes after planning but before execution
- **THEN** existing execution-time preconditions detect the change and fail safely despite the captured context

### Requirement: Bounded local machine output

Finite document invocation SHALL emit exactly one existing envelope-v1 JSON document to stdout. Bounded JSONL session invocation SHALL emit only typed contract-versioned records to stdout. Both transports SHALL be prompt-free, ANSI-free and progress-noise-free; sanitized diagnostics SHALL use stderr or typed error records as defined by the transport. Output shall be bounded by declared record and byte limits.

#### Scenario: Finite read is one document

- **WHEN** a bounded read succeeds through local invoke
- **THEN** stdout contains exactly one envelope-v1 document, stderr contains only sanitized diagnostics, and the documented exit code is returned

#### Scenario: Output limit is enforced

- **WHEN** an implementation attempts to exceed its declared finite or event-stream limit
- **THEN** the transport terminates with a typed bounded-output error without leaking the truncated raw payload

### Requirement: Installed Python operation providers

The selected interpreter SHALL discover operation providers only from the `odoo_instance_sdk.operations` entry-point group. A provider SHALL ship descriptor, concrete request/result/error DTOs and factory together, declare a compatible operation-contract version and return a finite binding tuple. Discovery SHALL be deterministic, deadline-bounded and sanitized. Core SHALL NOT enumerate provider domain packages.

#### Scenario: Installed provider becomes discoverable

- **WHEN** a compatible provider entry point is installed in the selected interpreter
- **THEN** its operation appears in discovery/export and is locally invocable without a core or Go source change

#### Scenario: Missing requested plugin is bounded

- **WHEN** a caller invokes an operation ID whose provider is absent
- **THEN** invocation returns `operation_unavailable` without importing arbitrary packages, prompting or scanning other interpreters

#### Scenario: Incompatible provider fails closed

- **WHEN** a provider declares an unsupported contract version or incomplete binding
- **THEN** registry construction rejects that provider with a sanitized typed error before any provider operation runs

### Requirement: No plugin lifecycle framework

Operation discovery SHALL occur once per process from installed trusted Python entry points. The SDK SHALL NOT implement hot reload, plugin installation/removal/update, dependency resolution, remote code download or untrusted-code sandboxing in this change.

#### Scenario: Installed set is stable for one process

- **WHEN** package installation changes after registry construction
- **THEN** the current process retains its original registry and a new process is required to observe the new installed set

### Requirement: Representative end-to-end contract proof

Verification SHALL cover at least one bounded read, one previewable mutation and one existing streaming or interactive transport. It SHALL assert schema aliases, request defaults/required flags, domain-negative result semantics, no prompt/ANSI/stdout pollution, redaction, approval snapshot parity, cancellation and native transport preservation.

#### Scenario: Representative matrix passes

- **WHEN** the contract proof suite runs against an isolated installed package
- **THEN** all three transport classes satisfy their descriptor, wire schema, privacy and exit contracts without a second executor or inventory
