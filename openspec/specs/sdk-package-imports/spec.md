# sdk-package-imports Specification

## Purpose

Keep the public SDK import surface lazy and lightweight while enforcing that CLI domain leaves delegate to typed public primitives recorded in `PUBLIC_LEAF_CASES`. Package roots defer heavy implementation imports until a caller resolves a declared export; architecture gates reject parallel domain execution inside Click callbacks.

## Requirements

### Requirement: Package root defers public export imports

The `odoo_instance_sdk` package root SHALL declare its existing public export names without importing the modules that implement those names until a caller accesses an export. Importing the package root alone SHALL NOT load `odoo_instance_sdk.client`, `odoo_instance_sdk.resources.monitor`, or `httpx`.

#### Scenario: Bare package import remains lightweight

- **WHEN** a fresh Python interpreter imports `odoo_instance_sdk` without accessing a public export
- **THEN** `odoo_instance_sdk.client`, `odoo_instance_sdk.resources.monitor`, and `httpx` are absent from `sys.modules`

### Requirement: Lazy exports preserve the public SDK contract

The package root SHALL retain the exact existing `__all__` names. Accessing any name in `__all__`, including through `from odoo_instance_sdk import <name>`, SHALL return the same object exported by that name's canonical implementation module, and repeated access SHALL preserve object identity. Accessing an undeclared package attribute SHALL raise `AttributeError`.

#### Scenario: Every declared export resolves compatibly

- **WHEN** a caller resolves every name listed in `odoo_instance_sdk.__all__`
- **THEN** each value is identical to the corresponding object in its canonical implementation module
- **AND** the ordered `__all__` value is unchanged from the pre-change contract

#### Scenario: Resolved export is cached

- **WHEN** a caller accesses the same declared export more than once
- **THEN** both accesses return the identical object

#### Scenario: Unknown package attribute is rejected

- **WHEN** a caller accesses a name that is not a declared package attribute or lazy public export
- **THEN** the package raises `AttributeError` naming the unknown attribute

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
