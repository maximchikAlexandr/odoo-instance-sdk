## ADDED Requirements

### Requirement: Odoo HTTP calls go through OdooHttpClient

All production HTTP calls to the Odoo origin SHALL go through `OdooHttpClient`. `database`, `health`, and `monitor` SHALL depend on the service client and SHALL NOT import `httpx` or reference `httpx` types/exceptions in their public interfaces. CLI commands SHALL NOT import `OdooHttpClient`. Each resource operation SHALL construct and close its client; there SHALL NOT be a process-wide shared singleton. Public results, error codes, and exit behaviour SHALL be preserved. The managed session SHALL reuse connections within its lifecycle, SHALL be deterministically closed on success/exception/cancellation, and SHALL NOT transfer cookies/Authorization between origins or credential contexts. Streaming, timeout, cancellation, and cleanup for backup/restore SHALL be preserved; large payloads SHALL NOT be buffered whole by the wrapper. No retry/backoff/circuit-breaker SHALL be added; each previously-single operation SHALL keep exactly one network attempt.

#### Scenario: database uses OdooHttpClient

- **WHEN** the database resource makes an HTTP call to the Odoo origin
- **THEN** it goes through `OdooHttpClient` and the resource does not import or reference `httpx`

#### Scenario: streaming backup is preserved

- **WHEN** a large backup/restore streams through the client
- **THEN** streaming, timeout, cancellation, and cleanup are preserved and the payload is not buffered whole

#### Scenario: one network attempt per operation

- **WHEN** a previously-single operation fails with a transport or status error
- **THEN** exactly one network attempt is made and no retry or backoff is added