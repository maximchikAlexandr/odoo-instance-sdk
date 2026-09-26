## ADDED Requirements

### Requirement: Independently installed Multica integration distribution

The repository SHALL provide `odcli-multica` as a separately versioned distribution, `odcli_multica` import package and `odcli-multica` executable under `packages/odcli-multica`. It SHALL depend on compatible public releases of `odoo-instance-sdk` and external `multica-py`; the core wheel SHALL NOT depend on or import the extension or Multica. Root core source placement SHALL remain unchanged, and the member SHALL reuse the single workspace/lock foundation owned by issue #69.

The extension SHALL import only public SDK APIs and SHALL NOT copy subprocess/HTTP execution, serializers, catalog schema or live-monitor loops. Package-specific publication SHALL use `odcli-multica-v<VERSION>` tags and independent versioning. Installing the extension SHALL NOT install/start a Multica daemon or disable SDK compatibility validation.

#### Scenario: Core-only installation

- **WHEN** a clean environment installs the built core wheel
- **THEN** no Multica package is installed or imported and existing core help/checkout behavior is unchanged

#### Scenario: Standalone and integrated tool installation

- **WHEN** clean tool environments install either `odcli-multica` alone or `uv tool install --with-executables-from odcli-multica odoo-instance-sdk`
- **THEN** the standalone case exposes the extension executable and the integrated case exposes both supported executables with correctly declared dependencies

#### Scenario: Published wheel does not rely on workspace paths

- **WHEN** the member is built with `uv build --package odcli-multica --no-sources` and installed with the compatible core wheel outside the repository
- **THEN** CLI/API smoke tests pass without undeclared cross-member imports or source checkout paths

#### Scenario: Required upstream capability is absent

- **WHEN** the installed Multica SDK/CLI combination lacks the tested native checkout/context contract
- **THEN** explicit integration use fails before mutation with the required compatibility information and core-only operations remain usable
