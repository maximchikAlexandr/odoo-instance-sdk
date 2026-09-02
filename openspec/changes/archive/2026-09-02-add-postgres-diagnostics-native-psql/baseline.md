## Block 1 baseline (tasks 1.1–1.3)

Captured on 2026-09-02 on Darwin, Python 3.12.13, uv 0.11.1, from the
`feat/MYL-70-postgres-diagnostics` worktree rebased onto the current
`origin/main` commit `ea24ac9e79ea497fee985e72a6854cf61f08d614`.

### 1.1 Prerequisite and rebase

The requested pre-change sequence completed successfully. `main` and the
feature branch are separate worktrees. The feature checkout was rebased onto
`origin/main`; this handoff checkout carries the resulting fast-forwarded
history:

```text
git fetch origin
git switch main
git merge --ff-only origin/main
git switch feat/MYL-70-postgres-diagnostics
git rebase origin/main
git merge-base --is-ancestor origin/main HEAD
```

After `git fetch origin`, `main` was already at
`ea24ac9e79ea497fee985e72a6854cf61f08d614` and
`git merge --ff-only origin/main` returned `Already up to date`. The merge
commit is
`Merge pull request #47 from maximchikAlexandr/feat/MYL-68-execution-architecture`.
The feature branch rebased cleanly onto current `origin/main`; the baseline
handoff before review fixes was `1662d50` (the commit's full SHA is recorded by
Git). The ancestry check above returned zero.

```text
git merge-base --is-ancestor origin/main HEAD
```

The launch prerequisite is MYL-68 in `done`. The shared execution contract
landed in the merge commit above and is present; no prerequisite gap was
found.

Exact shared APIs to reuse:

- `src/odoo_instance_sdk/execution.py:38-61` — frozen public `ProcessStep`;
  `:64-80` — frozen `ActionStep`; `:85-128` — `ExecutionPlan` and canonical
  redacted fingerprint; `:144-195` — immutable `Command[T]`,
  `Command.from_prepared()`, `Command.create()`, `.plan`, `.commands`, and
  `.run()`.
- `src/odoo_instance_sdk/internal/proc/__init__.py:71-128` — private
  `PreparedStep` capture and public projection; `:160-250` — per-run
  `RunContext` ledger (`process_prepared`, `spawn`, `action`, `skip`,
  `complete`); `:262-299` — `PreparedCommand` and `prepared_command()`.
- `src/odoo_instance_sdk/internal/proc/executor.py:173-262` — shared
  `SubprocessExecutor` for captured and inherited-stdio launches with
  `shell=False`; `:265-309` — `prepared_step()`; `:496-608` — `spawn()` and
  `wait_foreground()` with process-group signal cleanup.
- `src/odoo_instance_sdk/commands/output.py:106-148` — local output and
  dry-run option composition; `:290-312` — one typed JSON/TOON/Rich emitter;
  `:403-454` — `run_or_preview()` builds once and runs that same command.
- `tests/unit/test_cli_output_modes.py:84-202` — canonical
  `PublicLeafCase`/`PUBLIC_LEAF_CASES` inventory; `tests/unit/test_cli_characterization.py:98-108`
  — existing shared `_passthrough_instance`; no PostgreSQL-only fake is
  needed.

### 1.2 Registration/startup seam

The existing registration and startup contract is explicit:

- `src/odoo_instance_sdk/cli.py:15` imports the lightweight `db_group`, and
  `:202-204` registers `env`, `test`, and `db` on the root Click group.
- `src/odoo_instance_sdk/commands/db.py:31-44` keeps operation-only types in
  `TYPE_CHECKING` and defines the existing
  `@click.group()`/`@db_group.command` pattern; `:154-174` provides the typed
  lazy `__getattr__` that keeps client/config imports out of command
  discovery.
- `tests/unit/test_cli_startup.py:34-60` verifies bare-package fresh-process
  imports, `:70-106` verifies `cli --help/--version` do not import
  `httpx`, PostgreSQL-adjacent resources, execution, proc, or expression, and
  `:109-126` verifies lazy-export identity/order/error behavior.
- Existing PostgreSQL root callbacks are registered in
  `src/odoo_instance_sdk/cli.py:1180-1281`; this is the seam that the later
  `commands/pg.py` extraction must preserve.

The future `commands/pg.py` must remain startup-light: only Click/output and
typed context seams may load at module discovery; PostgreSQL resources and
transport must load lazily inside callbacks or through a typed lazy export.
This block records the contract only; no production code was changed.

### 1.2 PostgreSQL process/output audit

Current process/transport seams and violations to remove in this change:

- `src/odoo_instance_sdk/internal/postgres_transport.py:15-109` exposes the
  legacy `run_psql()` returning `subprocess.CompletedProcess | None`, manually
  reconstructs argv/environment, directly calls `SubprocessExecutor`, and
  bypasses an immutable public `Command`/plan. It is not an AST-level direct
  `subprocess.run`/`Popen` launch, but it is a PostgreSQL-specific executor
  adapter and duplicate process-result boundary.
- `src/odoo_instance_sdk/resources/database.py:62-109` duplicates psql argv
  and environment construction in `_database_psql_step()`; `:158-192`
  delegates the Odoo-unavailable fallback to `run_psql()`. The probe is wired
  through `exists/current/restore/drop` at `:318-403` and
  `:751-868`, so those consumers must eventually use the one shared builder.
- `src/odoo_instance_sdk/internal/postgres_size.py:3-41` and
  `src/odoo_instance_sdk/internal/test_selection.py:388-401` call the legacy
  transport and return ad-hoc scalar/`CompletedProcess` results.
- `src/odoo_instance_sdk/internal/automation.py:533-579` manually constructs
  a PostgreSQL `PreparedStep` for module provenance. The neighboring
  `src/odoo_instance_sdk/resources/monitor.py:481-549` manually constructs
  identity and database-size psql steps. These are consumers to audit during
  migration; their owning behavior remains outside the new public diagnostics
  resource surface unless the later task explicitly moves the PG seam.
- `src/odoo_instance_sdk/internal/database_preparation.py:876-937` imports
  and reuses the database-local duplicate builder for restore existence
  probes; it must keep its existing lifecycle ordering while the helper is
  consolidated.
- `src/odoo_instance_sdk/internal/postgres_cli.py:28-43` runs a generic
  callable operation rather than a command-local shared composition;
  `:46-57` and `:104-137` own PostgreSQL JSON/Rich projections. The root
  callbacks in `src/odoo_instance_sdk/cli.py:1180-1281` register the existing
  PostgreSQL leaves and should become thin registrations in `commands/pg.py`.
- `src/odoo_instance_sdk/models.py:628-636` has the existing
  `ClusterSnapshot` lifecycle/resource fields but no server summary. The
  existing shared cluster-resource seam is
  `src/odoo_instance_sdk/internal/cluster_resources.py:317-456`
  (`BatchClusterRequest` and `collect_cluster_resource_batch`) and must retain
  its current batch/cache ownership.

The repository-wide AST guard currently finds no production
`subprocess.run`/`subprocess.Popen` outside `internal/proc`. It does find only
the accepted direct output locations in
`tests/fixtures/architecture_inventory.py:19-44` (shared output, logs JSONL,
Rich-live, and lifecycle cleanup). PostgreSQL's current boundary problem is
therefore duplicate process/CLI/output composition, not a new raw subprocess
call.

### 1.3 Reproducible pre-change gates

All `make pr` targets were run individually from the rebased feature checkout
at baseline-only HEAD `1d18d30a2c501bc8c212ab03a15b6b674f09181a`, because a
literal `make pr` stops at its first failing target. This is the complete
`pr: lint types test compat dashboard smoke package` matrix:

| Gate | Result |
| --- | --- |
| `uv run openspec validate add-postgres-diagnostics-native-psql --strict` | PASS — change valid. |
| `make lint` | PASS — Ruff format: 196 files already formatted; Ruff check: all checks passed. Non-blocking uv 0.11.1/build-system and deprecated license-classifier warnings were emitted. |
| `make types` | PASS — mypy strict source: 71 files; tests/scripts: 124 files. |
| `make test` (coverage/threshold) | PASS — parallel 1309 passed in 268.13s; serial 11 passed, 1 skipped in 106.17s; total coverage 83%; `scripts/check_coverage.py` completed successfully. |
| `make compat` | **FAIL (environment/runtime blocker)** — 1308 passed, 1 failed in 256.54s; `test_production_docker_collection_batches_two_projects` hit pytest-timeout `>60.0s` while waiting in the monitor subprocess probe. The local Docker Engine is Colima-backed and available (`docker info` succeeds); the same test passes isolated in 30.04s and the serial compatibility target passes 11/1 skipped. |
| `make dashboard` (OpenAPI/codegen) | **FAIL (environment blocker)** — `npm ci` added 234 packages successfully, then `make web-codegen-check` failed at `scripts/export_openapi.py` with `ModuleNotFoundError: No module named 'fastapi'`. |
| `make smoke` | PASS with environment skip — 1 skipped because `fastapi` is not installed (`tests/integration/test_monitor_smoke.py:65`). |
| `make package` | PASS — web build, sdist/wheel creation, and packaging suite 8 passed in 25.48s; exactly one wheel and one sdist were produced. |

The only failing PR target is therefore the parallel compatibility timeout;
the concrete environment blocker is the Colima-backed subprocess probe timing
out under the xdist compatibility run. Dashboard/OpenAPI is independently
blocked by the missing optional `fastapi` package. No production code was
changed by block 1.

Focused startup/import and foreground/TTY guards:

| Guard | Result |
| --- | --- |
| `uv run pytest -q -o addopts='' tests/unit/test_cli_startup.py` | PASS — 4 passed in 4.52s |
| `uv run pytest -q -o addopts='' tests/unit/internal/test_proc_boundary.py -k 'inherited or foreground or tty'` | PASS — 1 passed, 13 deselected in 0.53s |
| `uv run pytest -q -o addopts='' tests/unit/test_cli_characterization.py -k 'passthrough or raw_stream'` | PASS — 45 passed, 12 deselected in 0.97s (prior block-1 baseline; no production changes since) |
| `uv run pytest -q -o addopts='' tests/integration/test_foreground_signal.py tests/integration/test_foreground_exception_cleanup.py` | PASS — 3 passed in 27.27s (prior block-1 baseline; no production changes since) |
| `uv run pytest -q -o addopts='' tests/unit/test_architecture_inventory.py` | PASS — 9 passed in 13.76s |
