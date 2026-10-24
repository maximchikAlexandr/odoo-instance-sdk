## MODIFIED Requirements

### Requirement: Shared compact runtime card presentation

Main checkouts and worktrees SHALL use the same card component and responsive grid, with equal card width and height at the same breakpoint. The panel SHALL load Mantine core styles and an Odoo 19-inspired theme with plum navigation/actions, compact field styling, a project selector, and an instance-state selector with exact values `all` and `running`. It SHALL retain all-project and selected-project views, show registered main checkouts without environment rows, and preserve snapshot polling, retry and request coalescing.

The two selectors SHALL compose over the same already-fetched snapshot. `running` SHALL include a project or environment only when its runtime state is `ready` or `not_ready`; lifecycle rows without a live runtime SHALL be excluded. The displayed instance count and empty state SHALL describe the post-filter card set. Selector changes SHALL NOT trigger a second or narrowed snapshot request.

Each card SHALL show instance name, Main checkout or Worktree kind, one status indicator, a Branch row, database, local endpoint, backup freshness, and Resources. The main Branch value SHALL use `ProjectSummary.runtime.branch`; the worktree SHALL use `EnvironmentSnapshot.branch`. Missing branch data SHALL show `Not recorded`. CPU and RAM SHALL be shown only for live runtimes with non-null values. Non-ready environment lifecycle state SHALL take precedence over runtime readiness in the single status indicator.

A known backup download timestamp SHALL be formatted in the browser's local timezone with year, month, day, hour, and minute and without seconds, while the source ISO timestamp remains in `<time dateTime>` and the canonical age remains visible. Unknown freshness SHALL retain the explicit unknown label. Resources SHALL use `ProjectSummary.storage.total_bytes` on main checkout cards and `EnvironmentSnapshot.storage.total_bytes` on worktree cards, formatted through the shared byte formatter. `complete=false` SHALL be visibly marked as incomplete and SHALL NOT expose paths or a per-component breakdown.

The compact panel SHALL hide cluster/container diagnostics and badges, separate port/publication-state text, PID/worker IDs, Git/SHA/diff details, storage component/worktree/venv breakdowns and pgAdmin controls/status. The same-origin Admin link SHALL target canonical `/admin/`. These presentation changes SHALL NOT remove backend fields or change runtime metric collection.

#### Scenario: Main checkout and worktree share geometry

- **WHEN** a selected project has one main runtime and one worktree
- **THEN** both render equal-size cards with the same field/action layout and exactly one status indicator

#### Scenario: Branch data is available or absent

- **WHEN** the generated snapshot supplies a launch branch for the main checkout and a catalog branch for a worktree
- **THEN** each card displays its corresponding value under Branch; absent main runtime/branch displays Not recorded

#### Scenario: Metrics and technical details are hidden selectively

- **WHEN** a runtime has unavailable CPU/RAM or its project cluster reports unavailable metrics
- **THEN** the card shows no empty metric placeholders or cluster diagnostics while preserving available runtime values and backend fields

#### Scenario: No hand-written Basic Auth credentials

- **WHEN** the panel and API share one HTTPS origin protected by Caddy Basic Auth
- **THEN** generated Fetch operations use the browser's same-origin credentials behavior; React does not embed the proxy login/password or manually construct its Authorization header

#### Scenario: Running filter composes with project selection

- **WHEN** the user selects one project and then selects `running`
- **THEN** only that project's `ready` and `not_ready` project/environment cards remain, the count matches those cards, and no additional snapshot request is made

#### Scenario: Running filter has no matches

- **WHEN** the selected scope contains no `ready` or `not_ready` runtime
- **THEN** the panel shows a filter-specific empty state without claiming that no projects are registered

#### Scenario: Backup time is human-readable and exact

- **WHEN** a card has a known UTC download timestamp and age
- **THEN** visible text uses the browser locale/timezone without seconds, `<time dateTime>` retains the exact source value, and the relative age remains visible

#### Scenario: Separate resource totals are honest

- **WHEN** a project checkout measurement and an environment storage measurement are available
- **THEN** their respective cards show separately formatted totals and no component path or technical storage breakdown
- **AND** an incomplete measurement is labelled incomplete rather than presented as complete

#### Scenario: Canonical Admin navigation

- **WHEN** the operator follows Admin from the monitor
- **THEN** the same-origin link targets `/admin/` and opens the mounted Admin root without relying on a missing-slash redirect

#### Scenario: Branch and optional runtime metrics remain compatible

- **WHEN** generated snapshot data contains or omits branch, CPU, or RAM values
- **THEN** existing branch fallback, live-metric visibility, single-status, publication action, and runtime control behavior remain unchanged
