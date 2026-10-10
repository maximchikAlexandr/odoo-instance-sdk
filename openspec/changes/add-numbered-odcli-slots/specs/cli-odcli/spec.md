## ADDED Requirements

### Requirement: Canonical numbered-slot management surface
The canonical CLI SHALL expose `odcli slot install NUMBER SHA [--replace]`, `odcli slot list`, and `odcli slot remove NUMBER`. The management leaves SHALL use the existing immutable command/process boundary and bounded Rich/JSON/TOON result pipeline, SHALL record concrete public leaf classifications, and SHALL keep machine stdout to exactly one semantic document. Slot management SHALL be rejected when invoked through `odcli-N`.

#### Scenario: Help exposes bounded manager commands
- **WHEN** canonical CLI help is requested for `slot`
- **THEN** it lists install, list, and remove with exact-SHA, replacement, destructive-removal, and isolation-boundary guidance

#### Scenario: Numbered launcher cannot manage slots
- **WHEN** `odcli-N slot list`, install, or remove is invoked
- **THEN** it fails before reading or mutating manager manifests, uv tool layouts, launchers, or state roots

#### Scenario: Machine formats preserve parity
- **WHEN** a slot manager success or typed failure is rendered in JSON and TOON
- **THEN** both formats carry semantically equal bounded data without prompts, progress, ANSI, raw subprocess output, or secrets

### Requirement: Alternate launcher delegates the ordinary CLI surface
An installed `odcli-N` or `odcli-fix-ISSUE` launcher SHALL hold its lifecycle lock for the full child lifetime, set only the selected user-root, alternate-launcher identity, and exact executable identity variables needed by the SDK, and delegate arguments and exit status without shell interpretation. Except for numbered slot management and self-update, the underlying revision's ordinary CLI surface SHALL remain available.

#### Scenario: Arguments and exit status are preserved
- **WHEN** an operator invokes `odcli-N` or `odcli-fix-ISSUE` with ordinary command arguments
- **THEN** the exact argument boundaries and child exit status are preserved without a shell and the lifecycle lock remains held until the child exits
