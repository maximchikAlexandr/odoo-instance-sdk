# PostgreSQL diagnostics verification

This is the final reproducible verification record for
`add-postgres-diagnostics-native-psql`. It records failures as environment or
pre-existing timing blockers; a skipped integration is not reported as a
successful PostgreSQL run.

## Command matrix

| Command | Result |
| --- | --- |
| `uv run openspec validate add-postgres-diagnostics-native-psql --strict` | PASS: change valid |
| `uv run ruff format --check .` | PASS: 215 files already formatted |
| `uv run ruff check .` | PASS |
| `uv run mypy --strict src/odoo_instance_sdk` | PASS: 83 source files |
| `uv run mypy tests scripts --namespace-packages --explicit-package-bases --ignore-missing-imports --follow-imports=silent --check-untyped-defs` | PASS: 131 files |
| focused diagnostics/builder/resource/status/CLI/startup/architecture/public-surface suite | PASS: 502 tests |
| `pytest -m integration tests/integration/test_postgres_diagnostics.py tests/integration/test_postgres_lifecycle.py` | PASS: 2 PostgreSQL integration tests in 62.94s (the new diagnostics test alone: 46.24s) |
| `make compat` | PASS: 1670 parallel tests; serial PASS: 12 passed, 1 skipped |
| `make dashboard` | BLOCKED after `npm ci` (2m): `ModuleNotFoundError: No module named 'fastapi'` during `web-codegen-check`; same dashboard/OpenAPI blocker as the pre-change baseline |
| `make smoke` | PASS as a command: 1 integration smoke test skipped in 9.40s because the dashboard extra (`fastapi`) is unavailable; not counted as a dashboard PASS |
| `make package` | PASS: web build, `uv build`, and 8 packaging tests in 19.79s |
| `make pr` | BLOCKED in its `test` target after 1669 passed by existing timing-sensitive `test_cluster_status_cached_5s` (`_calls == 2`, expected `1`) under xdist; isolated rerun PASS in 2.44s. The remaining targets were run independently above. |

The earlier complete offline `make test` reproduction likewise had 1668
passed and the same parallel environment behavior also produced a 60-second
architecture-guard timeout. Serial architecture/documentation guards passed
18/18 in 20.76s; the final non-parallel guard suite below passed 81/81.

## Guard and integration evidence

`tests/unit/internal/test_postgres_diagnostics.py` exercises the literal SQL
formulas and orders, top/timeout/scan bounds, null and zero decoding, cache
capability degradation, cumulative warnings, bloat methods, mixed optional
failure estimate retention, one-final-JSON recorded boundaries, rollback, and
temporary/persistent-object cleanup. `tests/integration/test_postgres_diagnostics.py`
then runs those contracts against a disposable PostgreSQL 16 Compose cluster:
it creates a table and index, holds an exclusive lock while a second session
waits, observes blocker PIDs, checks stats and bounded exact/estimated bloat,
runs monitoring initialization twice, checks healthy server status, and runs
native `psql -c 'SELECT current_database();'`. Backend termination and volume
cleanup are asserted in the teardown.

The final static command was:

```text
uv run pytest -q -o addopts='' tests/unit/test_architecture_inventory.py tests/unit/test_documentation_contract.py tests/unit/test_cli_startup.py tests/unit/test_cli_surface.py tests/unit/test_cli_characterization.py -o timeout=180
```

Result: 81 passed in 16.62s. This includes exact direct-process/output/type
inventories, startup/import budgets, README command inventory, CLI public leaf
surface, PTY/native-psql guards, JSON/TOON parity, redaction, and architecture
checks.

## Final `rg` audit

The production audit commands were:

```text
rg -n 'subprocess\.(run|Popen)|Popen\(' src/odoo_instance_sdk --glob '*.py' --glob '!internal/proc/**'
rg -n 'subprocess\.(run|Popen)|print\(|click\.(echo|secho)|json\.(dump|dumps)|toon|dry_run|Any|object' src/odoo_instance_sdk/internal/pg src/odoo_instance_sdk/commands/pg.py src/odoo_instance_sdk/resources/database.py
```

No PostgreSQL-specific direct launch, serializer, second executor, or
second dry-run boundary was found. No `Any`/`object` type escape was found in
the PostgreSQL implementation. The only residual production findings are the
pre-existing shared or intentionally native transports already pinned by
`tests/fixtures/architecture_inventory.py`:

- `src/odoo_instance_sdk/cli.py:650-651` — the documented `logs --follow`
  JSONL stream;
- `src/odoo_instance_sdk/commands/env.py:373` — existing Rich-live inventory;
- `src/odoo_instance_sdk/commands/output.py:190,300,302,309,311` — shared
  Rich/JSON/TOON/diagnostic emitters;
- `src/odoo_instance_sdk/resources/instance.py:812` — existing lifecycle
  cleanup diagnostic transport.

There are no new accepted violations and no PostgreSQL neighboring-domain
move. PostgreSQL SQL `json_build_object` occurrences are static server-side
final-document construction, not Python serializers; all process launches
remain behind `internal/proc`.
