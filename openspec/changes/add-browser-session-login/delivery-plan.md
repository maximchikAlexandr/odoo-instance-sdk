## Delivery contract

- Change: `add-browser-session-login`
- Planning revision: R4 on the exact OpenSpec-only commit named in the planning handoff.
- Approved base: `origin/main` at the base SHA recorded in that handoff.
- Delivery mode: `single_wp_no_dag`. The authoritative `Estimate, hours` root property is at or below the topology threshold.
- Scope authority: `proposal.md`, `design.md`, delta specs, and `tasks.md`; this file maps every task exactly once.
- Estimate authority: root issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`. No numeric total is duplicated here.
- Implementation remains stopped until Plan Verifier accepts this revision and the existing human-review gate is resolved.

## Ponytail and estimate evidence

`ponytail full` keeps only the unavoidable cross-host path: bounded SDK user query, SDK session creation with private SID transfer, a tiny process-local expiring one-time map, exact instance-host consume POST, one Caddy branch, and one dialog. It removes server-side pagination/admin defaulting, capacity and multi-worker machinery, a dedicated audit subsystem, exhaustive drift/MFA certification, the probe framework, and DAG orchestration.

Estimate confidence is **medium** for one experienced developer familiar with the stack. Strong inspected analogues exist in `run_shell_script_command()`, owner-only secret cleanup, `RuntimeController`, typed msgspec/OpenAPI routes, the single-worker executor, owned Caddy rendering/rollback, generated React clients, dashboard tests, and the source-backed real-Odoo job. The main uncertainty is the exact Odoo 19 filesystem-session API and cleanup behavior; no test or POC was run for estimation. Authoritative scenario totals are stored only in the root properties.

## Minimal E2E contract

`e2e_required=true` because lower-level tests cannot prove that the real instance host sets and Odoo accepts the browser cookie. Live scope is limited to the changed UI and one successful selected-user lifecycle; replay, expiry, malformed Origin/Host/proxy, and unsupported-store behavior are cheaper and sufficient as deterministic tests.

Test repository: the GitLab repository from the project description. Task 4.2 adds only `scripts/e2e/open_as_fixture.py` with `seed`, `reset`, `inspect`, and `cleanup` subcommands for two synthetic users, distinct IDs, publication state, safe session counts, and idempotent cleanup. It does not add a probe framework or diagnostic JSON to Git.

### Repeatable isolated setup

Use separate baseline and candidate numbered slots with the same test-repository revision and equivalent fixture. The baseline exists only for visible UI comparison.

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

Register failure-safe cleanup before the first create. The wrapper removes its own partial create failure before a valid manifest; each valid identifier and successful seed is registered immediately. Original failure status wins; cleanup failure makes a successful run fail.

```bash
set -euo pipefail
BASELINE_CREATED=0; CANDIDATE_CREATED=0
BASELINE_SEEDED=0; CANDIDATE_SEEDED=0

cleanup_open_as() {
  local failed=0
  if [ "${CANDIDATE_SEEDED:-0}" = 1 ]; then
    python3 "$CANDIDATE_CHECKOUT/scripts/e2e/open_as_fixture.py" cleanup \
      --project "$CANDIDATE_CHECKOUT" --environment-id "$CANDIDATE_ENV_ID" \
      --output "$EVIDENCE_DIR/candidate-cleanup.json" || failed=1
  fi
  if [ "${CANDIDATE_CREATED:-0}" = 1 ] && [ -n "${CANDIDATE_SLOT_ID:-}" ]; then
    python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" remove "$CANDIDATE_SLOT_ID" \
      --project "$NUMBERED_PROJECT" --checkout-parent "$CHECKOUT_PARENT" || failed=1
  fi
  if [ "${BASELINE_SEEDED:-0}" = 1 ]; then
    python3 "$BASELINE_CHECKOUT/scripts/e2e/open_as_fixture.py" cleanup \
      --project "$BASELINE_CHECKOUT" --environment-id "$BASELINE_ENV_ID" \
      --output "$EVIDENCE_DIR/baseline-cleanup.json" || failed=1
  fi
  if [ "${BASELINE_CREATED:-0}" = 1 ] && [ -n "${BASELINE_SLOT_ID:-}" ]; then
    python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" remove "$BASELINE_SLOT_ID" \
      --project "$NUMBERED_PROJECT" --checkout-parent "$CHECKOUT_PARENT" || failed=1
  fi
  return "$failed"
}
cleanup_on_exit() {
  local original=$? cleanup_status=0
  cleanup_open_as || cleanup_status=$?
  trap - EXIT INT TERM
  if [ "$original" -ne 0 ]; then exit "$original"; fi
  exit "$cleanup_status"
}
trap cleanup_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" create \
  --project "$NUMBERED_PROJECT" --reference-checkout "$REFERENCE_CHECKOUT" \
  --checkout-parent "$CHECKOUT_PARENT" --reference-sha "$TEST_REPO_SHA" \
  --sdk-sha "$BASELINE_SHA" --http-port "$BASELINE_HTTP_PORT" \
  --target-database "$BASELINE_DATABASE" > "$EVIDENCE_DIR/baseline-slot.json"
export BASELINE_SLOT_ID="$(jq -er .identifier "$EVIDENCE_DIR/baseline-slot.json")"; BASELINE_CREATED=1
export BASELINE_CHECKOUT="$(jq -er .checkout "$EVIDENCE_DIR/baseline-slot.json")"
export BASELINE_ENV_ID="$(jq -er .environment_id "$EVIDENCE_DIR/baseline-slot.json")"

python3 "$REFERENCE_CHECKOUT/scripts/agent_checkout.py" create \
  --project "$NUMBERED_PROJECT" --reference-checkout "$REFERENCE_CHECKOUT" \
  --checkout-parent "$CHECKOUT_PARENT" --reference-sha "$TEST_REPO_SHA" \
  --sdk-sha "$PRODUCT_SHA" --http-port "$CANDIDATE_HTTP_PORT" \
  --target-database "$CANDIDATE_DATABASE" > "$EVIDENCE_DIR/candidate-slot.json"
export CANDIDATE_SLOT_ID="$(jq -er .identifier "$EVIDENCE_DIR/candidate-slot.json")"; CANDIDATE_CREATED=1
export CANDIDATE_CHECKOUT="$(jq -er .checkout "$EVIDENCE_DIR/candidate-slot.json")"
export CANDIDATE_ENV_ID="$(jq -er .environment_id "$EVIDENCE_DIR/candidate-slot.json")"

python3 "$BASELINE_CHECKOUT/scripts/e2e/open_as_fixture.py" seed \
  --project "$BASELINE_CHECKOUT" --environment-id "$BASELINE_ENV_ID" \
  --output "$EVIDENCE_DIR/baseline-seed.json"; BASELINE_SEEDED=1
python3 "$CANDIDATE_CHECKOUT/scripts/e2e/open_as_fixture.py" seed \
  --project "$CANDIDATE_CHECKOUT" --environment-id "$CANDIDATE_ENV_ID" \
  --output "$EVIDENCE_DIR/candidate-seed.json"; CANDIDATE_SEEDED=1
```

Before each case run candidate `reset` and after it run `inspect` with `--project`, `--environment-id`, `--case`, and a private `--output` path. UI-01 runs the same reset/inspect commands on baseline and candidate. Readiness requires healthy PostgreSQL, exact SDK/test SHAs from wrapper `inspect`, compatible filesystem session store, live panel/publication, clean browser profile, and focused deterministic checks. Routine completion runs `trap - EXIT INT TERM; cleanup_open_as`; any cleanup failure blocks acceptance. Wrapper `list --limit 100` and the dedicated PostgreSQL catalogue query SHALL then prove both slots/databases absent.

### Case and CI mapping

| Case | Requirement / gap | Independent action and assertion | Deterministic CI disposition | Multica-only evidence |
| --- | --- | --- | --- | --- |
| UI-01 | Changed Open as browser UI | Reset/inspect both slots; at 1440×900, `en-US`, light scheme and clean profiles capture the same owner card, then open the candidate dialog and search/select the fixture user | `dashboard-tests`: `make web-codegen-check && (cd src/odoo_instance_sdk/web && npm test -- App.test.tsx generated.contract.test.ts && npm run build)` | `ui01-baseline-card.png`, `ui01-candidate-card.png`, `ui01-candidate-dialog.png`, equal fixture hashes |
| LOGIN-01 | Real cookie/session boundary cannot be proven lower-level | In candidate choose the non-default fixture user, inspect safe session-info UID/database, open the neighboring fixture host without a session, log out, and confirm old session-info is unauthorized | focused mappings below plus full source-backed job | `login01-selected-odoo.png`, `login01-neighbor-isolated.png`, `login01-after-logout.png`, safe commands/assertions and cleanup |

Missing named images, unequal UI fixture state, wrong revision, credential-bearing capture, failed host isolation/logout, or failed cleanup blocks acceptance.

| Task | Existing workflow/job | Planned path / fixture | Focused command and assertion |
| --- | --- | --- | --- |
| 1.2 | `.github/workflows/ci.yml` → `offline-tests` | add `tests/unit/test_browser_session_login.py`; reuse instance/runtime and temp-path fixtures | `uv run pytest -q -o addopts='' tests/unit/test_browser_session_login.py` proves filtering, fixed source, supported session creation, private-file cleanup and zero SID projections |
| 2.3 | `.github/workflows/ci.yml` → `dashboard-tests`; `offline-tests` for publication | add `tests/unit/test_http_browser_login.py`; extend `tests/unit/test_publication.py` | `uv run pytest -q -o addopts='' -m dashboard tests/unit/test_http_browser_login.py` and `uv run pytest -q -o addopts='' tests/unit/test_publication.py -k consume` prove CSRF, one-shot expiry/replay, host/Origin/proxy checks and exact routing |
| 3.2 | `.github/workflows/ci.yml` → `dashboard-tests` | extend `App.test.tsx` and `generated.contract.test.ts` | the UI-01 command proves generated contracts, transient form cleanup, duplicate prevention, sanitized errors and unchanged Open Odoo |
| 4.1 | `.github/workflows/ci.yml` → `offline-tests`; `.github/workflows/real-odoo-full.yml` → `.github/workflows/real-odoo-tier.yml` full job | add `tests/integration/test_browser_session_login.py` and `tests/integration/real_odoo/test_browser_session_login.py`; reuse existing source bootstrap/session/evidence fixtures | focused pytest paths prove fake boundary; the existing full command over `tests/integration/real_odoo` proves real Odoo session-info/logout/unsupported-store behavior and credential-canary rejection |

Tester publishes exact product/test revisions, MR/commit/file links, safe commands/exit codes, assertions, named images, and cleanup result with attachment read-back. Raw logs, screenshots, diagnostic JSON, hashes, and private hostnames stay out of Git; the evidence branch/MR remains available and is not merged/closed/deleted without separate instruction.

## Work package

### WP-01 — Minimal selected-user browser login

- Tasks: 1.1–1.2, 2.1–2.3, 3.1–3.2, 4.1–4.3.
- Deliverable: the complete minimal Open as flow from bounded eligible-user query through one-time instance-host consume, selected-user Odoo session, focused deterministic coverage, isolated browser proof, and operator limitations.
- Owned responsibility: SDK instance models/commands and related private handoff; FastAPI models/router/controller/OpenAPI; existing publication renderer; generated client and React card/dialog; directly related unit/integration/source-backed tests, fixtures, docs and test-repository helper/evidence contract.
- Contract surface: typed user-query/handoff panel operations; exact consume form route; host-only Odoo cookie and fixed redirect; existing owned Caddy route file; generated client; unchanged Open Odoo action.
- DoD/evidence: all tasks complete; strict OpenSpec traceability retained; focused and applicable repository jobs pass on one exact published revision; required browser images/assertions/cleanup are attached and read back; no credential appears in retained projections.
- Parallel-safety: not applicable. This is the sole all-covering WP and carries no `depends_on`, stage, frontier, or DAG orchestration.

Task coverage is complete and one-time: WP-01 owns every task in `tasks.md`.
