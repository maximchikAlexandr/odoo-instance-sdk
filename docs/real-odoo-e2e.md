## Real Odoo E2E verification

The harness is opt-in and starts with a fail-closed prerequisite check. The
bootstrap writes a redacted machine-readable manifest and exits non-zero when
Docker, Compose, the pinned images, the required platform, Python 3.12.13,
uv 0.10.8, or (for full E2E) the pinned Odoo source revision is unavailable.
Missing prerequisites are failures, not pytest skips.

Install the frozen development environment, then run the tier you need:

```bash
uv sync --frozen --group test
uv run python scripts/real_odoo_bootstrap.py --tier smoke --output .artifacts/real-odoo-e2e/bootstrap.json
uv run pytest -o addopts='' --junitxml=.artifacts/real-odoo-e2e/junit.xml -p scripts.real_odoo_timing -m 'real_odoo and e2e_smoke' tests/integration/real_odoo
```

The single local full command is:

```bash
uv run python scripts/real_odoo_bootstrap.py --tier full --output .artifacts/real-odoo-e2e/bootstrap.json
uv run pytest -o addopts='' --junitxml=.artifacts/real-odoo-e2e/junit.xml -p scripts.real_odoo_timing -m 'real_odoo and e2e_full' tests/integration/real_odoo
```

Linux amd64 is the required CI platform. Docker Desktop arm64 is supported
locally when the pinned image index has an arm64 manifest; other platforms
fail before provisioning. `make real-odoo-smoke` and `make real-odoo-full`
are equivalent local entry points.

The full CI job restores only content-addressed Odoo source and uv caches.
Databases, filestore, generated configuration, secrets, catalogs, ports,
logs, worktrees, and backups are never cached. Cache misses are cold runs;
only complete source and uv hits are warm. Setup budgets are 360/180 seconds
(smoke cold/warm) and 900/420 seconds (full cold/warm); full test runtime is
limited to 600 seconds. Job limits are 10 minutes for smoke and 25 minutes
for full.

The source cache key includes the pinned Odoo commit. The uv key is computed
from the exact bytes of the pinned Odoo `requirements.txt` followed by the
repository `uv.lock`; the same helper supplies the restore, save, and bootstrap
keys.

Failure packaging sanitizes and caps each text file at 2 MiB and the
compressed failure bundle at 50 MiB. Successful evidence is capped at 2 MiB;
all uploaded evidence is retained for seven days. A secret-canary match or
size overflow fails packaging closed. Set `ODCLI_E2E_KEEP_FAILED=1` only when
debugging locally; fixture cleanup still removes live processes, containers,
networks, ports, databases, volumes, and catalogs, retaining sanitized files
only.

Troubleshooting starts with `.artifacts/real-odoo-e2e/bootstrap.json`. Verify
the recorded platform, runner image metadata, exact image digests, cache
class, and missing prerequisite list before inspecting bounded logs.
