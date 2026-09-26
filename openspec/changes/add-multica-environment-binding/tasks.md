# Tasks

## 1. Consumed contracts and existing SDK adapters

- [ ] 1.1 Verify the exact core source/COPY APIs consumed from MYL-271/272 and #69's minimal workspace scaffold. Record versions in `research.md`; verify named-source and retained-UUID COPY contracts, without gating on detached readiness, retention or OpenSpec-progress features.
- [ ] 1.2 Reuse public `multica-py.cli.command_command` with two narrow decoders for native checkout stdout and daemon-status JSON. Test fixed argv, bounded options, single absolute path, malformed/redacted output, missing identity fields, cancellation/unknown outcome and secret projection using a fake executable/client. Document the existing public API recipe; no upstream SDK implementation task.

## 2. Core adoption and owned-only lifecycle

- [ ] 2.1 Add only necessary additive core catalog/public evidence for code ownership, SDK artifact root, configured project and external Git identity. Verify conservative legacy ownership, fresh/upgraded schema equivalence and one migration head; document forward-only migration.
- [ ] 2.2 Add public `adopt_command`/`adopt` by reusing COPY provisioning after code acquisition. Parameterize linked-worktree/independent-clone, explicit source/base, wrong/dirty checkout, secret isolation, no implicit venv and plan parity. Reuse core locks for concurrent reservation and same-input UUID retry without restore; test failed/conflicting records and retry after ordinary edits. Document first-adoption versus retry preconditions.
- [ ] 2.3 Update the affected core list/cwd/config/sync/runtime/diagnostic paths to honor separate project/code/artifact identity. Verify independent-clone lookup by project, UUID and cwd and startup with rebased addon paths and isolated data; document use through existing lifecycle commands.
- [ ] 2.4 Make rollback/removal preserve caller-owned code and its parent, even dirty/missing/replaced, while removing only proven owned artifacts. Parameterize symlink containment, active runtime, unknown ownership and partial cleanup; preserve normal SDK-owned checkout behavior and document safe recovery.

## 3. Minimal integration package

- [ ] 3.1 Add only the independently versioned `odcli-multica` member to the shared workspace with public dependencies and package-qualified releases. Verify no-sources wheel builds, standalone/combined tool installation and no undeclared/private imports; document setup without another project configuration file.
- [ ] 3.2 Add the small composition client and frozen `TaskContext` with read-only context SDK/CLI. Reuse typed issue/run operations and the bounded daemon decoder. Test explicit workspace/project/issue/run/repository/runtime identity, same-host containment, pagination and missing/forwarded filesystem evidence; document exact inputs and no persistence.
- [ ] 3.3 Add thin `prepare_command`/`prepare` and `env prepare`: observational preflight followed by the existing captured core adoption command/result. Test no implicit bind/start/task mutation, wrong context failing before effects, partial recovery and idempotent retry. Document two-phase composition and caller storage of separate context plus environment UUID.
- [ ] 3.4 Establish and document the narrow extension-facing contract directly in existing core `commands.output`, without a new facade module. Test SDK/CLI Rich/JSON/TOON parity, one machine document, redaction, dry-run and interruption for both new leaves. Preserve the core leaf inventory and package-local extension tests.

## 4. Integrated acceptance and publication

- [ ] 4.1 Run fake-boundary integration: native checkout result → context → adopted COPY → explicit core start/status → stop/remove. Assert one checkout/target DB, isolated filestore, stable retry UUID and no borrowed-code deletion; cover two tasks in one project without resource rewrites. Verify no project-link or binding files are created.
- [ ] 4.2 Repeat in an explicitly approved disposable native daemon/Odoo fixture, proving actual task cwd, credentials/permissions, same-host evidence and owned-only cleanup. Retain sanitized version evidence; no real customer data and no claim that static research equals live acceptance.
- [ ] 4.3 Run core-only, extension-only and combined installed-wheel smoke, supported compatibility and currently available all-member smoke, Ruff/strict mypy, schema/architecture/command contracts, diff check and strict OpenSpec validation. Publish only changed distributions after gates; leave planning checkboxes incomplete until implementation evidence exists.
