## ADDED Requirements

### Requirement: Use persistence stays behind the environment boundary

`last_used_at` и generic environment event `use/succeeded` MUST записываться только через environment/application boundary, не через CLI rendering helpers.

Public `EnvironmentResource` surface MUST остаться `checkout`, `sync_python`, `get`, `list`, `remove`. Этот change MUST NOT добавлять новый public resource, `history()`, `list(verify=)` или `EnvironmentEvent` type.

`checkout` / `sync` / `remove` продолжают писать свои events internally, как и раньше. `run` продолжает требовать `use/succeeded` после free-port preflight; writer MUST быть application/environment code, не JSON/human printer.

#### Scenario: Run records use without a new public resource

- **WHEN** `odcli run` passes free-port preflight
- **THEN** catalog receives `last_used_at` and `use/succeeded` without a new public `OdooClient` resource and without the CLI printer opening the catalog

#### Scenario: Shared checkout still does not write use

- **WHEN** `odcli env checkout` completes in `shared` mode
- **THEN** environment events contain checkout outcomes, not a `use` event from rendering
