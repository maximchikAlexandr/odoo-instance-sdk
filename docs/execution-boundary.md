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
`ExecutionPlan` and does not call `.run()`, prompt, or launch a process.
The normal path confirms only after planning and runs that same command object.
`--json` and `--format json` are aliases over the same frozen document;
Rich, JSON, and TOON are projections, not independent planners.

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
| `env checkout` | mutating-or-spawning |
| `env remove` | mutating-or-spawning |
| `env sync` | mutating-or-spawning |
| `db refresh` | mutating-or-spawning |
| `db reset-admin-password` | mutating-or-spawning |
| `eval` | process-previewable-read-only |
| `exec` | mutating-or-spawning |
| `test` | process-previewable-read-only |
| `module list` | process-previewable-read-only |
| `module update` | mutating-or-spawning |
| `module test` | mutating-or-spawning |
| `translations export` | mutating-or-spawning |
| `deps verify` | process-previewable-read-only |
| `vscode generate` | mutating-or-spawning |
| `postgres approve-image` | mutating-or-spawning |
| `postgres status` | process-previewable-read-only |
| `postgres up` | mutating-or-spawning |
| `postgres stop` | mutating-or-spawning |

PostgreSQL database diagnostics (`db locks`, `db stats`, and `db bloat`) are
bounded read-only typed documents and use the same resolver, captured
`Command`, redacted plan, and shared output projections. `db init-monitoring`
is a mutating leaf: it requires explicit confirmation, is inert in dry-run,
and is rejected before planning for external clusters. The root `psql` leaf
is a native inherited-stream transport; its passthrough arguments are checked
by the private grammar, but document formatting is rejected on normal runs.
All four diagnostics and native `psql` preserve the instance-bound cluster
identity and do not accept replacement host/user/password flags.

The complete shipped CLI also contains `doctor` and `env list` as bounded
read-only leaves, plus `run`, `shell`, `logs`, and `monitor` native/stream
leaves. They remain in `PUBLIC_LEAF_CASES` with their explicit classifications
and reasons; no parallel eligibility table is permitted.

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

- `src/odoo_instance_sdk/cli.py:740-741` — documented `logs --follow` JSONL
  stream; remove when that stream gets an explicit bounded transport.
- `src/odoo_instance_sdk/commands/env.py:373` — existing Rich-live inventory
  transport; remove when Rich live output is supplied by a distinct transport
  adapter rather than the live command callback.
- `src/odoo_instance_sdk/commands/output.py:190` — shared Rich output
  boundary; remove only if the output library gains a replacement emitter.
- `src/odoo_instance_sdk/commands/output.py:300` — shared JSON emitter;
  remove only with a replacement centralized serializer.
- `src/odoo_instance_sdk/commands/output.py:302` — shared TOON emitter;
  remove only with a replacement centralized serializer.
- `src/odoo_instance_sdk/commands/output.py:309` — shared diagnostic emitter;
  remove only when diagnostics have another centralized stderr adapter.
- `src/odoo_instance_sdk/commands/output.py:311` — shared diagnostic emitter;
  remove only when diagnostics have another centralized stderr adapter.
- `src/odoo_instance_sdk/resources/instance.py:918` — lifecycle cleanup
  diagnostic transport; remove when cleanup diagnostics have an explicit
  logger/diagnostic adapter without changing native cleanup behavior.

### Production type annotations

`EXPLICIT_IMPRECISE_ANNOTATIONS` is empty. The AST gate rejects direct,
qualified, and quoted `Any`/bare `object`, empty marker Protocols,
opaque-named aliases, and broad `Callable[..., ...]`; every finding includes
`file:line`. The removal condition for a future finding is to narrow it at the
external adapter boundary to `JsonValue`, a validated model, or a concrete
protocol—not to add an exception.

### Test-only subprocess patch seams

`MODULE_LOCAL_SUBPROCESS_PATCHES` records the remaining legacy test patch
locations while the production launch inventory is empty:

- `tests/unit/internal/test_pgadmin_files.py:421,480`
- `tests/unit/internal/test_postgres_size.py:28,55,78,109`
- `tests/unit/internal/test_postgres_transport.py:25,72,89,110,132,175`
- `tests/unit/resources/test_cli_automation.py:607`
- `tests/unit/resources/test_database_resource.py:395,421,448,473,518,539,558,573,585,599,617`
- `tests/unit/resources/test_environment_python.py:42,233,271`
- `tests/unit/test_monitor_cache_and_docker.py:128`

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
