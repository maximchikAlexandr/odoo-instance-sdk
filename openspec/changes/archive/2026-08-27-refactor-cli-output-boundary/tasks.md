## 1. Characterize the existing CLI contract

- [x] 1.1 Add root and affected subcommand help/command-tree tests that pin `odoo_instance_sdk.cli:cli`, `from odoo_instance_sdk.cli import cli`, all existing command names, root `--project`/`--env`, and current command-local `--json` placement.
- [x] 1.2 Add success/failure/usage/interrupt characterization tests that pin exit codes `0/1/2/130`, stdout/stderr routing, sanitization/redaction, and the complete JSON envelope-v1 success/error shapes including equal `result`/`data`.
- [x] 1.3 Add context characterization tests for explicit and cwd project/environment resolution, provenance, outside-project `env list`, `--all`, `--all-projects`, and lifecycle rejection of root `--env`.
- [x] 1.4 Add passthrough characterization tests proving `run`, interactive `shell`, plain/following `logs`, stdin/stdout/stderr, and child/interrupt exit behavior remain native and unbuffered.
- [x] 1.5 Run the new characterization slice against unrefactored behavior and commit it separately with a Conventional Commit test-only message.

## 2. Create the move-only CLI seam

- [x] 2.1 Add `src/odoo_instance_sdk/commands/` with only `__init__.py`, `context.py`, `output.py`, and `env.py`; move affected context/output/env code without semantic output changes and remove obsolete `internal/cli_env.py`/`internal/cli_output.py` definitions once no caller imports them.
- [x] 2.2 Implement the slots `CliContext` and native Click pass decorator; migrate every affected callback/resolver caller away from direct `ctx.obj` access while preserving selector values and `explicit`/`cwd`/`null` provenance.
- [x] 2.3 Change reusable project/environment resolvers to accept typed Python values rather than `click.Context`, update all affected callers, and add import/signature tests proving the reusable functions are usable without Click context.
- [x] 2.4 Add a CLI-only `OutputMode` and envelope builder in `commands/output.py`, initially route existing JSON emission/failure behavior through it, and prove byte/semantic compatibility with the characterization fixtures before enabling new formats.
- [x] 2.5 Keep `src/odoo_instance_sdk/cli.py` as the `cli` definition and composition/registration point, update coverage path configuration for the moved CLI modules, and verify the installed script/import surface is unchanged.
- [x] 2.6 Run CLI/context/env focused tests plus ruff and mypy for the moved modules, then commit the move-only/typed-context seam separately with a Conventional Commit refactor message.

## 3. Make EnvironmentMonitor the canonical inventory query

- [x] 3.1 Add and export frozen `PortObservation` and `EnvironmentArtifacts` public types; add required `observed_port`/`artifacts` fields to `EnvironmentSnapshot` and change `Snapshot.schema_version` to `2` without changing any version-1 field meaning.
- [x] 3.2 Extend `EnvironmentMonitor.snapshot()`/`watch()` and existing atomic `BackupCatalog.list_environments_with_runtimes()` with keyword-only `include_removed=False`; select active-only or active+removed environment rows together with runtime identities in one SQLite transaction/read snapshot, include removed-only projects only when requested, and make `environment_count` match included rows.
- [x] 3.3 Keep exactly one call to `list_environments_with_runtimes(include_removed=...)` per monitor planning pass, obtain backup identities/states/recorded paths before closing that catalog, and compute `backup_exists` without `OdooClient`, a second catalog, or separate environment/runtime reads.
- [x] 3.4 Move worktree registration, generated-config, Python existence/containment, dependency-lock, and backup-file reconciliation into the monitor using existing helpers and per-component failure isolation.
- [x] 3.5 Move bounded allocated-port observation into the monitor, emitting `PortObservation | None` exactly per the spec without HTTP health requests or lifecycle mutations.
- [x] 3.6 Cover default/removed/project-filter discovery, removed-only projects, one-call/one-SQLite-snapshot atomic environment+runtime selection under a concurrent-write fixture, every artifact field, available backup+file true, failed/deleted/missing-row/missing-file false, no-backup-id null, unknown/occupied/free port, component isolation, ordering, immutability, redaction, and cache pruning in monitor tests.
- [x] 3.7 Update headless API/snapshot/frontend fixtures for additive snapshot schema v2 and prove existing React field consumption and default non-removed FastAPI behavior remain unchanged.
- [x] 3.8 Refactor `commands/env.py` so each one-shot list calls `snapshot()` once and performs no client/catalog/backup/Git/Docker/filesystem/port collection; add spies/source-contract tests that fail on any such second read.
- [x] 3.9 Run monitor, env-list, FastAPI, React production-build, ruff, and mypy checks, then commit the schema-v2/canonical-inventory semantic change separately.

## 4. Add Rich, JSON, and TOON output modes

- [x] 4.1 Add exact core dependencies `rich>=15,<16` and `python-toon==0.1.3`, refresh the uv lock, and update wheel/sdist metadata tests to require the eight specified core dependencies and unchanged dashboard extra.
- [x] 4.2 Before wiring CLI emission, commit representative CLI-envelope-v1/snapshot-v2 success and error fixtures (nested project/environment objects, arrays, empty collections, nulls, booleans, numbers, and escaping), encode with `toon.encode`, and assert `toon.decode(encoded, DecodeOptions(indent=2, strict=True))` equals the original JSON-safe value; trace checked syntax cases to TOON spec v4.1 dated 2026-07-26 and do not write a fallback/custom encoder.
- [x] 4.3 Implement the shared command-local `--format rich|json|toon` plus `--json` alias resolution, including acceptance of `--json --format json`, pre-operation exit `2` for conflicting modes, and native Click behavior for parse failures before mode resolution.
- [x] 4.4 Apply command-local shared format options in place to exactly these 18 leaves: `init`, `doctor`, `env checkout`, `env list`, `env remove`, `env sync`, `eval`, `exec`, `module list`, `module update`, `module test`, `translations export`, `deps verify`, `vscode generate`, `postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`; do not move unrelated command groups or add document options to `run`, interactive `shell`, or `logs --follow`.
- [x] 4.5 Implement JSON and TOON emitters over the exact same JSON-safe envelope object, with exactly one stdout document, no ANSI/prompts/progress, sanitized errors, diagnostics only on stderr, and renderer-independent exit mapping.
- [x] 4.6 Convert each bounded command's existing human presentation to its local Rich-mode projection while preserving Rich prompt/confirmation semantics; for machine `env remove`, bypass `click.confirm`, require `--yes`, otherwise emit one `confirmation_required` failure envelope and exit `1`; use `Status`/`Progress` only for an operation with real measurable progress.
- [x] 4.7 Add parameterized success/error tests proving `--json` and `--format json` equivalence, strict decoded TOON/JSON semantic equality, stable envelope v1, conflict behavior, stderr split, redaction, no ANSI/prompts, and JSON/TOON/`--json` `env remove` behavior both without `--yes` (no operation, `confirmation_required`, exit `1`) and with `--yes` (one operation).
- [x] 4.8 Add isolated wheel tests proving Rich/TOON imports and representative command-local `odcli env list --format toon` execution work without Node, Textual, alternate CLI frameworks, custom TOON code, or dashboard extras; enumerate all 18 leaf help surfaces to prove `--format` parity and root help to prove `odcli --format` remains invalid.

## 5. Implement Rich environment rendering and live mode

- [x] 5.1 Implement the pure Rich project/cluster/environment renderer in `commands/env.py`, retaining all fifteen specified environment values, TTY-aware color, deterministic project/environment ordering, and no data collection from render functions.
- [x] 5.2 Preserve `env list` filter semantics: project context and `--all-projects` feed the same project selector; Rich `--all` calls `include_removed=True`; JSON/TOON `--all` keep the existing active-only machine contract.
- [x] 5.3 Add `--watch` and `--interval` with default `2.0`, Click exit `2` below `0.1` or with machine modes, and exit `1` before collection when stdout is not an interactive TTY.
- [x] 5.4 Implement the foreground `rich.live.Live` snapshot/sleep loop with the original filters, no thread/task/executor/queue, one canonical snapshot call per refresh, and no fabricated progress display.
- [x] 5.5 Implement first-sample failure exit `1`, later-failure last-successful-sample retention plus sanitized retry diagnostic, and `KeyboardInterrupt` cleanup with `Live(..., transient=True)` (or explicit equivalent) that restores the terminal, removes the live region/last table, then returns `130`.
- [x] 5.6 Add deterministic Live tests with injected monitor samples/failures, fake clock/sleep, and TTY/non-TTY streams covering refresh, filter retention, ordering, initial/transient failure, machine rejection, interval validation, terminal restoration, absence of the live table after Ctrl-C, exit `130`, and no surviving work.
- [x] 5.7 Run all CLI output/env/watch tests, ruff, and mypy, then commit Rich/TOON and live semantics in separate reviewable Conventional Commits as described by the design.

## 6. Compatibility, documentation, and delivery gates

- [x] 6.1 Add source-boundary tests or equivalent checks proving public resources/workflows import neither Click nor FastAPI, output modes/envelopes are not public SDK/FastAPI models, and no generic registry/DI/application/provider/rendering framework was added.
- [x] 6.2 Update README/CLI documentation for `--format`, the `--json` alias/conflict rule, TOON machine output, `env list --watch/--interval`, TTY restriction, `--all` machine compatibility, snapshot schema-v2 fields, and the `odoo_instance_sdk.cli:cli` stability guarantee.
- [x] 6.3 Run focused characterization, monitor, output-parity, Live, security/redaction, FastAPI, frontend-build, and packaging tests; record any skipped external Docker/Odoo prerequisites separately from regressions.
- [x] 6.4 Run the full `make pr` gate and `uv build`; inspect wheel/sdist contents and metadata, and resolve every project-owned regression before handoff.
- [x] 6.5 Review `git diff` for only the scoped CLI boundary/output/env/monitor/model/dependency/docs/tests changes, verify commit separation and Conventional Commit messages, and ensure no database/test/pgAdmin/OpenAPI/logs/debug feature work is included.
- [x] 6.6 Complete the implementation as a no-PR handoff by explicit user decision: verify the published SSH branch `feat/MYL-55-cli-output-boundary` and final SHA, report snapshot schema v2 and compatibility plus local `make pr`/build results and external prerequisite skips, and do not open or merge a PR or archive the change.
