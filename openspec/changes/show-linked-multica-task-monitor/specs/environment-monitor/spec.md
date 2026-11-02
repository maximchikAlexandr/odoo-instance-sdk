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

`Snapshot.schema_version` MUST always be `5`. Version 5 is additive from version 4: `ProjectSummary` and `EnvironmentSnapshot` gain only `facts: tuple[EnvironmentFactsSummary, ...] = ()`; all version-4 fields, ordering, filtering, reconciliation, partial-result, and removed-row meanings remain unchanged. `generated_at` MUST be timezone-aware UTC. Projects SHALL be ordered by project ID and environments by environment ID. `GET /api/v1/snapshot` SHALL return default non-removed schema-v5 JSON. CLI envelope version remains `1` and independent of snapshot schema version.

`project_id=None` SHALL select all discovered projects; a matching opaque ID SHALL select that project/environments; an unknown ID SHALL return empty tuples. `include_removed` governs environment rows only. `ProjectSummary.environment_count` SHALL count included rows. Facts SHALL be deterministically ordered by provider ID, SHALL contain no private binding fields, and SHALL default to empty for core-only installs, unbound owners, or provider failure.

#### Scenario: Full snapshot shape

- **WHEN** `monitor.snapshot()` runs with linked and unlinked environments
- **THEN** `schema_version==5`, every version-4 field is preserved, and only proven available fact summaries appear under their owners

#### Scenario: Core-only snapshot has empty facts

- **WHEN** no environment-facts package is installed
- **THEN** schema version is 5 and every project/environment has `facts==()`

#### Scenario: Removed-only project is conditional

- **WHEN** one project has only removed environments
- **THEN** it is absent by default and present with unchanged stopped/null behavior under `include_removed=True`

#### Scenario: Project filter narrows result

- **WHEN** a known project ID is requested
- **THEN** projects, environments, and facts are restricted to that project from the same sample

#### Scenario: Unknown project filter returns empty

- **WHEN** an unknown project ID is requested with removed rows enabled
- **THEN** projects and environments are empty and no provider receives an out-of-scope owner

#### Scenario: Version 4 migrates additively to version 5

- **WHEN** a version-4 fixture without optional facts is collected
- **THEN** all prior fields/order are unchanged, schema version becomes 5, and each facts tuple is empty

## ADDED Requirements

### Requirement: External-task facts remain isolated and private

The monitor SHALL pass private external-task binding evidence only to the isolated provider process and SHALL expose only returned `ExternalTaskSummary` values. A missing, incompatible, failed, timed-out, oversized, duplicate, or ambiguous provider response SHALL discard only the affected facts and SHALL NOT fail, delay beyond the existing hard deadline, or hide the core owner.

#### Scenario: Provider timeout preserves snapshot

- **WHEN** a provider exceeds the configured hard deadline
- **THEN** it is terminated, its facts are absent, and the remaining schema-v5 snapshot is returned

#### Scenario: Duplicate external tasks are ambiguous

- **WHEN** more than one summary claims an external task for one owner
- **THEN** that owner's external-task fact is omitted rather than selected by provider order
