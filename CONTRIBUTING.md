# Contributing to odoo-instance-sdk

## Setup

```bash
git clone https://github.com/maximchikAlexandr/odoo-instance-sdk.git
cd odoo-instance-sdk
uv sync --frozen --dev --extra dashboard
git config core.hooksPath .githooks
```

`uv sync --dev --extra dashboard` installs the local PR dependencies, including the monitor API/smoke-test extra. Mutation testing also needs:

```bash
uv sync --frozen --group mutation --group test
```

## Style & quality

```bash
make lint
make types
make test
```

`make pr` is the local equivalent of the required PR gates: lint, types, coverage tests, compatibility tests, dashboard unit/build checks, the mandatory monitor smoke check, and package checks. `make smoke` is intentionally separate so its integration marker cannot be hidden by dashboard unit-test selection. It does not run mutation or live Odoo. The package gate builds the bundled dashboard, so install a supported Node.js/npm runtime (CI uses Node 22) before running it locally.

The reproducible core offline gate from a clean checkout is `make test`. It uses
`not real_odoo and not packaging and not dashboard`, so it does not require
package artifacts, Node.js, or dashboard dependencies. The optional gates are
separate: `make package` runs `npm ci`, builds the dashboard and Python
artifacts, then runs the packaging tests; `make dashboard` requires the
dashboard extra plus Node.js/npm, runs `npm ci`, validates OpenAPI codegen, and
runs the dashboard tests. Package tests are skipped with an actionable message
when artifacts have not been built, so an explicitly broad offline invocation
remains clean-checkout safe; use the named optional gates to exercise them.

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) and are enforced by the local `commit-msg` hook. The `pre-commit` hook runs Ruff and mypy only.

## Test layers

| Marker | When it runs |
| --- | --- |
| `unit` | Default offline suite |
| `integration` | Default offline suite; local fake HTTP, PostgreSQL, or process boundaries |
| `serial` | After the parallel slice; signal, process, and catalog concurrency |
| `packaging` | `make package` only |
| `dashboard` | `make dashboard` only; requires the dashboard extra and Node.js/npm |
| `real_odoo` | `make live` only |

The default suite is offline: no real Odoo, credentials, or external network.
`make test` excludes `real_odoo`, `packaging`, and `dashboard`; run `make package`
or `make dashboard` for those optional prerequisites and checks.

```bash
make targeted PYTEST_ARGS='tests/unit/internal/test_urls.py'
make coverage
make package
make compat
make mutation
make live
```

`make live` requires `ODCLI_REAL_ODOO_ENABLE=1` plus `ODCLI_REAL_PROJECT`, `ODCLI_REAL_ODOO_BIN`, `ODCLI_REAL_PYTHON`, `ODCLI_REAL_CONFIG`, and `ODCLI_REAL_DATABASE`. Do not run it on ordinary PR runners.

`make mutation` needs the `mutation` group and writes `.artifacts/mutation/results.txt`. It is a scheduled diagnostic, not a PR gate.

## Pull requests

1. Create a feature branch (`git checkout -b feat/your-feature`).
2. Make your changes; ensure `make pr` passes locally.
3. Push and open a PR against `main`. CI must pass before merge.
4. Use [GitHub Issues](https://github.com/maximchikAlexandr/odoo-instance-sdk/issues) for bug reports and feature requests.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).

## Documentation source of truth

The Click command tree in `odoo_instance_sdk.cli:cli` is the source of truth for
shipped CLI paths and executable `--help` is the source of truth for flags. The
README must keep the complete command-path inventory and one purpose sentence
per leaf command; do not add a separate generated CLI reference.

Public Python examples belong in `docs/python-sdk.md` and must use exported SDK
types. When changing commands or public SDK usage, run:

```bash
uv run pytest -q tests/unit/test_documentation_contract.py
```

This contract recursively compares Click leaves with the README, compiles and
imports documentation examples, checks shell fences with `bash -n`, and verifies
relative Markdown links deterministically.
