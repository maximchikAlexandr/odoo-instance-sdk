## ADDED Requirements

### Requirement: Optional OpenSpec change on checkout

`odcli env checkout` SHALL accept `--openspec-change CHANGE`. Without that option, checkout behavior SHALL be unchanged. `CHANGE` SHALL be an OpenSpec change id, not a filesystem path and not a capability spec name. When the option is present and `odcli-openspec` cannot be imported, checkout SHALL fail before mutation and SHALL include `uv tool install --with-executables-from odcli-openspec odoo-instance-sdk` in the error. When the extension is present, checkout and binding SHALL share one inspectable plan. If the worktree is created and binding then fails, the result SHALL report `environment_created` and `binding_failed` with the worktree path and SHALL NOT claim the checkout was rolled back.

#### Scenario: Checkout without the option is unchanged

- **WHEN** `odcli env checkout` runs without `--openspec-change`
- **THEN** the plan and result omit OpenSpec validation and binding

#### Scenario: Missing extension fails before mutation

- **WHEN** `--openspec-change CHANGE` is set and `odcli-openspec` is not installed
- **THEN** checkout stops before creating a worktree and names the `uv tool install --with-executables-from` command

#### Scenario: Binding failure keeps the created environment

- **WHEN** checkout creates the worktree and OpenSpec validation then fails
- **THEN** the result identifies the created environment and worktree path and does not describe a checkout rollback

## MODIFIED Requirements

### Requirement: Environment facts entry point

One narrow environment-facts entry point SHALL allow `odcli-codex` (#68), `odcli-openspec` (#69), and `odcli-multica` (#70) to attach read-only facts to `CheckoutInventory` rows. The entry point SHALL be a single explicitly typed callable/protocol discovered via Python entry points. A provider SHALL receive immutable core checkout rows and return frozen summaries; it SHALL NOT patch Click or Rich, run its own live loop, or mutate the core snapshot.

Each installed provider SHALL add at most one compact Rich column. JSON/TOON SHALL store summaries nested under a stable provider ID without dynamic top-level fields. The minimal summary SHALL contain `provider`, `state`, `text`, and concrete typed details. Detail values SHALL preserve `str`, `int`, `bool`, and `null` so counts and absent percentages survive JSON and TOON. `Any`, `object`, and Rich renderables SHALL NOT be used. Collection SHALL have deterministic provider order, a short bounded timeout, and isolated per-provider/per-row errors. The parent SHALL consume a provider response while the provider process is running under the same overall deadline, so a valid serialized response larger than the operating-system channel buffer SHALL NOT deadlock behind process completion or be discarded. A missing optional package SHALL NOT add an empty column and SHALL NOT be an error. A failed, incompatible, or slow provider SHALL NOT hide core rows or other integrations and SHALL NOT write progress or noise to machine stdout.

This entry point SHALL NOT become a general lifecycle or provider framework; it serves only read-only facts for `CheckoutInventory`. Process groups continue to use the separate contract from the `process-inventory` capability.

#### Scenario: One provider adds one column

- **WHEN** exactly one environment-facts provider is installed
- **THEN** Rich `env list` shows at most one extra compact column from that provider

#### Scenario: Missing provider is not an error

- **WHEN** no environment-facts provider is installed
- **THEN** `env list` renders core rows with no extra column and no error

#### Scenario: Failed provider does not hide rows

- **WHEN** one environment-facts provider raises an error
- **THEN** core rows and other providers' summaries remain visible and machine stdout is uncontaminated

#### Scenario: Large provider response is collected within the deadline

- **WHEN** a provider returns a valid frozen summary whose serialized response exceeds the operating-system channel buffer before the configured deadline
- **THEN** the matching inventory row contains that summary, the child exits normally, and collection does not wait beyond the single configured deadline

#### Scenario: Numeric fact details stay numeric

- **WHEN** a provider summary detail is an integer or null
- **THEN** JSON and TOON emit that integer or null rather than a decimal string
