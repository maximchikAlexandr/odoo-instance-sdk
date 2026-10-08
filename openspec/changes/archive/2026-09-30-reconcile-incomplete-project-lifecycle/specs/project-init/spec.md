## MODIFIED Requirements

### Requirement: Idempotent init

An existing non-identical manifest MUST NEVER be overwritten silently. Identical init MUST preserve the manifest and MUST report a no-op only after every required lifecycle postcondition for the selected project mode is verified. For Compose init, an identical retry SHALL execute the existing immutable ensure-running and bootstrap verification flow when the owned cluster or SQL-proven `tmp` bootstrap is incomplete, and SHALL report success only after those postconditions hold. A fully complete identical Compose init SHALL NOT recreate `tmp`. `--yes` SHALL be the only non-interactive overwrite confirmation and SHALL work with `--no-input`; without it, non-identical headless or machine init SHALL retain the stable error `manifest exists and differs; remove it first or adjust options`. Interactive Rich init without `--yes` SHALL retain the Click confirmation prompt. `--yes` SHALL NOT weaken validation or make `--dry-run` mutating. Every real no-op response SHALL report the requested `dry_run` value truthfully.

#### Scenario: Identical complete re-init

- **WHEN** `odcli init` is run with options identical to an existing manifest and all required lifecycle postconditions are complete
- **THEN** it is a no-op, the manifest is unchanged, `tmp` is not recreated, and a non-dry-run machine response reports `dry_run=false`

#### Scenario: Identical re-init

- **WHEN** `odcli init` is run with options identical to the existing manifest
- **THEN** it is a no-op and the manifest is unchanged

#### Scenario: Identical incomplete Compose re-init

- **WHEN** a prior Compose init wrote the manifest and generated config but failed before the owned cluster and SQL-proven `tmp` bootstrap completed
- **THEN** an identical retry executes the captured Compose/bootstrap flow and cannot report success until both postconditions hold

#### Scenario: Incomplete retry still fails

- **WHEN** an identical Compose retry cannot satisfy image trust, cluster identity, startup, or SQL bootstrap verification
- **THEN** init exits unsuccessfully with the precise existing boundary error and does not publish a successful no-op result

#### Scenario: Interactive overwrite prompt

- **WHEN** interactive Rich `odcli init` resolves a manifest different from the existing manifest and `--yes` is absent
- **THEN** it asks for Click overwrite confirmation and performs no silent overwrite

#### Scenario: Non-identical existing manifest — TTY prompt

- **WHEN** `odcli init` runs in a TTY with options different from the existing manifest and `--yes` is absent
- **THEN** Click requests overwrite confirmation and no silent overwrite occurs

#### Scenario: Non-identical existing manifest — headless error

- **WHEN** `odcli init --no-input` resolves a different manifest without `--yes`
- **THEN** it emits the stable refusal, does not prompt, and does not overwrite

#### Scenario: Non-identical existing manifest — no-input error

- **WHEN** `odcli init --no-input` resolves options different from the existing manifest without `--yes`
- **THEN** it emits `manifest exists and differs; remove it first or adjust options`, does not prompt, and does not overwrite

#### Scenario: Headless overwrite remains refused by default

- **WHEN** `odcli init --no-input` resolves a different manifest without `--yes`
- **THEN** it emits the stable refusal, does not prompt, and does not overwrite

#### Scenario: Explicit headless overwrite

- **WHEN** `odcli init --no-input --yes` resolves a valid manifest different from the existing manifest
- **THEN** it atomically replaces the generated init artifacts without a prompt

#### Scenario: Confirmed dry-run remains inert

- **WHEN** `odcli init --no-input --yes --dry-run` resolves a different manifest
- **THEN** it reports the same resolved plan without changing any file or catalogue record
