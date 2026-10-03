## ADDED Requirements

### Requirement: Independently installed Multica integration distribution

The repository SHALL provide `odcli-multica` as a separately versioned distribution, `odcli_multica` import package and `odcli-multica` executable under `packages/odcli-multica`. It SHALL depend on an `odoo-instance-sdk` revision/release containing PR #110 and exact `multica-py` PR #95 merge revision `c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed` while that dependency remains an untagged `0.1.0`; an equivalent uniquely versioned release MAY replace the revision pin only with compatibility evidence. The core wheel SHALL NOT depend on or import the extension or Multica. Root core source placement SHALL remain unchanged, and the member SHALL reuse the single workspace/lock foundation owned by issue #69.

The extension SHALL import only public typed SDK APIs and SHALL NOT invoke checkout or daemon status through a raw CLI escape hatch, add local wire/output decoders for those operations, or copy subprocess/HTTP execution, serializers, catalog schema, or live-monitor loops. Package-specific publication SHALL use `odcli-multica-v<VERSION>` tags and independent versioning. Installing the extension SHALL NOT install/start a Multica daemon or disable SDK compatibility validation.

#### Scenario: Core-only installation

- **WHEN** a clean environment installs the built core wheel
- **THEN** no Multica package is installed or imported and existing core help/checkout behavior is unchanged

#### Scenario: Standalone and integrated tool installation

- **WHEN** clean tool environments install either `odcli-multica` alone or `uv tool install --with-executables-from odcli-multica odoo-instance-sdk`
- **THEN** the standalone case exposes the extension executable and the integrated case exposes both supported executables with correctly declared dependencies

#### Scenario: Published wheel does not rely on workspace paths

- **WHEN** the member is built with `uv build --package odcli-multica --no-sources` and installed with the compatible core wheel outside the repository
- **THEN** CLI/API smoke tests pass without undeclared cross-member imports or source checkout paths

#### Scenario: Required typed upstream capability is absent

- **WHEN** the installed Multica SDK/CLI combination lacks the tested typed native checkout or complete daemon-status contract
- **THEN** explicit integration use fails before mutation with the required compatibility information, does not fall back to raw CLI decoding, and core-only operations remain usable

### Requirement: Reuse the existing bounded output contract

The core SHALL document and support the narrow subset of its existing bounded output API consumed by the extension. The extension SHALL reuse it for equivalent Rich/JSON/TOON results without adding another facade module, serializer or rendering framework. Existing core-only behavior SHALL remain unchanged.

#### Scenario: Output reuse from an installed wheel

- **WHEN** the standalone extension emits its context or preparation result through the documented core output API outside the repository
- **THEN** Rich/JSON/TOON express equivalent sanitized facts and machine stdout contains one document without private imports or copied serializers
