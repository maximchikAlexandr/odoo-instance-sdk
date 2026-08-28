## ADDED Requirements

### Requirement: `odcli db` command group

The Click adapter SHALL add:

```text
odcli db refresh [--restore] [--reset-admin-password] [--source-branch BRANCH]
odcli db reset-admin-password
```

`commands/db.py` SHALL parse options, resolve project/environment context, call existing public resources, render typed results, and map typed exceptions. It SHALL not download, restore, acquire locks, run ORM scripts, edit manifests, or construct alternate result dictionaries itself. The group SHALL be registered through the stable `odoo_instance_sdk.cli:cli` entry point after rebasing the MYL-55 CLI foundation.

#### Scenario: Help exposes database commands

- **WHEN** `odcli db --help` runs
- **THEN** it lists `refresh` and `reset-admin-password` with the documented options

### Requirement: `odcli db refresh` option and context rules

`db refresh` SHALL require project context through explicit `--project`, nearest manifest, or exact registered worktree. It SHALL source the remote instance only from project `[test_instance]`. `--source-branch` SHALL override its configured branch. `--reset-admin-password` without `--restore` SHALL be a Click usage error with exit code 2 before SDK/network/catalog mutation.

Without `--restore`, the command SHALL download only. With `--restore`, it SHALL request the complete preparation flow. It SHALL not prompt for either master password and SHALL never accept a password option.

#### Scenario: Download-only refresh

- **WHEN** `odcli db refresh` runs in a configured project
- **THEN** it downloads/catalogs a backup and does not touch local databases or the project default

#### Scenario: Reset flag requires restore

- **WHEN** `odcli db refresh --reset-admin-password` runs without `--restore`
- **THEN** Click exits 2 with a usage error before any operation begins

### Requirement: Context-aware `odcli db reset-admin-password`

`db reset-admin-password` SHALL resolve an exact ready environment from `--env` or the current registered worktree using the shared instance-command resolver. It SHALL require the environment's generated config and recorded source/target ownership to identify exactly one database, verify the selected Odoo endpoint is local, and delegate to the existing database resource. It SHALL not choose the latest/only environment by recency or project membership.

#### Scenario: Reset from registered worktree

- **WHEN** the command runs inside one ready registered worktree
- **THEN** it resets that environment's single bound database through the resource and ORM

#### Scenario: Project root is not enough

- **WHEN** the command runs outside a registered worktree without `--env`
- **THEN** it fails with candidate guidance and modifies no database

### Requirement: Database command output and redaction

Database commands SHALL use the accepted MYL-55 output contract: Rich for human structured output and the same CLI envelope for JSON/TOON. Successful refresh output SHALL contain backup ID/path/size/checksum/downloaded timestamp, nullable source branch and branch origin, plus optional restored database, reset/default-switch state, provenance status, warnings, and retained-artifact state. Machine formats SHALL be semantically equal and contain no ANSI or prompt.

Passwords, secret environment values, multipart bodies, complete Odoo config content, and ORM script source SHALL never appear in output, errors, traceback summaries, or Rich renderables. Exit status SHALL follow the foundation's renderer-independent policy.

#### Scenario: Machine refresh output is complete and secret-free

- **WHEN** download-only refresh succeeds in JSON and TOON modes
- **THEN** decoded envelopes contain equal backup/provenance data and neither contains the remote password

#### Scenario: Retained artifact failure output

- **WHEN** reset fails after restore
- **THEN** the failure identifies retained backup/database and unchanged default without including `admin` as a password field/value
