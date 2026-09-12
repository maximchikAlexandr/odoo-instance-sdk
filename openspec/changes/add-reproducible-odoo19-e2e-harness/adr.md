# ADR: Reproducible Odoo 19 E2E topology and orchestration

- Status: Accepted
- Date: 2026-09-11
- Decision scope: MYL-153 graph revision 2, based on `0c04dfcac56a4c0ccf34eb928bb6348f48348397`

## Decision drivers

The full tier must verify OdCLI's checkout, owned `uv` environment, generated configuration, centralized process execution, backup/restore, module/test, and cleanup paths. It must also be reproducible, multi-run isolated, diagnosable, and implementable without a second production runner or a wrapper dependency that duplicates existing Compose behavior.

## Orchestration comparison

| Option | Readiness / dynamic ports | Log capture / cleanup | Repository fit | Decision |
| --- | --- | --- | --- | --- |
| pytest + Docker Compose CLI | Explicit `pg_isready`, HTTP probes, reserved loopback ports; Compose project isolation | Native service logs and exact `down --volumes --remove-orphans`; reuses existing Compose/process boundaries | No dependency; matches current PostgreSQL integration architecture | **Selected** |
| Docker SDK for Python | Direct engine API and dynamic ports | Requires a new resource ledger, retry policy, log adapter, and cleanup implementation | Adds a second lifecycle abstraction and bypasses existing inspectable command plans | Rejected |
| testcontainers-python | Convenient wait strategies and disposable objects | Ryuk/wrapper behavior adds another cleanup authority and dependency; logs still need repository sanitization | Benefit is insufficient because Compose and cleanup invariants already exist | Rejected |

Reconsider the orchestration choice only if measured evidence shows Compose cannot provide a required readiness signal or leaves owned resources after the exact cleanup audit. Convenience alone is not a trigger.

## Runtime topology comparison

| Option | Product paths verified | Reproducibility / cost | Decision |
| --- | --- | --- | --- |
| Fully containerized Odoo target | Backup/restore and HTTP behavior, but bypasses target checkout, `uv`, generated runtime config, and process ownership | Fastest and highly reproducible | **PR smoke only**; cannot be the full tier |
| Hybrid: Compose dependencies + OdCLI-managed source target | Verifies every required target product path and keeps the reference source stable | Moderate source/venv cost, cacheable by content | **Selected for full E2E** |
| Source-only Odoo on runner + container PostgreSQL | Verifies target path but makes the source backup producer and its lifecycle host-specific | Highest host coupling and slow duplicate source environments | Rejected |

## Accepted contract

The reference source-server uses the pinned official Odoo index digest and a pinned `base`-only addon. The full target is the pinned Odoo Git commit and is created with public `odcli init`, `env checkout --create-venv`, environment synchronization, and lifecycle operations. PostgreSQL remains Compose-owned. The POC may use an image-backed target because it isolates the backup/restore/filestore risk; only the full tier can claim target-source coverage.

No golden backup is stored or cached. No new production dependency, runner, CLI leaf, or SDK type is introduced. Graph revision 2 adds paired hash-lock path/digest parameters to the existing checkout and synchronization operations. Both operations share one neutral dependency-sync argv builder and the existing centralized process boundary; hash-lock mode is limited to an owned environment and captures `uv pip sync --require-hashes` without discovery or compile/install fallback. Test orchestration remains outside production and calls this public boundary.

## Consequences

The required PR signal is intentionally narrower than full coverage. Scheduled/manual runs bear source checkout and dependency-sync cost, controlled by budgets and content-addressed caches. Pinned revisions require deliberate refreshes, but failures are attributable and replayable. Hash-lock mode deliberately uses `sync`, not `install`, and therefore rejects reused environments to avoid deleting unrelated packages. The technical addon and fixtures become test assets, not shipped package contents.
