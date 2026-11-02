## MODIFIED Requirements

### Requirement: Environment facts entry point

One narrow environment-facts entry point SHALL allow optional packages to attach read-only facts to the canonical monitor owners and their `CheckoutInventory` projections. The entry point SHALL remain one explicitly typed protocol discovered through `odoo_instance_sdk.environment_facts`. A provider SHALL receive immutable core checkout rows plus matching private typed external-task bindings and return frozen summaries; it SHALL NOT patch Click/Rich/React, add HTTP routes or frontend assets, duplicate core models/renderers/serialization, run a live loop, mutate the core snapshot/catalogue, or retain binding values.

Provider collection SHALL occur once inside the canonical monitor sample. `ProjectSummary`/`EnvironmentSnapshot` SHALL retain the frozen summaries and `CheckoutInventory` SHALL copy them without invoking providers again. Core SHALL own the generic Rich/JSON/TOON/process/HTTP/card projections; a provider SHALL only populate typed data. Each provider SHALL add at most one compact Rich `env list` column through that core renderer. JSON/TOON SHALL store the same summaries nested under stable provider IDs without dynamic top-level fields. `EnvironmentFactsSummary` SHALL contain provider, state, text, concrete typed details, and an optional `ExternalTaskSummary`; it SHALL use no `Any`, bare `object`, or Rich/UI renderable.

Collection SHALL preserve deterministic order, the existing short hard timeout, live response-channel consumption, and isolated per-provider/per-owner errors. A missing, failed, incompatible, or slow provider SHALL add no empty column, hide no core row, contaminate no machine stdout, and fail no snapshot. This boundary SHALL remain read-only facts only, not lifecycle/provider orchestration; process contributions remain a separate process contract.

#### Scenario: One provider adds one column from snapshot facts

- **WHEN** one provider returns facts in a monitor sample
- **THEN** Rich `env list` shows at most one provider column and does not call the provider again

#### Scenario: Missing provider is not an error

- **WHEN** no provider is installed
- **THEN** core snapshot and `env list` rows render with empty facts, no extra column, and no error

#### Scenario: Failed provider does not hide rows

- **WHEN** one provider raises or times out
- **THEN** core rows and other provider summaries remain visible and machine stdout is uncontaminated

#### Scenario: Large provider response is collected within deadline

- **WHEN** a valid serialized response exceeds the operating-system channel buffer before the deadline
- **THEN** the snapshot owner contains the summary, the child exits normally, and collection does not wait beyond the deadline

## ADDED Requirements

### Requirement: Environment list projects linked task consistently

`odcli env list` and each successful `--watch` refresh SHALL render external-task identity, actual-or-unknown issue status, and activity from the facts already present in the one canonical sample. Rich SHALL use one compact provider cell/column. JSON and TOON SHALL serialize the identical frozen `CheckoutInventory` fact model. The command SHALL perform no second provider, Multica, catalogue, or monitor collection.

#### Scenario: Machine formats agree

- **WHEN** JSON and TOON render the same linked environment sample
- **THEN** decoded task identifier/link/status/activity/freshness values are equal

#### Scenario: Watch refresh replaces activity honestly

- **WHEN** one Rich watch sample is active and the next provider observation is unavailable
- **THEN** the next frame shows unknown/unavailable rather than retaining the green active text

#### Scenario: No binding means no empty task cell value

- **WHEN** a row has no external-task fact
- **THEN** Rich uses the normal absent-provider behavior and machine facts contain no fabricated task summary
