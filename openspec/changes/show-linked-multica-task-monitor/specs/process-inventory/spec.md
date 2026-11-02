## ADDED Requirements

### Requirement: Process inventory carries only a compact task annotation

`ProcessInventory` SHALL copy at most one `ExternalTaskSummary` from the same canonical snapshot into the existing main-checkout or environment `CheckoutProcessBlock`. `odcli ps` Rich SHALL render `Task <identifier> · <status-or-unknown> · <activity>` in that existing section's Details material. JSON and TOON SHALL project the same nested typed summary. The task SHALL NOT create a process contribution, shared block, daemon row, PID, metric, or second collector.

#### Scenario: Linked environment receives one annotation

- **WHEN** an environment block's source snapshot has a proven external-task summary
- **THEN** its existing section contains one compact task annotation and no additional process row or group

#### Scenario: Shared task does not create shared process ownership

- **WHEN** two environments link to the same issue
- **THEN** each existing environment block may show the same task summary, but no shared process block or run-to-checkout attribution is invented

#### Scenario: Machine output reuses the sample

- **WHEN** `odcli ps --format json|toon` emits a linked environment
- **THEN** it contains the same frozen external-task summary captured by the single monitor sample and performs no Multica read during rendering
