## 1. Named source configuration

- [ ] 1.1 Extend public project types and parser/writer with named entries; verify lab/staging round-trip, invalid names/URLs and legacy test-instance compatibility with parametrized tests.
- [ ] 1.2 Add public immutable add/update/remove commands using current manifest locking/writing; test identical no-op, conflicts, preservation of unrelated settings and dry-run.
- [ ] 1.3 Extend SDK init and CLI remote/init adapters; test repeatable inputs, completeness, re-init preservation, no-input and canonical output cases; document two-source setup.

## 2. Credential selection and remote preparation

- [ ] 2.1 Reuse dotenv resolution for source-specific passwords and trusted project URLs; test worktree root, process precedence, empty passwords and legacy password behavior.
- [ ] 2.2 Extend shared redaction and process environment filtering; verify no named remote secret reaches child argv/env, repr, fingerprints, output or redirected requests.
- [ ] 2.3 Add remote selection to public preparation and db refresh; test exact-source download, source-aware coalescing, stale profile rejection, local restore/default semantics and retained failures.
- [ ] 2.4 Extend existing public diagnostics and doctor selector; test missing variables/ref/runtime and truthful unverified authentication without download; document credential setup and rotation.
- [ ] 2.5 Remove existing origin-pin approval checks and treat ODCLI_TEST_INSTANCE_ORIGIN_PINS and proposed ODCLI_REMOTE_<NAME>_ORIGIN as ignored legacy variables; verify absent, empty, mismatched and malformed legacy values have no effect, init/doctor do not require them, URL/TLS/redirect safeguards remain, and documentation explains optional manual removal.

## 3. Explicit-source COPY checkout

- [ ] 3.1 Add mutually exclusive backup/remote options to public checkout; test incompatible inputs, no local source inference, base selection, missing ref and known/unknown branch compatibility.
- [ ] 3.2 Converge local, retained and named-remote inputs on existing COPY restore; test no intermediate database/default switch, no fallback, no source HTTP for retained UUID, isolated filestore and neutralization.
- [ ] 3.3 Add nullable source provenance and borrowed/owned journal state through additive Alembic migration; test old rows, retained backups on failure/removal, captured base commit and unchanged historical source identity.
- [ ] 3.4 Apply existing checksum/archive/disk/cluster checks and known Odoo major compatibility to explicit input; test corruption, insufficient space, busy backup, target collision and recovery output.
- [ ] 3.5 Expose CLI selectors through the public SDK; extend canonical output/dry-run cases and document staging and offline retained-backup examples.

## 4. Detached readiness

- [ ] 4.1 Extend detached command with opt-in readiness and positive timeout/default 60 seconds; test disabled compatibility, success, timeout, early exit and wrong listener/binding.
- [ ] 4.2 Reuse existing process cleanup and runtime identity; test successful cleanup and surviving-process evidence on cleanup failure.
- [ ] 4.3 Expose CLI wait-ready/timeout with existing environment selector; test invalid combinations, redaction and inert preview; document technical readiness limits.

## 5. User retention and safe pruning

- [ ] 5.1 Add typed public read/update for retention_days/auto_prune in existing user.toml; test defaults, invalid types/files, atomic preservation and dry-run; document platformdirs path discovery.
- [ ] 5.2 Add pin state/audit through Alembic and public pin/unpin operations; test migration, idempotence and direct deletion protection under lifecycle locks.
- [ ] 5.3 Add public project-scoped prune plan/command reusing catalog deletion; test age boundaries, newest-per-source protection, pins, active/recovery/environment references, external/unknown ownership and deterministic bytes/reasons.
- [ ] 5.4 Revalidate captured candidates under lifecycle locks; test changed policy, concurrent restore/pin/file replacement, partial deletion failure, idempotent repeat and preserved audit.
- [ ] 5.5 Attach one sequential post-success pass to project-aware backup/refresh/restore/checkout commands; test no duplicate nested pass, excluded input/output IDs, failure/cancellation/dry-run skips and primary success with cleanup warning.
- [ ] 5.6 Add retention/pin/unpin/prune CLI adapters to canonical leaf inventory; test confirmation, machine errors and dry-run; document protected archives and manual disk-recovery procedure.

## 6. Cross-cutting verification

- [ ] 6.1 Run an integration scenario with two differently authenticated remote fixtures: init, select staging, backup/COPY, ready launch, stop/remove, reuse retained backup and prune; assert lab and project default unchanged and no secrets in evidence.
- [ ] 6.2 Run focused SDK/CLI tests, required repository quality gates and strict OpenSpec validation; keep all implementation tasks unchecked until their behavior is verified.
