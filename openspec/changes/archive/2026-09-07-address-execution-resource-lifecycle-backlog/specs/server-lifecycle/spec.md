## ADDED Requirements

### Requirement: Scripted shell transaction finalization

Every existing consumer of the shared scripted Odoo shell wrapper SHALL use one transaction-finalization contract. Successful user code SHALL commit only when `commit=True` and otherwise SHALL roll back. Failed user code SHALL never commit and SHALL attempt rollback. A commit or rollback failure SHALL be a non-successful result and SHALL remain distinguishable from a user-code failure after existing sanitization. The contract SHALL NOT claim to undo explicit commits performed by user code or external non-transactional effects.

#### Scenario: User code fails with commit requested

- **WHEN** scripted user code performs a conditional write, raises, and the caller requested commit
- **THEN** the wrapper calls rollback and does not call commit
- **AND** the process and bounded envelope report a user-code failure

#### Scenario: Commit fails after successful user code

- **WHEN** user code succeeds but transaction commit raises
- **THEN** the operation fails as transaction finalization rather than returning `ok=true`
- **AND** the sanitized commit failure remains available to Rich and machine projections

#### Scenario: Rollback fails after user-code failure

- **WHEN** user code raises and the required rollback also raises
- **THEN** the operation reports the user-code failure and a distinct rollback-finalization failure
- **AND** neither failure is suppressed

#### Scenario: Successful transaction modes remain compatible

- **WHEN** user code and the requested commit or rollback succeed
- **THEN** the wrapper returns the existing successful framed result with `transaction=commit` or `transaction=rollback`

#### Scenario: All scripted consumers share the wrapper

- **WHEN** eval, exec, module update/test, translations export, administrator reset, or database preparation executes scripted Odoo code
- **THEN** each operation uses this same wrapper and transaction outcome classification
