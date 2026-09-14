## ADDED Requirements

### Requirement: Lightweight CLI metadata startup

The `odcli --help` and `odcli --version` paths SHALL execute without resolving project context or importing operation-only HTTP and monitoring implementations. In a fresh Python interpreter, either metadata path SHALL leave `httpx` and `odoo_instance_sdk.resources.monitor` absent from `sys.modules`. The optimization SHALL preserve the existing Click entry point, root selectors, command names, command ordering, help text, and exit semantics except for the additive version option.

#### Scenario: Root help avoids operation-only modules

- **WHEN** `odcli --help` runs in a fresh interpreter outside a project
- **THEN** it exits `0` and displays the existing root command and option surface plus `--version`
- **AND** `httpx` and `odoo_instance_sdk.resources.monitor` are not loaded

#### Scenario: Version avoids operation-only modules

- **WHEN** `odcli --version` runs in a fresh interpreter outside a project
- **THEN** it exits `0` after printing the installed distribution version
- **AND** project/environment resolution is not attempted
- **AND** `httpx` and `odoo_instance_sdk.resources.monitor` are not loaded

#### Scenario: Operation command behavior remains available

- **WHEN** a caller invokes an existing operation command after the lightweight CLI module has loaded
- **THEN** the command resolves its required implementation modules and retains its existing options, output, and exit behavior

### Requirement: Startup performance is recorded without a timing gate

The repository SHALL document reproducible before-and-after `python -X importtime` measurements for importing `odoo_instance_sdk.cli`, including the measured revision, Python/OS context, exact command, cumulative results, and selected module-presence checks. CI SHALL enforce module-boundary behavior deterministically and SHALL NOT fail on a wall-clock or import-duration threshold.

#### Scenario: Maintainer reproduces the comparison

- **WHEN** a maintainer follows the documented measurement procedure on the recorded revisions
- **THEN** the document provides the baseline and final cumulative import results and identifies whether `httpx` and `odoo_instance_sdk.resources.monitor` loaded

#### Scenario: Slow CI host does not create a false failure

- **WHEN** deterministic import-boundary tests pass on a host with variable performance
- **THEN** no test fails solely because an elapsed-time threshold was exceeded
