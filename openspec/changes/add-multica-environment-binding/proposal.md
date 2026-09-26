# Prepare Odoo on a native Multica checkout

## Why

The analysis workflow needs Multica's task checkout and Odoo's isolated database/runtime to use the same code. Reuse native Multica checkout and let core adopt its path; do not recreate Multica registration or build a second task/environment registry.

## What Changes

- Deliver independently installed `odcli-multica` using public Odoo Instance SDK and existing `multica-py` APIs. Core stays independent of Multica.
- Use existing `multica_client.cli.command_command()` for native checkout and daemon-status JSON. Two narrow output decoders replace the previously proposed mandatory upstream SDK changes.
- Add generic core `adopt_command()` / `adopt()` for caller-owned Git checkouts, reusing COPY provisioning and preserving separate code/artifact ownership and configured project identity.
- Expose read-only `context` and `env prepare` with explicit project, repository, issue/run and source/base inputs. Native checkout and Odoo preparation are two separate finite phases.
- Return context and the existing core environment result separately. The caller retains their association. No project-link file, binding registry/history, bind/unbind commands or extension runtime manager.
- Reuse core identity-based retries, runtime/status and owned-only cleanup. Telemetry/inventory enrichment stays in [#105](https://github.com/maximchikAlexandr/odoo-instance-sdk/issues/105).

## Capabilities

### New Capabilities

- `multica-environment-binding`: Verified, stateless task-to-environment handoff through context and preparation. The existing capability/change identifier is retained; it does not imply persistent binding storage.

### Modified Capabilities

- `development-environment`: Caller-owned checkout adoption and ownership-aware lifecycle without Git creation/deletion.
- `packaging`: Independent extension member and narrowly supported reuse of existing bounded output functions.

## Impact

Core changes cover adoption, necessary catalog ownership/project evidence, runtime/path resolution and cleanup. The extension lives in `packages/odcli-multica`; no implementation change in `multica-py` is required by this plan. Research and the full #70 split are recorded in `research.md` and `issue-70-scope.md`.

Consume the source/COPY contracts planned by MYL-271/272 without duplicating them. The inspected baseline had their planning branch but not their implementation in main; recheck actual APIs at implementation start. Only used source contracts and #69's workspace scaffold are dependencies. Detached readiness, retention, and #69's progress feature are not release gates for this change; use their existing APIs when available.

## Non-goals

No project configuration CRUD, persistent binding service, task dispatcher, Temporal worker, skills, reports, anonymizer, access-role system, dashboard, billing, plugin framework or automatic package/daemon installation. No project-resource rewrites, cross-machine provisioning or permanent checkout lease.
