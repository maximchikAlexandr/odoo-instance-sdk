# CI design

## Immutable inputs

| Input | Pin |
| --- | --- |
| Odoo 19 Community source | `cd992ceebbaf343c03e1941d39cfe423d35ba6c6` |
| Odoo official multi-arch image | `docker.io/library/odoo@sha256:a627eda6b4154eead21c4fca55f84f1671d870ca111aa57f93ca305861bc4613` |
| Odoo linux/amd64 image manifest | `sha256:515f8d24be9fed0b00804211c2bed4e50b1c7405a2b7e18c12e81b2292a84c88` |
| Odoo linux/arm64 image manifest | `sha256:78da2811af8e18a453f341260406ae9c872c793081ac674681b09b7a7018d3a2` |
| PostgreSQL 16 official multi-arch image | `docker.io/library/postgres@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685` |
| PostgreSQL linux/amd64 image manifest | `sha256:075f7ba66bc9b3ce7d6b8b635208ff61cd7cf1a67d71ec530eec5d7ae0cbe571` |
| PostgreSQL linux/arm64 image manifest | `sha256:738d1359df5aa0b6d50a9071e989c49fdd39152a2a805c6ff131bf5e2243e0b3` |
| CPython | `3.12.13` |
| uv | `0.10.8` |
| Odoo Python hash lock SHA-256 | `409063537bb93edb085304ac427effd7044a654393b5f9ff719fa105cd8c89d4` |
| Odoo Python audit report SHA-256 | `70ce80ce32dd490e1c52b3d43a092fc735209ba538805942cba1de30caea2284` |
| Python vulnerability scanner | `pip-audit 2.10.1` |
| `actions/checkout` | `11d5960a326750d5838078e36cf38b85af677262` |
| `astral-sh/setup-uv` | `d0cc045d04ccac9d8b7881df0226f9e82c39688e` |
| `actions/upload-artifact` | `ea165f8d65b6e75b540449e92b4886f43607fa02` |
| GitHub runner | `ubuntu-24.04`, Linux amd64; record `ImageOS` and `ImageVersion` in every run |

The pin manifest is machine-readable and bootstrap verifies resolved Git/image identities, both Python-resolution digests, and scanner identity before creating resources. It runs the pinned scanner against the exact hash lock, canonicalizes unique `(package, version, advisory ID)` tuples, and requires exact equality with the non-expired exception manifest. A missing/additional/version-mismatched advisory, malformed or expired exception, or pin mismatch is a hard failure.

## Jobs

### Required PR smoke

- Trigger: pull requests to `main`.
- Timeout: 10 minutes.
- Topology: official Odoo image for source and target plus disposable PostgreSQL; this is explicitly container-only.
- Selection: `uv run pytest -o addopts='' -m 'real_odoo and e2e_smoke' tests/integration/real_odoo`.
- Proves: pinned images, source addon/data, genuine ZIP download, database+filestore restore through OdCLI, machine JSON, secret-canary redaction, and zero-leak cleanup.
- Does not prove: Odoo Git checkout, owned `uv` environment, or OdCLI target process lifecycle.

### Scheduled/manual full E2E

- Trigger: daily schedule and `workflow_dispatch`; it is not required on every PR.
- Timeout: 25 minutes.
- Topology: Compose reference source/PostgreSQL plus pinned Odoo Git target managed by OdCLI.
- Selection: `uv run pytest -o addopts='' -m 'real_odoo and e2e_full' tests/integration/real_odoo`.
- Proves: all E2E-CP, E2E-FC, E2E-REC, and E2E-SEC evidence in `command-matrix.md`.
- Architecture: required Linux amd64; local arm64 is supported for reproduction but not a release gate.

## Phase budgets

| Tier | Cache class | Setup | Test after setup | Job | Success artifacts |
| --- | --- | ---: | ---: | ---: | ---: |
| PR smoke | cold | 360 s | 180 s | 600 s | 2 MiB |
| PR smoke | warm | 180 s | 180 s | 600 s | 2 MiB |
| Full | cold | 900 s | 600 s | 1500 s | 2 MiB |
| Full | warm | 420 s | 600 s | 1500 s | 2 MiB |

Each phase uses monotonic timing. A cache miss classifies the run as cold; all required source and uv cache hits classify it as warm. Partial hits are cold. The manifest records setup/test/cleanup seconds and compressed artifact bytes, and pytest writes the same values as JUnit properties. Budget excess fails after cleanup and evidence capture.

The local arm64 POC warm baseline is 27.871 seconds for the image-backed backup slice. It is evidence that smoke fits its warm budget, not a substitute for the first Linux CI cold/warm measurements.

## Cache contract

- Odoo bare source cache key: `odoo19-<os>-<arch>-cd992ceebbaf343c03e1941d39cfe423d35ba6c6`.
- uv cache key: `uv-<os>-<arch>-3.12.13-0.10.8-409063537bb93edb085304ac427effd7044a654393b5f9ff719fa105cd8c89d4`; changing the reviewed hash lock or audit contract invalidates the cache.
- Restore keys use the same prefixes without dropping commit, version, architecture, or input hash components.
- Checked-out target worktrees are recreated and verified at the pinned commit; they are not cached as mutable worktrees.
- Databases, volumes, filestore, backups, catalogs, XDG roots, configs, ports, logs, and secret files are never cache inputs or outputs.
- Backup caching is rejected for v1. A change requires a planning revision and p95 evidence that backup generation alone exceeds 180 seconds.

## Failure and success evidence

Failure upload, after cleanup and canary scan:

- JUnit XML and phase measurements;
- resolved pin/cache manifest and CLI matrix projection;
- exact run-owned resource manifest and leak-audit result;
- sanitized 2 MiB tails for source/target Odoo, PostgreSQL, and Compose;
- sanitized public command plans/results without secret environment or stdin.

The compressed bundle is capped at 50 MiB and retained for 7 days. Any secret-canary match, oversize input, or oversize bundle fails packaging and uploads only the sanitized manifest/JUnit that identifies that packaging failure. On success only JUnit, timings, pins, and final empty resource summary are uploaded, capped at 2 MiB.

## Bootstrap and cleanup order

1. Verify Linux/amd64 (CI) or supported local arm64, Docker/Compose readiness, Git commit availability, image index/platform digests, Python 3.12.13, uv 0.10.8, and GitHub runner metadata.
2. Create run id, isolated Docker-visible test root, XDG roots, owner-only secret files, resource ledger, and reserved loopback ports.
3. Provision session source dependencies and generate/validate the semantic backup.
4. Provision the function target, execute the chosen scenario, and collect bounded evidence.
5. Always stop OdCLI process groups, remove target environment/database/filestore/catalog state, run exact Compose down, remove worktrees/XDG roots, and release reservations.
6. Audit processes, Docker objects, ports, databases, filestore, Git worktrees, catalog rows, and runtime directories by run id. Only then evaluate budgets and package evidence.

Missing prerequisites in either CI job exit non-zero from bootstrap; the CI commands never translate them to pytest skips.
