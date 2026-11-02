## Context

`EnvironmentMonitor.snapshot()` is the canonical SDK/HTTP/dashboard sample. `EnvironmentResource.checkout_inventory()` and `EnvironmentMonitor.processes()` already project that monitor state for `odcli env list` and `odcli ps`. Environment-facts providers are already discovered through the `odoo_instance_sdk.environment_facts` entry-point group and isolated by a hard timeout, but today they run only while building `CheckoutInventory`; their generic string details are insufficient for an accessible link/status/activity UI. `VerifiedTaskContext` proves Multica issue/run/workspace/runtime/daemon/path identity during `odcli-multica env prepare`, but it is intentionally transient and is not available after a monitor restart.

GitHub #105 requires only three user-facing values for a registered environment: linked task identity/link, issue status, and current executing-agent activity. The solution must preserve core-only operation, never infer association from branch/basename, avoid a browser-to-Multica call, reuse the existing refresh loops, and not expose credentials or private binding fields through HTTP or CLI.

## Goals / Non-Goals

**Goals:**

- Preserve a verified environment-to-Multica-issue association across the originating run and process restarts.
- Produce one frozen typed external-task summary per linked environment and reuse it across SDK, CLI, process inventory, HTTP, and UI projections.
- Distinguish `active`, `inactive`, and `unknown` without treating queue presence or incomplete data as execution.
- Keep links safe and usable when dynamic Multica reads fail, while making freshness/unknown state explicit.
- Batch and deduplicate provider reads within the existing monitor sample and isolate extension failure from core monitoring.

**Non-Goals:**

- CPU/RAM/PID, token/cache/cost/budget, run counts/history, exact run-to-checkout attribution, dispatch, status mutation, or agent controls.
- A binding registry or CRUD surface, second database, history/cache of task status, separate collector/poller/service, generic tracker framework, new dashboard, notifications, or dynamic React plugins.
- Guessing links for main checkouts, legacy environments without verified evidence, or environments whose current path/host identity conflicts with the stored proof.

## Decisions

### D1. Store one write-on-prepare binding in the existing environment catalogue

Core adds a frozen `ExternalTaskBinding` accepted only as an optional input to caller-owned environment adoption. The environment catalogue row remains the authority and gains nullable columns for provider, server identity, workspace ID, issue ID, issue identifier, verified permalink, canonical checkout path, runtime ID, and daemon ID. The environment UUID is already the row key, so no second mapping table is needed. Values are private catalogue metadata and are not serialized in public monitor models.

`odcli-multica.prepare_command()` obtains these fields from the same freshly verified `VerifiedTaskContext`, public `Issue`, `Issue.permalink()`, and daemon evidence used before adoption. A successful new adoption writes the binding in the same catalogue transaction as the environment. A ready idempotent reuse may fill an absent binding from a new complete verification, but it must reject replacement when any stored provider/server/workspace/issue/path/runtime/daemon identity differs. Failed preparation writes no binding. There is no bind/list/update/delete command.

The permalink is accepted only when it is absolute HTTP(S), has no user information, and is produced by the configured public `multica-py` permalink surface for the same issue. Persisting the verified identifier/link is necessary so the link remains available during a later API outage; credentials, profile tokens, run IDs, task roots, and arbitrary metadata are never stored.

Alternative considered: keep `VerifiedTaskContext` in memory or infer the issue later from branch/path. That cannot survive restart and violates the no-guessing requirement. A separate binding database/CRUD API would duplicate the catalogue and is deliberately omitted.

### D2. Enrich the canonical snapshot once through the existing provider mechanism

Core promotes the existing environment-facts protocol and summary to a public, frozen, typed boundary while retaining the `odoo_instance_sdk.environment_facts` entry-point group and per-provider process isolation. A provider receives immutable checkout rows plus the matching private `ExternalTaskBinding` values; it returns `EnvironmentFactsSummary` values. The summary gains an optional frozen `ExternalTaskSummary` containing only:

- provider, task identifier, safe URL;
- current issue status or `None`;
- `active | inactive | unknown` activity;
- observation time, `current | unavailable` freshness, and one safe bounded unavailability reason.

This typed `ExternalTaskSummary` is the one provider-neutral capability slot owned by core. Core owns its validation, conflict rule, snapshot/OpenAPI/generated-client schema, CLI/process projections, and the existing `RuntimeCard` renderer. Providers own only translation from an external system into this slot. They cannot supply React/TypeScript/CSS, Click/Rich renderers, HTTP routes, alternate models, or provider-specific browser behavior. Consequently `odcli-multica` contains no copy of the card, activity indicator, serialization, provider runner, cache, or downstream projection logic.

Provider collection moves into `EnvironmentMonitor.snapshot_command()` after core catalogue/runtime collection and before the final result is returned. `ProjectSummary` and `EnvironmentSnapshot` gain `facts`, and snapshot schema version advances additively from 4 to 5. `CheckoutInventory` copies those facts to its rows instead of invoking providers again; `ProcessInventory` extracts the single external-task value as a compact annotation. Thus one monitor refresh invokes each provider once and every downstream surface observes the same result.

Facts from missing, incompatible, failed, or timed-out providers remain empty and never hide a project/environment or fail the snapshot. Duplicate provider IDs and more than one external-task result for an owner are rejected for that owner. The existing hard provider deadline remains the outer bound; providers must also bound their own remote calls and concurrency.

Alternative considered: add a dashboard endpoint that independently calls Multica. It would create a second collection path, disagree with CLI/SDK, and let extension failure affect the browser boundary. Calling providers separately from each projection was rejected for the same reason.

### D3. `odcli-multica` restores proof and maps activity conservatively

The extension registers one provider in the existing entry-point group. For each binding it first requires all of the following: provider is `multica`; environment UUID matches the catalogue owner; the current canonical checkout path equals the stored path; the configured server/workspace and current daemon/runtime identity equal the stored identities; and the checkout remains on the local host. Mismatch, ambiguity, or missing evidence produces no external-task summary, so the UI does not show an unconfirmed association.

Validated bindings are grouped by `(server, workspace, issue)` and one bounded public `multica-py` read is performed per unique group per refresh. The provider uses `issues.get(issue_id)`, `Issue.permalink()`, and the complete `issues.runs(issue_id, limit=100)` page. It uses a fixed concurrency ceiling of four unique issues and a per-operation timeout below the core provider deadline. Multiple environments for one issue receive the same immutable observation.

Run activity mapping is closed and explicit:

- any returned run with upstream status exactly `running` makes activity `active`;
- a complete page containing only `queued`, `dispatched`, `waiting_local_directory`, `completed`, `failed`, or `cancelled` (or no runs) makes activity `inactive`;
- an incomplete page with no `running` item, an unknown future status, timeout, authorization/transport/decode failure, or missing required page completeness evidence makes activity `unknown`.

Queue/dispatched/waiting states therefore never become green. A confirmed `running` item is sufficient even if older history is truncated. If issue fetch succeeds but run fetch fails, current identifier/link/status are returned with unknown activity. If dynamic issue fetch fails, the persisted identifier/link remain, while status is `None`, activity is `unknown`, freshness is `unavailable`, and only a sanitized reason category is exposed. No previous green state is cached or replayed.

Alternative considered: call `issues.runs(active=True)`. The current public CLI defines that filter as queued, dispatched, running, and waiting-local-directory, so it does not satisfy #105's executing-only meaning.

### D4. Keep projections additive and single-source

`odcli env list` Rich adds one provider column/cell derived from `ExternalTaskSummary`; JSON and TOON serialize the same schema-v5-derived `CheckoutInventory` without format-specific collection. Watch mode repeats the same one-sample path. `odcli ps` adds `Task <identifier> · <status-or-unknown> · <activity>` only to the Details cell of the existing linked checkout section; it creates no new process group and performs no second query.

The existing `GET /api/v1/snapshot` returns schema v5 with facts, OpenAPI is regenerated from the model, and the checked-in TypeScript client is regenerated through the existing script. Core selects the owner's single validated `ExternalTaskSummary` capability without branching on provider ID and passes it to the existing card. `multica` is data provenance, not a UI plugin key. No external-task capability means no block.

Alternative considered: add task fields independently to every SDK/CLI/HTTP model. That duplicates semantics and allows surfaces to drift; all projections instead retain one nested typed result.

### D5. The card communicates state independently of color

The existing core `RuntimeCard` receives the provider-neutral external-task summary and renders a compact block independent of Odoo runtime visibility. It neither imports `odcli-multica` nor checks `provider == "multica"`; any installed provider that satisfies the same typed capability gets identical core rendering. The task action includes the identifier and opens the verified URL in a new tab with `noopener,noreferrer`. The issue status is text. Activity uses green/red/gray form plus visible text and a focusable control or element whose accessible name and tooltip are respectively:

- active: “The task has an active agent run”;
- inactive: “The task has no active agent runs”;
- unknown: “Unable to verify agent run activity” plus a safe reason label.

Tooltips work on hover and keyboard focus. Stopped Odoo does not hide the task block. A failed refresh keeps the link but shows status/activity as unknown; it never displays the last green indicator as current. Existing card/runtime controls remain unchanged.

Alternative considered: encode activity only as a colored dot. That is inaccessible and makes red look like an Odoo or issue failure.

### D6. Optional installation remains explicit

`odcli-multica` declares its provider entry point and documents that the extension must be installed into the same Python environment that runs `odcli monitor`; finding an executable elsewhere on `PATH` is not sufficient for Python entry-point discovery. Core has no `multica-py` import or dependency, and the optional package owns no frontend bundle, card component, HTTP route, core-model fork, or CLI renderer. Without the package, discovery returns no Multica provider, facts remain empty, no task block is rendered, and every pre-existing core monitor/CLI/HTTP/dashboard path behaves normally. There is no auto-install, subprocess bridge, RPC, daemon, or dynamic JavaScript loading.

Alternative considered: invoke the companion executable from core when the package is absent. That introduces a second transport and credential boundary and is not needed for the three requested values.

### Ponytail full gate

No new subsystem is introduced. A separate binding store is unnecessary because the environment catalogue already owns durable environment metadata, so it is removed in favor of nullable row fields. A second Multica collector/polling loop is unnecessary because the canonical monitor refresh already feeds every surface, so provider execution is moved into that sample. A React plugin loader and companion daemon/RPC are unnecessary because the provider-neutral typed capability and existing core card renderer are sufficient; both are omitted. The only retained additions are the binding fields, one typed nested result/rendering path in core, and the optional data adapter required to cross the package boundary safely.

## Risks / Trade-offs

- **Catalogue migration leaves legacy rows unbound** → nullable columns preserve old rows; no link is shown until a future fully verified prepare/reuse records proof.
- **Provider deadline may expire when many unique issues are visible** → deduplicate, cap concurrency at four, use shorter per-operation timeouts, and return unknown/empty facts without delaying or failing the core sample beyond its existing hard deadline.
- **A saved permalink can become stale after server routing changes** → refresh from public `Issue.permalink()` when available; during outage retain the last verified link but label dynamic data unavailable.
- **Upstream adds a new run status** → closed mapping returns `unknown`, preventing a false inactive result until the contract is deliberately updated.
- **Multiple provider facts conflict** → deterministic provider IDs and single external-task-per-owner validation discard the ambiguous task summary rather than selecting by order.
- **Schema v5 affects generated clients and fixtures** → regenerate OpenAPI/TypeScript and update snapshot/CLI/process contract tests together; the change is additive and defaults facts to empty.

## Migration Plan

1. Add nullable catalogue columns and migration/backfill defaults without rewriting existing environment rows.
2. Add frozen binding/fact/summary models and schema-v5 projection with empty defaults; update OpenAPI and generated client fixtures.
3. Move existing provider invocation into canonical snapshot collection and prove one call per sample with timeout/error isolation.
4. Add `odcli-multica` binding creation, provider registration, conservative activity mapping, deduplication, and package documentation.
5. Add CLI/process/UI projections and focused tests, then run repository validation and the planned isolated end-to-end walkthrough.

Rollback may return binaries to the prior version because old code ignores the additive nullable columns. The migration does not remove columns or data. A later forward deployment can reuse proven bindings; manual column deletion or conversion is unnecessary.

## Open Questions

None. Future upstream run statuses remain `unknown` by contract until a separate reviewed change maps them.
