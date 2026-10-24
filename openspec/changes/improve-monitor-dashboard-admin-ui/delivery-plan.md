# Delivery Plan

## Contract baseline

- Task key: `MYL-554`.
- OpenSpec change: `improve-monitor-dashboard-admin-ui`.
- Approved base: `origin/main` at `350d308fd04ed9e32380571a848e04e231a9d0c9`.
- Planning branch/worktree: `feat/MYL-554-improve-monitor-dashboard-admin-ui` in the dedicated managed checkout. Production code is unchanged.
- Test repository: resolve `TEST_REPOSITORY_URL` from the current Multica project description at execution time. Its private hostname, credentials, personal data, and full URL SHALL NOT be copied into public Git artifacts or screenshots.

## Estimate contract

- Authoritative totals exist only in the root planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; numerical totals are intentionally not duplicated here.
- Basis: remaining work from the approved base to every acceptance scenario for one experienced developer familiar with Python, msgspec/FastAPI, React/TypeScript, Starlette Admin, and this repository, without AI acceleration.
- Confidence: medium. The affected collection, generated-contract, React, and Admin paths are inspectable and have close tests; uncertainty remains around the cooperative filesystem deadline, Starlette Admin composite-row routing, and live browser fixture setup.
- Calibration: uncalibrated because no comparable human actual-hours dataset was supplied. The estimate uses a PERT-style aggregation of non-overlapping implementation, focused-test, documentation, integration, and likely review-repair units.
- Evidence: the approved base already supplies the ten-second `du` subprocess timeout, symlink-safe traversal/deduplication, `StorageFootprint`, monitor TTL caches, canonical `Snapshot`, msgspec/OpenAPI/codegen pipeline, shared `RuntimeCard`, `formatBytes`, Starlette Admin `menu_label`/icons/default index, direct secret-store projections/CAS, dashboard CI, and numbered-slot infrastructure. It does not bound `_walk_size`; the estimate therefore includes the injected monotonic deadline, 100,000-entry accounting, discard-on-incomplete semantics, synthetic below/at-budget and fallback failure tests, the additive project footprint, client filters/formatters, explicit view metadata, one hidden safe Admin identity, generated artifacts, focused tests, and browser evidence.
- Tests were not run during planning or estimation.

## Ponytail review

`ponytail full` found no justified new subsystem. The existing traversal receives two constants, an injected clock seam, entry accounting, and fail-incomplete behavior; it does not gain a worker, process, scanner service, or dependency. Project sizing reuses the current probe/cache; running selection stays in the React view; Admin uses its built-in sidebar/index; secret routing gets one narrow projection identity; verification reuses repository and numbered-slot tooling. A new endpoint, inventory, background sampler, custom Admin dashboard/theme, ORM layer, composite-key framework, and browser automation framework are explicitly excluded.

## E2E decision

`e2e_required=true`.

Focused Python and React tests can prove model values, filter predicates, timestamps, generated schemas, navigation labels, and identity decoding. They cannot alone prove the packaged UI's browser-local date rendering, layout and storage labels, the actual Starlette Admin sidebar/default index, a list-row transition into a created GitLab secret detail page, or the canonical same-origin `/admin/` navigation. Those are material visible browser gaps and require genuine before/after screenshots.

The walkthrough SHALL use a fresh numbered `odcli-N` slot created from the Binta test repository wrapper with both `--project ~/e2e/odcli/numbered-project-e2e` and `--reference-checkout ~/e2e/odcli/binta-e2e-reference`, a free `--http-port`, a unique target database, and the exact baseline or candidate `--sdk-sha`. It SHALL use its own checkout, database, port, Admin config, browser session, and evidence directory; it SHALL NOT use or modify the reference checkout, the old unnumbered profile, or another Tester's slot. Planning does not execute any test.

No new test-repository script is planned: the existing `scripts/agent_checkout.py` create/remove wrapper plus the product's `odcli monitor`, Admin bootstrap, existing browser, and HTTP health check are sufficient. If execution discovers a reusable fixture gap, the Implementer owns the smallest script/fixture change in a separate functional test-repository commit and retained GitLab MR; Tester does not edit it. Screenshots, response bodies, logs, diagnostics, hashes, commands, and assertions remain Multica-only evidence, never Git commits.

### Verification matrix

| Case | OpenSpec requirement / scenario coverage | Affected OpenAPI operation and schemas | Remaining live gap and decision | Existing/planned automated coverage | Independent Tester actions | Required assertions | Multica-only artifacts / images |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `E2E-01` | `environment-monitor`: bounded fallback, separate main/worktree storage, unavailable/incomplete result, caching; `web-sdk-codegen`: separate resource totals | `GET /api/v1/snapshot`, operation `getMonitorSnapshot`; `Snapshot`, `ProjectSummary`, new `ProjectStorageFootprint`, `EnvironmentSnapshot`, existing `StorageFootprint` | Real filesystem attribution and packaged card output are not proven by fixtures alone; local E2E required. Deadline/100,000-entry exhaustion stays a deterministic lower-level test: a live tree large enough to force it is intentionally not created. | Implementer extends fallback deadline/budget/failure tests, monitor model/collection/cache/schema tests, generated-contract tests, React resource rendering tests, `dashboard-tests`, and stale-codegen gate | Create equivalent fresh baseline and candidate slots with the command contract below; use distinct small marker files in the registered checkout and environment worktree; start each exact monitor; inspect the main and worktree cards and the sanitized snapshot | Candidate shows separate human-readable Resources values sourced from the correct owner; no path/component breakdown or obvious double count; baseline lacks the project metric; `complete=false` maps to no invented project total in automated exhaustion/failure cases; live snapshot stays responsive | `e2e-01-baseline-resources.png`, `e2e-01-candidate-resources.png`, safe fixture-size note, exact baseline/candidate SHAs, browser viewport/locale/timezone, health/start/stop command exits |
| `E2E-02` | `web-sdk-codegen`: human-readable exact backup time; running filter composition, count, empty state | `GET /api/v1/snapshot`, operation `getMonitorSnapshot`; `Snapshot`, `ProjectSummary`, `EnvironmentSnapshot`, `RuntimeMetrics`, `RuntimeState`, `BackupFreshnessSnapshot` | Browser locale/timezone rendering and real interaction/state filtering need a browser; local E2E required | Implementer extends format helper and React tests with injected locale/timezone; tests `ready`, `not_ready`, stopped, project composition, count, empty state, no extra fetch | Use the command contract below to start the slot runtime and monitor; capture running state, stop the exact slot runtime, refresh the same project scope, and exercise `all`/`running` plus an empty scope in a reset browser context | Visible candidate timestamp has date/hour/minute and no seconds; DOM `dateTime` keeps exact ISO value; relative age remains; running includes `ready`/`not_ready`, excludes stopped, and selector changes cause no new request beyond normal polling | `e2e-02-baseline-time.png`, `e2e-02-candidate-running.png`, `e2e-02-candidate-empty.png`, safe DOM observations and request-count observation |
| `E2E-03` | `web-sdk-codegen`: canonical Admin navigation; `dashboard-http-api`: all five built-in labels | N/A: `/admin/` and its authenticated Starlette Admin HTML/session routes are intentionally outside the generated OpenAPI contract | Same-origin canonical navigation and actual built-in sidebar/index need packaged browser proof; local E2E required | Implementer extends React link test and authenticated Admin HTTP assertions for five labels/index panels and unchanged unauthorized behavior | Bootstrap the slot-scoped owner-only config with the command below, follow Admin from the monitor, authenticate, inspect root/sidebar, log out, and verify an unauthenticated entity route is denied | Link target is exactly `/admin/`; no missing-slash error/loop; sidebar and default index each name exactly the five current entities; no custom sixth entity; logout/authorization unchanged | `e2e-03-baseline-admin.png`, `e2e-03-candidate-admin-index.png`, `e2e-03-candidate-admin-sidebar.png`, navigation URL and safe status-code ledger; no credentials in capture |
| `E2E-04` | `dashboard-http-api` and `secret-store`: complete safe Admin identity; GitLab created-record detail, multi-host independence, revision and invalid identity | N/A: secret Admin list/detail/edit/delete HTML/form routes and hidden `admin_id` are deliberately not public OpenAPI operations or schemas | The reported list-to-detail failure depends on Starlette Admin's serialized row/action routing and must be exercised through a real browser; local E2E required | Implementer adds projection encode/decode, Admin HTTP list/detail/edit/delete/stale/invalid/multi-host tests and no-secret assertions; dashboard package CI runs them | In the disposable Admin create two GitLab records for a synthetic member on two safe example hosts with disposable tokens; baseline captures one failing row; candidate opens both, edits one with blank token, follows the new identity, attempts the old identity, then deletes only that host | Correct record opens; same member across hosts remains distinct; blank edit retains secret; new identity works; stale/invalid identity does not mutate; deleted host disappears while the other remains; token/password/admin identity internals never render | `e2e-04-baseline-gitlab-detail-error.png`, `e2e-04-candidate-gitlab-detail.png`, `e2e-04-candidate-two-hosts.png`, sanitized route/status/assertion ledger and attachment read-back |

### Repeatable isolated-slot command contract

Run this sequence once for `PRODUCT_SHA=<approved baseline>` and again from a fresh slot for `PRODUCT_SHA=<exact candidate>`. `PRODUCT_CHECKOUT` is the clean product source checkout at that exact SHA; it supplies the already-versioned Admin bootstrap script. `ODOO_HTTP_PORT` and `MONITOR_PORT` SHALL be two separately reserved free loopback ports, and `TARGET_DATABASE` SHALL be unique to this attempt. Command output, JSON, PID, logs, and response bodies are evidence staging only and SHALL NOT be committed.

```bash
PRODUCT_SHA=<40-character-product-sha>
PRODUCT_CHECKOUT=<absolute-clean-product-checkout-at-PRODUCT_SHA>
ODOO_HTTP_PORT=<reserved-free-odoo-port>
MONITOR_PORT=<different-reserved-free-monitor-port>
TARGET_DATABASE=<unique-copy-database-name>
EVIDENCE_DIR=<private-multica-evidence-directory>
WRAPPER="$HOME/e2e/odcli/binta-e2e-reference/scripts/agent_checkout.py"
PROJECT="$HOME/e2e/odcli/numbered-project-e2e"
REFERENCE="$HOME/e2e/odcli/binta-e2e-reference"
mkdir -p "$EVIDENCE_DIR"
test "$(git -C "$PRODUCT_CHECKOUT" rev-parse HEAD)" = "$PRODUCT_SHA"
test -z "$(git -C "$PRODUCT_CHECKOUT" status --porcelain --untracked-files=all)"
python3 "$WRAPPER" create \
  --project "$PROJECT" \
  --reference-checkout "$REFERENCE" \
  --reference-sha "$(git -C "$REFERENCE" rev-parse HEAD)" \
  --sdk-sha "$PRODUCT_SHA" \
  --http-port "$ODOO_HTTP_PORT" \
  --target-database "$TARGET_DATABASE" \
  >"$EVIDENCE_DIR/create.json"
SLOT_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["identifier"])' "$EVIDENCE_DIR/create.json")"
ENVIRONMENT_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["environment_id"])' "$EVIDENCE_DIR/create.json")"
LAUNCHER="$HOME/.local/bin/odcli-$SLOT_ID"
STATE_ROOT="$HOME/.odcli-$SLOT_ID"
SDK_PYTHON="$HOME/.local/share/odcli-slots/$SLOT_ID/tool/odoo-instance-sdk/bin/python"
test -x "$LAUNCHER"
test -x "$SDK_PYTHON"
```

Bootstrap Admin interactively; the password is entered only at the prompts and SHALL NOT be stored in a command, log, screenshot, or Git artifact:

```bash
ODCLI_USER_ROOT="$STATE_ROOT" "$SDK_PYTHON" \
  "$PRODUCT_CHECKOUT/scripts/bootstrap_admin_config.py"
```

Start the isolated Odoo runtime, start the monitor, and prove health/snapshot availability:

```bash
"$LAUNCHER" --env "$ENVIRONMENT_ID" run --detach --wait-ready \
  --readiness-timeout 60 --output json >"$EVIDENCE_DIR/runtime-start.json"
"$LAUNCHER" monitor --host 127.0.0.1 --port "$MONITOR_PORT" --no-open \
  >"$EVIDENCE_DIR/monitor.log" 2>&1 &
MONITOR_PID=$!
printf '%s\n' "$MONITOR_PID" >"$EVIDENCE_DIR/monitor.pid"
curl --fail --silent --show-error "http://127.0.0.1:$MONITOR_PORT/healthz" \
  >"$EVIDENCE_DIR/health.json"
curl --fail --silent --show-error "http://127.0.0.1:$MONITOR_PORT/api/v1/snapshot" \
  >"$EVIDENCE_DIR/snapshot.json"
```

Reset means stop and remove the current slot completely, then repeat `create` with a new unique database and two newly reserved ports; do not reuse a mutated baseline slot as candidate. Normal and failure cleanup use the same sequence. Stop only the recorded monitor PID after proving it is not the Multica daemon, stop the exact linked environment runtime, and remove through the same wrapper/project:

```bash
DAEMON_PID="$(multica daemon status --output json | python3 -c 'import json,sys; value=json.load(sys.stdin).get("pid"); print(value if isinstance(value,int) else "")')"
test -n "$MONITOR_PID"
test "$MONITOR_PID" != "$DAEMON_PID"
kill -TERM "$MONITOR_PID"
wait "$MONITOR_PID" || test "$?" -eq 143
"$LAUNCHER" --env "$ENVIRONMENT_ID" stop --output json \
  >"$EVIDENCE_DIR/runtime-stop.json"
python3 "$WRAPPER" remove "$SLOT_ID" --project "$PROJECT" \
  >"$EVIDENCE_DIR/remove.json"
```

If startup fails before `MONITOR_PID` exists, skip only the monitor stop; still invoke slot runtime stop when the launcher/environment were created and always invoke wrapper `remove`. After removal, `python3 "$WRAPPER" inspect "$SLOT_ID"` SHALL fail and the reserved runtime/monitor ports SHALL be free. Browser state is reset with a new private context between baseline and candidate. Cleanup preserves the product PR/branch/commits and any functional test-repository MR/branch/commits; it deletes only the disposable slot, its unique database through the wrapper, staged evidence files after attachment/read-back, and processes it owns.

### Automated and CI disposition

- Python focused command: `uv run pytest -q -o addopts='' tests/unit/internal/test_storage_footprint.py tests/unit/test_monitor_cache_and_docker.py tests/unit/test_monitor_snapshot.py tests/unit/test_monitor_schema_v2.py tests/unit/test_http_admin.py`. Implementer may replace a path with the repository's directly affected successor test, but SHALL not create a duplicate harness.
- Frontend command: `npm test -- --run` from `src/odoo_instance_sdk/web`, followed by `npm run build`; generated contract check: `make web-codegen-check` after Python dashboard/test and `npm ci` prerequisites.
- Repository gates: `make lint`, `make types`, the focused tests above, `make test`, `make package`, pinned scoped OpenSpec validation and pinned repository-wide OpenSpec non-regression as defined below, and `git diff --check`. Any command expected to exceed the workspace's ten-minute local limit SHALL run only in GitHub Actions on the exact published candidate; a local run reaching ten minutes is stopped by its exact owned PID and recorded as moved to CI.
- Existing `.github/workflows/ci.yml` `dashboard-tests` owns the dashboard extra, Python Admin tests, `npm ci`, generated-contract check, frontend tests, and build. Existing `offline-tests`, `lint`, `types`, and `package` jobs retain their normal scopes. Implementer owns any required test/fixture/generated artifact edits; Tester verifies and reports findings only.

#### Pinned OpenSpec validation contract

All OpenSpec validation SHALL use `@fission-ai/openspec@1.14.3`; the evidence SHALL record `1.14.3` before interpreting results. On the exact implementation candidate, `npx --yes @fission-ai/openspec@1.14.3 validate improve-monitor-dashboard-admin-ui --strict` SHALL exit zero.

Repository-wide strict validation is a baseline non-regression comparison, not a known-impossible clean-tree PASS gate. Run the following `validate --all --strict --json --report findings` command in two clean detached worktrees: approved baseline `350d308fd04ed9e32380571a848e04e231a9d0c9` and the exact candidate. Both use the same Node runtime and pinned CLI. Preserve each command's exit code and raw JSON as Multica-only evidence; a nonzero baseline or candidate exit is expected while pre-existing findings remain.

```bash
npx --yes @fission-ai/openspec@1.14.3 validate --all --strict \
  --json --report findings >"$EVIDENCE_DIR/openspec-all-$REVISION.json"
OPEN_SPEC_ALL_EXIT=$?
jq -cS '.itemFindings[] as $item | $item.issues[] | {type:$item.type,id:$item.id,level,path,message}' \
  "$EVIDENCE_DIR/openspec-all-$REVISION.json" | LC_ALL=C sort -u \
  >"$EVIDENCE_DIR/openspec-findings-$REVISION.jsonl"
```

Set `REVISION=baseline` in the approved-base worktree and `REVISION=candidate` in the exact-candidate worktree. The non-regression assertion is:

```bash
comm -13 "$EVIDENCE_DIR/openspec-findings-baseline.jsonl" \
  "$EVIDENCE_DIR/openspec-findings-candidate.jsonl" \
  >"$EVIDENCE_DIR/openspec-findings-new.jsonl"
test ! -s "$EVIDENCE_DIR/openspec-findings-new.jsonl"
```

The candidate passes this gate only when its scoped strict command exits zero, both diagnostics decode successfully, and `openspec-findings-new.jsonl` is empty. Removing baseline findings is allowed; adding any finding at any item/type/level/path/message tuple fails. The raw diagnostics, canonical finding sets, comparison output, exact SHAs, CLI version, commands, and exit codes remain Multica-only evidence and SHALL NOT be committed.

### Evidence and cleanup contract

Before a live attempt, Tester records the exact product SHA, baseline SHA, test-repository revision, slot ID, target database, port, locale/timezone/viewport, health result, focused CI disposition, and absence of another owner on those resources. Candidate and baseline use equivalent sanitized fixtures and independent/reset browser sessions.

Tester publishes one final Multica comment with product PR and exact commit links; any necessary test-repository MR/commit/file links; case-level PASS/FAIL and concise assertions; commands and exit codes; pinned OpenSpec CLI version plus scoped PASS and baseline/candidate non-regression result; generated image attachments; SHA/hashes and provenance; cleanup result; and attachment read-back. Private URLs/hostnames, credentials, personal identifiers, raw response bodies, logs, diagnostic JSON, and hashes belong only in Multica and SHALL be sanitized. No evidence-only JSON/HTML/image/log file is committed.

Cleanup stops only the exact monitor PID started by the case after comparing it with `multica daemon status --output json`, removes the disposable numbered slot through the same Binta wrapper and `--project`, and confirms the unique database/port are gone. It preserves the product PR/branch/commits and any evidence-bearing test-repository MR/branch/functional commits. It does not merge, close, or delete either branch/MR without a separate instruction.

## Delivery topology

`delivery_mode=single_wp_no_dag`. The authoritative `Estimate, hours` property is at or below the topology threshold. Exactly one work package covers every OpenSpec task, with no `depends_on`, stage, frontier, or DAG metadata.

### WP-MYL-554-01-ui-fixes

- **Task coverage:** `1.1`, `1.2`, `2.1`, `2.2`, `2.3`, `2.4`, `3.1`, `3.2`, `4.1`, `4.2`, `4.3`, each exactly once.
- **Independent deliverable:** one integrated candidate that presents readable/filterable/storage-aware monitor cards, canonical Admin navigation with five named resources, and reliable secret detail/edit/delete routing, together with generated contracts, focused tests, documentation, and independent browser evidence.
- **Owned responsibility scope:** the existing `_walk_size` deadline/100,000-entry boundary, discard-on-incomplete semantics, and synthetic fallback/large-tree tests; project checkout storage model/collection/cache and directly related tests; canonical msgspec/OpenAPI/TypeScript generated artifacts; existing React app/format/style/tests; five current Admin view metadata; safe secret projection identity and Admin route handling; affected documentation, fixtures, packaging, and validation files. Directly related tests, snapshots, generated artifacts, and service files are included without turning this scope into an exhaustive allowlist.
- **Critical shared files:** `models/monitor.py`, `models/footprint.py`, `internal/storage_footprint.py`, `resources/monitor/collection_parts/snapshot.py`, monitor planning/cache code, `http/admin*.py`, `internal/secret_store.py`, `openapi.json`, `web/src/generated/`, `web/src/App.tsx`, `web/src/format.ts`, `web/src/monitor.css`, focused Python/React tests, README, and packaging/generated-contract metadata.
- **Contract surface:** the four delta specs in this change; ten-second `du` timeout plus ten-second cooperative fallback deadline and 100,000-entry budget; no partial project total on incomplete traversal; existing snapshot section/caching/redaction rules; environment storage ownership; generated-client determinism; runtime action/publication behavior; Starlette Admin auth/session/read-only/write-only/CAS guarantees; optional dashboard dependencies.
- **Definition of done / evidence:** every delta scenario has a focused automated assertion or mapped independent browser case; `du` missing/failure/timeout and synthetic below/at-budget/deadline fallback cases prove finite traversal and no partial project total; main/worktree totals are separate and path-free; time has no visible seconds but retains exact `dateTime`; running filter/count/empty state are correct without extra fetches; `/admin/` and five built-in labels work; GitLab detail and multi-host identities work without secret disclosure; generated artifacts are current; required product/CI gates pass on the exact candidate; pinned scoped OpenSpec strict validation passes and the candidate adds no repository-wide finding versus the approved-base diagnostic; numbered-slot images and assertions are attached/read back; cleanup is proven.
- **Parallel-safety rationale:** this is the sole all-covering WP. There are no sibling write zones, dependencies, stages, frontiers, or DAG coordination.

## Coverage audit

Every task in `tasks.md` is assigned exactly once to `WP-MYL-554-01-ui-fixes`. Automated checks remain owned by the Implementer; independent live browser execution remains the Tester gate described above. Any product-scope expansion, new dependency, changed storage ownership rule, custom Admin UI, or required shared-infrastructure mutation requires a planning revision and re-estimation.
