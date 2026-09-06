## ADDED Requirements

### Requirement: Real-time observed captured output

When a caller opts into output observation for a captured process step, the single production process boundary SHALL emit sanitized stdout and stderr events while the child is still running, tagged with the captured `step_id`, while preserving the final captured `ProcessResult` and exit code. Before observer delivery, a stateful per-stream incremental decoder/redactor SHALL preserve all canonical structural detectors from `internal/proc/redaction.py`: password/token-style assignments, Bearer and Basic credentials, Authorization/Proxy-Authorization/Cookie/Set-Cookie headers, URI userinfo, and JWTs, plus configured/captured runtime secrets. It SHALL carry algorithmically sufficient detector state and withhold every unresolved candidate from its earliest possible prefix through its value until it can emit safe text or a redaction marker across arbitrary byte, decoder, and observer chunk boundaries. No ordered combination of observer events SHALL reconstruct any raw detected secret. Success, failure, timeout, and Ctrl-C SHALL resolve/redact incomplete candidates before safe flush. The final captured result SHALL retain the current canonical whole-value projection semantics. This SHALL NOT require a second process executor or caller-owned pipe reader.

#### Scenario: Output arrives before process completion

- **WHEN** a controlled process writes and flushes one line and then remains running
- **THEN** the observer receives the sanitized stream event before it receives the completed event
- **AND** the final result still contains the captured output and original exit code

#### Scenario: Canonical structural detector crosses chunks

- **WHEN** any canonical assignment, credential scheme, sensitive header, URI-userinfo, or JWT detector has a significant split within its prefix or value across byte, decoder, or observer chunks
- **THEN** the redactor withholds the unresolved candidate and no observer event sequence exposes fragments that reconstruct its raw value
- **AND** safe surrounding text and the redaction marker are eventually emitted in order

#### Scenario: Captured runtime secret crosses chunks

- **WHEN** a configured or captured runtime secret is split at any significant byte or decoder boundary
- **THEN** no observer event sequence exposes fragments that reconstruct the secret and safe surrounding text is emitted in order

#### Scenario: Incomplete candidate reaches terminal flush

- **WHEN** success, failure, timeout, or Ctrl-C occurs while a structural candidate or multibyte character is incomplete
- **THEN** final flush redacts or safely resolves the retained candidate without emitting reconstructable raw fragments

#### Scenario: Final capture keeps canonical semantics

- **WHEN** observed output contains any value recognized by the current canonical whole-value projection
- **THEN** the final captured projection redacts it with the unchanged canonical semantics independently of observer chunking

#### Scenario: Machine command does not opt in

- **WHEN** a bounded JSON or TOON command executes without output observation
- **THEN** no stream event is written to stdout or stderr by the output renderer
- **AND** stdout remains one final document

### Requirement: Bounded timeout diagnostics

A timed-out captured process SHALL terminate and reap through `internal/proc`, and its typed timeout failure SHALL retain elapsed time plus bounded sanitized tails for available stdout and stderr with independent truncation indicators. The retained tail SHALL preserve the newest diagnostic output rather than allowing an arbitrarily long startup prefix to hide the final cause.

#### Scenario: Timeout after long startup output

- **WHEN** a process exceeds its timeout after writing more output than the configured diagnostic bound
- **THEN** the failure reports elapsed time, the bounded newest stdout/stderr tails, and truncation status
- **AND** the process and owned descendants are not left running

#### Scenario: Timeout tail contains a secret

- **WHEN** timed-out output contains a captured secret
- **THEN** the exception, observer failure event, Rich diagnostic, and machine error details contain only the redacted value

### Requirement: Captured stdin is delivered exactly without deadlock or disclosure

The single production process pump SHALL write the immutable `PreparedStep.stdin` byte snapshot to child stdin without decoding, normalization, logging, observer delivery, or inclusion in captured output. It SHALL write stdin concurrently with draining stdout and stderr, close stdin after all bytes are written, and close stdin during early exit, timeout, interruption, or write failure without leaving writer, reader, child, or owned descendants running.

#### Scenario: Large stdin and output proceed concurrently

- **WHEN** a captured child emits enough stdout or stderr to fill a pipe before consuming a large `PreparedStep.stdin` snapshot
- **THEN** the pump concurrently drains output and delivers the exact stdin bytes, closes stdin, and completes without deadlock
- **AND** no stdin byte is emitted to the observer or projected output

#### Scenario: Child exits before consuming stdin

- **WHEN** a child exits while the pump is writing the captured stdin snapshot
- **THEN** the pump closes stdin and both output pipes, reaps the child, and preserves the established process-result semantics without hanging

#### Scenario: Timeout while stdin remains

- **WHEN** the captured timeout expires before a child consumes all stdin
- **THEN** the pump stops and closes the stdin writer, terminates and reaps the exact owned process group, finishes pipe cleanup, and returns the bounded sanitized timeout diagnostics

### Requirement: One internal process pump serves every captured pipe path

Ordinary observed/timeout capture and the existing limited-output API SHALL delegate to one common pump inside `internal/proc`. `run_captured_limited()` SHALL preserve immediate child termination when either output stream would exceed its configured byte limit. `internal/proc` SHALL NOT contain a second pipe-drain, timeout, or process-cleanup algorithm.

#### Scenario: Limited output crosses its bound

- **WHEN** `run_captured_limited()` receives a chunk that would exceed the configured stream limit
- **THEN** the common pump immediately terminates and reaps the child and returns the existing limit failure semantics

#### Scenario: Architecture inventory is checked

- **WHEN** the production process architecture is inspected by its regression gate
- **THEN** ordinary capture and limited capture are shown to use the same pipe, timeout, stdin, and cleanup implementation

### Requirement: Logical progress corresponds to completed effects

The existing command run context SHALL emit start, progress, completion, and failure events for planned process and action steps. An action completion event SHALL be emitted only after that action's effect and required postcondition finish; consuming an action through `RunContext.action()` alone SHALL NOT mark it completed. Progress events MAY report completed units and a total only when the total is reliable, and SHALL NOT imply effect completion.

#### Scenario: Action fails after it starts

- **WHEN** a planned action starts and its effect raises before its postcondition
- **THEN** observers receive started then failed for that action and never completed

#### Scenario: Reliable byte total

- **WHEN** an action reports received bytes and a trustworthy total byte count
- **THEN** the observer may derive a percentage from those byte units

#### Scenario: Unknown duration

- **WHEN** a long action has no trustworthy total
- **THEN** its events expose status and elapsed time without a percentage or synthetic time estimate

### Requirement: Interrupted bounded execution closes observation

When a bounded command is interrupted, the process boundary and run context SHALL close all owned processes, response/file handles, and started progress steps before propagating the interrupt. Observer and renderer cleanup SHALL NOT replace exit code 130 or the operation's typed retained-resource context.

#### Scenario: Ctrl-C during a captured step

- **WHEN** Ctrl-C interrupts a bounded command while a captured process or action is running
- **THEN** the active step is closed as failed/interrupted, owned resources are cleaned up, and the CLI exits 130
- **AND** no live renderer control sequence contaminates a final machine document
