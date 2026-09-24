# Execution boundary and CLI inventory

This page documents the public inspect-then-run contract implemented by the
SDK and CLI. The checked `PublicLeafCase`/`PUBLIC_LEAF_CASES` table in
`tests/unit/test_cli_output_modes.py` is the only leaf inventory; this page is
an explanatory mirror of that table, not a second source of truth. If a leaf
changes, update the canonical table and its characterization tests first,
then update this page.

## Inspect-then-run contract

Every eligible bounded leaf resolves inputs once and captures one immutable
`Command` before confirmation or mutation. `--dry-run` emits its redacted
`ExecutionPlan` and does not call `.run()`, prompt, or launch a process, except
for the explicit `update` read-only resolve/preflight stage described below.
The normal path confirms only after planning and runs that same command object.
`--format json` is the only JSON selector; removed `--json` is a Click usage
error with exit code `2`. Rich, JSON, and TOON are projections, not independent
planners.

`update` with a mutable ref is the explicit two-stage exception: it first runs
the captured read-only resolver command, then captures the resolved full-SHA
mutation command. The second command is the only command shown for mutation
preview, confirmation, and execution; `update --dry-run` may run the captured
read-only resolver and preflight phases, then shows the immutable install and
migration steps without running either. Exact-SHA ancestry validation is also
represented by captured Git `ProcessStep`s. Their ephemeral object-store
setup/fetch steps are explicitly marked as mutating, and no unvalidated
provenance URL reaches `git fetch`. A user-triggered
migrate journal also resumes through this coordinator; direct maintenance is
reserved for the explicit `ODCLI_MAINTENANCE=1` hand-off.

Plans preserve ordered process/action steps, argv boundaries, sanitized
environment policy, multiline stdin/source previews, observations, warnings,
classification flags, and a redacted fingerprint. Private callbacks,
snapshots, secret values, and executor ledgers are never serialized. A stale
captured precondition raises a typed error before the first effect rather than
reselecting or rebuilding the command.

## Eligible CLI leaves

The following entries are the current `PUBLIC_LEAF_CASES` members whose
classification is bounded and whose contract requires `--dry-run`:

| CLI leaf | canonical classification |
| --- | --- |
| `init` | mutating-or-spawning |
| `stop` | mutating-or-spawning |
| `env create` | mutating-or-spawning |
| `env rm` | mutating-or-spawning |
| `env sync` | mutating-or-spawning |
| `backup rm` | mutating-or-spawning |
| `db refresh` | mutating-or-spawning |
| `db restore` | mutating-or-spawning |
| `db rm` | guarded mutating-or-spawning |
| `db reset-admin-password` | mutating-or-spawning |
| `db init-monitoring` | mutating-or-spawning |
| `eval` | process-previewable-read-only |
| `exec` | mutating-or-spawning |
| `test` | process-previewable-read-only |
| `module ls` | process-previewable-read-only |
| `module update` | mutating-or-spawning |
| `module test` | mutating-or-spawning |
| `module install-order` | process-previewable-read-only |
| `translations export` | mutating-or-spawning |
| `deps verify` | process-previewable-read-only |
| `vscode generate` | mutating-or-spawning |
| `postgres approve-image` | mutating-or-spawning |
| `postgres up` | mutating-or-spawning |
| `postgres stop` | mutating-or-spawning |
| `git commit` | mutating-or-spawning |
| `git absorb` | mutating-or-spawning |
| `git sync` | mutating-or-spawning |
| `bug-report init` | mutating-or-spawning |
| `bug-report submit` | mutating-or-spawning |
| `update` | mutating-or-spawning (`update --check` is process-previewable-read-only) |
| `psql` | native-passthrough |
| `run` | native-passthrough |
| `shell` | native-passthrough |

PostgreSQL database diagnostics (`db locks`, `db stats`, and `db bloat`) are
bounded read-only typed documents and use the same resolver, captured
`Command`, redacted plan, and shared output projections. `db init-monitoring`
is a mutating leaf: it requires explicit confirmation, is inert in dry-run,
and is rejected before planning for external clusters. The root `psql` leaf
is a native inherited-stream transport; its passthrough arguments are checked
by the private grammar, but document formatting is rejected on normal runs.
All four diagnostics and native `psql` preserve the instance-bound cluster
identity and do not accept replacement host/user/password flags.

The backup catalogue leaves (`backup ls`, `backup inspect`, and `backup validate`)
are bounded read-only documents independent of Odoo/worktree context. They
resolve complete UUIDs through the state-aware catalogue projection; `backup
rm` adds the same immutable preview and explicit confirmation contract as
the other guarded mutations.

`db rm` is a guarded database mutation. Its plan records the bounded,
read-only planning inspection as an observation; execution retains separate
revalidation, optional target-session termination, drop, and absence-verification
steps. Dry-run performs only the planning inspection and never mutates the
cluster or catalogue. `backup rm`, `db rm`, and `env rm` additionally accept
variadic multi-target arguments: each target is resolved and previewed
independently. Planning/preflight failures abort before mutation; once
execution starts, a guarded failure is recorded for that target and the
remaining prepared targets continue. The aggregate result exits non-zero when
any target fails and does not claim to roll back earlier targets.

`ps` and `postgres ps` are bounded read-only leaves. Root `ps` is backed by
the public `EnvironmentMonitor.processes_command()` SDK primitive; `postgres ps`
is backed by `PostgresCluster.status_command()`. Both project typed read-only
inventory without `--dry-run` and support Rich, JSON, and TOON output where
applicable.

The complete shipped CLI also contains `doctor` and `env ls` as bounded
read only leaves, plus `resource ls`, `resource doctor`, `run`, `shell`,
`logs`, and `monitor` native/stream leaves. They remain in `PUBLIC_LEAF_CASES` with their explicit classifications
and reasons; no parallel eligibility table is permitted.

`run` is a native foreground/stream leaf, but `run -d` / `--detach` is a
bounded detached launch: its `--dry-run` captures the detached plan without
spawning, and execution returns a typed `DetachedLaunchResult` once Odoo is
alive. The SDK sibling is `instance.run_detached_command()`.
`resolve_effective_logfile()` chooses one path for detached spawn, `logs`,
structured results, and runtime metadata: explicit non-empty `logfile` in
effective `odoo.conf` wins; otherwise `odoo.log` next to the effective config
is created before spawn and passed through `--logfile` without editing the
user's config.

`bug-report init` and `bug-report submit` are bounded mutating leaves backed by
`bug_report_init_command()` and `bug_report_submit_command()`. Submit locks
`get_locks_dir()/bug-report-{REPORT_ID}.lock` without Expression, records submit
intent as an ActionStep, and invokes `gh` through `internal/proc` with
`--body-file -`.

`update` is a bounded mutating leaf backed by `update_command()`. Mutating
execution acquires `get_locks_dir()/odcli-update.lock`, snapshots affected
metadata under `get_user_root()/update/`, and runs maintenance as a child
`odcli update --format json` with `ODCLI_MAINTENANCE=1`.

The Git workflow leaves use the same captured-plan boundary: `git commit` and
`git absorb` require explicit confirmation for mutation, while `git check` is
read-only and `git sync` captures fetch, integration, validation, and optional
same-name publication before confirmation. Dry-run emits the complete Git
plan without launching fetch, rebase, absorb, commit, or publication steps.

Resource inventory and doctor are observation-only projections. They report
logical versus measured bytes, ownership confidence, retained data, and
incomplete probes, but never turn an observation into a lifecycle mutation.
Age-based backup pruning, log rotation, and `postgres destroy` remain separate
evidence-gated future changes: each needs its own immutable preview, ownership
proof, active-reference protection, and postcondition-tested cleanup policy.

## Migration and failure notes

The catalogue migration is additive and preserves legacy rows with nullable
cluster and restore provenance. Older rows remain readable as unknown; no
second store is introduced. The schema is managed by Alembic migrations; the
first catalogue-backed operation stamps the current revision and applies any
pending upgrade under one locked, journaled transaction. Deployments should
retain the existing catalogue backup before applying a schema migration and
restore that backup before running older code. No migration deletes backups,
restores, databases, filestores, volumes, or audit history.

Streaming failures retain the exact backup UUID and sanitized state context.
The `.part` file is removed only for a handled pre-publication failure; a
published backup is retained. Restore and drop failures retain confirmed
artifacts and return typed partial outcomes rather than claiming rollback of
remote or filesystem effects. Ctrl-C closes owned progress, locks, response,
and file handles and returns `130` where the command owns the interruption.

These notes describe the shipped boundary, not an authorization for automatic
cleanup. Prune, log rotation, and `postgres destroy` require separate
evidence-gated changes with their own preview, ownership proof, active-reference
checks, and postcondition tests.

## Reasoned native and stream exceptions

| leaf | canonical exception | removal condition |
| --- | --- | --- |
| `run` | normal execution owns inherited foreground Odoo TTY streams; dry-run remains available | remove only if Odoo foreground I/O becomes a finite bounded document without changing native exit/stream semantics |
| `shell` | normal execution owns interactive Odoo streams and delimiter/passthrough args; dry-run remains available | remove only if interactive shell is replaced by a finite protocol while preserving TTY behavior |
| `logs --follow` | read-only logfile subscription is an unbounded JSONL stream, not a finite child plan | remove when the product offers a bounded log snapshot with an explicit follow transport |
| `monitor` | long-running monitor server is an unbounded HTTP/dashboard coordinator | remove when the server is no longer a service or gains a separate finite snapshot leaf |

These are transport exceptions, not process-boundary exceptions: Odoo child
launches still go through `internal/proc`, and output-option validation for
`run`/`shell` still happens before SDK resolution.

## SDK-first leaf inventory

`tests/unit/test_cli_output_modes.py::PUBLIC_LEAF_CASES` is the only CLI leaf
inventory. Every entry records either a public `sdk_primitive` or a concrete
`cli_only_reason` for transport-only leaves such as `run`, `shell`, `logs
--follow`, and `monitor`. The contract test rejects a leaf without one of
those two values, and an architecture gate rejects Click callbacks that call
parallel internal domain builders where a public SDK primitive already exists.

## Checked architectural inventories and allowlists

The exact checked fixture is `tests/fixtures/architecture_inventory.py` and
the enforcing tests are `tests/unit/test_architecture_inventory.py`.

### Process launches

`DIRECT_SUBPROCESS_LAUNCHES` is an empty set outside
`src/odoo_instance_sdk/internal/proc`. The AST gate reports every unexpected
launch as `file:line`; no production process allowlist remains. The removal
condition for any future finding is to route that launch through the private
prepared-step executor before merging. `PUBLIC_PROCESS_METHODS` is also empty
because all discovered public spawning methods now delegate through command
siblings.

### Direct output writes

The only production output allowlist is line-specific and each entry is
documented by `OUTPUT_WRITE_REASONS`:

- `src/odoo_instance_sdk/commands/cli_parts/callbacks.py:427-428` — documented
  `logs --follow` JSONL stream; remove when that stream gets an explicit bounded
  transport.
- `src/odoo_instance_sdk/commands/cli_parts/registration.py:473` — documented
  `--version` metadata flag transport; remove only if `--version` gains a
  replacement centralized emitter.
- `src/odoo_instance_sdk/commands/backup.py:348` — shared Rich validation
  boundary; remove only if validation gains a replacement centralized emitter.
- `src/odoo_instance_sdk/commands/output.py:247` — shared Rich output
  boundary; remove only if the output library gains a replacement emitter.
- `src/odoo_instance_sdk/commands/output.py:416` — shared JSON emitter;
  remove only with a replacement centralized serializer.
- `src/odoo_instance_sdk/commands/output.py:418` — shared TOON emitter;
  remove only with a replacement centralized serializer.
- `src/odoo_instance_sdk/commands/output.py:425` — shared diagnostic emitter;
  remove only when diagnostics have another centralized stderr adapter.
- `src/odoo_instance_sdk/commands/output.py:427` — shared diagnostic emitter;
  remove only when diagnostics have another centralized stderr adapter.
- `src/odoo_instance_sdk/internal/self_update.py:813-815` — maintenance child
  JSON stdout transport; remove when maintenance output gains a replacement
  centralized emitter.
- `src/odoo_instance_sdk/resources/instance/identity.py:494` — lifecycle cleanup
  diagnostic transport; remove when cleanup diagnostics have an explicit
  logger/diagnostic adapter without changing native cleanup behavior.

### Production type annotations

`EXPLICIT_IMPRECISE_ANNOTATIONS` records deliberate third-party adapter seams
in `bug_report.py`, `internal/bug_report.py`, `internal/dbprep/bootstrap.py`,
`internal/transport/*`, and `project_init.py`. The AST gate rejects direct,
qualified, and quoted `Any`/bare `object`, empty marker Protocols,
opaque-named aliases, and broad `Callable[..., ...]`; every finding includes
`file:line`. The removal condition for a future finding is to narrow it at the
external adapter boundary to `JsonValue`, a validated model, or a concrete
protocol—not to add an exception.

### HTTP transport and XML-RPC boundary

`DIRECT_HTTPX_USAGE` is a line-specific allowlist under
`src/odoo_instance_sdk/internal/transport/` only. Production Odoo HTTP calls
go through the internal `OdooHttpClient`; `httpx` types and exceptions do not
leak into public SDK or resource interfaces. `import odoo_instance_sdk.cli`
remains free of `httpx`. An architecture gate requires zero
`xmlrpc.client.ServerProxy` call sites in `src/`; XML-RPC verification stays in
`tests/fixtures/xmlrpc_probe.py` for real-Odoo acceptance only.

### Self-update and bug-report locks

`odcli update` acquires `get_locks_dir()/odcli-update.lock` during mutating
execution. Unfinished migration journals cause `update` to resume and other
commands to fail with `update_incomplete`. `bug-report submit` acquires
`get_locks_dir()/bug-report-{REPORT_ID}.lock` so uncertain `gh` outcomes do
not blind re-POST.

### Test-only subprocess patch seams

`MODULE_LOCAL_SUBPROCESS_PATCHES` records the remaining legacy test patch
locations while the production launch inventory is empty:

- `tests/unit/resources/test_database_resource.py:632`
- `tests/unit/test_monitor_cache_and_docker.py:119`
- `tests/unit/test_cluster_resources.py:188`
- `tests/unit/test_real_odoo_ci_components.py:40,109,151`
- `tests/unit/test_real_odoo_foundation.py:325,348,367`

These are not production launches or public behavior exceptions. Their removal
condition is migration of each fixture to the shared recording executor; the
architecture test rejects both additions and unexplained line changes.

## Startup evidence (MYL-67)

The final fresh-interpreter boundary was checked on the verification tree with
Python 3.12.13 on Darwin arm64. The exact import-time command was run three
times:

```console
$ uv run python -X importtime -c 'import odoo_instance_sdk.cli'
import time:      5887 |     352511 | odoo_instance_sdk.cli
$ uv run python -X importtime -c 'import odoo_instance_sdk.cli'
import time:      8778 |     215221 | odoo_instance_sdk.cli
$ uv run python -X importtime -c 'import odoo_instance_sdk.cli'
import time:      5942 |     211052 | odoo_instance_sdk.cli
```

These values are evidence only; no timing threshold is part of the gate. The
fresh-process module-presence check reported `execution`, `internal.proc`,
Expression, and `httpx` all absent after importing the package. The checked
startup tests separately cover `odcli --help` and `odcli --version` with the
same forbidden-module boundary.
