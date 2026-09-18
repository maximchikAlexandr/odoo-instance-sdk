# sdk-package-imports Specification

## Purpose
TBD - created by archiving change preserve-lightweight-cli-startup. Update Purpose after archive.
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

A new or changed CLI domain read, mutation, or spawn operation SHALL be built on a public typed SDK primitive. The CLI SHALL retain Click parsing, context resolution, confirmation, and Rich/JSON/TOON rendering. A CLI-only operation SHALL be allowed only with a concrete transport or presentation `cli_only_reason` recorded in the canonical `PUBLIC_LEAF_CASES`. A generic formulation SHALL NOT be accepted.

The existing `PUBLIC_LEAF_CASES` SHALL remain the single inventory. A contract test SHALL reject a leaf without `sdk_primitive` or `cli_only_reason`. An architecture gate SHALL protect the boundary from new self-contained domain execution in a Click callback without introducing a separate command bus or a second manual allowlist.

#### Scenario: New CLI domain operation has an SDK primitive

- **WHEN** a new CLI leaf that performs a domain read, mutation, or spawn is added
- **THEN** its `PUBLIC_LEAF_CASES` entry records a public `sdk_primitive`

#### Scenario: CLI-only reason is concrete

- **WHEN** a leaf is marked CLI-only
- **THEN** its `cli_only_reason` names a specific transport or presentation boundary

#### Scenario: Architecture gate rejects parallel domain execution

- **WHEN** a Click callback builds a self-contained domain operation through `internal.*` where a public SDK primitive applies
- **THEN** the architecture gate fails
