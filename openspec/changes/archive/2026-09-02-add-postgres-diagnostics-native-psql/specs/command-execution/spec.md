## MODIFIED Requirements

### Requirement: Deadline-bound attempts are plan-visible

When an operation runs multiple captured process attempts under one monotonic
budget, its `ExecutionPlan.observations` SHALL contain a frozen,
serializable private deadline observation with the attempt step IDs and total
budget. The observation SHALL describe the stable budget only; a
per-invocation monotonic start time SHALL remain private run state. The shared
process boundary SHALL compute each attempt's remaining timeout from that
single deadline and SHALL receive the original captured process step together
with explicit deadline context, never a substituted `PreparedStep`. This
observation is plan-visible but is not part of the public SDK model vocabulary.

#### Scenario: Deadline policy remains visible while runtime controls vary

- **WHEN** a recording executor runs two inspected attempts under one shared deadline
- **THEN** the public plan identifies both step IDs and the total budget
- **AND** the ledger and recording executor receive the exact captured steps from that plan
- **AND** the subprocess timeout and server statement timeout use no more than the current monotonic remainder
- **AND** an exhausted or sub-millisecond remainder starts no process
