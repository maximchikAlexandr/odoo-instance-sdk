## ADDED Requirements

### Requirement: Optional Multica provider is co-installed explicitly

The `odcli-multica` distribution SHALL declare exactly one `odoo_instance_sdk.environment_facts` entry point for its task provider. Core and dashboard distributions SHALL NOT depend on or import `multica-py`. Documentation SHALL state that `odcli-multica` must be installed in the same Python environment that executes `odcli monitor`, `odcli env list`, or `odcli ps`; an executable found elsewhere on `PATH` SHALL NOT count as provider installation.

The packages SHALL add no auto-install, companion subprocess transport, RPC, daemon, second service, dynamic React bundle, or fallback raw CLI invocation. Existing independently pinned compatibility and core-only import contracts SHALL remain in force.

#### Scenario: Co-installed package is discovered

- **WHEN** core and `odcli-multica` are installed in one compatible Python environment
- **THEN** standard entry-point discovery loads the Multica facts provider without Click or browser registration

#### Scenario: PATH-only executable is not loaded

- **WHEN** `odcli-multica` exists on `PATH` but its distribution is absent from the monitor interpreter
- **THEN** core runs without Multica facts and does not spawn the executable

#### Scenario: Core wheel remains Multica-neutral

- **WHEN** core metadata/imports are inspected without the extension
- **THEN** `multica-py` is not a dependency/import and monitor/CLI/dashboard remain usable
