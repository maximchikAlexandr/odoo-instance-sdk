## Context

The current revision already has the right architectural seams. `ExecutionPlan` contains immutable `ActionStep` values with redacted JSON-safe `details`; `PreparedAction.public_projection()` binds those values to the private executable snapshot. `commands/output.py` builds one JSON/TOON envelope and runs bounded commands with a shared `StepEvent` observer. `internal/output_rich.py` renders plans but currently omits action details. Rich Click owns the executable command graph, `PUBLIC_LEAF_CASES` is the test-owned characterization inventory, and restore flows already validate database existence and filestore directory safety before publishing bindings.

The missing behavior is projection and domain data, not a new runner. The implementation must coexist with the active `refactor-cli-output-boundary` change, keep `odoo_instance_sdk.cli:cli`, preserve raw passthrough commands, use the single redacted projection/fingerprint path, and retain the repository rule that preview and execution consume one frozen command snapshot.

## Goals / Non-Goals

**Goals:**

- Make applicable user-owned filesystem consequences explicit, ordered, structured, redacted, and consistent across Rich, JSON, and TOON dry-run output.
- Reuse the exact planned targets during execution and fail closed or revalidate when the relevant source state changes.
- Make long-operation progress consumable without corrupting the final machine document.
- Make the live Click command graph discoverable in JSON/TOON without duplicating command metadata.
- Distinguish restore completion from neutralization and filestore-integrity verification.
- Keep changes additive where public serialization is affected.

**Non-Goals:**

- A filesystem tracer, content diff, recursive enumeration of worktrees/venvs/filestores, or prediction of arbitrary Odoo/Python/Git-conflict effects.
- A second plan, runner, command/help registry, progress scheduler, renderer hierarchy, or background worker.
- Listing OdCLI logs, SQLite journals, synchronization locks, atomic temporary files, package-manager internals, PostgreSQL/pgAdmin service files, or `postgres approve-image` effects.
- Changing `run` ports, inventing exact translation filenames before Odoo resolves the ISO code, or treating OdCLI metadata snapshots as Odoo database/filestore backups.

## Ponytail Full Gate

| Candidate subsystem | Necessity | Existing alternative | Decision |
|---|---|---|---|
| Filesystem-effect model | A stable action/path/condition vocabulary is required. | `ActionStep.details` is already immutable, JSON-safe, redacted, fingerprinted, and paired with execution. | Keep the existing field and standardize a compact `file_effects` value; do not add a planner or public top-level model. |
| Machine progress transport | Machine clients need live events without contaminating final stdout. | `StepEvent`, `RunContext.progress`, the shared observer, and JSON/TOON encoders already exist. | Extend those values and add a small stderr framing adapter; do not add a bus, queue, or scheduler. |
| Machine help projection | Automation needs structured command metadata. | Rich Click/Click already owns one command tree and parameter metadata. | Traverse that graph at help time; do not add a registry or generated runtime manifest. |
| Restore verification service | Results need explicit independent states and counts. | Restore validation and result construction already run before publication. | Extend the existing validation/result path; do not add a second restore verifier. |

## Decisions

### D1: Standardize file effects inside `ActionStep.details`

An applicable mutating action SHALL place an ordered `file_effects` array in its existing details object. Each effect has `action` (`create`, `modify`, `replace`, `delete`, or `move`), absolute normalized `path`, optional `destination` for `move`, `subject` (`file` or `directory`), `condition`, and optional `uncertainty`. Omitting an inapplicable effect is different from a conditional effect; an unknown exact child name uses the known directory plus a non-empty uncertainty reason and never a fabricated path.

The implementation adds one narrow internal builder/validator returning JSON-safe values. It does not add a public filesystem class or a second plan. `_rich_step_lines` renders the same fields in plan order; JSON and TOON already serialize them. Recursive directory members are not listed.

Alternative: add `ExecutionPlan.file_effects`. Rejected because effects belong to ordered actions, would duplicate step association, and would require a parallel plan/execution consistency mechanism.

### D2: Planning captures targets; execution consumes or revalidates them

Each affected planner resolves paths and existing-state classification before mutation. Prepared actions retain the same private normalized targets used to build public details. Execution revalidates identity/existence assumptions at the existing trust boundary: it either performs the captured effect, safely recomputes only a value explicitly declared uncertain (such as the Odoo-resolved ISO name), or fails stale. It never silently mutates a different target.

`translations export` keeps temporary-file and atomic replacement internals private while showing directory creation and final `.po`/`.pot` publication. `vscode generate` reports no write without `--write` and preserves refusal on an existing file. `init` classifies each applicable generated file as create/modify/no-op. Environment, restore, refresh, drop, and removal flows express ownership and rollback conditions rather than claiming every conditional deletion.

Alternative: infer effects after execution from result objects. Rejected because dry-run needs the information before mutation and post-hoc inference cannot enforce plan/execution parity.

### D3: Command-family adoption stays at existing planners

The shared schema is populated where each immutable command is already assembled:

- translations, init, VS Code, and bug-report expose concrete publication targets;
- environment create/checkout/sync/removal expose worktree root, generated config, dependency lock, owned/reused venv, backup, and filestore consequences;
- database restore/refresh/drop and backup deletion expose captured archives, configuration, filestore move/delete/restore, and retention conditions;
- self-update describes the bounded metadata snapshot and possible OdCLI data migration without mislabelling them as Odoo backups;
- Git commit/absorb/sync describes index/history/worktree/rebase/push scope and declares conflict-dependent file results unknown;
- run/shell/exec/eval/test/module operations expose only managed config/log/data roots and explicitly bound arbitrary-code uncertainty.

Existing test-owned `PUBLIC_LEAF_CASES` remains the only exhaustive CLI characterization inventory. No production module imports it.

### D4: Machine progress is a framed stderr side stream

`StepEvent` gains additive `unit` and `rate_per_second` fields. Clone, backup download, and restore report trustworthy byte counts through `RunContext.progress`; downloaded bytes are monotonic, total bytes is absent when unknown, rate is derived from monotonic deltas, and human MiB/GiB values are renderer-only. Lifecycle stage events remain identified by planned `step_id`, operation, and target.

Normal `--format json` execution writes one compact progress-event JSON object per stderr line. Normal `--format toon` writes each complete TOON event to stderr preceded by ASCII Record Separator (`0x1e`) and followed by LF, so multiline TOON values remain frameable. Every event has `schema_version=1`, `kind="progress"`, command, step identity, stage state, elapsed time, optional byte fields, and no file contents or secrets. Stdout remains exactly one final envelope. Dry-run emits no runtime events. Diagnostics remain sanitized stderr records/text and are never interleaved inside a frame.

Alternative: buffer progress into the final result. Rejected because it is not live and can grow without bound. JSONL for TOON was rejected because it would not be TOON; unframed TOON was rejected because multiline documents are ambiguous.

### D5: Machine help is projected from the live Click graph

The existing Rich command/group base receives one eager help-format resolver that recognizes `--format json|toon` in a help invocation at root or descendant position without making format global for normal execution. It traverses the actual Click `Command`, `Group`, `Argument`, and `Option` objects after registration and emits a versioned tree containing path/name, description, hidden/deprecated state, arguments, options, parameter type/metavars, required flag, repeatability, default when safely serializable, choices/ranges, and child commands. Sensitive/default callables and environment-derived defaults are represented as unavailable, never evaluated or revealed.

Rich help remains the default. Machine help exits successfully without resolving project context or importing optional command runtime modules beyond what normal help registration already requires. The output uses the existing JSON/TOON encoders and semantic parity tests. `PUBLIC_LEAF_CASES` characterizes behavior but is not the help source.

Alternative: commit a command metadata file or walk decorators separately. Rejected because either becomes a second authority and can drift from executable help.

### D6: Restore verification is explicit and independent

The restore result gains two typed checks, each with `status` (`passed`, `failed`, `not_checked`), a sanitized reason when not passed, and applicable counts. Neutralization reflects only the existing neutralization operation and its postcondition. Filestore verification queries unique stored attachment identifiers through the bound database, normalizes safe store filenames, and counts expected, found, and missing unique files under the captured filestore root. It does not expose attachment contents or duplicate rows and does not follow paths outside the root.

When verification is required and runs, missing files yield `failed` with the concrete count mismatch and the operation does not present an aggregate verified success. When a source/flow cannot perform a check, it returns `not_checked`; password reset or database existence never upgrades either status. Existing restore publication/compensation rules remain authoritative.

Alternative: infer success from restore completion or password reset. Rejected because those outcomes prove neither neutralization nor filestore completeness.

### D7: Verification extends current matrices and CI

Repeated command/state/format cases use `pytest.mark.parametrize`. Unit tests pin schema validation, redaction, Rich projection, help parity, progress framing, restore count semantics, and no-op/conditional effects. Integration tests compare dry-run effects with the exact executed prepared targets and inject drift/failure deterministically. Existing real-Odoo full-tier coverage gains focused restore and selected lifecycle assertions; no second CI framework or command registry is added.

## Risks / Trade-offs

- **Broad command adoption can overstate certainty** → require conditional/uncertain forms, domain-owned planners, and negative tests that omit inapplicable effects.
- **Absolute paths can disclose usernames or secrets embedded in paths** → run every path/condition through the existing redacted projection and secret canaries; never include file contents.
- **stderr progress may surprise consumers that treated it as plain diagnostics** → version and frame events, keep stdout compatibility, document draining both streams, and restrict events to opted-in machine execution of the named long operations.
- **Click defaults may be lazy or sensitive** → never invoke callables during help projection and omit unsafe defaults with a typed availability marker.
- **Attachment queries can be expensive** → select distinct stored file identifiers only, stream/count without contents, and reuse the bound database/filestore identity before publication.
- **Active output-boundary work can conflict** → build on current `commands/output.py` and command classes, avoid moving callbacks, and rebase normally before implementation integration.

## Migration Plan

1. Add and characterize the shared details schema, Rich projection, redaction, and stale-target checks without adopting commands.
2. Adopt command families in parallel behind the same schema, preserving existing output fields and behavior.
3. Add machine progress framing, machine help projection, and restore verification on their existing boundaries.
4. Extend parametrized unit/integration/real-Odoo tests and user documentation; regenerate only repository-owned derived inventories.
5. Rollback is a normal code revert: no persistent schema or database migration is introduced. Consumers ignoring additive action details remain compatible; machine progress is confined to stderr.

## Open Questions

None. The framing, help source, effect vocabulary, restore states, exclusions, and verification ownership are fixed by this design.
