## ADDED Requirements

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
