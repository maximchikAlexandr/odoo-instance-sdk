# Multica checkout with an Odoo environment

## Why

The self-service analysis workflow needs the code checkout used by a Multica task to be the same checkout used by its isolated Odoo database and runtime. Issue #70's proposed per-environment `local_directory` registration is not task-scoped: Multica permits only one such resource per project and daemon, so it cannot safely route several task checkouts.

## What Changes

- Deliver the focused checkout/binding part of #70 as the independently installed `odcli-multica` distribution, depending only on public Odoo Instance SDK and `multica-py` APIs.
- Reuse native `multica repo checkout` through a required typed `multica-py` command; preserve existing daemon JSON identity fields in that SDK for same-host verification. Multica remains the owner of its Git checkout, branch, task context and cleanup. Do not simulate its private registration.
- Add a tracker-neutral core `EnvironmentResource.adopt_command()` to provision Odoo against an existing caller-owned Git checkout, reusing COPY, configuration, Python, provenance and lifecycle primitives. Persist separate code ownership and SDK artifact-root evidence.
- Expose explicit phase boundaries: native checkout first; `odcli-multica env prepare` adopts its verified result; `env bind`, `env status` and `env unbind` manage a small task/environment association. No hidden second worktree and no all-in-one dynamically replanned command.
- Validate workspace, project, issue, run and machine context before provisioning. Require explicit COPY source selection and preserve the source/secret/retention contracts owned by MYL-271/272.
- Support exact-ID retry, partial results, non-destructive unbinding, and diagnostics for missing/replaced external checkouts. Start/readiness/stop remain existing core runtime operations, called separately by the workflow.
- Move daemon metrics, usage/token attribution and inventory/watch enrichment to [later issue #105](https://github.com/maximchikAlexandr/odoo-instance-sdk/issues/105). They do not block this technical-specification workflow.

## Capabilities

### New Capabilities

- `multica-environment-binding`: Native checkout handoff, explicit project/task context, extension SDK/CLI binding and recoverable phase results.

### Modified Capabilities

- `development-environment`: Public adoption of a caller-owned checkout and ownership-aware lifecycle without Git creation/deletion.
- `packaging`: Independently installable extension member using the workspace foundation owned by #69; core isolation remains mandatory.

## Impact

Core changes are limited to the environment resource, additive catalog evidence/migration, project/runtime resolution, cleanup and focused tests. The extension lives in `packages/odcli-multica`; the typed native checkout operation belongs in the separately maintained `multica-py`, not a copied subprocess/HTTP adapter here. The source research and complete #70 disposition are in `research.md` and `issue-70-scope.md`.

MYL-271/272 are consumed as predecessor contracts, not reimplemented. Live verification on 2026-09-26 found planning on branch `feat/add-self-service-odoo-control-plane` at `f94fea797fe1fe6fc6575bf6afb7f76d5960f45c`, MYL-271 in progress and MYL-272 in backlog; their implementation is not present in the inspected main. Integration acceptance waits for those APIs to land. #69's workspace scaffold and a compatible published `multica-py` checkout contract are additional explicit prerequisites.

## Non-goals

No workflow engine, Temporal worker, skill authoring, task dispatch, role system, anonymizer, report generator, business-analysis engine, dashboard, billing, generic plugin framework, automatic package/daemon installation, or automatic changes to Multica project resources. No promise that an environment binding grants access rights, that neutralization anonymizes data, or that registration proves Odoo readiness.
