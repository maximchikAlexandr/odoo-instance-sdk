## ADDED Requirements

### Requirement: Multica prepare records and restores task visibility

`odcli-multica` SHALL construct the core `ExternalTaskBinding` from the same successful `VerifiedTaskContext`, public `Issue`, `Issue.permalink()`, and daemon evidence used to authorize caller-owned adoption. The permalink SHALL be absolute HTTP(S), SHALL contain no user information or credentials, and SHALL identify the exact issue through the configured public Multica application route. `prepare_command()` SHALL pass the binding into the captured core adoption operation rather than writing the catalogue through an internal API.

The independently installed package SHALL register exactly one `multica` provider through `odoo_instance_sdk.environment_facts`. For each current sample it SHALL validate stored server/workspace/issue/path/runtime/daemon identity against current public configuration and daemon evidence, group equal issues, and use public `multica-py` issue and run operations only. It SHALL use no raw Multica CLI parsing, private HTTP/storage, browser credential, or core import from `odoo_instance_sdk.internal`.

#### Scenario: Prepare creates a restart-safe link

- **WHEN** `odcli-multica env prepare` completes against a verified local task checkout
- **THEN** the returned environment carries the private binding needed for a later fresh monitor process to obtain its task summary

#### Scenario: Public provider supplies current issue data

- **WHEN** the provider validates a binding and public `multica-py` returns the issue plus a complete run page
- **THEN** the provider returns the issue identifier/permalink, actual status, and conservatively mapped activity without a second collector

#### Scenario: Multica package is absent

- **WHEN** core and dashboard run without `odcli-multica` installed in that Python environment
- **THEN** provider discovery yields no Multica facts and every core surface continues normally

#### Scenario: Server or daemon identity changed

- **WHEN** stored binding identity does not match current scoped Multica server/workspace/runtime/daemon evidence
- **THEN** the provider emits no confirmed external-task summary and does not contact another server by guess
