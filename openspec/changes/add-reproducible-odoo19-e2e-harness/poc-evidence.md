# POC evidence

## Scope

`poc/run_probe.py` isolates the riskiest existing vertical slice: pinned source Odoo → supported database-manager ZIP → public `odcli db refresh --restore` → PostgreSQL data plus filestore publication → restarted target Odoo → XML-RPC verification → unconditional cleanup. The target uses the official image in this POC; the accepted full-tier design replaces it with the pinned OdCLI-managed source checkout and owned `uv` environment.

## Inputs and environment

- Repository base: `0ff164636617c03a51277055af45cef009277368`
- Host: macOS arm64, Europe/Minsk
- Docker Engine: `29.5.2`; Docker Compose: `5.3.1`
- Local runner: CPython `3.12.13`, `uv 0.11.1`
- Odoo index: `docker.io/library/odoo@sha256:a627eda6b4154eead21c4fca55f84f1671d870ca111aa57f93ca305861bc4613`; selected arm64 manifest `sha256:78da2811af8e18a453f341260406ae9c872c793081ac674681b09b7a7018d3a2`
- PostgreSQL index: `docker.io/library/postgres@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`; selected arm64 manifest `sha256:738d1359df5aa0b6d50a9071e989c49fdd39152a2a805c6ff131bf5e2243e0b3`
- Fixture addon: `odcli_e2e_probe`, dependency list exactly `["base"]`

Secrets were generated in process, written only to owner-readable runtime files, omitted from argv and output, and deleted with the workspace-visible temporary root.

## Successful runs

Command:

```text
uv run python openspec/changes/add-reproducible-odoo19-e2e-harness/poc/run_probe.py
```

| Run | Exit | Elapsed | ZIP SHA-256 | Filestore members | Restored marker |
| --- | ---: | ---: | --- | ---: | --- |
| POC-1 | 0 | 28.194 s | `8abec9855bf23ab24d81654f2a449db045e6631f479139128cd0923dae58fec8` | 22 | `ODCLI-E2E-RESTORED` |
| POC-2 (warm repeat) | 0 | 27.871 s | `6fb3da0a5cf8f8a893525107a9bf94bd4c35fc9bfa0eb25638917b4ea9e6844c` | 22 | `ODCLI-E2E-RESTORED` |

The different archive hashes are expected: Odoo dump/archive metadata is time-dependent. Reproducibility therefore means pinned inputs plus identical semantic postconditions, while every produced archive receives its own size/SHA identity.

## Cleanup evidence

After POC-1, each command returned exit 0 and empty output:

```text
docker ps -a --filter name=odcli-poc --format '{{.Names}}'
docker volume ls --filter name=odcli-poc --format '{{.Name}}'
docker network ls --filter name=odcli-poc --format '{{.Name}}'
find openspec/changes/add-reproducible-odoo19-e2e-harness/poc -maxdepth 1 -type d -name '.runtime-*' -print
```

POC-2 used a new run id and completed successfully, proving rerun isolation. The runner's `finally` removes exact container names, exact network, named PostgreSQL volumes, Docker-visible runtime roots, XDG state, catalogs, backup, target database, and filestore.

## Evidence-backed constraints

- A host port can accept connections before PostgreSQL is ready; the harness must gate on `pg_isready`.
- Odoo 19's executable uses the `server` command for server options.
- Docker Desktop could not see the runtime's system temp mount; Docker-mounted runtime files must live under a Docker-visible test root.
- An empty target cluster makes the current OdCLI restore preflight report the database manager unavailable; the harness must create one namespaced initialized `base` sentinel database before restore.
- `/web/health` is the readiness gate only after an initialized database is selectable; pre-restore target bootstrap uses process/TCP readiness followed by the actual database-manager call.
- Semantic fixture verification through XML-RPC caught both database and attachment content; ZIP membership alone is insufficient.

## POC limitation and resolution

The POC intentionally does not claim checkout/`uv`/target-process coverage because its target is image-backed. `adr.md` resolves this by limiting that topology to PR smoke and requiring the full tier to substitute the pinned source checkout and public OdCLI lifecycle while retaining the proven backup slice.
