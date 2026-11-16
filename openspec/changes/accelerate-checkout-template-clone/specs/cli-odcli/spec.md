# Spec Delta

## ADDED Requirements

### Requirement: Checkout exposes database copy strategy

`odcli env checkout` and `odcli env create` SHALL accept `--db-copy-strategy` with choices `backup` and `template`. The default SHALL be `backup`. Rich, JSON, and TOON preview and execution output SHALL show the same selected strategy. The flag SHALL NOT change shared-mode checkout and SHALL NOT add a filestore mode flag.

#### Scenario: Help shows the default strategy

- **WHEN** environment checkout help is rendered
- **THEN** `--db-copy-strategy` offers `backup` and `template` and shows default `backup`

#### Scenario: Template preview names the strategy

- **WHEN** checkout is previewed with `--db-mode copy --db-copy-strategy template`
- **THEN** the rendered plan strategy is `template` and no database or filestore is mutated
