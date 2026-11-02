## Delivery contract

- Change: `add-browser-session-login`
- Planning revision: R1 on the exact OpenSpec-only commit named in the planning handoff.
- Approved base: `origin/main` at the base SHA recorded in that handoff.
- Delivery mode: `dag`. The authoritative `Estimate, hours` issue property is above the single-WP threshold, and the graph contains genuine parallel fronts with disjoint primary write zones.
- Scope authority: `proposal.md`, `design.md`, delta specs, and `tasks.md`. This file maps every task exactly once and does not replace requirements.
- Estimate authority: the root planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; this repository artifact intentionally contains no duplicated numeric total.
- Implementation parent/children SHALL copy the change ID and exact planning commit; live status and assignees remain in Multica, not this file.

## Ponytail and E2E gates

`ponytail full` retained only the SDK primitives, closed SID handoff, bounded in-memory ticket store, exact consume transport/route, and focused dialog. It rejected Redis, durable tickets, a daemon, new CLI leaf, Odoo addon, generic service/runner/auth frameworks, and unrelated packaging work.

`e2e_required=true`. Lower-level tests cannot prove that an actual browser receives a host-only cookie on the published instance host, Odoo accepts the session-store entry, `/web/session/get_session_info` reports the selected UID/database, two users retain different rights, or Caddy isolates instance hosts. The selected local verification is the smallest real cross-component walkthrough that closes those gaps. Planning does not execute it.

Test repository: use the project description's GitLab test repository. The Tester SHALL use its bundled numbered-slot wrapper with `--project ~/e2e/odcli/numbered-project-e2e`, `--reference-checkout ~/e2e/odcli/binta-e2e-reference`, a free HTTP port, unique target database, and the exact implementation SDK SHA. The slot SHALL be named `odcli-N`; the reference checkout, legacy source checkout, existing databases, Colima, and unrelated containers SHALL remain untouched.

## Verification matrix

| Case | Requirement / scenario | Gap and local decision | Public boundary | Repeatable script / CI disposition | Independent Tester actions | Assertions | Multica-only artifacts / images |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BL-01 | Eligible discovery; Open as dialog | No live E2E: deterministic SDK/HTTP/React coverage is sufficient | `POST /api/v1/odoo/users/query` schemas | Implementer adds focused SDK/dashboard/React tests in normal CI | Inspect two-user fixture and dialog search/page/default without exposing credentials | eligibility, ordering, cursor, no poll spawn, generated client | command/exit summary; no image because existing list styling is not an acceptance output |
| BL-02 | Successful consume and real identity | Local E2E required: only browser+proxy+Odoo proves cookie/session acceptance | ticket operation; form `POST /_odcli/auth/consume`; Odoo session-info | Test-repo script prepares two eligible fixture users; product CI covers deterministic contracts | Clean browser profile; open as user A, inspect safe session-info and one allowed action; repeat as user B and observe denied/changed action | expected UID/db, no `su`, distinct rights, fixed `/odoo`, shared same-host behavior | baseline normal-login/open page and candidate Open-as `/odoo` PNGs; safe session-info text; product/test SHA |
| BL-03 | Host-only isolation | Local E2E required: cookie scoping is browser behavior | two published instance hosts | Lower-level cookie/Caddy tests plus the BL-02 slot lifecycle | Publish two isolated hosts, open as on one, inspect other with clean/no prior session | no `Domain`; second host receives no selected session | two sanitized browser PNGs showing host and logged-in/logged-out state; no cookie values |
| BL-04 | Replay, expiry, concurrency, restart | Focused local API/proxy E2E required for atomic store integration; browser image adds no value | consume form response/error | Implementer adds fake-clock/thread concurrency and restart tests; test repo provides safe request driver | Issue fresh tickets for each subcase; send controlled concurrent POSTs through proxy; restart backend only in isolated slot | one creator maximum; terminal 410; no second session; runtime unchanged | sanitized status/assertion log, commands/exits, correlation IDs; no credential/body capture |
| BL-05 | Origin/host/proxy/runtime/DB/user mismatch | Focused live proxy E2E required for deployed trust composition | consume plus publication route | Implementer adds header/model/Caddy contract tests; test repo driver mutates one bound fact at a time | Exercise invalid Origin, direct backend, wrong host, changed publication/runtime/eligibility using isolated fixtures | refusal class; no session/start/reconfigure; ticket terminal where specified | safe response classifications, route/state before/after; no private hostname in public Git |
| BL-06 | Logout, MFA coexistence, unsupported extension | Real Odoo behavior required; keep setup minimal | Odoo logout/session-info and consume | Implementer extends source-backed CI for session/logout and extension probes; test repo owns isolated fixture config | Logout and retry safe session-info; select MFA-configured compatible user; enable declared incompatible session fixture only if repeatable | old cookie unauthorized; MFA settings unchanged; audit says impersonation; unsupported fails closed | safe audit excerpts and assertions; browser PNG after logout; no auth secrets |
| BL-07 | Secret handoff/projections/cleanup | No live browser gap for projections; deterministic security tests plus independent file/state inspection | SDK command/events/log/audit/evidence | Implementer adds exhaustive canary tests to normal and real-Odoo CI | Inspect temp root before/after one success and one forced failure; scan allowed artifacts | no temp secret; no canary/ticket/SID/token in any projection; best-effort orphan cleanup result | hashes and scan summary only; raw diagnostics attached only if secret-free |

Visible output changes are browser UI only. Capture genuine baseline/candidate screenshots for BL-02, host-isolation screenshots for BL-03, and logout result for BL-06 at equivalent viewport/locale with a clean profile. Do not manufacture renders. Mac terminal before/after PNGs are not required because CLI output does not change; if diagnostic terminal text is nevertheless cited, preserve the actual text and render it with bundled `scripts/render_terminal.swift`, declaring ANSI/TTY limitations.

## Readiness, attempts, and evidence

Before a live attempt, the Tester SHALL prove: exact product SHA and planning revision; isolated numbered slot/database/port; healthy dedicated PostgreSQL on `127.0.0.1:15434`; compatible Odoo 19 filesystem session store; focused deterministic tests and required CI disposition; two synthetic users with non-personal data and distinct rights; proxy/panel readiness; clean browser profile; secret-safe capture settings; and cleanup command availability. A failed readiness gate consumes no live attempt.

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
- Parallel safety: foundation predecessor; no sibling writes before its contract is integrated.

### WP-02 — Dashboard ticket and consume boundary

- `stage: 2`
- `depends_on: [WP-01]`
- Tasks: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7.
- Deliverable: typed panel APIs, bounded ticket state machine, trusted consume boundary, SDK delegation, cookie redirect, audit, and focused HTTP/security tests.
- Owned responsibility: `http` models/router/controller/app composition and related tests; it consumes but does not alter WP-01 SDK contracts. Critical shared contract: OpenAPI models consumed by WP-04 and monitor endpoint consumed by WP-03.
- Contract surface: operations/schemas/status codes, ticket lifecycle, proxy/origin/host validation, deadline/capacity/thread rules, cookie/redirect headers, sanitized audit.
- DoD/evidence: OpenAPI and HTTP tests prove success/failure/concurrency/restart and absence of secret values; direct backend and other instance-host APIs fail closed.
- Parallel safety: after WP-01 it can run alongside WP-03 because it owns HTTP files while WP-03 owns publication files; agreed endpoint/route contract is fixed by OpenSpec.

### WP-03 — Exact publication consume route

- `stage: 2`
- `depends_on: [WP-01]`
- Tasks: 3.1, 3.2, 3.3.
- Deliverable: deterministic exact-path Caddy route to the captured dashboard endpoint with unchanged publication safety/recovery.
- Owned responsibility: publication route/resource models/rendering and focused publication/removal/monitor fixtures/tests; it does not edit FastAPI or React.
- Contract surface: exact method/path, authoritative forwarded fields, monitor endpoint capture, Basic Auth and upstream proxy behavior, no dashboard API exposure.
- DoD/evidence: renderer and reconciliation tests cover project/environment, methods, auth modes, unavailable monitor, deterministic bytes, validation/reload rollback.
- Parallel safety: disjoint from WP-02 HTTP ownership and WP-04 frontend ownership; route grammar is authoritative in specs.

### WP-04 — Generated client and Open as UI

- `stage: 3`
- `depends_on: [WP-02]`
- Tasks: 4.1, 4.2, 4.3, 4.4.
- Deliverable: regenerated client plus tested Open as dialog/transient POST workflow while preserving Open Odoo.
- Owned responsibility: generated web client, React components/styles/tests, production frontend artifact expectations; no backend model edits.
- Contract surface: WP-02 OpenAPI operations/models/errors; exact ticket response; browser form and popup lifecycle.
- DoD/evidence: codegen check, frontend tests and production build prove gating, pagination, popup/form cleanup, sanitized errors, and existing action regression.
- Parallel safety: may run alongside WP-05 after WP-02; WP-05 owns integration/CI fixtures, not web source/tests except read-only execution.

### WP-05 — Integrated deterministic and CI verification

- `stage: 3`
- `depends_on: [WP-02, WP-03]`
- Tasks: 5.1, 5.2, 5.3, 5.4.
- Deliverable: cross-boundary deterministic integration, source-backed real-Odoo CI coverage, and consolidated quality/security gates.
- Owned responsibility: integration/real-Odoo fixtures/tests, CI workflow additions if needed, secret registry/evidence packaging assertions, and repository-wide verification. It may add directly related fixtures/docs but does not rewrite active siblings' product source.
- Contract surface: completed SDK/HTTP/publication behavior, real-Odoo source/session APIs, existing CI evidence contract.
- DoD/evidence: focused/local-under-limit commands pass; longer suites run in GitHub CI on exact SHA; secret and process-boundary reviews are recorded.
- Parallel safety: starts after backend and route contracts stabilize; can run alongside WP-04 with disjoint primary write zones.

### WP-06 — Independent numbered-slot acceptance and operator documentation

- `stage: 4`
- `depends_on: [WP-04, WP-05]`
- Tasks: 6.1, 6.2, 6.3, 6.4.
- Deliverable: repeatable test-repository fixtures, independent real browser/proxy/Odoo evidence, cleanup, and final operator/limitation documentation.
- Owned responsibility: separate test-repository scripts/fixtures/MR, independent actions, Multica evidence/images/read-back, and final docs directly related to the workflow. Tester does not edit product CI tests.
- Contract surface: exact product SHA, numbered-slot wrapper/profile/reference checkout, actual Open as UX/API/proxy/session behavior, evidence publication rules.
- DoD/evidence: BL-01 through BL-07 are PASS or explicitly failed; required images and safe evidence are attached/read back; slot/database/service cleanup succeeds; evidence branch/MR remains available.
- Parallel safety: acceptance successor needs the consolidated product and deterministic verification; no sibling remains active at its write boundary.

## Topology and coverage checks

Stage mapping: `WP-01 -> 1`, `WP-02 -> 2`, `WP-03 -> 2`, `WP-04 -> 3`, `WP-05 -> 3`, `WP-06 -> 4`. Stages are consecutive. Every dependency points to a lower stage. Direct dependencies only are listed.

Parallel frontiers exist at stage 2 (`WP-02`, `WP-03`) and stage 3 (`WP-04`, `WP-05`), with disjoint primary write zones. Therefore the multi-WP DAG is permitted. Task coverage is complete and one-time: WP-01 owns 1.1–1.5; WP-02 owns 2.1–2.7; WP-03 owns 3.1–3.3; WP-04 owns 4.1–4.4; WP-05 owns 5.1–5.4; WP-06 owns 6.1–6.4.
