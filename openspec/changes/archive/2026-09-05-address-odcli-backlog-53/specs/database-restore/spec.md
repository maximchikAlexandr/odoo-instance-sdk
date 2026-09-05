## ADDED Requirements

### Requirement: Observable restore-plan execution

Restore execution SHALL emit typed lifecycle events for logical plan-step start, completion, and failure. When explicitly requested, process-backed steps SHALL also emit sanitized stdout/stderr chunks associated with that step. Event observation SHALL not change plan contents, execution order, return values, exit codes, or captured subprocess output, and secrets SHALL be redacted before an event reaches a consumer.

#### Scenario: Step lifecycle is observable
- **WHEN** a multi-step restore executes with an observer
- **THEN** each executed logical step produces ordered start and terminal events with stable step identity

#### Scenario: Streaming is opt-in and redacted
- **WHEN** command-output streaming is enabled and a restore child writes stdout or stderr containing a secret
- **THEN** the consumer receives step-associated sanitized chunks while the final captured result preserves its existing contract
