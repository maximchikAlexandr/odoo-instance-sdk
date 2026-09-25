## ADDED Requirements

### Requirement: CLI MUST expose checkout from an existing backup

The existing `env checkout` leaf MUST accept `--backup BACKUP_UUID` and delegate once to the public checkout operation. It MUST preserve the SDK's validation, dry-run plan, typed failure, redaction, and output contracts.

#### Scenario: Automation checks out from a backup

- **WHEN** a caller invokes `odcli env checkout TASK-123 --db-mode copy --backup BACKUP_UUID --base staging --format json`
- **THEN** the CLI passes the exact UUID to the public checkout operation
- **AND** emits the established bounded JSON envelope

### Requirement: CLI MUST expose opt-in detached readiness

The existing `run` leaf MUST accept `--wait-ready` only for root `--env` plus `--detach`. `--readiness-timeout` MUST be positive, MUST default to 60 seconds when readiness is enabled, and MUST be rejected without `--wait-ready`. The CLI MUST delegate polling and cleanup to the public SDK operation.

#### Scenario: Automation waits for detached readiness

- **WHEN** a caller invokes `odcli --env ENVIRONMENT run --detach --wait-ready --format json`
- **THEN** the CLI emits success only after the SDK confirms readiness
- **AND** emits SDK failure without implementing its own polling or cleanup

#### Scenario: Readiness options are incompatible

- **WHEN** readiness is requested without an explicit environment and detached mode, the timeout is non-positive, or a timeout is supplied without readiness
- **THEN** the CLI rejects the invocation before starting Odoo
- **AND** returns the established usage-error contract

### Requirement: CLI exposes named remote configuration

CLI SHALL expose `remote ls`, `remote add NAME --url URL --database DB --branch REF`, `remote update NAME` with the same configurable fields, and `remote remove NAME`. Update SHALL preserve unspecified fields. Init SHALL accept repeatable `--remote NAME URL DATABASE GIT_REF`. Every operation SHALL delegate to a public SDK primitive; credentials SHALL NOT be accepted as literal CLI arguments.

#### Scenario: Add staging after init

- **WHEN** a caller adds staging with URL, database and branch
- **THEN** the SDK atomically stores the entry and the CLI reports expected credential variable names without values

#### Scenario: Preview configuration change

- **WHEN** add, update, remove or init uses dry-run
- **THEN** the output describes the configuration change and no configuration or operational resource is mutated

### Requirement: CLI exposes exact source selection and diagnostics

`db refresh` and `env checkout` SHALL accept `--remote NAME`; checkout SHALL require COPY mode and reject conflicting source flags. Existing `doctor` SHALL accept the same selector and project context. Selection SHALL NOT be inferred from prose, branch names or ordering of profiles.

#### Scenario: Checkout staging

- **WHEN** `odcli env checkout TASK-123 --db-mode copy --remote staging --format json` is invoked
- **THEN** the CLI delegates to the SDK and reports the selected source, base commit and resulting backup/environment identities

#### Scenario: Invalid source combination

- **WHEN** checkout combines remote, backup or local source inputs
- **THEN** it fails before download or mutation with a stable actionable error

### Requirement: CLI exposes retention and pinning

CLI SHALL expose `backup retention` to inspect user settings, optional `--days N` and `--auto/--no-auto` to update them, `backup pin UUID`, `backup unpin UUID`, and project-scoped `backup prune`. Prune SHALL require normal destructive confirmation interactively or explicit `--yes` without input; dry-run SHALL require no confirmation and perform no deletion. Retention updates SHALL require an explicit enablement flag before enabling automatic deletion.

#### Scenario: Enable two-week retention

- **WHEN** `odcli backup retention --days 14 --auto` succeeds
- **THEN** output identifies effective user settings and their actual file path

#### Scenario: Preview and apply pruning

- **WHEN** a caller uses `backup prune --dry-run --format json`
- **THEN** output lists exact candidates, protections and bytes without deletion
- **WHEN** a later confirmed prune command is created
- **THEN** it produces its own current plan and applies only that plan after execution-time rechecks

### Requirement: New operations share the existing automation contract

Every new CLI leaf SHALL have a public typed SDK equivalent and use existing bounded human/JSON/TOON output, redaction, immutable plans and stable failure contracts. Partial pruning outcomes SHALL remain machine-readable; automatic maintenance failure SHALL be a warning alongside primary success, whereas explicit prune failure SHALL use a non-success exit. Missing non-interactive inputs SHALL produce errors without prompts or guessed defaults.

#### Scenario: Script handles cleanup warning

- **WHEN** a successful restore is followed by unsuccessful opportunistic pruning
- **THEN** machine output retains the successful restore identity and separate maintenance warning
- **AND** its exit status does not instruct the script to repeat restore
