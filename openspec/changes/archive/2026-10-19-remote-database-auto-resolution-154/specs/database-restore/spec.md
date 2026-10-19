## MODIFIED Requirements

### Requirement: Generated configuration publication preserves captured Git validation

Main-checkout Compose restore SHALL capture the exact read-only Git checks for the generated configuration during planning and SHALL consume that immutable snapshot before changing the project default. Execution SHALL reject a tracked target, failed check, changed repository root, or changed captured argv before any configuration or default publication; the result SHALL remain sanitized.

#### Scenario: Compose project default switch validates the generated configuration through captured Git steps

- **WHEN** a main-checkout restore switches a Compose project default and an existing generated configuration belongs to a Git checkout
- **THEN** the immutable plan contains the exact read-only Git checks and execution consumes them before publication

#### Scenario: Captured Git failure blocks publication

- **WHEN** the captured check fails or the repository root/argv differs at execution
- **THEN** restore fails closed without changing the project default or exposing secrets
