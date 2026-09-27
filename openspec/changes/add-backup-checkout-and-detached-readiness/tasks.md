## 1. Named source configuration and secret boundary

- [ ] 1.1 Add frozen named-source types plus deterministic `[remote_instances.NAME]` parse/write in `ProjectConfig`; parameterize normalized round-trip, name/URL/field validation, duplicate rejection and legacy `[test_instance]` compatibility.
- [ ] 1.2 Add public list/configure/remove source operations on the existing project-init surface using immutable commands, project locking, manifest fingerprint revalidation and the atomic manifest writer; verify idempotent add, explicit replace, partial CLI update preservation, concurrent drift and inert preview.
- [ ] 1.3 Extend typed SDK init and repeatable CLI init input with named entries; verify completeness, re-init preservation, no-input, dry-run and reporting of credential key names without writing secrets.
- [ ] 1.4 Extend project dotenv resolution, redaction and child-environment filtering for derived named-source password keys; verify canonical worktree root, process precedence, empty override failure and absence from argv/env/repr/fingerprints/output.
- [ ] 1.5 Remove configured-source origin approval from init, preparation and doctor; test absent/empty/mismatched/malformed legacy origin variables as no-ops while URL validation, TLS, cleartext warning, disabled redirects and legacy password compatibility remain.

## 2. Named preparation and diagnostics

- [ ] 2.1 Extend preparation options and immutable source snapshots with explicit remote name, normalized origin/database/declared branch and historical source name; verify unknown/omitted selection, no fallback, profile drift rejection and source-aware coalescing.
- [ ] 2.2 Reuse existing download/validation/restore/default-switch behavior for named `db refresh`; verify download-only and restore modes, retained failure artifacts, source-specific credentials and unchanged legacy calls.
- [ ] 2.3 Extend existing public diagnostics with the source selector; verify exact password-key presence, local ref and restore prerequisites, offline no-network behavior and truthful unverified authentication without backup creation.

## 3. Catalog provenance and explicit-source COPY

- [ ] 3.1 Add one linear Alembic successor to the current source-neutral restore-provenance head for nullable backup `source_name`, audited pin state and COPY journal ownership; update Core metadata/projections and verify upgrade from both prior revisions, fresh schema, conservative legacy defaults, single-head and schema-equivalence gates.
- [ ] 3.2 Add mutually exclusive `remote_name`/`backup_id` COPY options and immutable plan/result provenance; test incompatible modes/inputs, named default base, explicit compatible base, missing ref, stale profile and legacy unknown-branch handling.
- [ ] 3.3 Route named download and exact retained UUID through the existing COPY validation/restore/neutralization/journal pipeline, reusing the shared source-neutral archive evidence and verified-snapshot transport; verify no intermediate database/default switch, no source HTTP for retained UUID, no fallback, no duplicate extractor/verifier, separate writable filestore and postconditions.
- [ ] 3.4 Persist `owned` only for local-source archives and `borrowed` for named/retained input; verify rollback/removal preserves borrowed and migrated-unknown archives while existing owned cleanup remains idempotent.
- [ ] 3.5 Reuse checksum, archive, disk, cluster, target-collision, lifecycle-lock and Odoo-major checks; cover corruption, insufficient space, busy/replaced input, known mismatch, unknown version and actionable retained recovery evidence.

## 4. Environment-bound detached readiness

- [ ] 4.1 Extract the existing runtime/listener ownership proof into one shared internal helper used by auxiliary restore and detached readiness; retain exact PID/create-time/argv/cwd/config/socket checks, including the injected effective `--logfile` in expected argv, and fail closed on unavailable inspection.
- [ ] 4.2 Extend detached command/convenience APIs with opt-in readiness and finite positive timeout/default 60 seconds; represent wait and cleanup in the immutable plan and verify disabled compatibility, success, timeout, early exit, wrong binding/listener and inert preview.
- [ ] 4.3 Reuse owned process-group termination and identity-conditional catalog cleanup; verify confirmed cleanup, preserved logfile/tail/environment/database evidence, and retained runtime identity plus typed surviving-process error when cleanup cannot be proven.

## 5. Retention, pinning and protected deletion

- [ ] 5.1 Add typed `client.backups.retention()` and immutable retention update using the existing `user.toml`; verify defaults, types, malformed/unreadable policy, preservation of `max_uncompressed_bytes` and unrelated content, user-only atomic write and preview.
- [ ] 5.2 Add idempotent audited pin/unpin operations and direct-delete guards; verify pin, busy lock, non-removed environment, unresolved recovery and newest historical source-group protection with no force bypass.
- [ ] 5.3 Add project-scoped immutable prune planning with captured policy/cutoff/file identities/bytes and deterministic named or legacy groups; test age boundary, ties, newest protection, pins/references/busy files, unknown/unowned/external rows, caller-owned local archives without `Backup` rows and inert preview.
- [ ] 5.4 Execute only captured candidates through the existing deletion path and lifecycle lock; test changed policy/pin/reference/file/latest state, concurrent restore, partial failure, exact bytes, audit preservation and idempotent repeat.
- [ ] 5.5 Attach one captured sequential post-success prune phase to outermost project-aware backup, refresh, restore and checkout commands; test nested de-duplication, input/output UUID exclusions, failure/cancellation/dry-run skips and primary success with maintenance warning.

## 6. CLI, documentation and integrated evidence

- [ ] 6.1 Add thin bounded CLI adapters for remote list/add/update/remove, refresh/checkout/doctor selectors, detached wait/timeout and backup retention/pin/unpin/prune; update `PUBLIC_LEAF_CASES`, aliases/help, confirmations, dry-run and Rich/JSON/TOON/error contracts.
- [ ] 6.2 Document two-source setup and rotation, ignored origin variables, staging and retained-backup COPY, readiness limits, retention path/policy, protected archives, manual prune and existing stop/remove/recovery composition without introducing orchestration claims.
- [ ] 6.3 Run a two-source integration scenario with different credentials: init, select staging, COPY, ready launch, stop/remove, reuse retained UUID and prune; prove lab/project default isolation, exact provenance and absence of secrets in plans/results/logs/child environments.
- [ ] 6.4 Run focused SDK/CLI/migration/recovery suites, current real-Odoo contract coverage where available, Ruff check/format, strict mypy, repository architecture/leaf/schema gates, command-plan step-parity regressions, the repository mutation gate, `git diff --check` and strict OpenSpec validation; keep every implementation checkbox unchecked until behavior is verified.
