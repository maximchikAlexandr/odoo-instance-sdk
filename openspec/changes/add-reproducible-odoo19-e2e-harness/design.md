## Context

The repository already has offline characterization tests, Docker-backed PostgreSQL integration tests, centralized process execution, generated project/runtime configuration, and opt-in `real_odoo` tests. The latter require externally prepared Odoo, Python, config, database, and source-server state, so CI can skip them and no single scenario proves the public workflow. `tests/unit/test_cli_output_modes.py::PUBLIC_LEAF_CASES` is the authoritative CLI inventory and must remain the only leaf registry.

The local POC in `poc/run_probe.py` used the pinned multi-arch Odoo and PostgreSQL indexes on arm64 Docker Desktop. Two consecutive successful runs restored both data and an attachment through OdCLI in 28.194 and 27.871 seconds and left no matching containers, volumes, networks, runtime directories, or XDG state. `poc-evidence.md` records the evidence and discovered readiness/configuration constraints.

## Goals / Non-Goals

**Goals:**

- Make a required, self-provisioning real-Odoo verification path whose target checkout, Python environment, configuration, process lifecycle, and commands are owned by OdCLI.
- Exercise one coherent public critical path and focused failure cases without mechanically multiplying every option combination.
- Generate deterministic fixture state and a fresh genuine database-plus-filestore ZIP from pinned inputs.
- Preserve strict ownership, redaction, bounded evidence, parallel-run isolation, and complete cleanup on all exits.
- Split a fast image-backed PR smoke from a pinned source-backed scheduled/manual full tier with measurable cold/warm budgets.

**Non-Goals:**

- Enterprise repositories, private credentials, business addons, browser acceptance, or Odoo majors other than 19.
- A new production process runner, container abstraction, public CLI leaf, SDK type, or runtime dependency.
- Replacing offline/unit/characterization tests or publishing a custom production Odoo image.
- Caching databases, filestore, mutable backups, catalogs, generated configs, or secrets.

## Decisions

### 1. Use pytest plus Docker Compose, with no new orchestration dependency

The test harness will call the existing Compose lifecycle/process boundary and public OdCLI entry points. A small fixture layer owns readiness, logs, namespaces, and cleanup. Docker SDK and Testcontainers both add a second lifecycle abstraction and dependency without improving this repository's existing command snapshots, ownership labels, or cleanup invariants. The comparison and stop conditions are in `adr.md`.

Session scope owns immutable/download-heavy inputs: the pinned Odoo bare source cache, reference PostgreSQL, source Odoo server, and generated source backup. Function scope owns the target checkout/environment, target database/filestore, failure injection, and catalog/XDG roots. Full E2E is serial within one job; unique run IDs keep separate jobs safe.

### 2. Use hybrid topology for full E2E and container-only topology only for PR smoke

The full job uses Compose for disposable dependencies but requires OdCLI to checkout Odoo source at `cd992ceebbaf343c03e1941d39cfe423d35ba6c6`, run `env checkout --create-venv` with CPython 3.12.13, sync with `uv` 0.10.8, and own target process lifecycle. This is the only option that verifies the promised checkout/uv/config/process path while keeping the reference backup producer reproducible.

The PR smoke reuses the POC shape and may run an image-backed target, but its name, evidence, and matrix disposition explicitly exclude source-checkout coverage. Source-only orchestration is rejected because it makes the reference server and PostgreSQL host-dependent and weakens cleanup/reproducibility.

### 3. Generate source data and backup every run

`odcli_e2e_probe` contains one model record and one binary attachment and depends only on `base`. The source server installs it from XML, then Odoo's database-manager endpoint creates a ZIP with filestore. The archive is validated and identified by per-run size/SHA-256. It is not expected to be byte-identical because dump/archive metadata changes; determinism is defined by pinned inputs and verified semantic postconditions. A versioned golden backup is rejected because it would hide fixture drift and create a binary migration burden.

The target cluster first contains one initialized `base` sentinel database. This is required by the current restore preflight, which treats an empty database-manager result as unavailable. The sentinel is namespaced and deleted with the target cluster; it is never the restore target.

### 4. Make readiness and cleanup state-based

PostgreSQL advances only after `pg_isready`. Odoo advances only after `/web/health` succeeds for the intended database; a listening socket alone is insufficient. Timeouts are: PostgreSQL 90 seconds, reference Odoo 180 seconds, target Odoo 180 seconds, source checkout/uv sync 600 seconds, backup/restore 300 seconds, and cleanup 60 seconds.

Each resource receives a random run-id name/label and is recorded immediately after creation. Finalizers unwind the ledger in reverse order. OdCLI-owned process groups stop through existing lifecycle APIs; Compose runs `down --volumes --remove-orphans` for the exact project; worktrees, databases, filestore, XDG roots, and catalog rows are removed only after ownership checks. A final audit fails on any remaining run-id match. `ODCLI_E2E_KEEP_FAILED=1` retains sanitized files only, never live resources.

### 5. Extend the canonical CLI inventory with verification disposition

`PublicLeafCase` gains test-only E2E disposition/rationale/evidence fields. The harness filters and parameterizes that same tuple, and a generated Markdown projection is compared with `command-matrix.md`. This preserves one registry while making every current and future leaf explicitly critical, focused, smoke, or not applicable. No duplicated tuple/set of paths is permitted.

The critical scenario emphasizes state transitions; focused cases cover failure semantics and idempotency. Commands whose transports are interactive, streaming, or unrelated to this fixture receive explicit rationale rather than forced execution.

### 6. Pin inputs and use content-addressed caches only

`ci-design.md` is the authoritative pin and cache contract. Odoo source, image indexes, PostgreSQL image, Python, `uv`, and Actions are exact. Cache keys include OS/architecture and content identity. The runner label is `ubuntu-24.04`; its emitted GitHub image version is recorded as evidence because hosted runner filesystem images cannot be selected by OCI digest.

The first full job on a cache miss is cold; a cache-hit job is warm. Phase metrics are emitted into JUnit and a small JSON manifest and compared to the spec budgets. Backups remain uncached in v1; if full backup generation alone later exceeds 180 seconds at p95, changing that policy requires a separate planning revision.

### 7. Bound and redact failure evidence before upload

The harness captures only bounded tails and structured manifests. Existing sanitizers process argv, errors, logs, and plans; a generated random canary secret is asserted absent from every artifact before upload. Text files cap at 2 MiB, failure bundles at 50 MiB compressed, and successful evidence at 2 MiB. Missing prerequisites fail in bootstrap rather than becoming pytest skips.

## Risks / Trade-offs

- **[Pinned Odoo source becomes incompatible with upstream package indexes]** → `uv` resolves only from the pinned requirements and recorded lock inputs; dependency-pin updates require a reviewed pin/evidence revision.
- **[Hosted runner image remains mutable]** → pin `ubuntu-24.04`, record `ImageOS`/`ImageVersion`, pin all fetchable actions and artifacts, and fail unsupported architecture/tool versions before provisioning.
- **[Full source checkout is too slow for PRs]** → keep it scheduled/manual and enforce 900-second cold / 420-second warm setup budgets; PR uses the explicitly weaker smoke tier.
- **[Cleanup hides the primary failure]** → preserve the primary exception and report cleanup/leak-audit failures as distinct JUnit properties and bounded diagnostics.
- **[Port allocation races]** → hold loopback reservation sockets until the instant before Compose/Odoo launch, then verify the actual binding and ownership label before use.
- **[Backup bytes vary across identical semantic inputs]** → verify semantic database/attachment content and record each archive SHA rather than asserting a golden SHA.
- **[Inventory metadata creates coupling in a unit-test module]** → keep the metadata test-only and co-located with the already canonical registry; do not expose it through production imports.

## Migration Plan

1. Add canonical inventory dispositions and generation checks without enabling a CI job.
2. Add pinned Compose/source fixtures, technical addon, cleanup ledger, and prerequisite bootstrap.
3. Replace prerequisite-only real-Odoo cases with the critical and focused self-provisioning suites while retaining the `real_odoo` opt-in marker locally.
4. Add required PR smoke and scheduled/manual full workflows with immutable Actions, budgets, and evidence upload.
5. Run one cache-miss and one cache-hit full job, verify the leak audit and budgets, then make smoke required. Rollback disables the new workflow jobs and removes the new test fixtures; no product data or API migration is involved.

## Open Questions

None. Any change to topology, pins, cacheable state, product command coverage, or direct contract dependencies requires a new planning revision.
