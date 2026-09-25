## ADDED Requirements

### Requirement: Environment-bound detached launch supports readiness waiting

The public detached launch operation MUST accept opt-in readiness waiting and a positive timeout that defaults to 60 seconds when enabled. The immutable command snapshot MUST represent readiness and failure cleanup as actions and MUST reuse the existing process boundary, runtime identity, and health probe.

#### Scenario: Detached launch becomes ready

- **WHEN** readiness waiting is enabled for an environment-bound launch and the process remains alive with the expected environment/database binding until the health probe succeeds
- **THEN** the operation returns the established bounded detached result with readiness confirmed

#### Scenario: Readiness fails

- **WHEN** the process exits early, has an invalid binding, or misses the readiness deadline
- **THEN** the operation terminates the process it started, clears its runtime identity, and returns the established typed error

#### Scenario: Failure cleanup cannot stop the process

- **WHEN** readiness fails and termination cannot be confirmed
- **THEN** the error reports the surviving owned process and cleanup failure
- **AND** its recovery identity is retained instead of falsely reporting successful cleanup

#### Scenario: Another process occupies the endpoint

- **WHEN** the health endpoint responds but belongs to a different process or database binding
- **THEN** launch is not reported ready and no unrelated process is terminated

#### Scenario: Readiness is disabled

- **WHEN** a caller performs detached launch without readiness waiting
- **THEN** existing launch behavior and result compatibility remain unchanged
