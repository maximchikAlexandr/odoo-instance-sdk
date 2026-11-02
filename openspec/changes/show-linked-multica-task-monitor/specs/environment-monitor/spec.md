## MODIFIED Requirements

### Requirement: One `EnvironmentMonitor` collector

SDK MUST предоставлять один public collector primitive в `odoo_instance_sdk.resources.monitor`:

```python
from odoo_instance_sdk import EnvironmentMonitor

monitor = EnvironmentMonitor()
snapshot = monitor.snapshot(project_id=None, include_removed=False)

async for snapshot in monitor.watch(
    interval=2.0,
    project_id=None,
    include_removed=False,
):
    await publish(snapshot)
```

`EnvironmentMonitor` MUST быть `@dataclass(frozen=True, slots=True, kw_only=True)` без обязательных полей (default constructor `EnvironmentMonitor()`); all core dependencies (catalog, `psutil`, Docker CLI runner, Git CLI) resolve internally from existing internal helpers. Configurability SHALL remain optional keyword injection for tests. The one explicitly allowed public extension boundary is the narrow read-only environment-facts protocol discovered through the existing Python entry-point group; it SHALL NOT become a general collector, lifecycle interface, factory, or ABC hierarchy.

`snapshot(project_id: str | None = None, *, include_removed: bool = False) -> Snapshot` MUST perform one coherent collection and return a typed immutable `Snapshot`. It SHALL own catalog discovery, removed-row selection, backup/artifact reconciliation, port observation, project grouping, runtime/PID/resource metrics, Git activity, storage footprint, and one bounded environment-facts enrichment after core rows are captured. Collection SHALL remain non-mutating and non-blocking with respect to foreground Odoo processes.

`watch(interval: float = 2.0, project_id: str | None = None, *, include_removed: bool = False) -> AsyncIterator[Snapshot]` MUST remain a thin async generator over `snapshot(...)` and stdlib `asyncio.sleep(interval)`, without its own scheduler, queue, thread pool, or background task. Consumer cancellation SHALL leave no background process or thread. `interval < 0.1` SHALL raise `ValueError`.

`EnvironmentMonitor` MUST remain the single owner of discovery, reconciliation, metrics, and facts sampling. FastAPI, CLI `monitor`, `odcli ps`, `odcli env list`, and React UI SHALL consume that one snapshot or its pure projections and MUST NOT duplicate collection. `CheckoutInventory` SHALL copy facts already captured by the snapshot rather than invoking providers again. No `CliEnvironmentSnapshot` or second task collector SHALL be added.

#### Scenario: Default constructor works

- **WHEN** `EnvironmentMonitor()` is constructed without arguments
- **THEN** catalog path and core providers resolve lazily, and absence of optional entry-point packages is not an error

#### Scenario: Snapshot is typed immutable

- **WHEN** `monitor.snapshot()` returns
- **THEN** the result and all nested core/fact models are frozen unknown-field-forbidden `msgspec.Struct` values

#### Scenario: Provider runs once per sample

- **WHEN** SDK, `env list`, `ps`, HTTP, or UI consumes one monitor sample with an installed facts provider
- **THEN** that provider is invoked at most once for the sample and every downstream projection reuses its frozen result

#### Scenario: Removed inclusion is query-owned

- **WHEN** `monitor.snapshot(include_removed=True)` runs
- **THEN** the same typed graph includes active and removed catalogue environments without a second catalogue or facts read

#### Scenario: Watch is cancellable without leaks

- **WHEN** a consumer cancels `monitor.watch()` mid-iteration
- **THEN** no watch or provider background thread/process survives consumer exit

#### Scenario: Interval floor enforced

- **WHEN** `monitor.watch(interval=0.05)` is called
- **THEN** `ValueError` is raised before any iteration

### Requirement: Snapshot top-level contract

`Snapshot.schema_version` MUST always be `5`. Version 5 is an additive migration from version 4: every `ProjectSummary` and `EnvironmentSnapshot` gains only required `facts: tuple[EnvironmentFactsSummary, ...] = ()`; every version-4 project field including `runtime`, every environment field including `pgadmin`, required `observed_port` and `artifacts`, and all v4 collection/filter/removed-row meanings remain unchanged. `generated_at` MUST be tz-aware UTC. `projects` ordered by `project_id` ascending; `environments` ordered by `id` ascending. `GET /api/v1/snapshot` returns the default non-removed version-5 JSON (msgspec encode). `odcli env list --format json` wraps the frozen `CheckoutInventory` model in CLI envelope v1 `result`/`data` (`command="env.list"`); process/resource columns live in `odcli ps`, not in `env list`. CLI envelope version remains `1` and is independent of snapshot schema version.

`project_id` filter: `None` MUST select all discovered projects; an opaque id matching a discovered project MUST select that project and its environments; an unknown id MUST return `projects == ()` and `environments == ()` without raising. A registered project exists even with no environment rows. `include_removed` SHALL govern environment rows only: with `include_removed=False`, removed rows are omitted while their registered project remains; with `include_removed=True`, a project containing only removed environments MUST appear with those rows, all from the single atomic catalog selection. `ProjectSummary.environment_count` MUST count the environments included in the returned snapshot; project counts and partial-result behavior remain unchanged. pgAdmin eligibility for an included removed row MUST be `environment_not_ready` without adding a database, port, health, or Docker probe.

Facts SHALL be deterministically ordered by provider ID, SHALL contain no private binding fields, and SHALL default to empty for core-only installs, unbound owners, or provider failure. Facts SHALL be collected from the same selected owners and canonical sample as the version-4 fields, so they SHALL NOT change atomic selection, ordering, project filters, removed-row behavior, cache keys/TTL/single-flight, invalidation, redaction, or the existing CLI/API/generated-client/dashboard consumer contracts.

#### Scenario: Full snapshot shape

- **WHEN** `monitor.snapshot()` runs with two projects each having environments
- **THEN** `schema_version==5`, `projects` contains two `ProjectSummary`, and `environments` contains all non-removed environments of both projects with every version-4 field plus pgAdmin eligibility and owner-scoped facts

#### Scenario: Default full snapshot shape

- **WHEN** `monitor.snapshot()` runs with two projects each having active environments
- **THEN** `schema_version==5`, `projects` contains two `ProjectSummary`, and `environments` contains all non-removed environments with every version-4 field plus pgAdmin eligibility and owner-scoped facts

#### Scenario: Core-only snapshot has empty facts

- **WHEN** no environment-facts package is installed
- **THEN** schema version is 5 and every project/environment has `facts==()`

#### Scenario: Removed-only project is conditional

- **WHEN** one project has only removed environments
- **THEN** it is absent from `snapshot()` and present with those rows in `snapshot(include_removed=True)`; each included removed environment retains the v2 stopped/null runtime, artifact, and no-port-probe semantics, has `pgadmin.state=="environment_not_ready"`, and may carry only facts scoped to that selected owner

#### Scenario: Removed environment keeps v2 behavior in v3

- **WHEN** `monitor.snapshot(include_removed=True)` includes a removed environment
- **THEN** its MYL-55 stopped/null runtime, artifact, and no-port-probe semantics are unchanged in the additive v5 snapshot, `observed_port is None`, `pgadmin.state=="environment_not_ready"`, and facts collection adds no database, port, health, or Docker probe

#### Scenario: Project filter narrows result

- **WHEN** `monitor.snapshot(project_id="project_comerta_7e3d8a01")` runs
- **THEN** `projects` contains only the matching `ProjectSummary`, `environments` contains only that project's non-removed environments, and facts are restricted to those owners from the same sample

#### Scenario: Unknown project filter returns empty

- **WHEN** `monitor.snapshot(project_id="project_unknown", include_removed=True)` runs
- **THEN** `projects == ()` and `environments == ()`, no exception is raised, and no provider receives an out-of-scope owner

#### Scenario: Version 4 migrates additively to version 5

- **WHEN** a version-4 fixture without optional facts is collected
- **THEN** all prior fields/order are unchanged, schema version becomes 5, and each facts tuple is empty

#### Scenario: Version 3 migrates additively through version 4 to version 5

- **WHEN** a frozen version-3 fixture is collected with no project-owned runtime or optional facts
- **THEN** schema version becomes 5, every version-3 field and ordering is unchanged, each project adds `runtime=null`, and each project/environment adds `facts==()`

#### Scenario: Version 4 preserves cache, redaction, and consumers

- **WHEN** live, stopped, null, mixed-owner, filtered, cached, or redacted snapshots pass through JSON/TOON, API, generated OpenAPI/client, and dashboard tests
- **THEN** the same atomic selection, ordering, filter, partial-result, cache-key/TTL/single-flight, invalidation, and redaction contracts apply to the canonical version-5 field without a parallel DTO or endpoint, and facts expose no private binding fields

## ADDED Requirements

### Requirement: External-task facts remain isolated and private

The monitor SHALL pass private external-task binding evidence only to the isolated provider process and SHALL expose only returned `ExternalTaskSummary` values. A missing, incompatible, failed, timed-out, oversized, duplicate, or ambiguous provider response SHALL discard only the affected facts and SHALL NOT fail, delay beyond the existing hard deadline, or hide the core owner.

#### Scenario: Provider timeout preserves snapshot

- **WHEN** a provider exceeds the configured hard deadline
- **THEN** it is terminated, its facts are absent, and the remaining schema-v5 snapshot is returned

#### Scenario: Duplicate external tasks are ambiguous

- **WHEN** more than one summary claims an external task for one owner
- **THEN** that owner's external-task fact is omitted rather than selected by provider order
