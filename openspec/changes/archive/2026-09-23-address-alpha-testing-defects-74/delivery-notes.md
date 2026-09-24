# Delivery notes — section 19 (address-alpha-testing-defects-74)

CI baseline: GitHub Actions run [35857144617](https://github.com/maximchikAlexandr/odoo-instance-sdk/actions/runs/35857144617).

## Local gates (2026-09-23)

| Gate | Result | Notes |
| --- | --- | --- |
| `make lint` | **pass** | Ruff format/check, Alembic catalog, and production line-limit checks pass |
| `make types` | **pass** | Strict production mypy plus test/script mypy pass |
| `make test` | **pass** | 3256 non-serial tests passed; 13 serial passed, 2 skipped; coverage gate passed with catalog branches at 83.16% |
| `make compat` | **pass** | 3257 non-serial tests passed; 13 serial passed, 2 skipped; run with the repository's `ulimit -n 4096` gate setting |
| `uv build` | **pass** | Wheel and sdist built successfully |
| `make package` | **pass** | 11 packaging tests passed; the historical full-update E2E was skipped because the target revision was not installable from GitHub in that runner environment |
| `make dashboard` / `make smoke` / `make live` | **skipped** | External prerequisites (Node dashboard, Docker smoke, real Odoo) |

## OpenSpec

- Archived delta specs remain EOF-clean and validate with `openspec validate --specs`.
- Completion markers are retained only for gates with recorded passing evidence; external-prerequisite gates remain separately identified above.

## PR

- Existing PR [#79](https://github.com/maximchikAlexandr/odoo-instance-sdk/pull/79) / GitHub issue [#74](https://github.com/maximchikAlexandr/odoo-instance-sdk/issues/74) remains authoritative.
