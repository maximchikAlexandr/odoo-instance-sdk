## Context

At `origin/main` commit `cc8f1ff`, the lightweight package/CLI startup prerequisite from MYL-67 is merged. The production package still contains direct `subprocess.run`/`Popen` launches across Git, Odoo lifecycle and automation, environment provisioning, PostgreSQL/Compose, backup validation, pgAdmin helpers, changed-test selection, disk/Git probes, and Windows termination. Tests patch those module-local calls, so the seams themselves are fragmented.

`EnvironmentResource` already has `EnvironmentCheckoutPlan`, `_CheckoutPlan`, `plan_checkout()`, and `checkout_with_plan()`, but execution reconstructs work after preview. `internal/server.py` separately implements captured, foreground, long-running, stdin-script, and termination behavior. PostgreSQL has `ComposeRunner` and transport-specific runners. These are inputs to one boundary, not contracts to preserve in parallel.

`commands/output.py` already owns `OutputMode`, envelope creation, JSON/TOON emission, sanitization, and failure mapping. Command modules and `cli.py` still contain Rich/machine branching and direct stdout/stderr writes. The accepted output work therefore supplies the base to deepen; a second formatter framework would be a regression.

The project uses Python 3.12, frozen `msgspec.Struct` models, strict mypy, Ruff, Click, Rich, TOON, and lazy root exports. The new execution package must not be imported by bare package import, `odcli --help`, or `odcli --version`. GitHub #45 fixes Expression as the bounded typed-pipeline experiment; GitHub #40 fixes the public command/plan behavior and full launch-site migration.

## Goals / Non-Goals

**Goals:**

- Make one captured executable snapshot the source for public preview and actual execution.
- Give every process-spawning public operation an inspectable `*_command()` sibling without breaking convenience APIs.
- Centralize every production child-process launch, including foreground handles and Windows `taskkill`.
- Make eligible CLI dry-run universal and make bounded Rich/JSON/TOON output projections of one typed document.
- Preserve explicit domain lifecycle code for locks, cleanup, rollback, retained artifacts, and native streams.
- Eliminate explicit `Any` and bare `object` production annotations and prevent architectural regressions statically.
- Keep MYL-67 import boundaries green.

**Non-Goals:**

- A DAG, workflow engine, resumable/persisted plan, remote executor, provider registry, or generic continuation abstraction.
- Replaying a serialized plan or accepting plan JSON/TOON as executable input.
- Exposing secrets for visual byte parity, or including secrets in fingerprints.
- Replacing Click, Rich, msgspec, existing public resource names, domain plans, or command-specific Rich renderers.
- Implementing product behavior from #35, #33, #34, or later roadmap issues beyond migrating existing operations to the new boundaries.
- Mechanically repartitioning all of `internal/`; only responsibility moves required by this change are allowed.

## Decisions

### 1. Separate the public projection from the private executable snapshot

Add public frozen models in a lazily imported `odoo_instance_sdk.execution` module:

- `Command[T]` exposes `.plan`, `.commands`, and `.run() -> T`;
- `ExecutionPlan` contains ordered steps, planning observations, warnings, and `fingerprint`;
- `ProcessStep` contains redacted argv, display string, executable, cwd, sanitized environment policy/overrides, input preview, timeout, execution mode, and mutation/interactive/long-running flags;
- `ActionStep` describes a real in-process filesystem, HTTP, database, lock, signal, or precondition action without inventing a shell command;
- concrete `PlanError` variants and `StalePlanError` represent expected planning failures and failed revalidation.

The public plan/value models contain only immutable JSON-safe values. Define a recursive `JsonValue` alias rather than `Any`/`object`. `Command[T]` is immutable and typed but stores one private prepared-command object behind its public methods; that callback/snapshot is intentionally non-serializable and excluded from project model conversion. `repr(Command)` and explicit conversion of its public plan expose only the public projection.

The private snapshot lives under `internal/proc` and retains exact argv, cwd, environment overrides, stdin bytes, timeouts, mode, and operation-specific execution callback. The callback may contain explicit lifecycle sequencing, but every child launch must consume a captured private process step through the executor. Step identifiers and a consumed-step check prevent callbacks from launching an unplanned command or silently substituting values.

This two-view design is preferred over putting raw values in public models because passwords and generated secret paths must remain executable but never printable. A public planner/service hierarchy is rejected: `Command[T]` is the only generic wrapper, while resources remain the operation owners.

### 2. Fingerprint only the canonical redacted projection

Build the fingerprint from deterministic canonical JSON of the plan projection, excluding the fingerprint field itself. Tuple order is preserved; mapping keys are sorted; display strings are derived with `shlex.join` and are never executable. The same redaction function produces public argv, environment, stdin/script previews, errors, `repr`, and fingerprint input.

Secret changes therefore do not leak through a digest. Exact execution parity is tested by comparing recorded private executor inputs after applying the same redaction function, not by exposing raw secrets. This is preferred over hashing the private snapshot, whose low-entropy secret fields could be guessed offline.

### 3. Use one small `internal/proc` package for all child-process effects

Create a short deep package with three responsibilities:

- frozen private process specifications and projection/redaction;
- the real executor for captured calls, inherited stdio, foreground/session handles, timeout, stdin bytes, waiting, termination, and sanitized environment construction;
- a recording/fake executor used by unit and parity tests.

Keep the file count minimal; split only model/projection, real executor/lifecycle, and testing support if one module becomes unclear. The executor always calls subprocess APIs with argv and `shell=False`. `shlex.join` is display-only. Existing `internal/server.py` lifecycle helpers may delegate to `internal/proc`, but no other production module may call `subprocess.run` or `Popen`.

Replace `ComposeRunner`, PostgreSQL transport runners, Git `_run`, and module-local subprocess monkeypatch seams with this executor. OS APIs such as `os.killpg` remain explicit `ActionStep` lifecycle effects. `OdooInstance.stop_command()` is a platform-independent public method: Windows projects `taskkill` as a real process step through the executor, while POSIX projects its signal/no-child path as an honest action step. Type annotations that store `Popen` handles are not launches and remain allowed.

One AST contract test scans the production package and ultimately permits launch call nodes only inside `internal/proc`. While sections 4-6 migrate the existing families, the test uses a checked, line-specific baseline that rejects growth and shrinks with each migrated family. Immediately after the migration inventory reaches zero, the baseline is replaced by the final empty allowlist. A later non-empty exception is allowed only when a platform operation cannot be represented as a child command and is documented beside the gate with its removal condition.

### 4. Planning captures all process steps before mutation

Each resource `*_command()` pipeline follows:

```text
raw input -> resolve -> validate -> capture immutable steps
          -> redact public projection -> fingerprint
          -> Result[PreparedCommand[T], PlanError]
```

Planning starts none of the planned processes and performs no mutation. Bounded read-only probes required to resolve a plan may run through the same proc executor; their redacted command/result metadata is appended to `ExecutionPlan.observations` with `read_only=true` and `executed_during_planning=true`. Planning-time filesystem/network reads use equivalent typed observations where relevant.

All later process argv must be captured before the first mutation. An operation whose later argv genuinely depends on a prior mutation must be split at its existing domain phase boundary and return/construct the next command explicitly; no continuation engine is introduced.

Immediately before the first mutation, operation-specific validators recheck volatile facts captured by planning: relevant paths, Git HEAD/base, environment/database identity, port availability, lock ownership, and deterministic future temp/config path collisions. A changed precondition raises `StalePlanError`; execution never replans or substitutes a new value. Deterministic future paths are selected without creation during planning and created exclusively during execution.

### 5. Keep lifecycle orchestration operation-specific

`Command.run()` is repeatable and delegates to the captured operation callback with the executor, immutable steps, and a fresh per-run consumption ledger. No ledger state is stored back onto `Command`, so sequential or concurrent invocations independently execute the same snapshot. A request for an unplanned, substituted, or already-consumed identifier fails before that requested child launches. The callback-completion check detects omitted steps; because earlier effects may already have occurred, omission is a run-completion failure rather than a global fail-before-launch guarantee. Environment checkout continues to own catalog state, locks, cleanup, and compatibility result construction. Database preparation continues to own serialization, restore retention, rollback/compensation, and atomic default switching. Odoo lifecycle continues to own process registration, signal forwarding, bounded TERM/KILL/reap, and secret-config cleanup.

These actions appear as `ActionStep` projections where they are meaningful to the user, but are not forced through Expression or a generic transaction API. This retains honest previews without hiding failure semantics behind a universal workflow abstraction.

### 6. Add command siblings incrementally, then make convenience methods delegate

For every finite public operation that can spawn a process, add the sibling `<operation>_command(...) -> Command[T]`; the existing `<operation>(...) -> T` becomes exactly `return self.<operation>_command(...).run()` after compatibility arguments are normalized. Read-only operations that spawn probes still use the process executor and need a public command sibling when they are public process-spawning SDK operations under #40.

`EnvironmentMonitor.snapshot()` is such a finite operation: add `snapshot_command()` and delegate one-shot collection to it. `watch()` is the deliberate exception to finite delegate-once semantics because it is an unbounded streaming coordinator and one frozen plan cannot honestly capture future probe inputs. Each watch tick constructs and runs one fresh immutable `snapshot_command()`; it never launches directly, never reuses a prior tick's plan/ledger, and all process-backed Git/storage/Docker/PostgreSQL probes still pass through `internal/proc`.

Start with checkout because it already has a public domain plan and dry-run. `checkout_command()` attaches/preserves `EnvironmentCheckoutPlan`; `plan_checkout()` remains the domain projection; `checkout_with_plan()` uses the same command and does not rebuild private commands. Then migrate Odoo captured/foreground/shell paths, tests/automation, Git and uv provisioning, Docker/PostgreSQL, backup validation, pgAdmin helpers, dependency probes, ACL/disk usage, and termination.

Only after a family has parity tests should its old runner/reconstruction path be deleted. The final tree may not retain parallel executors or preview planners.

### 7. Restrict Expression to pure expected-error stages

Add Expression as a bounded runtime dependency and use its typed `Result`/pipeline primitives only for pure resolve, validation, selection, normalization, and capture stages. One existing complex planning flow—checkout—is the initial proof. Convert the final expected `PlanError` to the project’s concrete public exception vocabulary at `Command` construction/CLI boundaries; Expression types never appear in public annotations or values.

Do not use Expression for Click decorators, serializers, subprocess execution, process handles, locks, cleanup, rollback, compensation, foreground waits, or OS/runtime faults. Those faults stay concrete exceptions from the effect adapter.

Record a small before/after count for planning branches and required unwrap/adapters in the checkout slice. This is a preliminary check: if the slice adds more adapters/unwraps than branching it removes, remove Expression immediately while retaining the same typed stage signatures. If checkout is positive, retain a repository-local rule requiring #35 to repeat the same measurement after its vertical slice; no checkout result may waive that stop condition, and Expression may not expand beyond the already approved bounded use until #35 records its result.

### 8. Deepen the existing output module into one typed document boundary

Replace dicts typed with `Any` in `commands/output.py` with a CLI-private frozen `OutputDocument`, typed error/context structures, and `JsonValue`. Success and failure constructors enforce the envelope-v1 invariant. JSON and TOON serialize the same builtins projection. Rich renderers remain adjacent to commands and return renderables or sanitized text; they never write output themselves.

One `emit(document, mode, rich=...)` path owns bounded stdout/stderr, serializer selection, failure exit mapping, and diagnostics. Reusable decorators own `--format`, `--json`, and `--dry-run` resolution. Callbacks resolve context, call one SDK/use-case command, construct one document, and invoke the emitter; they contain no output-mode branches or subprocess argv.

Native Odoo foreground/shell stdio, `logs --follow`, and explicitly documented JSONL streams bypass bounded documents during normal execution but still use the process executor where they launch children and use a small native-stream allowlist. Transport and preview are separate axes: eligible spawning `run` and `shell` leaves build the same command for both paths, retain native streams in normal mode, and emit a bounded plan without launching in dry-run mode. Their dry-run accepts `--format rich|json|toon` (default Rich) and `--json` as the JSON shorthand; the same output options without `--dry-run` fail during Click option resolution with exit `2` before SDK resolution or process launch. A non-previewable native exception must be read-only with no finite child-process/mutation plan and record that concrete reason in the canonical CLI inventory. `PublicLeafCase`/`PUBLIC_LEAF_CASES` in `tests/unit/test_cli_output_modes.py` remains the single source for the bounded normal-execution inventory, including `test`, `db refresh`, and `db reset-admin-password`; architecture/spec tests compare against it rather than copy it. Click usage errors that occur before mode resolution retain Click’s native stderr/exit-2 behavior. Existing Rich live views must obtain their console/live transport from the output adapter rather than constructing `Console()` in callbacks.

An AST contract test forbids production `print`, `click.echo`/`secho`, direct stdout/stderr writes, and `Console().print` outside the output boundary and documented native-stream/Odoo-stdin source-string allowlists. Source text intentionally sent to Odoo shell is distinguished from a Python output call node in production code.

### 9. Remove explicit production `Any` and bare `object` annotations at boundaries

Introduce recursive `JsonValue`, precise unions, `Protocol`, `TypedDict`/msgspec structs, and concrete FastAPI/Starlette callable and response types. Untyped third-party results are accepted in one adapter, validated/narrowed immediately, and returned as a concrete type. Dynamic lazy-export helpers use an exhaustive private union/protocol vocabulary rather than annotating returns as `Any` or `object`.

Add an AST gate over `src/odoo_instance_sdk` that rejects explicit `Any`, `object`, `typing.Any`, and quoted equivalents in annotations; it does not reject legitimate runtime `isinstance(value, object)` use or test annotations. Strict mypy remains the semantic gate. Do not add mypy plugins or weaken existing overrides to pass.

### 10. Preserve lightweight startup by lazy public exports and callback-local imports

Add execution public names to the existing lazy root export map and keep process/output/Expression imports behind operation callbacks or first SDK access. Extend MYL-67 fresh-interpreter tests so package import, `odcli --help`, and `odcli --version` still exclude `httpx`, monitor implementations, `internal.proc`, and Expression modules. No timing threshold is added; importtime remains developer evidence.

## Risks / Trade-offs

- [A private callback launches a command absent from the plan] -> Require every launch to consume a captured step identifier and assert all/only expected process steps were consumed in parity tests.
- [Redaction changes argument boundaries or hides executable behavior] -> Store argv boundaries before display rendering and compare recorded executor input after the same field-wise redaction function.
- [A stale validator mutates or silently rebuilds the plan] -> Keep validators read-only, test changed Git/path/port/database facts, and require `StalePlanError` before the first mutating step.
- [Full migration becomes a long-lived dual architecture] -> Land family-by-family on one branch but delete each legacy runner once its family passes; the final static gate has an empty direct-launch allowlist.
- [Expression adds ceremonial adapters] -> Measure checkout preliminarily, preserve the mandatory repository-local #35 vertical-slice recheck, constrain usage by import/contract tests, and remove the dependency whenever either stop condition is met.
- [Typed output centralization erases useful Rich UX] -> Keep command-local pure Rich projections and centralize only transport, document, sanitization, mode, and exit ownership.
- [The `Any`/`object` gate collides with weak third-party stubs] -> Narrow once in typed adapters and use concrete framework protocols; do not weaken the gate or spread casts.
- [New public exports regress metadata startup] -> Preserve the lazy export map and run fresh-interpreter boundary tests in every migration phase.

## Migration Plan

1. Add architecture rules, the preliminary checkout Expression decision check plus the mandatory post-#35 recheck rule, public frozen models/errors, private prepared-step model, redaction/fingerprint logic, recording executor, and focused unit tests without routing production launches yet.
2. Migrate checkout as the compatibility proof: one command object, one domain plan, dry-run/run parity, stale checks, no mutation during planning, and measured Expression payoff.
3. Implement the real proc executor and migrate Odoo process lifecycle/captured shell, preserving TTY, signals, handles, stdin, timeout, and secret cleanup.
4. Migrate remaining Git/uv/environment, tests/automation, Docker/PostgreSQL, backup/pgAdmin/probe launch sites; update tests from module-local subprocess patches to recording/fake executor seams and delete old runners.
5. Add all remaining public `*_command()` siblings and delegate convenience APIs; verify every production launch is represented and consumed from a captured plan.
6. Replace the loose output envelope with the typed document/emitter, add universal eligible `--dry-run`, migrate bounded callbacks and native-stream allowlists, and delete mode-specific emission branches.
7. Narrow remaining explicit production `Any`/`object` annotations, enable the final empty-allowlist process gate plus output/type AST gates, and update `AGENTS.md`, README, and Python SDK docs.
8. Run focused parity/redaction/stale/TTY tests, the MYL-67 startup checks, strict mypy, Ruff, offline and integration suites, packaging tests, and full `make pr`.

Rollback before merge is a normal branch revert because no persisted schema or user data migration is introduced. If a migration phase fails, revert that family to `origin/main`; do not ship a parallel executor/output path as fallback.

## Open Questions

None. GitHub #45/#40 and current repository contracts fix the public vocabulary, migration scope, output reuse, Expression boundary, compatibility requirements, and stop conditions.
