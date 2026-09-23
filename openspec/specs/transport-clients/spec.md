# transport-clients Specification

## Purpose
TBD - created by archiving change address-alpha-testing-defects-74. Update Purpose after archive.

## Requirements

### Requirement: Narrow internal transport layer

A narrow internal transport layer SHALL live in `src/odoo_instance_sdk/internal/transport/` with one base HTTP wrapper over `httpx` and exactly one subclass `OdooHttpClient`. Application resources (`database`, `health`, `monitor`) SHALL depend on `OdooHttpClient`, not on `httpx`. CLI commands SHALL NOT import `OdooHttpClient`; they SHALL call public SDK resource primitives. The base client SHALL own only common transport mechanics: managed `httpx.Client` with connection pooling, context-manager/`close()` lifecycle, constructor injection for tests, no global mutable singleton, no cross-origin cookie/auth reuse, preserved timeout and streaming semantics, structured redacted logging, and conversion of connect/read/write/timeout/protocol/status/stream errors into existing typed domain errors without leaking `httpx` exceptions. The DI seam SHALL be a concrete protocol with typed methods, not `Any`, `object`, or `Callable[..., ...]`. `OdooHttpClient` SHALL NOT be exported as a public SDK type and SHALL NOT appear on public resource constructor signatures. Tests inject via an internal factory.

The transport layer SHALL be lazily imported. `httpx` SHALL remain absent after `import odoo_instance_sdk.cli`. Production code SHALL NOT contain direct `import httpx`, `from httpx ...`, `httpx.get/request/stream`, or `httpx.Client` creation outside files listed line-by-line in `architecture_inventory.py` under `internal/transport/`. `httpx` types SHALL NOT leak into public SDK/resource interfaces.

#### Scenario: resources depend on the service client

- **WHEN** `database`, `health`, and `monitor` make an HTTP call to the Odoo origin
- **THEN** they depend on `OdooHttpClient` and do not import or reference `httpx` types or exceptions

#### Scenario: architecture gate bans bare httpx

- **WHEN** the architecture/inventory gate runs on `src/`
- **THEN** direct `import httpx`, `httpx.get/request/stream`, and `httpx.Client` creation outside the allowlisted transport modules are rejected

#### Scenario: httpx stays absent at CLI import time

- **WHEN** `import odoo_instance_sdk.cli` runs in a fresh interpreter
- **THEN** `httpx` is absent and the startup-evidence gate is preserved

#### Scenario: injected client is used in tests

- **WHEN** a unit test substitutes the transport boundary
- **THEN** the test injects the client/transport factory and does not patch global `httpx.Client` or `httpx.get`

### Requirement: `OdooHttpClient` serves one Odoo origin

`OdooHttpClient` SHALL serve all Odoo HTTP endpoints for one origin: health/status, database list/create/drop/backup/restore, and other existing Odoo HTTP endpoints. One Odoo origin SHALL use one client type and one transport contract; the Odoo API SHALL NOT be split into a client per command or resource. New subclasses SHALL be created only for genuinely distinct external services.

`OdooHttpClient` SHALL own Odoo-specific JSON/HTML/error payload parsing and safe error transformation. Each resource operation SHALL construct a client for a bounded lifecycle, SHALL support context manager/`close()`, SHALL close it on success, exception, and cancellation, and SHALL reuse connections only within that lifecycle. There SHALL NOT be a process-wide shared singleton. Cookies, auth, and session state SHALL be reused only within one Odoo origin and one agreed credential context; they SHALL NOT be transferred between origins, projects, or test instances. Streaming responses SHALL NOT be closed before the consumer and SHALL be closed on error/interruption. A mutable sync-client SHALL NOT be shared between concurrent operations. Async transport SHALL NOT be introduced without a separate need.

#### Scenario: one client type for one Odoo origin

- **WHEN** `database`, `health`, and `monitor` interact with the same Odoo origin
- **THEN** each resource operation constructs and closes its own `OdooHttpClient` for that origin; there is no process-wide shared singleton

#### Scenario: CLI does not import the client

- **WHEN** a CLI command needs Odoo HTTP
- **THEN** it calls a public SDK resource primitive and does not import `OdooHttpClient`

#### Scenario: streaming backup is not buffered whole

- **WHEN** a large backup/restore streams through `OdooHttpClient`
- **THEN** the streaming response is not closed before the consumer and is closed on error/interruption; the payload is not buffered whole

### Requirement: Centralized redacted logging and no retry

The base transport and `OdooHttpClient` SHALL centrally log structured request start/completion on the existing logging boundary: service, operation, HTTP method, safe origin/path, status, elapsed time, and result class. They SHALL NOT log request/response body, passwords, cookies, Authorization, master password, session id, tokens, or sensitive query values; existing redact/sanitize helpers SHALL be reused.

Connect/read/write/timeout/protocol/status/stream errors SHALL be centrally converted into existing typed domain/SDK errors without leaking `httpx` exceptions. Diagnostically useful causes SHALL be preserved via exception chaining. Already-published error codes, messages, and exit behaviour SHALL NOT change without a separate requirement. Odoo-specific JSON/HTML/error payload parsing SHALL happen in `OdooHttpClient`, not in the base client.

Automatic retry SHALL NOT be added. An operation that previously performed one network attempt SHALL continue to perform exactly one. Existing retry SHALL only be preserved with regression evidence; the retryable status/exception list SHALL NOT be expanded and backoff SHALL NOT be added as a side effect. Non-idempotent create/drop/restore/password/module operations SHALL NOT be retried.

#### Scenario: no secrets in logs

- **WHEN** a request is logged
- **THEN** the log contains service, operation, method, safe origin/path, status, elapsed time, and result class, and no body, password, cookie, Authorization, master password, session id, token, or sensitive query value

#### Scenario: one network attempt per operation

- **WHEN** a previously-single operation fails with a transport or status error
- **THEN** exactly one network attempt is made and no retry or backoff is added

#### Scenario: httpx exceptions do not leak

- **WHEN** a connect/timeout/status/malformed/stream failure occurs
- **THEN** the caller receives an existing typed domain/SDK error and the `httpx` exception is not part of the public interface

### Requirement: XML-RPC stays in test support

Production `src/` SHALL contain zero `xmlrpc.client.ServerProxy` call sites. Real-E2E XML-RPC SHALL use a helper next to `tests/integration/real_odoo/test_critical_path.py`; tests SHALL NOT assemble `ServerProxy` inline. A production `OdooXmlRpcClient` SHALL NOT be added.

#### Scenario: src has no ServerProxy

- **WHEN** the architecture gate runs on `src/`
- **THEN** `xmlrpc.client.ServerProxy` is absent

#### Scenario: E2E uses the test-support helper

- **WHEN** the critical-path probe needs XML-RPC
- **THEN** it calls the test-support helper and does not construct `ServerProxy` in the test body

### Requirement: Characterization tests confirm no behavioural change

Before the transport refactor, characterization tests SHALL record existing Odoo HTTP scenarios. After the refactor, the same tests SHALL confirm no behavioural change in public results, error codes, exit behaviour, streaming semantics, and redaction. XML-RPC characterization SHALL stay on the test-support helper and SHALL NOT require a production client.

#### Scenario: HTTP characterization is stable across the refactor

- **WHEN** the HTTP characterization tests run before and after the transport centralization
- **THEN** public results, error codes, exit behaviour, streaming semantics, and redaction are unchanged
