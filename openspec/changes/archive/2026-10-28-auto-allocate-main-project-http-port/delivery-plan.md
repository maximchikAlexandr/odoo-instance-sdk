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
- Definition of done / evidence: every delta scenario maps to a focused passing check or `RUN-PORT`; applicable repository/CI gates and strict OpenSpec validation pass at the exact candidate revision; the complete detached, foreground, explicit-pin, and reuse before/after text-and-PNG set plus commands, exit codes, assertions, SHAs, and MR/file links are attached/read back in Multica; cleanup removes both disposable slots while preserving the evidence MR/branch/commits.
- Parallel-safety rationale: not applicable. The estimate contract mandates one WP, and the model, allocation, generated config, command snapshot, output, and endpoint tests share one coupled contract/write zone.

## E2E decision

`e2e_required=true`.

Focused unit/component tests can prove manifest compatibility, locking, candidate ordering, mutation boundaries, output schemas, and publication's use of a captured endpoint. They cannot by themselves prove that a real occupied loopback port causes a main-project Odoo process to bind the selected fallback, become ready there, persist that endpoint, stop, and restart on the same assignment. Two isolated numbered slots—one at the approved base and one at the exact candidate—close that cross-component gap without changing an installation in place. The changed detached, foreground, and explicit-pin terminal states require genuine baseline/candidate text and PNG evidence.

Tester SHALL use two dedicated `odcli-N` slots, one baseline and one candidate, each with its own checkout, database, HTTP port, XDG/catalog roots, and process/log ownership. The reference checkout and legacy unnumbered stand SHALL remain untouched. Both slots SHALL be created by the same Binta wrapper command shape with the same full test-fixture `--reference-sha`; only their unique wrapper port/database and `--sdk-sha` SHALL differ. The baseline uses the approved implementation base and the candidate uses the exact verified product SHA. The initial main-project fixture SHALL have no `preferred_http_port` or `assigned_http_port`; its automatic/default Odoo port SHALL be `8069` for the collision scenario.

## Verification matrix

| Case ID | OpenSpec requirement/scenario | Verification gap / local E2E decision | Public boundary | Test script / independent Tester steps | CI disposition | Assertions | Artifacts / images |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `CFG-PORT` | `project-init`: legacy load, assignment round trip, precedence, owned config, source immutability, preview | Lower-level checks are sufficient for deterministic serialization and write boundaries | `ProjectConfig`, manifest writer, generated-config writer, `run --dry-run` | Implementer adds focused unit/CLI fixtures; Tester inspects mappings and runs the focused command on the exact candidate | `offline-tests`; `compatibility` for macOS/Python coverage | Optional field compatibility; explicit preference wins; atomic assignment; generated config aligned; source bytes unchanged; preview has no writes | Focused command/exit status and changed-file links in Multica; no image because internal config text is unchanged public UX |
| `RUN-PORT` | `instance-runtime-binding`: occupied default, foreground/detached fallback, reuse, replacement, explicit conflict, spawn race | **Selected local E2E.** A real listener plus real Odoo lifecycle is required to prove bind/readiness/restart integration and the changed terminal states | `odcli --project SLOT run`, `run --detach --wait-ready`, `status`, `stop` | Commit `tests/e2e/myl582_main_project_port_fallback.py` and its fixture in a dedicated test-repository branch/MR. Use the exact parameterized create/reset/case/remove commands below against separate baseline and candidate slots | Local-only live case; deterministic branches remain in `offline-tests`; existing real-Odoo jobs are regression gates, not a duplicate required scenario | Baseline fails without reassignment; candidate foreground and detached runs choose a non-8069 free port and report fallback; readiness/status use it; manifest and owned config agree; restart reuses it; explicit occupied pin stays unchanged, gives actionable guidance, and spawns nothing | Product SHA, test SHA, MR/commit/file links, commands/exit codes, actual sanitized text, assertions, runtime endpoint, and every text/PNG named in the image contract attached to Multica |
| `PUB-PORT` | `instance-runtime-binding`: publication uses selected runtime endpoint | No separate live Caddy stand is needed: publication already accepts only a persisted ready runtime and deterministically renders its captured `base_url`; a component regression closes the stale-route risk | `PublicationResource.publish_command()` / `odcli publish --project` | Implementer adds a focused test with assigned port differing from stale config; Tester inspects candidate route bytes and result projection without editing the test | `offline-tests`; existing publication tests | Local endpoint and reverse-proxy target equal the captured selected runtime; stale `8069` is absent; failed publish preserves prior route | Focused command/exit status, assertion summary, and GitHub file/PR links in Multica; no decorative image |
| `OUT-PORT` | Bounded selected-port provenance and explicit conflict guidance | Snapshot/component coverage proves schemas; selected `RUN-PORT` walkthrough proves every changed human-visible state | Rich, JSON, and TOON run output | Implementer extends characterization snapshots/tests. Tester captures fixed-width/locale baseline and candidate detached, foreground, and explicit-pin output and confirms machine fields match Rich facts | `offline-tests`; `lint`; `types` | Existing result fields remain; selected port/kind/reason are consistent and secret-free; foreground fallback is visible; explicit conflict tells the operator to change/remove the pin | Actual text and all seven `RUN-PORT` PNGs; JSON and diagnostic evidence only in Multica |

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

### Parameter contract and exact slot creation

The commands below are the required entry points. Values in angle brackets are execution-time parameters, not repository literals: `NUMBERED_PROJECT`, `REFERENCE_CHECKOUT`, and `CHECKOUT_PARENT` are the private paths from workspace project context; `TEST_REPO` is an independent checkout of `TEST_REPOSITORY_URL`; `TEST_SHA` is the full evidence-MR SHA containing the script and fixture; `CANDIDATE_SHA` is the independently verified product SHA; ports and database names are unique resources selected by Tester. `BASELINE_SHA` is fixed to the approved implementation base recorded above. No secret, private hostname, or personal path SHALL be copied into Git.

```sh
python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" create \
  --project "$NUMBERED_PROJECT" \
  --reference-checkout "$REFERENCE_CHECKOUT" \
  --checkout-parent "$CHECKOUT_PARENT" \
  --reference-sha "$TEST_SHA" \
  --sdk-sha "$BASELINE_SHA" \
  --http-port "$BASELINE_WRAPPER_PORT" \
  --target-database "$BASELINE_DATABASE"

python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" create \
  --project "$NUMBERED_PROJECT" \
  --reference-checkout "$REFERENCE_CHECKOUT" \
  --checkout-parent "$CHECKOUT_PARENT" \
  --reference-sha "$TEST_SHA" \
  --sdk-sha "$CANDIDATE_SHA" \
  --http-port "$CANDIDATE_WRAPPER_PORT" \
  --target-database "$CANDIDATE_DATABASE"
```

Tester SHALL save each command's JSON output in the private evidence directory and read `identifier`, `launcher`, `checkout`, `checkout_sha`, and `database` from it. Read-back SHALL prove the two `checkout_sha` values equal `TEST_SHA`, the launchers/identifiers/checkouts/databases differ, and each launcher's installed SDK resolves to its requested full SHA. These JSON records are Multica-only evidence.

The test-repository MR SHALL provide `tests/e2e/myl582_main_project_port_fallback.py` with exactly these public subcommands:

```sh
python3 "$TEST_REPO/tests/e2e/myl582_main_project_port_fallback.py" reset \
  --slot-json "$SLOT_JSON" \
  --fixture "$TEST_REPO/tests/e2e/fixtures/myl582-main-project" \
  --expected-fixture-sha "$TEST_SHA"

python3 "$TEST_REPO/tests/e2e/myl582_main_project_port_fallback.py" capture \
  --case "$CASE" \
  --slot-json "$SLOT_JSON" \
  --expected-sdk-sha "$EXPECTED_SDK_SHA" \
  --collision-port 8069 \
  --locale C.UTF-8 \
  --columns 120 \
  --evidence-dir "$CASE_EVIDENCE_DIR"
```

`reset` SHALL stop only the runtime/listener PIDs recorded by that script after comparing each with `multica daemon status --output json`; restore the fixture's byte-identical manifest and owned generated config; remove its prior runtime row, transient logs, and evidence directory; and assert no `preferred_http_port`, no `assigned_http_port`, generated `http_port = 8069`, no Odoo child, and a free collision port. It SHALL fail rather than clean an unrecorded PID or out-of-slot path. `capture` SHALL bind its own loopback listener on `8069`, record its PID, invoke the launcher and checkout obtained from `--slot-json`, save raw stdout/stderr and exit status, perform the case assertions, and release only its owned processes on failure. For foreground capture it SHALL use a PTY, wait for the fallback notice and readiness, then send SIGINT to the exact owned Odoo child; ANSI, TTY, progress, locale, and width SHALL be recorded.

The script SHALL map cases to these product calls, without substituting mocks:

- `baseline-detached-default-conflict` and `candidate-detached-fallback`: `"$LAUNCHER" --project "$CHECKOUT" run --detach --wait-ready`.
- `baseline-foreground-default-conflict` and `candidate-foreground-fallback`: `"$LAUNCHER" --project "$CHECKOUT" run` under the bounded PTY procedure above.
- `baseline-explicit-pin-conflict` and `candidate-explicit-pin-conflict`: first set the same fixture-owned explicit preference to occupied `8069`, then invoke `"$LAUNCHER" --project "$CHECKOUT" run --detach --wait-ready`.
- `candidate-detached-reuse`: after the successful candidate detached-fallback case, invoke `"$LAUNCHER" --project "$CHECKOUT" stop`, retain the assigned port and the independent `8069` collision, then invoke `"$LAUNCHER" --project "$CHECKOUT" run --detach --wait-ready` again. This is the sole case intentionally not preceded by `reset`.

### Readiness gate and ordered commands

1. Record the baseline/candidate/test/fixture SHAs, clean worktrees, GitHub PR URL, test-repository MR/file URLs, Python/uv versions, fixed locale/width, and selected unique resources. Complete required candidate product checks and review before consuming the live attempt; planning itself executes none of them.
2. Confirm the dedicated PostgreSQL cluster is healthy at the project-context endpoint without printing its secret. Run both exact create commands above and verify their JSON read-back. Do not modify the reference checkout.
3. For every case except reuse, run the exact `reset` command first. Baseline and candidate cases with the same suffix SHALL use the same fixture bytes, collision port, locale, terminal width, and case assertions; only `SLOT_JSON`, `EXPECTED_SDK_SHA`, and evidence directory differ.
4. Run the exact capture commands in this order:

```sh
# Reset immediately before each of these six commands.
python3 "$TEST_REPO/tests/e2e/myl582_main_project_port_fallback.py" capture --case baseline-detached-default-conflict --slot-json "$BASELINE_SLOT_JSON" --expected-sdk-sha "$BASELINE_SHA" --collision-port 8069 --locale C.UTF-8 --columns 120 --evidence-dir "$EVIDENCE_ROOT/baseline-detached-default-conflict"
python3 "$TEST_REPO/tests/e2e/myl582_main_project_port_fallback.py" capture --case candidate-detached-fallback --slot-json "$CANDIDATE_SLOT_JSON" --expected-sdk-sha "$CANDIDATE_SHA" --collision-port 8069 --locale C.UTF-8 --columns 120 --evidence-dir "$EVIDENCE_ROOT/candidate-detached-fallback"
python3 "$TEST_REPO/tests/e2e/myl582_main_project_port_fallback.py" capture --case candidate-detached-reuse --slot-json "$CANDIDATE_SLOT_JSON" --expected-sdk-sha "$CANDIDATE_SHA" --collision-port 8069 --locale C.UTF-8 --columns 120 --evidence-dir "$EVIDENCE_ROOT/candidate-detached-reuse"
python3 "$TEST_REPO/tests/e2e/myl582_main_project_port_fallback.py" capture --case baseline-foreground-default-conflict --slot-json "$BASELINE_SLOT_JSON" --expected-sdk-sha "$BASELINE_SHA" --collision-port 8069 --locale C.UTF-8 --columns 120 --evidence-dir "$EVIDENCE_ROOT/baseline-foreground-default-conflict"
python3 "$TEST_REPO/tests/e2e/myl582_main_project_port_fallback.py" capture --case candidate-foreground-fallback --slot-json "$CANDIDATE_SLOT_JSON" --expected-sdk-sha "$CANDIDATE_SHA" --collision-port 8069 --locale C.UTF-8 --columns 120 --evidence-dir "$EVIDENCE_ROOT/candidate-foreground-fallback"
python3 "$TEST_REPO/tests/e2e/myl582_main_project_port_fallback.py" capture --case baseline-explicit-pin-conflict --slot-json "$BASELINE_SLOT_JSON" --expected-sdk-sha "$BASELINE_SHA" --collision-port 8069 --locale C.UTF-8 --columns 120 --evidence-dir "$EVIDENCE_ROOT/baseline-explicit-pin-conflict"
python3 "$TEST_REPO/tests/e2e/myl582_main_project_port_fallback.py" capture --case candidate-explicit-pin-conflict --slot-json "$CANDIDATE_SLOT_JSON" --expected-sdk-sha "$CANDIDATE_SHA" --collision-port 8069 --locale C.UTF-8 --columns 120 --evidence-dir "$EVIDENCE_ROOT/candidate-explicit-pin-conflict"
```

The reuse command follows candidate detached fallback without reset; after it completes, reset the candidate before foreground. Assertions SHALL show baseline default collisions do not allocate or spawn; candidate fallback chooses a free non-8069 port and aligns output, manifest, owned config, runtime, readiness, and status; reuse preserves that exact assignment; candidate foreground emits the new fallback notice; and explicit-pin baseline/candidate both fail closed while only the candidate supplies the new actionable change/remove-pin guidance. Inspect the focused publication evidence at the candidate SHA and confirm its route consumes the captured selected endpoint; no second live Caddy stand is required.

### Image and publication contract

Save actual terminal text before rendering. On macOS, render each file through bundled `scripts/render_terminal.swift` from `openspec-e2e-testing`, label it as a render rather than a screenshot, and preserve tested language and stated ANSI/TTY/progress limitations. The mandatory pairs are:

- `myl582-baseline-detached-default-conflict.txt` / `.png`
- `myl582-candidate-detached-fallback.txt` / `.png`
- `myl582-candidate-detached-reuse.txt` / `.png`
- `myl582-baseline-foreground-default-conflict.txt` / `.png`
- `myl582-candidate-foreground-fallback.txt` / `.png`
- `myl582-baseline-explicit-pin-conflict.txt` / `.png`
- `myl582-candidate-explicit-pin-conflict.txt` / `.png`

Publish exact product/baseline/test/fixture SHAs, commands, exit codes, assertions, sanitized provenance, GitHub PR/file links, GitLab MR/commit/file links, and all seven PNG attachments in the final Multica Tester comment, then read back attachment metadata. Raw text/logs, JSON diagnostics, hashes, and images SHALL remain in Multica, not GitLab. The GitLab MR contains only the reproducible script and necessary fixtures. Test-code and any genuine functional commit created by the tested Git utility remain separate; do not invent a second commit when the tested flow creates none. Do not close/merge the MR or delete its branch without separate authority.

### Cleanup

After final reset, remove both slots with their recorded identifiers and the same profile/parent:

```sh
python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" remove "$BASELINE_IDENTIFIER" --project "$NUMBERED_PROJECT" --checkout-parent "$CHECKOUT_PARENT"
python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" remove "$CANDIDATE_IDENTIFIER" --project "$NUMBERED_PROJECT" --checkout-parent "$CHECKOUT_PARENT"
```

Stop only Tester-owned exact Odoo/listener PIDs after confirming they are not the Multica daemon. Read back that both slots, databases, port bindings, temporary XDG/catalog roots, logs, and disposable configs are absent. Preserve the GitLab evidence branch/MR/commits and Multica attachments. Do not stop Colima or the persistent PostgreSQL cluster.
