## Summary

Deliver an independently installable `odcli-multica` package that prepares an isolated Odoo environment on a **native Multica task checkout**. Keep one checkout owner: Multica owns code/task routing, core Odoo Instance SDK owns its database, filestore, generated configuration and runtime lifecycle.

This is a primitives-only slice. Skills/scripts/Temporal activities compose operations and retain their results. Daemon metrics and task/token inventory annotations are deferred to #105.

## Selected integration

Two separately captured phases:

1. Native `multica repo checkout URL --ref REF`, invoked through the existing public `multica-py` bounded command API.
2. `odcli-multica env prepare PATH`, validating exact task context and delegating to a new generic public core adoption operation.

Read-only `odcli-multica context PATH` returns verified task/context facts. Preparation returns the existing core environment result/UUID. The caller can save both; no extension binding registry is required.

Source research rejects the original OdCLI-owned checkout plus per-issue `local_directory` registration: Multica selects that resource at project/daemon level, allowing one matching directory, not independent issue routes. Do not rewrite project resources, emulate daemon registries, create a project per environment, copy/move code into a second root or introduce another general-purpose checkout command.

## Scope

### 1. Core adoption and safety

- Add inspectable `EnvironmentResource.adopt_command(project, checkout_path, options=...)` and delegating `adopt()`, reusing COPY provisioning.
- Support caller-owned linked worktrees and independent clones, explicit compatible base and exactly one explicit source: configured remote, retained backup UUID or source database.
- First adoption checks repository, branch/HEAD against explicit base, Git-reported dirty state, project/source identity and normal disk/Python/port preconditions. Reject mismatches unchanged; do not reset code, copy credentials or enable a venv implicitly.
- Record only necessary code ownership, explicit SDK artifact root and configured-project/external-checkout identity in the existing core catalog. Update affected lookup/config/runtime/cleanup paths; do not infer ownership from the checkout's parent.
- Reserve under existing core locks. Same-input ready retry returns the same UUID without another backup/restore; incompatible inputs conflict, incomplete records retain recovery evidence. Normal later edits do not invalidate a matching retry.
- Rollback/removal deletes only proven SDK-owned artifacts and never external code, Git metadata or branches. Dirty, missing or replaced external code must not prevent independently safe cleanup by UUID. Preserve active-runtime, database-ownership and conservative legacy guards.

### 2. Small integration SDK/CLI

Use a concrete `MulticaOdooClient(core_client, multica_client)` with public `context_command`/`context` and `prepare_command`/`prepare`.

```text
odcli-multica context PATH --project CORE_PROJECT --multica-project PROJECT --issue ISSUE --run RUN
odcli-multica env prepare PATH --project CORE_PROJECT --multica-project PROJECT --issue ISSUE --run RUN --base REF (--remote NAME | --backup-id UUID | --source-db NAME)
```

- Use existing scoped Multica configuration for server/workspace/authentication. No additional project-link TOML or configuration CRUD.
- Validate issue/project/workspace/run membership, local owning daemon/runtime, exact repository and checkout containment under the run's absolute current/durable directory. Explicit IDs override no conflicting hints; never infer from names, branch or list order. Incomplete evidence/pagination fails before effects.
- A reachable loopback endpoint or equal path string does not prove a shared filesystem. Cross-machine/forwarded filesystems without proof are unsupported.
- Context is frozen observational data with timestamp, not an access policy or lifetime lock.
- Preparation performs read-only preflight then returns/runs the exact captured core adoption command. No new command compositor, executor or combined-result wrapper.
- Existing core commands provide status/start/stop/remove. No implicit Odoo startup, task dispatch/status changes, persistent binding/history/locks, `project link/show`, `env bind/unbind/status` or remote metadata writes.

### 3. Existing multica-py is sufficient

Verified at remote main `d1b5f0e154c5587eca4cebd8bd2a6d39ae3d4d06`, targeting Multica v0.5.3:

```python
checkout = client.cli.command_command("repo", "checkout", url, "--ref", ref, options=options)
daemon = client.cli.command_command("daemon", "status", "--output", "json", options=options)
```

The existing public API provides captured commands, scoped options, transport, redacted `CliResult`, timeouts and cancellation. Its bounded-operation guard permits both calls. Keep two narrow integration decoders: one absolute-path stdout result and the necessary daemon JSON identity subset. Reject malformed/redacted/missing facts; do not parse human tables or stderr as data. Reuse existing typed issue/run operations for all other context.

The typed repository resource lacks checkout and the current typed `DaemonStatus` omits identity fields, but these convenience-wrapper gaps are **not implementation blockers**. No mandatory `multica-py` change/release is needed. Do not use private modules, raw HTTP or a copied subprocess runner. If suitable upstream typed wrappers later appear, replace the two adapters.

## Packaging and dependencies

- Separate distribution `odcli-multica`, import `odcli_multica`, executable `odcli-multica`, under `packages/odcli-multica`; core stays at `src/odoo_instance_sdk` and imports no Multica dependency.
- Reuse #69's one-time root uv workspace/shared lock scaffold, not its full feature. Consume only needed MYL-271/272 source/COPY contracts, not detached-readiness/retention completeness. Recheck merged APIs at implementation start rather than inferring implementation from ticket status.
- Independent version, compatible published core/Multica dependency ranges and `odcli-multica-v<VERSION>` release tags; no lockstep releases or automatic installation/startup.
- Support standalone `uv tool install odcli-multica` and combined `uv tool install --with-executables-from odcli-multica odoo-instance-sdk`. Core-only installation contains no extension dependencies.
- Support the narrowly used existing core `commands.output` functions/types as a documented public extension contract. Do not add a facade module, copied serializers or generic renderer/plugin hierarchy.
- Core-only, extension installed-wheel, combined-tool and available all-member smoke tests; `uv build --package odcli-multica --no-sources`, Ruff and strict mypy per distribution. Publish only changed distributions.

## Acceptance criteria

- [ ] Two tasks in one project use their own native checkout paths without project-resource changes or duplicate Git checkouts.
- [ ] Linked-worktree and independent-clone adoption preserve configured core project identity and use isolated COPY database/filestore.
- [ ] Exact context/source/base checks reject wrong host, project, repository, dirty initial input and incomplete facts before provisioning.
- [ ] Preview captures the actual phase plan; no mutation during preflight/dry-run and no hidden later argv construction.
- [ ] Same-input retry returns the same environment UUID; failures retain completed-phase/core recovery IDs without false distributed rollback.
- [ ] Dirty/missing/replaced borrowed code is never deleted; only independently proven owned resources can be cleaned up.
- [ ] No project-link/binding files are created. Caller can retain context and UUID and use core lifecycle while Multica is unavailable.
- [ ] Fake executable/client tests verify the existing public raw SDK calls, strict output decoding, timeout/cancellation, redaction and membership/host/pagination checks without live user configuration.
- [ ] Rich/JSON/TOON agree; machine stdout contains one bounded sanitized document. No `Any`, bare `object` or private imports in extension production APIs.
- [ ] Clean-wheel installation/compatibility gates pass and core-only checkout/help remain unchanged.
- [ ] An explicitly approved disposable native-daemon/Odoo acceptance test proves actual task permissions/cwd and owned-only cleanup; source inspection alone is not this test.

## Deferred and excluded

#105 owns daemon CPU/RSS/process attribution, issue/run/task-count annotations, token/cache usage and reuse of existing watch loops. It must not assume a binding store is delivered by this issue; define minimal attribution when that feature is implemented.

No task/UI lifecycle replacement, scheduling, machine selection, background worker, reporting/analysis skill, Temporal workflow, anonymizer, role system, billing, dashboard, metric history, universal provider abstraction or permanent code lease. Native Multica can garbage-collect its checkout; caller controls Odoo lifecycle and persists workflow outcomes.
