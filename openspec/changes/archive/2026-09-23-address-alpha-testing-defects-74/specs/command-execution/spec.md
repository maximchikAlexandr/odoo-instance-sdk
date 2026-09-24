## MODIFIED Requirements

### Requirement: One internal process pump serves every captured pipe path

The internal process pump SHALL drain `stdout` and `stderr` concurrently and continuously for every spawned long-running handle whose `inherit_stdio` is false. Each stream SHALL keep the last `_TIMEOUT_TAIL_BYTES = 8192` bytes after redaction. It SHALL attach that tail and the original error type to readiness/startup failures. Readers SHALL be terminated together with the owned process group on cleanup and interrupt. `DEVNULL` SHALL NOT be used. JSON/TOON SHALL remain one final document; drained bytes SHALL NOT be written to CLI stdout.

#### Scenario: Limited output crosses its bound

- **WHEN** `run_captured_limited()` receives a chunk that would exceed the configured stream limit
- **THEN** the common pump immediately terminates and reaps the child and returns the existing limit failure semantics

#### Scenario: Architecture inventory is checked

- **WHEN** the production process architecture is inspected by its regression gate
- **THEN** ordinary capture and limited capture are shown to use the same pipe, timeout, stdin, and cleanup implementation

#### Scenario: concurrent drain prevents pipe blockage

- **WHEN** a spawned long-running child writes more than the pipe capacity to `stdout` or `stderr`
- **THEN** the concurrent drain prevents the child from blocking

#### Scenario: bounded tail is attached to readiness failure

- **WHEN** readiness/startup fails for a spawned long-running handle
- **THEN** the bounded redacted tail and the original error type are attached to the failure

#### Scenario: readers terminate with the process group

- **WHEN** cleanup or interrupt runs
- **THEN** the child process group and the readers are terminated with no leaked threads or handles

#### Scenario: machine stdout stays a single document

- **WHEN** a long-running drain runs under JSON or TOON
- **THEN** one final document is emitted and child pipe bytes do not appear on CLI stdout
