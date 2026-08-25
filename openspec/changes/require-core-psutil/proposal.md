## Why

Runtime identity is a safety boundary: a PID may be reused, so a catalog row is
valid only when its recorded creation time exactly matches the live process.
Making `psutil` optional forced an imprecise `time.time()` fallback which made
valid foreground runtimes appear stopped.

## What Changes

- **BREAKING** Make `psutil` a mandatory core runtime dependency and remove the
  obsolete `metrics` extra.
- Persist and reconcile environment foreground runtimes with the single exact
  `psutil.Process(pid).create_time()` identity primitive.
- Ensure an unexpected foreground wait failure reaps the owned process group
  before runtime cleanup and re-raise.
- Harden all internal `psql` invocations against ambient libpq/psql startup
  configuration while retaining default `.pgpass` authentication.
- Define the monitor catalog read as one transactionally consistent aggregate
  snapshot using two SELECTs.
- Remove the former public `MonitorExtrasMissingError` compatibility contract:
  `psutil` is now core, so a missing-install hint is no longer a supported SDK
  outcome.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `packaging`: make exact process identity support part of every installation.
- `server-lifecycle`: require exact persisted foreground process identity and
  owned-process cleanup on exceptional wait failure.
- `environment-monitor`: require the same exact identity provider and atomic
  environment/runtime aggregate read.
- `development-environment`: expose the catalog's atomic environment/runtime
  aggregate read rather than two independently observed lists.
- `database-restore-tracking`: require hermetic `psql` startup inputs without
  disabling normal `.pgpass` authentication.

## Impact

`pyproject.toml`, `uv.lock`, foreground process management, monitor collection,
the shared PostgreSQL CLI transport, tests, and the five capability contracts.
