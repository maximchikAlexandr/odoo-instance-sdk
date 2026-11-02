## ADDED Requirements

### Requirement: Proven external-task binding survives restart

Core SHALL persist at most one private `ExternalTaskBinding` on an environment catalogue row only when caller-owned adoption receives complete verified evidence. The binding SHALL contain provider, server, workspace, issue, task identifier, safe permalink, canonical checkout path, runtime identity, and daemon identity; the environment UUID SHALL remain the mapping key. It SHALL NOT contain credentials, profile tokens, run IDs, task roots, arbitrary metadata, status history, or agent telemetry.

WHEN a ready idempotent adoption is reused with a newly complete proof and no stored binding, THEN the same adoption operation SHALL attach the binding. WHEN a binding already exists, THEN reuse SHALL require exact equality of provider/server/workspace/issue/path/runtime/daemon identity and SHALL refuse replacement on conflict. Core SHALL expose no bind/list/update/delete CLI or SDK workflow.

#### Scenario: Successful prepare persists proof

- **WHEN** optional integration preparation verifies one task checkout and successfully creates or reuses its caller-owned environment
- **THEN** the environment row contains the matching private external-task binding and the association remains available after the originating run and monitor process end

#### Scenario: Failed prepare records no association

- **WHEN** context verification or adoption fails before success
- **THEN** no new external-task binding is committed

#### Scenario: Conflicting proof cannot replace a binding

- **WHEN** a ready environment already names one external task and a later adoption supplies a different server, workspace, issue, path, runtime, or daemon identity
- **THEN** adoption fails closed and preserves the original binding

#### Scenario: Legacy environment is not guessed

- **WHEN** an existing environment has no verified external-task binding
- **THEN** branch, basename, repository name, and path similarity do not create a task association

### Requirement: Frozen external-task observation has three independent values

The SDK SHALL expose frozen unknown-field-forbidden `ExternalTaskBinding` and `ExternalTaskSummary` models. The public summary SHALL contain only provider, task identifier, safe URL, nullable current issue status, `active | inactive | unknown` activity, observation time, `current | unavailable` freshness, and a bounded safe unavailability reason. Private binding identity SHALL NOT be serialized into `Snapshot`, `CheckoutInventory`, `ProcessInventory`, CLI output, HTTP, tooltip text, or browser state.

Issue status, activity, and Odoo runtime state SHALL remain independent. Activity SHALL describe the linked issue as a whole and SHALL NOT claim that a running agent belongs to the displayed checkout. Multiple proven environments for one issue MAY therefore show the same task observation.

#### Scenario: Running Odoo does not imply active task

- **WHEN** Odoo is running but the complete external-task observation finds no executing run
- **THEN** Odoo remains running and task activity is `inactive`

#### Scenario: Stopped Odoo retains task observation

- **WHEN** a linked environment's Odoo process is stopped
- **THEN** its task identifier, link, status, and independently observed activity remain present

#### Scenario: Public summary omits private binding

- **WHEN** a summary is serialized through SDK, CLI, or HTTP
- **THEN** it contains no server identity, workspace ID, canonical local path, runtime ID, daemon ID, credentials, or token

### Requirement: Activity mapping fails closed

For Multica observations, any returned run whose upstream status is exactly `running` SHALL yield `active`. A complete run-history page containing no `running` item and only `queued`, `dispatched`, `waiting_local_directory`, `completed`, `failed`, or `cancelled` SHALL yield `inactive`, including a complete empty page. Queued, dispatched, and waiting-local-directory states SHALL NOT by themselves yield `active`.

An incomplete page without a `running` item, an unknown run status, timeout, authorization/transport/decode error, or absent completeness evidence SHALL yield `unknown`. A found `running` item SHALL remain sufficient for `active` even when older history is truncated. The observation SHALL NOT cache or replay a previous `active` value as current.

#### Scenario: Queue is not execution

- **WHEN** a complete run page contains queued or dispatched work but no `running` run
- **THEN** activity is `inactive`

#### Scenario: Incomplete negative is unknown

- **WHEN** no returned item is `running` but pagination is incomplete
- **THEN** activity is `unknown`, not `inactive`

#### Scenario: Unknown upstream status is unknown

- **WHEN** the complete page contains a run status outside the closed mapping and no `running` item
- **THEN** activity is `unknown`

#### Scenario: Running item proves activity

- **WHEN** at least one returned run has status exactly `running`
- **THEN** activity is `active` even if older history is unavailable

### Requirement: One bounded deduplicated observation per monitor sample

External-task providers SHALL group proven bindings by provider/server/workspace/issue and perform at most one remote issue/status/run observation per unique group in one monitor sample. Provider execution SHALL use a fixed concurrency ceiling and per-operation timeout within the core provider deadline. Every environment in a group SHALL receive the same immutable observation time and values.

A provider failure, timeout, malformed response, or missing optional package SHALL NOT fail or hide core projects/environments. If a binding remains proven but dynamic reads fail, the persisted identifier and safe link SHALL remain, status SHALL be null, activity SHALL be `unknown`, freshness SHALL be `unavailable`, and only a sanitized reason category SHALL be public. If current host/path/binding proof no longer matches, no external-task summary SHALL be emitted.

#### Scenario: Shared issue is fetched once

- **WHEN** three environments have the same proven server/workspace/issue binding in one refresh
- **THEN** the provider performs one issue/run observation and attaches equal summaries to all three

#### Scenario: API outage preserves only safe stable data

- **WHEN** a proven binding exists but the external API times out
- **THEN** the link and identifier remain, status is unknown, activity is `unknown`, freshness is unavailable, and core monitoring still succeeds

#### Scenario: Wrong current path suppresses the block

- **WHEN** the environment UUID is known but its current canonical checkout path differs from the stored proof
- **THEN** no external-task summary is emitted and no issue is guessed
