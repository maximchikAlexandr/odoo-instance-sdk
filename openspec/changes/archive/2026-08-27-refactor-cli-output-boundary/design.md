## Context

`src/odoo_instance_sdk/cli.py` currently registers the full Click tree and also resolves context, coordinates operations, formats results, and emits output. `internal/cli_env.py` goes further: after `EnvironmentMonitor.snapshot()` it opens the public client again, lists backups and removed environments, looks up each environment, probes ports, reconciles worktree/filesystem artifacts, builds a second grouping, and prints a fixed-width table. `internal/cli_output.py` only knows JSON, so human output and error handling are repeated across callbacks.

The existing application boundary is already the public resources and typed `msgspec` models. The refactor therefore needs only a small inbound-adapter seam, not a new application layer. The stable constraints are Click, `odoo_instance_sdk.cli:cli`, public resource imports, command names, context resolution, exit codes, redaction, JSON envelope v1, and raw passthrough streams.

The current monitor snapshot is a strong base for `env list`, but it excludes removed rows and omits the artifact/backup and port-observation values that the human renderer currently recollects. Adding those fields is an intentional additive public snapshot schema migration. FastAPI and React continue consuming the default non-removed snapshot and require no behavior-specific branch.

Rich 15 provides the required terminal components. For TOON, the selected dependency is `python-toon==0.1.3`. Its `toon.encode` plus `toon.decode(..., DecodeOptions(indent=2, strict=True))` API was checked in-process against representative CLI-envelope-v1/snapshot-v2 success data with nested project/environment objects, arrays, booleans, numbers, strings, empty collections, and `None`; the decoded value equalled the input. The fixture syntax is sourced from published TOON specification v4.1 (2026-07-26), but the project promises only this checked envelope boundary rather than full-library v4.1 conformance.

## Goals / Non-Goals

**Goals:**

- Make Click a thin inbound adapter over existing resources and typed results on the affected context/output/environment path.
- Give every bounded structured command a consistent `rich|json|toon` selection without changing operation execution or envelope v1.
- Make `EnvironmentMonitor.snapshot()` the sole inventory/reconciliation collector used by all `env list` renderers.
- Provide an interactive Rich live view with deterministic polling, last-good-sample behavior, and synchronous cleanup.
- Preserve public imports, CLI names, exit codes, stream routing, redaction, and passthrough behavior with characterization tests.
- Keep the implementation small enough that unrelated command groups can migrate only when next changed.

**Non-Goals:**

- Migrating from Click or introducing Typer, Cyclopts, argparse, Textual, or curses.
- Adding a command bus, handler registry, DI container, renderer abstraction/DSL, generic application/service/provider layer, or interfaces with one implementation.
- Moving every command group for symmetry.
- Changing database, lifecycle, test, logs, debug, FastAPI endpoint, React UI, pgAdmin, or OpenAPI-codegen semantics.
- Reusing the CLI envelope as a public SDK or FastAPI model.
- Wrapping `run`, interactive `shell`, or `logs --follow` in document or Rich-live output.

## Decisions

### D1: Three focused CLI modules, direct composition

Add `commands/context.py`, `commands/output.py`, and `commands/env.py` (plus the package initializer) and leave `cli.py` as the stable entry point and registration/composition root.

- `commands/context.py` owns `CliContext`, its Click pass decorator, and adapters from CLI selections to existing pure resolution helpers.
- `commands/output.py` owns the CLI-only `OutputMode`, shared option resolution, envelope construction, JSON/TOON emission, diagnostic sanitization, and exit mapping.
- `commands/env.py` owns the existing `env` group callbacks and command-local Rich environment renderers/live loop.
- Existing internal business helpers stay concrete and capability-oriented. Unrelated callbacks can import the shared output/context helpers in place; they are not moved solely to satisfy directory symmetry.

Alternative considered: create `application/`, use-case classes, ports, adapters, and providers. Rejected because public resources already serve as the application boundary and there is only one production implementation.

### D2: One small mutable-at-composition, typed-at-use CLI context

Use a slots dataclass `CliContext` created by the root callback and passed with `click.make_pass_decorator(CliContext, ensure=True)`. It contains raw root selectors plus resolved values/provenance as concrete typed fields; it has no service container or command registry.

Pure resolver functions accept paths/selectors/cwd and return typed resolution data. Only Click callbacks adapt `CliContext` to those parameters. Paths touched by this change stop using `ctx.obj`; unrelated callbacks may continue temporarily if migrating them is not required for correctness.

Alternative considered: subclass `click.Context`. Rejected because it couples reusable resolution to the framework and adds behavior that native object passing already supplies.

### D3: Build one envelope, then serialize it

`commands/output.py` contains a simple `build_envelope(...) -> dict[str, object]`. It converts typed results once with `msgspec.to_builtins`, applies the existing envelope-v1 shape, duplicates the same builtin object into `result` and `data`, and sanitizes errors before either serializer sees them.

Machine emission is one explicit branch:

```text
typed result -> JSON-safe envelope v1 -> json.dumps OR toon.encode -> stdout
```

`--format` and `--json` use one reusable Click option callback/decorator. Resolution rules are: no option = `rich`; `--json` = `json`; `--json --format json` = `json`; any other explicit conflict raises `click.UsageError` before the operation runs. The selected mode is passed as a value, never read from a global or resource.

Rich remains command-local because tables and prose are part of each CLI command's transport contract. There is no common renderer protocol; the only shared output is envelope construction and machine serialization.

Alternative considered: a renderer base class/registry. Rejected because three enum branches and command-local Rich functions are shorter, more discoverable, and avoid a speculative extension point.

### D4: Apply format options to bounded commands in place

The bounded leaf inventory is exactly: `init`, `doctor`, `env checkout`, `env list`, `env remove`, `env sync`, `eval`, `exec`, `module list`, `module update`, `module test`, `translations export`, `deps verify`, `vscode generate`, `postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`. Each gains command-local `--format` through the shared option helper while keeping its callback in the current module unless it is part of the `env` move. Its existing operation/result building stays intact; JSON calls are replaced mechanically by the common envelope/emitter path, and its existing human presentation becomes the Rich-mode path with the same information and TTY-aware color.

Passthrough commands do not gain the helper. `logs` remains native when streaming (`--follow`); no buffering is introduced merely to support a document mode.

Alternative considered: support formats only on `env list`. Rejected because the requested single `OutputMode` and backward-compatible `--json` contract apply to all existing bounded structured commands, while the shared helper permits that without moving their modules.

### D5: Extend the existing snapshot graph; do not add a CLI model

Add two public transport-neutral values in `models.py`:

- `PortObservation` (`free|occupied|unknown`), used as `EnvironmentSnapshot.observed_port | None`.
- `EnvironmentArtifacts`, containing the seven reconciliation booleans/optional backup presence already rendered by the CLI.

Add `artifacts` and `observed_port` to `EnvironmentSnapshot`, bump `Snapshot.schema_version` from 1 to 2, and keep every version-1 field and meaning unchanged. This is an explicit additive migration: msgspec/FastAPI JSON gets two new required environment fields, while CLI envelope schema stays at 1. React's existing field reads remain valid.

Extend `snapshot()` and `watch()` with keyword-only `include_removed=False`. The default preserves active-only API/JSON behavior. With `include_removed=True`, the same graph contains removed rows and removed-only project summaries. This avoids `CliEnvironmentSnapshot`, a parallel project graph, or CLI-side merging.

Alternative considered: return a separate `EnvironmentInventory` or embed reconciliation in a CLI dictionary. Rejected because both duplicate the canonical graph and invite SDK/CLI/HTTP drift.

### D6: Move reconciliation into the monitor's existing catalog pass

The monitor already opens `BackupCatalog` once and obtains environments plus runtime identities atomically through `list_environments_with_runtimes()`. Extend that existing helper with keyword-only `include_removed=False`; its environment query changes selection inside the same SQLite transaction while the runtime query remains in that transaction. The monitor invokes it exactly once per snapshot, reads required backup identities/states/recorded paths during the same catalog planning phase, then closes the catalog before expensive collection. The plan carries only typed/internal immutable inputs required for later filesystem/Git/port collection; no open SQLite connection crosses into renderers and no separate environment/runtime read is introduced.

Reuse existing helpers:

- Git canonical-dir/worktree helpers for registration.
- safe `Path.is_file/is_dir` patterns for artifact presence.
- `probe_address` for bounded read-only port observation.
- existing monitor Git, storage, process, and Docker collectors/caches.

Artifact failures are isolated per field/environment. Removed rows use the same model; unavailable live/Git/storage values follow existing partial-result rules rather than inventing a reduced removed-row DTO. The default query excludes them, so normal API cost remains comparable.

Alternative considered: let the renderer continue calling `OdooClient`, catalog, and probes. Rejected because it produces a second result graph and makes live/JSON/TOON disagree.

### D7: Rich rendering is a pure projection

`commands/env.py` groups the returned `ProjectSummary` and `EnvironmentSnapshot` objects and creates Rich `Table` renderables. It performs no I/O except terminal emission and no collection. Existing column information is retained; cell truncation/wrapping uses Rich's column overflow behavior rather than a generic formatting DSL.

Non-TTY one-shot Rich output uses Rich's console detection, so tests and redirection contain no forced ANSI. Machine modes bypass Rich entirely.

Alternative considered: preserve the hand-built fixed-width string table. Rejected because Rich tables and Live are the requested capability and one renderer avoids maintaining two human layouts.

### D8: Live mode is a foreground loop with no scheduler

The Click command validates `rich`, TTY, and interval before collection. A `with Live(..., transient=True)` foreground loop calls the same synchronous `monitor.snapshot(...)`, updates the table, then waits with bounded standard-library sleep. It does not create a thread, executor, queue, background task, Textual app, or curses screen. `EnvironmentMonitor.watch()` remains the public async primitive for SDK consumers; the Click adapter does not need to create an event loop merely to sleep.

The first failure exits 1. After a success, the loop retains the last renderable and adds a sanitized stale/error marker while continuing to retry. `KeyboardInterrupt` is caught only at the adapter boundary; exiting the transient `Live` context restores the terminal and explicitly removes the live region/last table, then Click exits 130.

Alternative considered: run the async monitor iterator under `asyncio.run`. Rejected because Click and Rich Live are synchronous here, and a foreground `snapshot`/sleep loop satisfies cleanup and filter retention with less lifecycle machinery.

### D9: Pin and verify TOON at the supported-envelope boundary

Use `python-toon==0.1.3` directly: `from toon import encode, decode, DecodeOptions`, encode only `msgspec.to_builtins`/JSON-safe values, and verify with `decode(encoded, DecodeOptions(indent=2, strict=True))`. Before this plan was accepted, that exact installed release/API successfully round-tripped a representative project success envelope containing nested snapshot data, homogeneous and heterogeneous arrays, empty collections, null/boolean/number values, and strings. Implementation tests add the corresponding error envelope and escaping cases.

The committed fixture source is CLI envelope v1 plus snapshot schema v2, with expected syntax traced to TOON specification v4.1 dated 2026-07-26. The project does not promise arbitrary TOON parsing, full dependency conformance to every v4.1 production, or a TOON SDK. The dependency is exact-pinned; upgrading it or the spec is a conscious packaging/spec/test change.

Alternative considered: implement the few required TOON constructs locally or shell out to the official Node reference implementation. Rejected because both violate the dependency requirement and create an unnecessary parser/runtime maintenance surface.

### D10: Characterize, move, then change semantics

Implementation history is split into reviewable commits:

1. characterization tests only;
2. move/typed-context/output-seam refactor with existing behavior green;
3. canonical inventory schema v2 and CLI de-collection;
4. Rich/TOON/format options;
5. live mode and final coverage/docs.

Each step keeps `odoo_instance_sdk.cli:cli` importable and the test suite runnable. This ordering makes a move regression distinguishable from new output behavior.

## Risks / Trade-offs

- **Snapshot schema v2 may affect strict external decoders** → Keep all v1 fields unchanged, bump `Snapshot.schema_version` explicitly, document the two new fields, and test the default FastAPI/CLI JSON contract.
- **Pinned TOON implementation may contain unsupported edge cases** → Exact-pin it, feed only JSON-safe builtins, limit the promise to committed CLI envelope fixtures, verify strict round trips with the explicit API, and require a deliberate pin/spec fixture update for upgrades.
- **Rich 15 is a new major dependency** → Bound it `<16`, use only stable `Console`, `Table`, and `Live` APIs, and verify isolated wheel execution.
- **Artifact collection can increase snapshot latency** → Reuse the single catalog pass and existing bounded helpers/caches; do not hash dependency locks or add new deep scans because the renderer only needs availability.
- **Removed paths commonly do not exist** → Treat absence as typed reconciliation data and isolate failures; do not fail the whole snapshot.
- **Live refresh can flicker or erase useful data on transient errors** → Retain the last successful renderable and update one Live region; never replace a successful sample with an empty graph after failure.
- **Broad `--format` rollout can disturb legacy callbacks** → Characterize first, replace output emission mechanically, and leave operation bodies/module placement intact.
- **Mixed old/new `ctx.obj` access during incremental migration can diverge** → Define one root `CliContext` and migrate every callback sharing the affected resolvers in the same move commit; tests pin both explicit and cwd provenance.

## Migration Plan

1. Fetch `origin/main`, work only on `feat/MYL-55-cli-output-boundary`, and keep unrelated work out of the branch.
2. Add characterization tests without changing implementation behavior.
3. Introduce the three focused CLI modules, typed context, and shared envelope builder; move `env` callbacks with move-only behavior preserved.
4. Add snapshot v2 types and monitor-owned active/removed discovery, artifact/backup reconciliation, and port observation; update FastAPI/monitor fixtures for the additive schema.
5. Remove CLI-side `OdooClient`/catalog/Git/Docker/filesystem/port collection from `env list` and render only the canonical result.
6. Add bounded-command format options, Rich renderers, JSON/TOON serializers, dependency metadata/lock updates, and semantic parity tests.
7. Add the foreground Rich Live loop and cleanup/error-retention tests.
8. Run focused tests after each semantic step and full `make pr` at handoff; distinguish missing external prerequisites from regressions.

Rollback is commit-wise: revert live/output commits first, then snapshot-v2/de-collection, then the move. No catalog schema or user data migration is involved. Reverting snapshot v2 removes only additive generated fields and restores `schema_version=1`.

## Open Questions

None. The output option scope, `--all` compatibility, snapshot schema migration, TOON implementation/pin, live-loop ownership, and module boundaries are fixed above.
