## Delivery contract

- Change: `add-browser-session-login`
- Planning revision: R2 on the exact OpenSpec-only commit named in the planning handoff.
- Approved base: `origin/main` at the base SHA recorded in that handoff.
- Delivery mode: `dag`. The authoritative `Estimate, hours` issue property is above the single-WP threshold, and the graph contains genuine parallel fronts with disjoint primary write zones.
- Scope authority: `proposal.md`, `design.md`, delta specs, and `tasks.md`. This file maps every task exactly once and does not replace requirements.
- Estimate authority: the root planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; this repository artifact intentionally contains no duplicated numeric total.
- Implementation parent/children SHALL copy the change ID and exact planning commit; live status and assignees remain in Multica, not this file.

## Estimate evidence

Confidence is **medium**. The current code provides strong, inspected analogues for immutable `run_shell_script_command()` snapshots and secret-config cleanup, `RuntimeController` owner resolution, typed msgspec/OpenAPI generation, same-origin CSRF mutation routes, deterministic Caddy route rendering/reload rollback, generated React clients, and source-backed real-Odoo evidence packaging. The estimate also includes the new closed SID handoff, atomic ticket state, exact proxy boundary, UI/codegen work, deterministic and live verification, documentation, and normal review/fix iterations.

The principal coupled uncertainties are compatibility with Odoo 19 session-store/auth extensions, safe best-effort cleanup after a saved session, trusted forwarded-header composition across supported proxy modes, and real browser cookie/navigation behavior. The estimate is for one experienced developer familiar with the stack, measured as active developer-hours from the recorded base to acceptance; unattended CI time, approval queues, and external waiting are excluded. Tests were not run to produce the estimate. The authoritative weighted/minimum/maximum values remain only in the root issue properties.

## Ponytail and E2E gates

`ponytail full` retained only the SDK primitives, closed SID handoff, bounded in-memory ticket store, exact consume transport/route, and focused dialog. It rejected Redis, durable tickets, a daemon, new CLI leaf, Odoo addon, generic service/runner/auth frameworks, and unrelated packaging work.

`e2e_required=true`. Lower-level tests cannot prove that an actual browser receives a host-only cookie on the published instance host, Odoo accepts the session-store entry, `/web/session/get_session_info` reports the selected UID/database, two users retain different rights, or Caddy isolates instance hosts. The selected local verification is the smallest real cross-component walkthrough that closes those gaps. Planning does not execute it.

Test repository: use the project description's GitLab test repository. Task 6.1 SHALL add these paths to that repository; the plan does not claim they already exist:

- `scripts/e2e/open_as_fixture.py`: `seed`, `reset`, `inspect`, and `cleanup` subcommands for synthetic users, rights, MFA-compatible state, publication state, and safe session counts.
- `scripts/e2e/open_as_probe.py`: `run --case <case-id>` driver for replay, expiry, concurrency, restart, origin/host/direct-backend, runtime/database/publication drift, and user-ineligible probes; it writes only sanitized assertions.
- `tests/e2e/fixtures/open_as_users.json`: non-personal user identities and deterministic distinct access-right fixtures.

The existing wrapper is `scripts/agent_checkout.py` in the reference test checkout. The Tester SHALL set explicit non-secret variables and keep diagnostics outside both Git worktrees:

```bash
export NUMBERED_PROJECT="$HOME/e2e/odcli/numbered-project-e2e"
export REFERENCE_CHECKOUT="$HOME/e2e/odcli/binta-e2e-reference"
export CHECKOUT_PARENT="$HOME/e2e/odcli/agent-checkouts"
export TEST_REPO_SHA='<exact pushed test-repository commit>'
export BASELINE_SHA='f0918da2d36b59c35cf0a949efc58744d1efb7f9'
export PRODUCT_SHA='<exact candidate product SHA>'
export BASELINE_HTTP_PORT='<free dedicated port>'
export CANDIDATE_HTTP_PORT='<different free dedicated port>'
export BASELINE_DATABASE='<unique disposable baseline database>'
export CANDIDATE_DATABASE='<unique disposable candidate database>'
export EVIDENCE_DIR='<private directory outside every Git checkout>'
mkdir -p "$EVIDENCE_DIR"
```

Create equivalent baseline and candidate numbered slots. The baseline exists only for changed-UI comparison; all behavioral cases use the candidate slot:

```bash
python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" create \
  --project "$NUMBERED_PROJECT" \
  --reference-checkout "$REFERENCE_CHECKOUT" \
  --checkout-parent "$CHECKOUT_PARENT" \
  --reference-sha "$TEST_REPO_SHA" \
  --sdk-sha "$BASELINE_SHA" \
  --http-port "$BASELINE_HTTP_PORT" \
  --target-database "$BASELINE_DATABASE" > "$EVIDENCE_DIR/baseline-slot.json"

python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" create \
  --project "$NUMBERED_PROJECT" \
  --reference-checkout "$REFERENCE_CHECKOUT" \
  --checkout-parent "$CHECKOUT_PARENT" \
  --reference-sha "$TEST_REPO_SHA" \
  --sdk-sha "$PRODUCT_SHA" \
  --http-port "$CANDIDATE_HTTP_PORT" \
  --target-database "$CANDIDATE_DATABASE" > "$EVIDENCE_DIR/candidate-slot.json"

export BASELINE_SLOT_ID="$(jq -er .identifier "$EVIDENCE_DIR/baseline-slot.json")"
export BASELINE_CHECKOUT="$(jq -er .checkout "$EVIDENCE_DIR/baseline-slot.json")"
export BASELINE_ENV_ID="$(jq -er .environment_id "$EVIDENCE_DIR/baseline-slot.json")"
export CANDIDATE_SLOT_ID="$(jq -er .identifier "$EVIDENCE_DIR/candidate-slot.json")"
export CANDIDATE_CHECKOUT="$(jq -er .checkout "$EVIDENCE_DIR/candidate-slot.json")"
export CANDIDATE_ENV_ID="$(jq -er .environment_id "$EVIDENCE_DIR/candidate-slot.json")"
```

Seed the same fixture in each slot and register failure-safe cleanup before browser work:

```bash
python3 "$BASELINE_CHECKOUT/scripts/e2e/open_as_fixture.py" seed \
  --project "$BASELINE_CHECKOUT" --environment-id "$BASELINE_ENV_ID" \
  --fixture "$BASELINE_CHECKOUT/tests/e2e/fixtures/open_as_users.json" \
  --output "$EVIDENCE_DIR/baseline-fixture.json"
python3 "$CANDIDATE_CHECKOUT/scripts/e2e/open_as_fixture.py" seed \
  --project "$CANDIDATE_CHECKOUT" --environment-id "$CANDIDATE_ENV_ID" \
  --fixture "$CANDIDATE_CHECKOUT/tests/e2e/fixtures/open_as_users.json" \
  --output "$EVIDENCE_DIR/candidate-fixture.json"

cleanup_open_as_slots() {
  local cleanup_failed=0
  python3 "$CANDIDATE_CHECKOUT/scripts/e2e/open_as_fixture.py" cleanup \
    --project "$CANDIDATE_CHECKOUT" --environment-id "$CANDIDATE_ENV_ID" \
    --output "$EVIDENCE_DIR/candidate-fixture-cleanup.json" || cleanup_failed=1
  python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" remove "$CANDIDATE_SLOT_ID" \
    --project "$NUMBERED_PROJECT" --checkout-parent "$CHECKOUT_PARENT" || cleanup_failed=1
  python3 "$BASELINE_CHECKOUT/scripts/e2e/open_as_fixture.py" cleanup \
    --project "$BASELINE_CHECKOUT" --environment-id "$BASELINE_ENV_ID" \
    --output "$EVIDENCE_DIR/baseline-fixture-cleanup.json" || cleanup_failed=1
  python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" remove "$BASELINE_SLOT_ID" \
    --project "$NUMBERED_PROJECT" --checkout-parent "$CHECKOUT_PARENT" || cleanup_failed=1
  return "$cleanup_failed"
}
trap cleanup_open_as_slots EXIT INT TERM
```

Before each case, run `open_as_fixture.py reset --project "$CANDIDATE_CHECKOUT" --environment-id "$CANDIDATE_ENV_ID" --case BL-XX --output "$EVIDENCE_DIR/bl-xx-reset.json"`; after each case run the same arguments with `inspect` to prove expected user/session/publication state. BL-04 and BL-05 additionally run `open_as_probe.py run --case <subcase> --project "$CANDIDATE_CHECKOUT" --environment-id "$CANDIDATE_ENV_ID" --output "$EVIDENCE_DIR/<case>-<subcase>.json"`. The driver SHALL obtain and consume tickets internally, never print them, and reject output containing a credential canary. Routine completion SHALL run `trap - EXIT INT TERM; cleanup_open_as_slots`; any non-zero cleanup command blocks acceptance. Then `python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" list --limit 100` SHALL show neither identifier, and this exact catalogue assertion SHALL return zero:

```bash
test "$(docker compose -p odcli-e2e-pg \
  -f "$HOME/e2e/odcli/e2e-postgres/compose.yml" \
  --env-file "$HOME/e2e/odcli/e2e-postgres/postgres.env" \
  exec -T postgres psql -At \
  -v baseline="$BASELINE_DATABASE" -v candidate="$CANDIDATE_DATABASE" \
  -c "SELECT count(*) FROM pg_database WHERE datname IN (:'baseline', :'candidate')")" = 0
```

## Verification matrix

| Case | Requirement / scenario | Gap and local decision | Public boundary | Repeatable script / CI disposition | Independent Tester actions | Assertions | Multica-only artifacts / images |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BL-01 | Eligible discovery; Open as action/dialog | Live walkthrough is required for changed visible UI; SDK/HTTP semantics remain covered deterministically | `POST /api/v1/odoo/users/query`; panel card/dialog | `open_as_fixture.py reset/inspect --case BL-01`; normal CI runs SDK/dashboard/React tests | At 1440×900, `en-US`, light scheme and clean profile, open the same owner card on baseline and candidate; on candidate open the dialog, search, paginate, and select default | equivalent fixture/card state; baseline has existing Open Odoo only; candidate action/dialog, search/page/default are usable; no poll spawn | `bl01-baseline-card.png`, `bl01-candidate-card.png`, `bl01-candidate-dialog.png`; commands/exits and fixture hashes |
| BL-02 | Successful consume and real identity | Local E2E required: only browser+proxy+Odoo proves cookie/session acceptance | ticket operation; form `POST /_odcli/auth/consume`; Odoo session-info | `open_as_fixture.py reset/inspect --case BL-02`; product CI covers deterministic contracts | In candidate clean profile, open as user A, inspect safe session-info and one allowed action; reset browser/session, repeat as user B and observe denied/changed action | expected UID/db, no `su`, distinct rights, fixed `/odoo`, shared same-host behavior | `bl02-user-a-odoo.png`, `bl02-user-b-odoo.png`; safe session-info text; product/test SHA |
| BL-03 | Host-only isolation | Local E2E required: cookie scoping is browser behavior | two published instance hosts | `open_as_fixture.py reset/inspect --case BL-03`; lower-level cookie/Caddy tests | Publish the fixture's two isolated hosts, open as on host A, then inspect host B with no prior host-B session | no `Domain`; host B receives no selected session; host A stays selected | `bl03-host-a-selected.png`, `bl03-host-b-isolated.png`; sanitized publication state |
| BL-04 | Replay, expiry, concurrency, restart | Focused local API/proxy E2E required for atomic store integration; images add no evidence | consume form response/error | `open_as_fixture.py reset/inspect --case BL-04`; `open_as_probe.py run --case replay|expiry|concurrency|restart`; fake-clock/thread tests in CI | Run each probe subcase through the published proxy; restart only the isolated candidate backend for `restart` | one creator maximum; terminal 410; no second session; runtime unchanged | sanitized per-subcase JSON outside Git, commands/exits, correlation IDs; no credential/body capture |
| BL-05 | Origin/host/proxy/runtime/DB/user mismatch | Focused live proxy E2E required for deployed trust composition | consume plus publication route | `open_as_fixture.py reset/inspect --case BL-05`; `open_as_probe.py run --case invalid-origin|direct-backend|wrong-host|runtime-changed|database-changed|publication-changed|user-ineligible`; header/model/Caddy CI tests | Run one controlled mutation per fresh ticket, resetting fixture state between subcases | refusal class; no session/start/reconfigure; ticket terminal where specified | sanitized per-subcase JSON outside Git and safe route/state before/after; no private hostname in Git |
| BL-06 | Logout, MFA coexistence, unsupported extension | Live logout/MFA behavior is required. Unsupported-extension behavior is lower-level/source-backed because no safe repeatable incompatible live extension fixture is part of the MVP | Odoo logout/session-info and consume | `open_as_fixture.py reset/inspect --case BL-06`; source-backed `tests/integration/real_odoo/test_browser_session_login.py -k 'unsupported_session_extension'` | Select the compatible MFA-configured fixture user, verify audit/MFA state, perform normal logout, then retry safe session-info; do not install an extension live | old cookie unauthorized; MFA settings unchanged; audit says impersonation; source-backed unsupported case fails closed | `bl06-before-logout.png`, `bl06-after-logout.png`; safe audit/assertion text; CI link for unsupported case |
| BL-07 | Secret handoff/projections/cleanup | No extra browser path: deterministic security tests plus independent file/state inspection are sufficient | SDK command/events/log/audit/evidence | `open_as_fixture.py reset/inspect --case BL-07`; exhaustive canary tests in normal/real-Odoo CI | Inspect owned temp root before/after one success and one forced failure; scan every permitted evidence output and run final slot/database cleanup checks | no temp secret; no canary/ticket/SID/token outside the issuance response/transient form; best-effort orphan cleanup result; no remaining slot/database | hashes and scan summary only; raw diagnostics attached only if secret-free |

Visible output changes are browser UI only. BL-01 SHALL capture the genuine baseline and candidate panel at the same 1440×900 viewport, `en-US` locale, light color scheme, owner fixture, publication state, card position, and clean browser profile after `reset --case BL-01`; it SHALL additionally capture the candidate dialog open with the same fixture. BL-02, BL-03, and BL-06 SHALL capture the post-login, host-isolation, and before/after logout states named in the matrix. Baseline and candidate use separate fresh numbered slots pinned to their exact product SHAs, the same test-repository SHA and equivalent seeded data. Missing any named image, unequal fixture state, unverified product SHA, or credential-bearing capture SHALL block acceptance. Do not manufacture renders. Mac terminal before/after PNGs are not required because CLI output does not change; if diagnostic terminal text is nevertheless cited, preserve the actual text and render it with the bundled `scripts/render_terminal.swift` from the testing tooling checkout, declaring ANSI/TTY limitations.

## Readiness, attempts, and evidence

Before a live attempt, the Tester SHALL prove: exact baseline/candidate product SHAs and planning revision; exact pushed test-repository SHA; isolated numbered slots/databases/ports; healthy dedicated PostgreSQL on `127.0.0.1:15434`; compatible Odoo 19 filesystem session store; focused deterministic tests and required CI disposition; two synthetic users with non-personal data and distinct rights; proxy/panel readiness; clean browser profile; secret-safe capture settings; and cleanup command availability. The readiness command sequence is:

```bash
docker compose -p odcli-e2e-pg \
  -f "$HOME/e2e/odcli/e2e-postgres/compose.yml" \
  --env-file "$HOME/e2e/odcli/e2e-postgres/postgres.env" ps --format json
docker compose -p odcli-e2e-pg \
  -f "$HOME/e2e/odcli/e2e-postgres/compose.yml" \
  --env-file "$HOME/e2e/odcli/e2e-postgres/postgres.env" \
  exec -T postgres pg_isready
test "$(git -C "$BASELINE_CHECKOUT" rev-parse HEAD)" = "$TEST_REPO_SHA"
test "$(git -C "$CANDIDATE_CHECKOUT" rev-parse HEAD)" = "$TEST_REPO_SHA"
python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" inspect "$BASELINE_SLOT_ID" \
  > "$EVIDENCE_DIR/baseline-slot-inspect.json"
python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" inspect "$CANDIDATE_SLOT_ID" \
  > "$EVIDENCE_DIR/candidate-slot-inspect.json"
jq -e --arg sha "$BASELINE_SHA" '.healthy == true and .sha == $sha' \
  "$EVIDENCE_DIR/baseline-slot-inspect.json"
jq -e --arg sha "$PRODUCT_SHA" '.healthy == true and .sha == $sha' \
  "$EVIDENCE_DIR/candidate-slot-inspect.json"
python3 "$BASELINE_CHECKOUT/scripts/e2e/open_as_fixture.py" inspect \
  --project "$BASELINE_CHECKOUT" --environment-id "$BASELINE_ENV_ID" --case readiness \
  --output "$EVIDENCE_DIR/baseline-readiness.json"
python3 "$CANDIDATE_CHECKOUT/scripts/e2e/open_as_fixture.py" inspect \
  --project "$CANDIDATE_CHECKOUT" --environment-id "$CANDIDATE_ENV_ID" --case readiness \
  --output "$EVIDENCE_DIR/candidate-readiness.json"
```

The slot manifests and `inspect` output SHALL prove their SDK SHA, checkout identity, copied database, port, compatible session store, ready runtime, current publication, live panel health, fixture revision, and cleanup target without printing private configuration or credentials. A failed readiness gate consumes no live attempt.

The first consolidated revision receives the full walkthrough. A later product SHA requires an impact assessment and rerun only of affected cases; uncertainty leaves them unverified. Failed live behavior is diagnosed with deterministic checks before a new attempt. PASS always names the tested product SHA and fixture revision.

Tester evidence SHALL be posted in the final Multica comment with read-back and include MR/branch/commit/file links for normal test-repository results, exact safe commands/exit codes, safe assertions, hashes/provenance, image attachments, and cleanup result. Real logs, diagnostics, JSON, screenshots, commands, hashes, and assertions stay in Multica and are not committed unless generated content is itself the tested product output. Do not close/merge the evidence MR or delete its branch without separate instruction. Never include tickets, SID/session tokens, password/API key data, Cookie/Set-Cookie values, form bodies, private hostnames, or personal data.

## Work packages

### WP-01 — SDK browser-session foundation

- `stage: 1`
- Tasks: 1.1, 1.2, 1.3, 1.4, 1.5.
- Deliverable: public typed user/session primitives, fixed scripts, command siblings, closed SID transport, and focused SDK/security tests.
- Owned responsibility: SDK public models/exports and instance command implementation; fixed shell sources; related unit fixtures/tests. Critical shared contract: safe models and command result consumed by WP-02.
- Contract surface: immutable inputs/results, error taxonomy, runtime/database/session-store compatibility, private SID accessor semantics, `internal/proc`/ActionStep/redaction rules.
- DoD/evidence: focused tests cover every task and secret projection, preview is inert, command sibling delegates once, and no dashboard dependency enters core imports.
- Parallel safety: can run with WP-03 because SDK/model files and publication route/resource files are disjoint; both consume contracts fixed by OpenSpec and neither consumes the other's deliverable.

### WP-02 — Dashboard ticket and consume boundary

- `stage: 2`
- `depends_on: [WP-01]`
- Tasks: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7.
- Deliverable: typed panel APIs, bounded ticket state machine, trusted consume boundary, SDK delegation, cookie redirect, audit, and focused HTTP/security tests.
- Owned responsibility: `http` models/router/controller/app composition and related tests; it consumes but does not alter WP-01 SDK contracts. Critical shared contract: OpenAPI models consumed by WP-04. WP-03 independently implements the OpenSpec-fixed route target and consumes no WP-02 artifact.
- Contract surface: operations/schemas/status codes, ticket lifecycle, proxy/origin/host validation, deadline/capacity/thread rules, cookie/redirect headers, sanitized audit.
- DoD/evidence: OpenAPI and HTTP tests prove success/failure/concurrency/restart and absence of secret values; direct backend and other instance-host APIs fail closed.
- Parallel safety: starts after the SDK contract is integrated; its HTTP write zone is disjoint from completed/concurrent downstream frontend and integration work only after their stated predecessors.

### WP-03 — Exact publication consume route

- `stage: 1`
- Tasks: 3.1, 3.2, 3.3.
- Deliverable: deterministic exact-path Caddy route to the captured dashboard endpoint with unchanged publication safety/recovery.
- Owned responsibility: publication route/resource models/rendering and focused publication/removal/monitor fixtures/tests; it does not edit FastAPI or React.
- Contract surface: exact method/path, authoritative forwarded fields, monitor endpoint capture, Basic Auth and upstream proxy behavior, no dashboard API exposure.
- DoD/evidence: renderer and reconciliation tests cover project/environment, methods, auth modes, unavailable monitor, deterministic bytes, validation/reload rollback.
- Parallel safety: can run with WP-01 because it consumes no SDK deliverable; its exact route grammar and monitor endpoint contract are authoritative in OpenSpec, and its publication write zone is disjoint from SDK/model files.

### WP-04 — Generated client and Open as UI

- `stage: 3`
- `depends_on: [WP-02]`
- Tasks: 4.1, 4.2, 4.3, 4.4.
- Deliverable: regenerated client plus tested Open as dialog/transient POST workflow while preserving Open Odoo.
- Owned responsibility: generated web client, React components/styles/tests, production frontend artifact expectations; no backend model edits.
- Contract surface: WP-02 OpenAPI operations/models/errors; exact ticket response; browser form and popup lifecycle.
- DoD/evidence: codegen check, frontend tests and production build prove gating, pagination, popup/form cleanup, sanitized errors, and existing action regression.
- Parallel safety: may run alongside WP-05 after WP-02; WP-05 owns integration/CI fixtures, not web source/tests except read-only execution.

### WP-05 — Integrated deterministic and source-backed coverage

- `stage: 3`
- `depends_on: [WP-02, WP-03]`
- Tasks: 5.1, 5.2.
- Deliverable: cross-boundary deterministic integration and source-backed real-Odoo CI coverage, including the lower-level unsupported-extension disposition.
- Owned responsibility: integration/real-Odoo fixtures/tests, CI workflow additions if needed, and secret registry/evidence packaging assertions. It may add directly related fixtures but does not rewrite active siblings' product source.
- Contract surface: completed SDK/HTTP/publication behavior, real-Odoo source/session APIs, existing CI evidence contract.
- DoD/evidence: integrated fakes and source-backed tests cover the assigned cases, including incompatible session-store/auth extension refusal and secret evidence packaging; applicable focused commands pass.
- Parallel safety: starts after backend and route contracts stabilize; can run with WP-04 because it owns integration/real-Odoo/CI fixtures while WP-04 owns generated web and React source/tests. Neither performs final repository-wide verification.

### WP-06 — Consolidated exact-revision verification

- `stage: 4`
- `depends_on: [WP-04, WP-05]`
- Tasks: 5.3, 5.4.
- Deliverable: one consolidated candidate revision that passes the complete required quality, generated-artifact, frontend, backend, packaging, real-Odoo, secret-projection, and process-boundary gates.
- Owned responsibility: read-only execution of repository checks plus directly related repair findings routed back to the owning completed WP scope; it does not create live E2E evidence or alter test-repository fixtures.
- Contract surface: all completed product/generated/test artifacts from WP-01 through WP-05 and the exact candidate SHA to be handed to independent acceptance.
- DoD/evidence: focused SDK/HTTP/publication/frontend tests, codegen check, dashboard build/tests, lint, types, packaging/smoke, applicable real-Odoo CI, and secret/process reviews all pass on the same published SHA; automatic local suites follow the workspace time limit.
- Parallel safety: fan-in successor; it starts only after UI and integrated coverage are complete, so repository-wide verification cannot race generated client or frontend changes.

### WP-07 — Independent numbered-slot acceptance and operator documentation

- `stage: 5`
- `depends_on: [WP-06]`
- Tasks: 6.1, 6.2, 6.3, 6.4.
- Deliverable: repeatable test-repository fixtures, independent real browser/proxy/Odoo evidence, cleanup, and final operator/limitation documentation.
- Owned responsibility: separate test-repository scripts/fixtures/MR, independent actions, Multica evidence/images/read-back, and final docs directly related to the workflow. Tester does not edit product CI tests.
- Contract surface: exact product SHA, numbered-slot wrapper/profile/reference checkout, actual Open as UX/API/proxy/session behavior, evidence publication rules.
- DoD/evidence: BL-01 through BL-07 are PASS or explicitly failed; required images and safe evidence are attached/read back; slot/database/service cleanup succeeds; evidence branch/MR remains available.
- Parallel safety: acceptance successor consumes WP-06's consolidated published SHA; no sibling remains active at its product or evidence write boundary.

## Topology and coverage checks

Stage mapping: `WP-01 -> 1`, `WP-03 -> 1`, `WP-02 -> 2`, `WP-04 -> 3`, `WP-05 -> 3`, `WP-06 -> 4`, `WP-07 -> 5`. Stages are consecutive. Every dependency points to a lower stage. Direct dependencies only are listed.

Parallel frontiers exist at stage 1 (`WP-01`, `WP-03`) and stage 3 (`WP-04`, `WP-05`), with disjoint primary write zones. Therefore the multi-WP DAG is permitted. Task coverage is complete and one-time: WP-01 owns 1.1–1.5; WP-02 owns 2.1–2.7; WP-03 owns 3.1–3.3; WP-04 owns 4.1–4.4; WP-05 owns 5.1–5.2; WP-06 owns 5.3–5.4; WP-07 owns 6.1–6.4.
