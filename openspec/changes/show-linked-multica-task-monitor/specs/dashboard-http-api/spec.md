## ADDED Requirements

### Requirement: Snapshot publishes safe external-task facts

`GET /api/v1/snapshot` SHALL publish schema-v5 `ProjectSummary.facts` and `EnvironmentSnapshot.facts` through the existing named `Snapshot` response, msgspec-to-OpenAPI bridge, and checked-in generated TypeScript client. It SHALL perform no Multica call in the route or browser and SHALL expose no private binding identity or credentials. A provider failure SHALL remain a successful core snapshot with empty or unavailable facts as defined by `external-task-observability`.

#### Scenario: OpenAPI contains the external-task model

- **WHEN** the production OpenAPI schema is generated
- **THEN** `Snapshot` references named fact and external-task schemas with the exact activity/freshness enums and nullable status

#### Scenario: Browser consumes the snapshot only

- **WHEN** the monitor UI refreshes a linked card
- **THEN** it obtains task identity/status/activity from `getMonitorSnapshot` and performs no browser request to Multica

### Requirement: Existing runtime card renders an accessible task block

The existing core `RuntimeCard` SHALL render a compact external-task block only when its owner has one proven `ExternalTaskSummary`. This is a provider-neutral typed capability: the browser SHALL NOT branch on provider ID, import an optional-provider package, load provider JavaScript/CSS, or accept provider-supplied renderables. Any provider satisfying the same core schema SHALL receive the same core-owned block; provider-specific fetching, mapping, and credentials SHALL remain behind the Python entry point. The block SHALL remain visible when Odoo is stopped. It SHALL include the task identifier/link, textual issue status or `Unknown`, and textual activity plus a green/red/gray indicator for `active`/`inactive`/`unknown`. Activity meaning SHALL NOT depend on color.

The link SHALL open the verified HTTP(S) URL in a new tab with `noopener,noreferrer`. Activity help SHALL be available on hover and keyboard focus with an accessible name. On unavailable freshness the link SHALL remain, status and activity SHALL visibly become unknown, and a safe reason SHALL be available; a previous green value SHALL NOT be presented as current. Owners without a summary SHALL render no empty task block.

#### Scenario: Active linked task is accessible

- **WHEN** a card receives current status and `active` activity
- **THEN** it shows the identifier/link, status text, green indicator, visible activity text, and focusable help reading “The task has an active agent run”

#### Scenario: Inactive is not an Odoo error

- **WHEN** activity is `inactive` while Odoo is running or stopped
- **THEN** the block shows red plus “No active agent runs” without changing the Odoo or issue-status presentation

#### Scenario: Unavailable refresh is honest

- **WHEN** a proven link remains but dynamic status/activity observation is unavailable
- **THEN** the link stays usable, status/activity show unknown with gray semantics and a safe reason, and no stale green indicator is shown

#### Scenario: Unbound and core-only cards stay unchanged

- **WHEN** an environment is unbound or the optional extension is absent
- **THEN** its existing card renders without an empty link, badge, dot, or placeholder

#### Scenario: Card rendering is provider-neutral

- **WHEN** a non-Multica test provider returns the same validated `ExternalTaskSummary` capability
- **THEN** the existing core card renders the identical task block without provider-specific code, assets, routes, or dynamic UI loading
