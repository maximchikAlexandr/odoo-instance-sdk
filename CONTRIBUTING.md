# Contributing to odoo-instance-sdk

## Setup

```bash
git clone https://github.com/maximchikAlexandr/odoo-instance-sdk.git
cd odoo-instance-sdk
uv sync --frozen --dev
git config core.hooksPath .githooks
```

`uv sync --dev` installs the `lint`, `type-check`, and `test` groups. Mutation testing also needs:

```bash
uv sync --frozen --group mutation --group test
```

## Style & quality

```bash
make lint
make types
make test
```

`make pr` is the local equivalent of the required PR gates: lint, types, offline tests with zonal coverage, and package checks. It does not run mutation or live Odoo.

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) and are enforced by the local `commit-msg` hook. The `pre-commit` hook runs Ruff and mypy only.

## Test layers

| Marker | When it runs |
| --- | --- |
| `unit` | Default offline suite |
| `integration` | Default offline suite; local fake HTTP, PostgreSQL, or process boundaries |
| `serial` | After the parallel slice; signal, process, and catalog concurrency |
| `packaging` | `make package` only |
| `real_odoo` | `make live` only |

The default suite is offline: no real Odoo, credentials, or external network. `pytest` already excludes `real_odoo` and `packaging`.

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
