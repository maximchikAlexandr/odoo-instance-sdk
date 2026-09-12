## 1. Canonical contracts and inventory

- [ ] 1.1 [T01] Extend the existing test-only `PublicLeafCase` rows with one E2E disposition, rationale, and evidence identifier without creating another leaf-path registry.
- [ ] 1.2 [T02] Add collection-time assertions and a deterministic generator/check that project all 50 rows of `PUBLIC_LEAF_CASES` into the reviewed command matrix, emit canonical-inventory base `af9e1b3e8d127145b9488f11ec79519f9442db46` separately from original full-change audit base `0ff164636617c03a51277055af45cef009277368`, require byte-for-byte document equality, and fail on missing or stale classifications or provenance.
- [ ] 1.3 [T03] Add the `e2e_smoke` and `e2e_full` pytest markers, immutable pin manifest including lock/audit SHA-256 and the exact scanner distribution pin `pip-audit==2.10.1`, phase-budget constants, and supported-platform validation.
- [ ] 1.4 [T04] Add unit checks for every pin including malformed audit SHA-256 and any scanner value other than `pip-audit==2.10.1`, exact matrix generation, budget classification, and fail-closed prerequisite reporting.

## 2. Disposable source and target foundation

- [ ] 2.1 [T05] Add the pinned Compose topology for reference PostgreSQL/source Odoo and target PostgreSQL, using run-id names/labels and loopback-only reserved ports.
- [ ] 2.2 [T06] Add session/function fixtures with Docker-visible runtime roots, isolated XDG/catalog state, owner-only file-backed secrets, a reverse-order resource ledger, and bounded readiness probes.
- [ ] 2.3 [T07] Add the `base`-only `odcli_e2e_probe` addon with deterministic XML model data, a binary filestore attachment, access rules, and a minimal native Odoo test.
- [ ] 2.4 [T08] Add source database and target sentinel initialization, genuine database-manager ZIP generation, archive identity/filestore validation, and fresh-per-run semantics.
- [ ] 2.5 [T09] Add exact cleanup and leak-audit helpers for processes, containers, networks, volumes, ports, databases, filestore, worktrees, XDG roots, catalogs, and debug-file retention.

## 3. Full critical path

- [ ] 3.1 [T10] Initialize a disposable Git target through public `odcli init`, assert the generated manifest, approve/start/status the pinned PostgreSQL image, and verify project ownership.
- [ ] 3.2 [T11] Add paired hash-lock fields to `EnvironmentCheckoutOptions` and paired keywords to the `EnvironmentResource`/`OdooClient.environments` sync and command signatures; expose `--hash-lock PATH --hash-lock-sha256 SHA256` on owned checkout/sync, use one neutral immutable argv builder and `uv pip sync --require-hashes`, verify validation/dry-run/legacy defaults, exercise checkout and repeated `env sync` with the reviewed lock, prove trusted first-install ordering, and reject any discovery monkeypatch or direct test-owned package-install substitute.
- [ ] 3.3 [T12] Start target Odoo through the public lifecycle, require bounded HTTP readiness, discover/install/update the probe addon, and execute its native test through top-level `test`.
- [ ] 3.4 [T13] Download and restore the source ZIP through public `db refresh --restore`, validate catalog identity, and verify the restored record and attachment bytes through running target Odoo.
- [ ] 3.5 [T14] Exercise the remaining critical list/path/status/doctor/resource/backup/database/eval lifecycle observations, repeat idempotent operations, then stop and remove only owned resources.

## 4. Focused failure and recovery cases

- [ ] 4.1 [T15] Add incorrect-master-password and unreachable-source cases with non-zero machine output, unpublished-backup assertions, and secret-canary scans.
- [ ] 4.2 [T16] Add truncated/incompatible archive, occupied database name, exact catalog restore, and repeated-restore cases with database/filestore publication assertions.
- [ ] 4.3 [T17] Add reset-password, exec, module-test alias, backup deletion, database drop, diagnostics/monitoring, psql, shell, and bounded logs cases identified by the canonical matrix.
- [ ] 4.4 [T18] Add controlled SIGINT, step timeout, and injected partial-publication failures that preserve the primary error and report cleanup failures separately.
- [ ] 4.5 [T19] Assert zero-leak finalization after every focused failure and validate `ODCLI_E2E_KEEP_FAILED=1` retains only bounded sanitized files.

## 5. CI and operator evidence

- [ ] 5.1 [T20] Add the required PR smoke job with pinned Actions/images, fail-closed bootstrap, 10-minute timeout, cold/warm measurements, and success/failure evidence limits.
- [ ] 5.2 [T21] Add the scheduled/manual Linux-amd64 full job with pinned Odoo/Python/uv inputs and exact scanner distribution pin `pip-audit==2.10.1`, 25-minute timeout, content-addressed source/uv caches, no mutable-state caches, and a live audit gate requiring exact canonical equality between scanner results and non-expired reviewed exceptions.
- [ ] 5.3 [T22] Add bounded log/resource/pin/timing/JUnit packaging, 7-day failure retention, 2/50 MiB gates, and a pre-upload secret-canary scan.
- [ ] 5.4 [T23] Document the single local smoke/full bootstrap and pytest commands, supported arm64/amd64 behavior, debug retention, cache policy, budgets, and troubleshooting.

## 6. Verification and handoff evidence

- [ ] 6.1 [T24] Run formatting, lint, type, unit, inventory-generation, and strict OpenSpec checks without weakening existing suites or thresholds.
- [ ] 6.2 [T25] Run smoke twice and full once as cache-miss plus once as cache-hit, record phase/bundle measurements, and require all post-run leak audits to be empty.
- [ ] 6.3 [T26] Verify every `PUBLIC_LEAF_CASES` row maps to exactly one disposition/evidence entry and every spec scenario has executable evidence; audit the complete diff from original planning base `0ff164636617c03a51277055af45cef009277368`, allowing only the graph-revision-3 public hash-lock behavior in `resources/environment.py`, `commands/env.py`, and neutral `internal/dependency_sync.py` while rejecting any other production runner/API, Enterprise input, mutable backup, or hidden prerequisite; rebase the feature branch on fetched current `origin/main`, assess applicable upstream public behavior, run all local gates, publish with `--force-with-lease`, require terminal exact-head PR and Linux-amd64 full CI evidence, and record the repository-wide OpenSpec sync/archive audit plus strict validation.
