## MODIFIED Requirements

### Requirement: SDK-first rule for CLI domain operations

Every entry in the canonical `PUBLIC_LEAF_CASES` SHALL carry exactly one of: an `sdk_primitive` referencing the public typed SDK call the CLI delegates to, or a `cli_only_reason` with a concrete transport/presentation reason. The new `bug-report init`, `bug-report submit`, and `update` leaves SHALL set `sdk_primitive` to `bug_report_init_command`, `bug_report_submit_command`, and `update_command` respectively. `cli_only_reason` SHALL NOT be used for those domain leaves. CLI callbacks SHALL NOT build a self-contained domain read/mutation/spawn operation through `internal.*` when a public typed SDK primitive applies. CLI callbacks SHALL NOT import `OdooHttpClient` or `internal.transport`; they SHALL call public SDK resource primitives. Only `src/odoo_instance_sdk/internal/transport/` files listed in `architecture_inventory.py` SHALL import `httpx`. `src/` SHALL NOT import `xmlrpc.client.ServerProxy`. `OdooHttpClient` SHALL NOT be a public SDK export.

#### Scenario: New CLI domain operation has an SDK primitive

- **WHEN** a new CLI leaf that performs a domain read, mutation, or spawn is added
- **THEN** its `PUBLIC_LEAF_CASES` entry records a public `sdk_primitive`

#### Scenario: CLI-only reason is concrete

- **WHEN** a leaf is marked CLI-only
- **THEN** its `cli_only_reason` names a specific transport or presentation boundary

#### Scenario: Architecture gate rejects parallel domain execution

- **WHEN** a Click callback builds a self-contained domain operation through `internal.*` where a public SDK primitive applies
- **THEN** the architecture gate fails

#### Scenario: new leaves carry named primitives

- **WHEN** the `PUBLIC_LEAF_CASES` contract test runs
- **THEN** `bug-report init`, `bug-report submit`, and `update` each have the `sdk_primitive` names above

#### Scenario: transport is the only httpx importer

- **WHEN** the architecture gate runs on `src/`
- **THEN** only the line-specific `internal/transport/` allowlist imports `httpx`

