## ADDED Requirements

### Requirement: Failure envelopes preserve resolved invocation mode

Every bounded CLI success or failure envelope SHALL report the resolved invocation's actual `dry_run` value. The shared failure boundary SHALL require the caller's resolved mode rather than defaulting it, and all dry-run-capable sibling commands using that boundary SHALL pass the same mode used to build or execute their plan. This change SHALL NOT weaken a precondition, execute a dry-run plan, change an error code, or change an exit code.

#### Scenario: Dry-run precondition failure

- **WHEN** a dry-run command fails during planning or precondition validation before any process starts
- **THEN** its machine failure envelope contains `dry_run: true` and preserves the existing error and exit status

#### Scenario: Normal execution failure

- **WHEN** the equivalent non-preview invocation fails
- **THEN** its machine failure envelope contains `dry_run: false`

#### Scenario: Successful preview and failed preview agree

- **WHEN** parameterized JSON and TOON tests exercise a successful plan and a genuine precondition failure for the same dry-run-capable family
- **THEN** both documents preserve `dry_run: true` without executing a child process
