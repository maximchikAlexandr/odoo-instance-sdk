## Why

An OdCLI environment prepared from a verified Multica checkout loses its task context after the originating run ends, so operators cannot see the linked task, its current status, or whether an agent is actually executing it from the existing CLI and monitor card. GitHub #105 requires that narrow context to survive monitor restarts without guessing from branch names or introducing another control plane.

## What Changes

- Persist one minimal private external-task binding when `odcli-multica` successfully prepares or reuses a caller-owned environment: provider/server/workspace/issue identity plus the environment UUID, canonical checkout path, and runtime-host identity used to prove the association.
- Add one provider-neutral frozen external-task capability slot, its shared renderers, and a narrow read-only provider contract to core. The optional `odcli-multica` package supplies only verified binding production and Multica-to-core data adaptation: it restores proven bindings and fetches the issue identifier/permalink, current status, and `active | inactive | unknown` activity through public `multica-py` operations, without duplicating core collection, models, HTTP/CLI/UI projection, or card rendering.
- Enrich one canonical monitor sample once per refresh, deduplicating Multica issue reads across environments, and project that result through the Python SDK, `odcli env list`/watch, `odcli ps`, the typed HTTP snapshot, generated web client, and the existing `RuntimeCard`.
- Keep activity independent from issue/Odoo state: only a run whose upstream status is exactly `running` is active; queues are not active, and failures, unknown statuses, or incomplete pagination produce `unknown` rather than a false negative.
- Preserve the task link when dynamic refresh fails, label status/activity as unknown or stale, and omit the entire block for main checkouts, unbound environments, and core-only installations.
- Keep core independent of Multica. The browser never calls Multica, the extension stays optional and co-installed in the monitor Python environment, and no task mutation, run dispatch, binding CRUD, history, new poller, daemon, or dashboard is added.

## Capabilities

### New Capabilities

- `external-task-observability`: Proven persistent environment-to-task association, frozen task/status/activity semantics, safe links, deduplicated refresh, and degradation rules.

### Modified Capabilities

- `multica-environment-binding`: Successful prepare/reuse records the minimal binding and the optional package provides the Multica task facts provider through public `multica-py`.
- `environment-monitor`: The canonical snapshot accepts one bounded optional external-task enrichment and exposes the typed result without leaking private binding data.
- `dashboard-http-api`: The existing snapshot/OpenAPI response carries external-task summaries to the generated web client.
- `cli-odcli`: `env list`/watch render the typed task summary consistently in Rich, JSON, and TOON from the same sample.
- `process-inventory`: `odcli ps` adds only a compact task annotation to the existing linked-environment section.
- `packaging`: The optional extension registers the existing supported provider entry point and documents co-installation in the monitor environment.

## Impact

The change affects the environment catalogue and migration path, public frozen monitor/process models, monitor collection/projection, CLI renderers, FastAPI/OpenAPI and generated TypeScript, the existing React `RuntimeCard`, and `packages/odcli-multica` models/provider/client/packaging/tests. It reuses the pinned `multica-py` dependency and current refresh/watch loops; no new runtime dependency is added to core.
