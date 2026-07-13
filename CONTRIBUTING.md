# Contributing to odoo-instance-sdk

## Setup

```bash
git clone https://github.com/maximchikAlexandr/odoo-instance-sdk.git
cd odoo-instance-sdk
uv sync --dev
git config core.hooksPath .githooks
```

## Style & quality

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src/odoo_instance_sdk
uv run pytest tests/ -v
```

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) and are enforced by the local `commit-msg` hook.

## Pull requests

1. Create a feature branch (`git checkout -b feat/your-feature`).
2. Make your changes; ensure the four commands above pass locally.
3. Push and open a PR against `main`. CI must pass before merge.
4. Use [GitHub Issues](https://github.com/maximchikAlexandr/odoo-instance-sdk/issues) for bug reports and feature requests.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
