# Design

## Context

See `proposal.md` for scope and `research.md` for pinned source evidence. Multica's project/daemon-wide `local_directory` cannot select a separate checkout per issue. Native checkout already establishes the task/code relationship. Core currently assumes it owns Git checkout placement and deletion, so adopting a path requires an explicit ownership distinction.

## Goals / Non-Goals

Provide finite SDK/CLI primitives, not the DOCX workflow. Keep two effectful phases, one Git checkout, one core environment catalog and no extension persistence. Skills/scripts/Temporal activities retain results and decide when to start, inspect, stop and remove Odoo.

## Decisions

### D1. Native checkout through the existing public SDK

Use the existing scoped `MulticaClient` and `OperationOptions` for server/workspace, cwd, environment and timeout. Invoke `client.cli.command_command("repo", "checkout", url, "--ref", ref, options=...)`. This public bounded API returns an inspectable captured `Command[CliResult]`; it already supplies transport, redaction, compatibility, cancellation and execution. Do not access private fields or add a runner.

Decode successful stdout as exactly one absolute path, allowing only the CLI's terminal line ending. Reject malformed, multiline, empty or redacted output. Stderr is diagnostic, never a path fallback. Confirm canonical Git root and repository identity before adoption. Timeout/cancellation means unknown checkout outcome, not permission to delete or immediately recreate it. Never use `--fresh` here.

The caller executes native checkout first, then captures Odoo preparation with its actual returned path. No composite command constructs later argv after mutation. The native CLI remains `multica repo checkout`; do not add an extension checkout clone or `--multica-issue` to core checkout.

### D2. Explicit inputs and read-only context, no configuration CRUD

Use a small `MulticaOdooClient(core_client, multica_client)` exposing `context_command`/`context` and `prepare_command`/`prepare`. Both accept the selected core project, exact checkout path, expected Multica project ID, issue ID and run ID. Server/workspace and credentials come from the already scoped public Multica client. Repository identity comes from trusted selected core project configuration; an ambiguous repository requires an explicit selection.

CLI equivalents:

```text
odcli-multica context PATH --project CORE_PROJECT --multica-project PROJECT --issue ISSUE --run RUN
odcli-multica env prepare PATH --project CORE_PROJECT --multica-project PROJECT --issue ISSUE --run RUN --base REF (--remote NAME | --backup-id UUID | --source-db NAME)
```

Reuse existing Multica profile/workspace configuration and explicit overrides rather than storing another mapping. No `.odcli/multica.toml`, project link/show, hostname registry or inference from task prose/branch names.

Context uses typed `issues.get_command` and `issues.runs_command` for membership and absolute work-directory evidence. Use the existing public `cli.command_command("daemon", "status", "--output", "json")` and a narrow strict decoder for daemon ID, server origin, lifecycle status, OS and workspace/runtime IDs. Match the selected run's runtime ID in the daemon's selected workspace. The current typed `DaemonStatus` drops those fields; public raw JSON already provides them, so no upstream release is required.

Return a concrete frozen `TaskContext` with verified IDs, checkout identity and observation time. Missing fields, unavailable/incomplete runs, mismatched IDs or a relative-only task path fail before preparation. Complete relevant pagination or report incomplete. Path equality alone is not host evidence. Limit this release to execution on the owning daemon's local filesystem; a forwarded/container endpoint without a proven shared filesystem is unsupported. Environment hints cannot override conflicting explicit values.

The actual Git root must lie beneath the verified current/durable task directory and match the selected repository. Context never creates resources, reroutes tasks or proves role/access/anonymization policy. Existing typed issue/run APIs remain the preferred path; raw commands are restricted to the two concrete uncovered output contracts.

### D3. Preparation delegates to generic core adoption

Add core `EnvironmentResource.adopt_command(project, checkout_path, *, options: EnvironmentCheckoutOptions)` and delegating `adopt() -> DevelopmentEnvironment`. Accept COPY only, an explicit compatible base and exactly one supported explicit COPY source. Support linked worktrees and independent clones because native Multica produces both. Do not add a provider interface.

The extension's `prepare_command` performs explicit read-only context preflight and returns the exact captured core adoption command. `prepare` executes that command; it returns the existing `DevelopmentEnvironment`. A caller needing the task association stores the separate `context` result plus environment UUID. Do not introduce an execution wrapper just to combine results. Preflight observations are timestamped facts, not a remote lifetime lock; core revalidates captured local inputs before effects.

First adoption verifies repository, canonical path, branch/HEAD, manifest, source provenance and locally resolved explicit base; HEAD must equal that base and Git must report no tracked/untracked changes. Respect Git ignore rules without Multica filename exceptions. Wrong/dirty retained native checkout fails unchanged, never resets. Preserve the selected configured core project and secret root; rebase repository-local config/addon/dependency paths while leaving external Odoo paths external. Never copy source `.env` into the borrowed checkout.

Reuse core COPY restore, neutralization, isolated database/filestore, disk/Python/port checks and recovery. Do not mutate the default source, build another backup GC or implicitly create a venv. `create_venv` stays false unless explicitly selected.

### D4. Ownership, retry and lifecycle

Persist only necessary additive evidence: code ownership (`sdk_owned | caller_owned | unknown`), explicit SDK artifact root, configured project identity and actual checkout identity. Keep existing core catalog/locks and provisioning journal. Legacy cleanup retains SDK ownership only where canonical recorded layout proves it; unknown evidence never grants new deletion rights.

Generated config/log/lock/optional venv live in the existing global SDK environment root, not beside borrowed code. Unique COPY database/filestore remain SDK-owned. Audit all affected list/cwd/config/sync/runtime/diagnostics/cleanup paths: independent clones must resolve to the configured project, and no code-path parent may become an artifact root by inference.

Reserve by core project plus canonical checkout identity before effects. Same captured source/base/options against a ready record returns the same UUID without download/restore/dependency changes; conflicting inputs fail, incomplete state reports existing recovery. Resolve a matching ready record before first-adoption clean/base checks: later edits/commits on its recorded branch are allowed and do not request a refresh. Do not reinterpret a moving source/ref as new retry inputs.

Rollback/removal never resets, prunes, renames or recursively deletes caller-owned code, even if dirty, absent or replaced. Remove only independently proven SDK artifacts by environment UUID, subject to existing active-runtime and database ownership checks. Normal SDK-owned checkout behavior remains unchanged.

Use existing core get/list/status/diagnostics/start/stop/remove surfaces. No extension binding files, history, locks, stale-state machine, bind/unbind/status commands or remote issue metadata writes. Native Multica owns task-to-code association; the caller owns workflow-result persistence. #105 can later define only the attribution it actually needs.

### D5. Packaging, output and minimum dependencies

Deliver `packages/odcli-multica`, independently versioned distribution/import/executable, compatible public SDK dependencies and isolated-wheel tests. Reuse #69's one-time workspace scaffold; do not wait for its progress functionality or other future members. Likewise require only predecessor source/COPY operations actually consumed, not the complete readiness/retention feature set.

Document and support the narrowly reused existing `odoo_instance_sdk.commands.output` functions/types as the extension-facing bounded output contract. Its current CLI-private designation must be deliberately revised with compatibility tests. Do not add `cli_output.py`, duplicate serializers or a renderer hierarchy. Domain callbacks remain SDK delegates; JSON/TOON emit one sanitized bounded document, Rich expresses equivalent facts. Keep the single core leaf inventory; extension leaves have their package-local contract tests.

## Risks / Trade-offs

- **Native checkout can retain dirty code or cached refs** → reject incompatible first-adoption input; no implicit reset.
- **Task code may be garbage-collected during/after restore** → no lifetime guarantee; preserve partial environment IDs, use core diagnostics and owned-only cleanup. Caller stops/removes Odoo before releasing code when feasible.
- **No persisted association in this slice** → caller stores context and UUID; do not promise later cross-task discovery or telemetry attribution.
- **Raw SDK output contracts can change** → pin/test the selected supported CLI/SDK combination and fail on invalid output; replace decoders with typed wrappers if upstream later supplies them.
- **Prerequisite APIs or permissions unavailable** → explicit diagnostic before mutation; do not substitute private HTTP or live-data tests.

## Migration Plan

1. Verify consumed source/COPY APIs and workspace scaffold; keep independent readiness/retention work outside the gate.
2. Add minimal core ownership/project evidence migration, adoption and affected lifecycle handling together. No second catalog and no extension-state migration.
3. Add the two raw-SDK adapters, read-only context and thin prepare CLI/SDK; document two-phase composition and caller-owned persistence.
4. Validate fake boundaries and an explicitly approved disposable native-daemon/Odoo flow before release. Static research is not live acceptance.

Rollback: uninstall the extension without deleting environments; use a compatible core to clean SDK-owned assets by UUID. Catalog migration is forward-only; old core must not manage upgraded ownership rows.
