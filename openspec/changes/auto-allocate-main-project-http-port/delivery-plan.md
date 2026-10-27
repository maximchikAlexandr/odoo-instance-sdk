## Planning baseline

- Task key: `MYL-582`; source requirement: GitHub issue `#189`.
- OpenSpec change: `auto-allocate-main-project-http-port`.
- Approved implementation base: `origin/main` at `f0918da2d36b59c35cf0a949efc58744d1efb7f9`.
- Test repository: use `TEST_REPOSITORY_URL` from the current Multica project description at execution time. Do not copy its private hostname, credentials, or personal data into public Git.
- Current-code evidence: `ProjectConfig` manifest round trip; `resolve_project_http_port()`; `find_free_port()` and its reservation/live-probe tests; `InstanceFactory.from_project()`; foreground CLI preflight; detached immutable planning/persistence/readiness; typed detached output; publication's captured-runtime endpoint; `offline-tests`, `compatibility`, and real-Odoo CI jobs.
- Planning performs no product test or E2E run.

## Estimate contract

- Exact estimate totals SHALL exist only in the planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; this file, comments, and visual artifacts SHALL NOT duplicate them.
- Basis: remaining work from the recorded base to all acceptance criteria for one experienced developer familiar with Python, Click, msgspec, and the repository, without AI acceleration. Active investigation, implementation, required tests, documentation, review repair, and attended verification are included; unattended CI/service waits and approval queues are excluded.
- Confidence: medium. The allocator, project factory, manifest/generated-config writers, runtime identity, typed output, publication target, focused tests, and CI ownership are directly inspectable and have close local analogues. The bounded uncertainty is concentrated in keeping two owned config files coherent around failures and preserving every foreground/detached output mode.
- Calibration: uncalibrated because no comparable completed-work timing series for the specified human executor was supplied. Tests were not run for estimation.
- Assumptions: the existing manifest/project locks remain the synchronization authority; `assigned_http_port` is additive and optional; publication continues to consume persisted runtime `base_url`; the numbered-slot infrastructure described by the project is available to the independent Tester.
- Estimate coverage: manifest/model compatibility, resolver/allocation coordination, generated-config reconciliation, foreground/detached integration, typed output, deterministic tests/fixtures, documentation, likely review repair, and attended local verification are included. New reservation services, external ingress deployment, task-environment changes, and auxiliary-restore redesign are excluded by scope.

## Delivery topology

The authoritative `Estimate, hours` property is at or below the mandatory topology threshold. Delivery therefore uses `delivery_mode=single_wp_no_dag`: exactly one all-covering work package, with no `depends_on`, no `stage`, no parallel frontier, and no DAG orchestration. Manager SHALL materialize no implementation child until this planning revision passes independent verification.

### WP-01 — Implement and verify automatic main-project HTTP port selection

- Task coverage: all OpenSpec tasks `1.1`–`4.2`, each covered exactly once.
- Independent deliverable: a backward-compatible main-project port contract that distinguishes explicit and automatic intent, persists/reuses safe automatic choices, keeps command/runtime/publication endpoints consistent, reports bounded provenance, and passes deterministic plus selected live verification.
- Owned responsibility scope: `ProjectConfig` and manifest serialization; shared project HTTP resolution/allocation; project lock and owned generated-config reconciliation; main-project foreground/detached CLI/runtime preparation; detached result and output projections; runtime/status/publication endpoint regressions; directly related tests, fixtures, snapshots, documentation, CI files, and the test-repository verification script. This is a responsibility boundary, not an exhaustive file allowlist.
- Contract surface: optional `assigned_http_port`; explicit-pin fail-closed semantics; automatic candidate/reuse/replacement rules; dry-run/read-only inertness; immutable command endpoint; captured runtime identity; additive safe output provenance; unchanged task-environment and auxiliary-restore behavior.
- Definition of done / evidence: every delta scenario maps to a focused passing check or `RUN-PORT`; applicable repository/CI gates and strict OpenSpec validation pass at the exact candidate revision; baseline/candidate/reuse text and PNGs plus commands, exit codes, assertions, SHAs, and MR/file links are attached/read back in Multica; cleanup removes the disposable slot while preserving the evidence MR/branch/commits.
- Parallel-safety rationale: not applicable. The estimate contract mandates one WP, and the model, allocation, generated config, command snapshot, output, and endpoint tests share one coupled contract/write zone.

## E2E decision

`e2e_required=true`.

Focused unit/component tests can prove manifest compatibility, locking, candidate ordering, mutation boundaries, output schemas, and publication's use of a captured endpoint. They cannot by themselves prove that a real occupied loopback port causes a main-project Odoo process to bind the selected fallback, become ready there, persist that endpoint, stop, and restart on the same assignment. One isolated numbered-slot lifecycle closes that cross-component gap. The changed detached terminal output also requires genuine baseline/candidate text and PNG evidence.

Tester SHALL use a dedicated `odcli-N` slot with its own checkout, database, HTTP port, XDG/catalog roots, and process/log ownership. The reference checkout and legacy unnumbered stand SHALL remain untouched. Slot creation SHALL use the Binta wrapper with both the numbered project profile and clean reference checkout from workspace project context, a unique database and free wrapper port, and the exact candidate SDK SHA. The initial main-project fixture SHALL have no `preferred_http_port`; its automatic/default Odoo port SHALL be `8069` for the collision scenario.

## Verification matrix

| Case ID | OpenSpec requirement/scenario | Verification gap / local E2E decision | Public boundary | Test script / independent Tester steps | CI disposition | Assertions | Artifacts / images |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `CFG-PORT` | `project-init`: legacy load, assignment round trip, precedence, owned config, source immutability, preview | Lower-level checks are sufficient for deterministic serialization and write boundaries | `ProjectConfig`, manifest writer, generated-config writer, `run --dry-run` | Implementer adds focused unit/CLI fixtures; Tester inspects mappings and runs the focused command on the exact candidate | `offline-tests`; `compatibility` for macOS/Python coverage | Optional field compatibility; explicit preference wins; atomic assignment; generated config aligned; source bytes unchanged; preview has no writes | Focused command/exit status and changed-file links in Multica; no image because internal config text is unchanged public UX |
| `RUN-PORT` | `instance-runtime-binding`: occupied default, reuse, replacement, explicit conflict, spawn race | **Selected local E2E.** A real listener plus real Odoo lifecycle is required to prove bind/readiness/restart integration | `odcli --project SLOT run --detach --wait-ready`, `status`, `stop` | Commit `tests/e2e/myl582_main_project_port_fallback.py` and minimal fixtures in a dedicated test-repository branch/MR. Independently bind `127.0.0.1:8069`, run baseline and candidate with equivalent state, inspect output/manifest/generated config/runtime, stop candidate, rerun with `8069` still occupied, inspect reuse, then separately set an explicit occupied preference and verify fail-closed | Local-only live case; deterministic branches remain in `offline-tests`; existing real-Odoo jobs are regression gates, not a duplicate required scenario | Baseline fails without reassignment; candidate chooses a non-8069 free port, reports fallback, readiness/status use it, manifest and owned config agree, restart reuses it, explicit occupied pin stays unchanged and spawns nothing | Product SHA, test SHA, MR/commit/file links, commands/exit codes, actual sanitized text, assertions, runtime endpoint, `myl582-baseline-port-conflict.png`, `myl582-candidate-port-fallback.png`, and `myl582-candidate-port-reuse.png` attached to Multica |
| `PUB-PORT` | `instance-runtime-binding`: publication uses selected runtime endpoint | No separate live Caddy stand is needed: publication already accepts only a persisted ready runtime and deterministically renders its captured `base_url`; a component regression closes the stale-route risk | `PublicationResource.publish_command()` / `odcli publish --project` | Implementer adds a focused test with assigned port differing from stale config; Tester inspects candidate route bytes and result projection without editing the test | `offline-tests`; existing publication tests | Local endpoint and reverse-proxy target equal the captured selected runtime; stale `8069` is absent; failed publish preserves prior route | Focused command/exit status, assertion summary, and GitHub file/PR links in Multica; no decorative image |
| `OUT-PORT` | Bounded selected-port provenance and explicit conflict guidance | Snapshot/component coverage proves schemas; selected `RUN-PORT` walkthrough proves human-visible output | Rich, JSON, and TOON run output | Implementer extends characterization snapshots/tests. Tester captures equivalent-width/locale baseline and candidate detached output and confirms machine fields match Rich facts | `offline-tests`; `lint`; `types` | Existing result fields remain; selected port/kind/reason are consistent and secret-free; explicit conflict tells the operator to change/remove the pin | Actual text plus the three `RUN-PORT` PNGs; JSON and diagnostic evidence only in Multica |

## Automated verification ownership

The Implementer owns all GitHub product tests, fixtures, snapshots, documentation, and CI-compatible changes: project config/manifest tests, allocator/resolver tests, run coordination and race tests, foreground/detached CLI characterization, runtime/status/publication regressions, and directly related real-Odoo fixture adjustments if implementation impact requires them. The independent Tester SHALL not edit those GitHub tests; Tester inspects coverage, reports findings, and owns only the dedicated test-repository script/fixture commit and evidence execution.

Expected focused commands include:

```text
uv run pytest -q -o addopts='' tests/unit/project/test_project_config.py tests/unit/internal/test_port_allocation.py tests/unit/test_run_detached.py tests/unit/test_run_canonical_worktree_path.py tests/unit/test_publication.py
make lint
make types
openspec validate auto-allocate-main-project-http-port --strict
```

The consolidated candidate SHALL run the applicable `offline-tests`, `compatibility`, `lint`, `types`, and existing real-Odoo CI jobs. If a local automatic suite is predicted to exceed ten minutes or reaches ten minutes, stop only that owned test process and rely on CI at the published exact revision; do not split the suite to evade the limit. Manual Tester actions are not subject to that automatic-suite limit.

## `RUN-PORT` readiness, walkthrough, and evidence

### Readiness gate

1. Record approved baseline SHA, exact candidate SHA, clean worktrees, GitHub PR URL, test-repository branch/MR/test SHA, Python/uv versions, fixed locale, terminal width, and selected free numbered-slot resources. Complete focused product tests and required review on the consolidated candidate before consuming the live attempt.
2. Confirm the dedicated PostgreSQL cluster is healthy on the project-context endpoint without printing its secret. Create a fresh `odcli-N` slot through `scripts/agent_checkout.py create` using `--project`, `--reference-checkout`, unique `--http-port`, unique `--target-database`, and candidate `--sdk-sha`. Do not use or modify the reference checkout.
3. Prepare equivalent baseline and candidate fixture state: no `preferred_http_port`, no prior `assigned_http_port`, generated/main config at `8069`, owned database/filestore, and an independently managed listener bound to loopback `8069`. Record the exact listener PID and verify it is not the Multica daemon before any cleanup signal.
4. Validate the test script against disposable fixture setup before the live acceptance action. A setup/script defect SHALL be repaired through deterministic checks rather than consuming repeated product attempts.

### Independent actions

1. Baseline: with `8069` occupied, invoke detached main-project run and capture the real `port-conflict` text/exit status; confirm no alternate assignment or runtime row appears.
2. Candidate: reset to equivalent state, invoke detached main-project run with readiness, and inspect the actual output, manifest, owned generated config, live listener, status, and runtime record. The selected port SHALL be free before spawn, differ from `8069`, and agree everywhere afterward.
3. Stop the exact candidate runtime, keep the independent `8069` listener, start again, and confirm the same automatic assignment and endpoint are reused.
4. Stop/reset, set an explicit preferred port occupied by a Tester-owned listener, invoke run once, and confirm actionable failure, unchanged manifest/config, no fallback, no Odoo child, and no runtime publication.
5. Inspect the focused publication component evidence at the same product SHA and confirm route generation uses the captured selected endpoint. A second live Caddy publication is not required.

### Image and publication contract

- Save actual terminal text before rendering. On macOS render the baseline conflict, candidate fallback, and candidate reuse through bundled `scripts/render_terminal.swift` from `openspec-e2e-testing`; label them as renders of captured terminal text, not screenshots. Preserve the tested output language and state ANSI/TTY/progress limitations honestly.
- Publish exact product/baseline/test SHAs, commands, exit codes, assertions, sanitized provenance, GitHub PR/file links, GitLab MR/commit/file links, and all PNG attachments in the final Multica Tester comment, then read back attachment metadata. Raw logs, JSON diagnostics, hashes, and screenshots SHALL remain in Multica, not GitLab.
- The GitLab MR SHALL contain only the ordinary reproducible script and necessary fixtures. Test-code and any genuine functional commit created by the tested Git utility SHALL remain separate; do not invent a second commit when the tested flow creates none. Do not close/merge the MR or delete its branch without separate authority.

### Cleanup

Stop only Tester-owned exact Odoo/listener PIDs after confirming they are not the Multica daemon. Remove the numbered slot through the same Binta wrapper and `--project` profile, including its database, port binding, temporary XDG/catalog roots, logs, and disposable config. Preserve the GitLab evidence branch/MR/commits and Multica attachments. Do not stop Colima or the persistent PostgreSQL cluster.
