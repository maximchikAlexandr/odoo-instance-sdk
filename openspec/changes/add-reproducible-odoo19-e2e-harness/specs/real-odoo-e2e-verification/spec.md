## ADDED Requirements

### Requirement: Pinned hybrid full-E2E topology

The full real-Odoo tier SHALL run on Linux amd64 with Odoo Community 19 source commit `cd992ceebbaf343c03e1941d39cfe423d35ba6c6`, CPython `3.12.13`, `uv` `0.10.8`, and PostgreSQL image `docker.io/library/postgres@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`. Docker Compose SHALL provision the disposable PostgreSQL and reference source-server dependencies. OdCLI SHALL own target project initialization, source checkout, explicit `--create-venv` environment creation, generated configuration, target Odoo process lifecycle, database operations, and environment removal. The full tier MUST NOT substitute an Odoo container for the target checkout, Python environment, or target process.

#### Scenario: Full tier exercises product-owned target runtime
- **WHEN** the scheduled or manual full-E2E job runs
- **THEN** the target Odoo executable and Python interpreter SHALL come from the pinned OdCLI-managed source worktree and owned `uv` environment
- **THEN** only the reference source-server and PostgreSQL dependencies SHALL run as Compose services

#### Scenario: Pinned input changes are explicit
- **WHEN** an Odoo, PostgreSQL, Python, `uv`, or GitHub Action pin changes
- **THEN** the change SHALL update the pin manifest, expected cache keys, POC evidence, and reviewable dependency diff in one commit

### Requirement: Deterministic source fixture and genuine backup

The harness SHALL install a dedicated addon named `odcli_e2e_probe` that depends only on `base`. Pinned XML data SHALL create a uniquely identifiable model record and a binary `ir.attachment` whose bytes are stored in the Odoo filestore. Every full run SHALL generate a fresh ZIP through Odoo's supported database-manager backup endpoint with filestore enabled; neither Git nor a cache SHALL contain a mutable golden backup.

#### Scenario: Backup contains database and filestore
- **WHEN** the source fixture database is backed up
- **THEN** the ZIP SHALL contain a PostgreSQL dump and at least one non-directory `filestore/` member
- **THEN** the recorded backup identity SHALL include its run-specific size and SHA-256

#### Scenario: Fixture remains lightweight
- **WHEN** the probe addon is installed on a new Odoo 19 Community database
- **THEN** its manifest SHALL declare exactly `base` as its dependency
- **THEN** no Enterprise or business-stack addon SHALL be installed by the fixture

### Requirement: Owned, collision-free orchestration

Every run SHALL derive a DNS-safe Compose project name, database names, container names, volume names, worktree/environment names, artifact directory, XDG roots, and OdCLI catalog from a cryptographically random run id. Ports SHALL bind only to loopback, be reserved until immediately before launch, and be verified from the running service. Session-scoped fixtures SHALL own pinned source checkout/cache inputs and the reference source-server; function-scoped fixtures SHALL own target databases, filestore, OdCLI project/environment state, and failure injection. The full tier SHALL be serial inside one job, while independent jobs and local runs SHALL remain collision-free.

#### Scenario: Concurrent runs do not share mutable state
- **WHEN** two jobs run on the same host
- **THEN** they SHALL have disjoint Compose names, ports, databases, volumes, filestore paths, XDG roots, and OdCLI catalogs
- **THEN** neither run SHALL discover or delete the other's resources

#### Scenario: Readiness is semantic
- **WHEN** PostgreSQL or Odoo exposes a host port before it is usable
- **THEN** PostgreSQL readiness SHALL require `pg_isready` success and Odoo readiness SHALL require a successful bounded `/web/health` request
- **THEN** a port-open result alone SHALL NOT advance the fixture

### Requirement: End-to-end public critical path

One serial critical-path test SHALL use the public CLI or public SDK for project initialization, valid `.odcli/project.toml` inspection, Odoo 19 source checkout, owned `uv` environment creation/synchronization, PostgreSQL status/up/stop, target Odoo start/readiness/stop, probe addon discovery/install/update/test, remote backup download, database and filestore restore, restored-data verification, environment list/path/sync/remove, resource list/doctor, database list/diagnostics, backup list/show/validate, dependency verification, and top-level doctor. The critical path SHALL invoke `env checkout --create-venv` because the existing development-environment contract makes that explicit flag the sole product path that creates an owned `uv` environment.

#### Scenario: Restored state is verified through Odoo
- **WHEN** OdCLI completes the remote download and restore
- **THEN** the target Odoo process SHALL reach `/web/health`
- **THEN** XML-RPC SHALL return the pinned probe record and the original attachment bytes from the restored database
- **THEN** SQL-only or filesystem-only evidence SHALL NOT satisfy the scenario

#### Scenario: Repeated public workflow is idempotent
- **WHEN** safe list, status, doctor, module update, environment sync, stop, and cleanup operations are repeated as defined by their public contracts
- **THEN** the second invocation SHALL preserve the expected state and SHALL NOT create duplicate environments, databases, catalog rows, processes, or containers

### Requirement: Canonical CLI traceability

`PUBLIC_LEAF_CASES` SHALL remain the sole public CLI leaf inventory. The test-only `PublicLeafCase` record SHALL carry an E2E disposition of `critical`, `focused`, `smoke`, or `not-applicable` with a non-empty rationale, and collection-time assertions SHALL require exactly one disposition for every inventory row. E2E parameterization and the published matrix SHALL be generated from that inventory; no second list of leaf paths SHALL be introduced.

#### Scenario: New CLI leaf cannot escape classification
- **WHEN** a public leaf is added to Click registration and `PUBLIC_LEAF_CASES`
- **THEN** test collection SHALL fail until that same inventory row receives an E2E disposition and rationale

#### Scenario: Matrix is generated from the canonical inventory
- **WHEN** the traceability document is checked
- **THEN** its leaf paths, classes, dry-run requirements, E2E dispositions, and evidence identifiers SHALL equal the generated projection of `PUBLIC_LEAF_CASES`

### Requirement: Focused failures, redaction, and recovery

Focused integration cases SHALL cover an incorrect source master password, unavailable source-server, truncated ZIP, ZIP whose embedded database identity is incompatible, restore into an occupied name, repeated restore, and failure after partial database or filestore publication. Each failure SHALL return non-zero machine-readable output with a stable public error code and sanitized bounded diagnostics. Secrets SHALL be file-backed or project-dotenv-backed with owner-only permissions and SHALL NOT appear in argv, logs, pytest output, artifacts, fingerprints, exception graphs, or generated manifests.

#### Scenario: Remote authentication fails safely
- **WHEN** the remote master password is incorrect
- **THEN** OdCLI SHALL exit non-zero without publishing an available backup or target database
- **THEN** neither the supplied value nor a reversible derivative SHALL appear in captured evidence

#### Scenario: Truncated archive is rejected before publication
- **WHEN** the source response is truncated or the ZIP identity changes before restore
- **THEN** validation SHALL fail before a restored database or filestore is published
- **THEN** partial download, catalog, database, and filestore state SHALL be removed or reported as an explicitly owned retained resource

### Requirement: Fail-safe cleanup and leak audit

Fixture finalizers SHALL execute after success, assertion failure, timeout, and controlled SIGINT. Cleanup SHALL stop OdCLI-owned process groups through existing lifecycle APIs, remove only run-owned target environments/databases/filestore/catalog data, run Compose down with volumes and orphans for the exact project name, and remove run-owned worktrees and XDG roots. A final leak audit SHALL query process metadata, Docker labels/names, volumes, networks, ports, databases, filestore, worktrees, and catalog records by run id and SHALL fail the test if any unretained resource remains.

#### Scenario: Interrupted test leaves no owned resource
- **WHEN** the target Odoo step receives SIGINT or a bounded step times out
- **THEN** the process exits with the public interruption semantics and fixture finalizers SHALL run
- **THEN** the leak audit SHALL find no run-owned process, container, volume, network, port listener, database, filestore, worktree, or catalog row

#### Scenario: Debug retention is explicit
- **WHEN** `ODCLI_E2E_KEEP_FAILED=1` is set for a failed local run
- **THEN** only the sanitized run-owned artifact directory SHALL be retained
- **THEN** live processes, containers, networks, ports, databases, and volumes SHALL still be removed

### Requirement: CI tiers, budgets, and cache policy

The required PR smoke job SHALL use the pinned Odoo image `docker.io/library/odoo@sha256:a627eda6b4154eead21c4fca55f84f1671d870ca111aa57f93ca305861bc4613` as a container-only target to validate orchestration, genuine download/restore, redaction, and cleanup without claiming source-checkout coverage. The scheduled/manual full job SHALL run the hybrid target-source critical path. PR smoke SHALL have cold/warm setup budgets of 360/180 seconds and a 10-minute job timeout. Full E2E SHALL have cold/warm setup budgets of 900/420 seconds, a 600-second test-runtime budget after setup, and a 25-minute job timeout. Each job SHALL emit measured phase durations and compare them to the applicable budget.

Odoo source and `uv` downloads/wheels SHALL use content-addressed caches keyed by Odoo commit, OS, architecture, CPython version, `uv` version, and the SHA-256 of Odoo requirements plus repository lock inputs. Databases, filestore, generated configs, secrets, catalogs, and backups SHALL never be cached. Cache misses SHALL be valid cold runs; cache hits SHALL be reported as warm runs.

#### Scenario: Required E2E cannot pass by skipping
- **WHEN** Docker, Compose, a pinned image, source revision, Python, or `uv` prerequisite is missing in a required CI job
- **THEN** the job SHALL fail before pytest with a machine-readable prerequisite manifest
- **THEN** a pytest skip SHALL NOT be reported as green E2E

#### Scenario: Budget classification is measurable
- **WHEN** a CI run completes setup and the critical path
- **THEN** JUnit properties and the resource manifest SHALL record cache hit/miss, setup seconds, test seconds, artifact bytes, architecture, and every resolved pin
- **THEN** exceeding an applicable budget SHALL fail the job

### Requirement: Bounded failure evidence

On failure, CI SHALL upload sanitized Odoo, PostgreSQL, and Compose log tails; JUnit; the pin/cache manifest; the generated CLI traceability projection; and a minimal run-owned resource manifest. Each text log SHALL be capped at 2 MiB, the compressed bundle SHALL be capped at 50 MiB, and retention SHALL be 7 days. Successful jobs SHALL upload only JUnit, phase measurements, and the pin/resource summary, capped at 2 MiB. GitHub Actions SHALL be referenced by immutable commit SHA.

#### Scenario: Failure artifact is useful and secret-free
- **WHEN** a full-E2E assertion fails
- **THEN** the bounded artifact SHALL identify the failed phase and owned resources without containing secret values or unbounded logs
- **THEN** artifact-size overflow or redaction-sentinel detection SHALL fail evidence packaging

### Requirement: Supported architectures and local entry point

Required CI SHALL run on Linux amd64. The local harness SHALL support Linux amd64 and Docker Desktop arm64 by resolving the pinned multi-architecture image indexes and recording the selected platform digest. The sole documented local full command SHALL be `uv run pytest -o addopts='' -m 'real_odoo and e2e_full' tests/integration/real_odoo`, preceded by the pinned prerequisite bootstrap command; the smoke command SHALL select `real_odoo and e2e_smoke`.

#### Scenario: Unsupported platform fails explicitly
- **WHEN** the host architecture is neither amd64 nor arm64 or the pinned index has no matching manifest
- **THEN** prerequisite validation SHALL fail with the normalized architecture and pin manifest before provisioning any resource
