## Delivery Contract

- **Task key:** `MYL-409`
- **Change:** `extend-odcli-automation-primitives`
- **Approved base:** `origin/main@ab71895f7031eabe129fbd5dbfcda71298cd4016`
- **Planning branch:** `feat/MYL-409-extend-odcli-automation-primitives-v4`
- **Graph revision:** `MYL-409-GR4`
- **Delivery mode:** `dag`
- **Sizing source:** authoritative `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` properties on the root planning issue; numerical totals are intentionally not duplicated in artifacts
- **Topology decision:** the weighted property remains above the multi-WP threshold and stages 1 and 2 each contain genuinely independent write scopes
- **Estimate basis:** remaining active developer-hours for one experienced developer familiar with the repository and stack, without AI acceleration; merged Multica binding/adoption is excluded as already satisfied, each remaining row includes focused checks and likely repair, and shared integration/verification is counted once
- **Confidence:** medium and uncalibrated; typed Multica issue/creator APIs and the extension package are now directly inspectable, while Caddy/Odoo proxy behavior and external trusted-proxy acceptance remain bounded integration risks
- **Ponytail constraint:** reuse merged `odcli-multica`, core adoption, concrete resources, process/HTTP boundaries, configuration conventions, generated contracts, and test harnesses; do not add a second Multica adapter/package/binding, coordinator, plugin/provider registry, publication database, second module index, new acceptance harness, or duplicate verification layer

Any product scope, public contract, persistence owner, external dependency, direct dependency edge, owned responsibility, or approved-base change requires a new graph revision. Operational status, assignee, WIP, and stage promotion do not change this artifact.

## DAG and Stages

```text
WP-01 ─┬─> WP-04 ──────────┐
       └─> WP-05 ──────────┤
WP-02 ───> WP-03 ──────────┴─> WP-06
```

Direct edge set: `WP-01→WP-04`, `WP-01→WP-05`, `WP-02→WP-03`, `WP-03→WP-06`, `WP-04→WP-06`, `WP-05→WP-06`.

Required child stage mapping:

| Work package | Stage |
|---|---:|
| `WP-01-project-binding-runtime` | 1 |
| `WP-02-multica-credentials` | 1 |
| `WP-03-git-gitlab` | 2 |
| `WP-04-module-context` | 2 |
| `WP-05-caddy-publication` | 2 |
| `WP-06-monitor-panel-verification` | 3 |

Stages are consecutive topological layers. Stage 1 permits project binding/runtime and Multica credential context concurrently because they edit disjoint core project/runtime versus extension-package paths. Stage 2 permits Git/GitLab, module context, and Caddy publication concurrently: each consumes only its direct predecessor, and their mutable scopes are disjoint. WIP is operational and is not encoded as an edge.

`WP-03` owns the existing `odcli-multica` Git/GitLab CLI and never writes the core registry. `WP-05` exports complete core `publish` and `unpublish` Click objects without writing the registry. Their common successor `WP-06` is the sole owner of `src/odoo_instance_sdk/commands/cli_parts/registration.py` and registers only those completed publication commands during convergence.

## Task Coverage

Every OpenSpec task is owned exactly once:

| OpenSpec tasks | Work package |
|---|---|
| `1.1`–`1.4` | `WP-01-project-binding-runtime` |
| `2.1`–`2.3` | `WP-02-multica-credentials` |
| `3.1`–`3.6` | `WP-03-git-gitlab` |
| `4.1`–`4.4` | `WP-04-module-context` |
| `5.1`–`5.5` | `WP-05-caddy-publication` |
| `6.1`–`6.6`, `7.1`–`7.3` | `WP-06-monitor-panel-verification` |

## WP-01-project-binding-runtime

- **Stage:** 1
- **OpenSpec task coverage:** `1.1`–`1.4`
- **Direct `depends_on`:** none
- **Independent deliverable:** a main checkout can publish one paired database/filestore binding through the existing project manifest and run/status/stop the exact detached runtime that consumes it.
- **Owned responsibility scope:** `project.py` fields/serialization, existing `internal/dbprep/` project publication, restore projections, project runtime construction/identity/readiness, and directly related focused tests. Critical shared files are `src/odoo_instance_sdk/project.py`, project-facing preparation/materialization code, `commands/db.py`, project runtime modules, and their tests. It also defines ordered addon repositories consumed read-only by WP-04.
- **Contract surface:** backward-compatible managed filestore and addon repositories; atomic stale-checked database/filestore publication; complete bounded restore outcome; exact project runtime/readiness identity.
- **DoD / evidence:** old/new manifests round-trip; download-only and every failed/stale preparation preserve the prior pair; successful restore feeds the exact project run; focused ready/timeout/stale identity checks and existing project/environment compatibility tests pass.
- **Parallel-safety rationale:** it owns project manifest, preparation, and project runtime paths. WP-02 owns only the existing extension package's identity/credential paths, so stage-1 writes do not overlap.

## WP-02-multica-credentials

- **Stage:** 1
- **OpenSpec task coverage:** `2.1`–`2.3`
- **Direct `depends_on`:** none
- **Independent deliverable:** the existing verified `odcli-multica` task context resolves its finite same-workspace lineage to one human root creator and one exact host-scoped GitLab credential context without persisting or exposing a token.
- **Owned responsibility scope:** `packages/odcli-multica/src/odcli_multica/client.py`, its frozen public models, one owner-only mapping reader using existing project dotenv semantics, and focused extension-package tests. It reuses the pinned dependency, `VerifiedTaskContext`, context/preparation commands, core adoption, and cleanup unchanged. Critical shared extension client/CLI files pass to WP-03 only after this predecessor completes.
- **Contract surface:** verified issue/workspace starting point; finite public typed parent traversal; human root creator; exact user/host/login/token-key mapping; private per-command credential value and safe public identifiers; no second checkout binding or adoption path.
- **DoD / evidence:** existing checkout/context/preparation/adoption tests remain unchanged and pass; missing/cyclic/cross-workspace/non-human lineage and credential gaps fail before Git/remote work; two users remain isolated; one secret-canary check passes.
- **Parallel-safety rationale:** it does not edit project manifest/dbprep/project-runtime paths owned by WP-01. Shared process redaction is reused rather than redesigned.

## WP-03-git-gitlab

- **Stage:** 2
- **OpenSpec task coverage:** `3.1`–`3.6`
- **Direct `depends_on`:** `WP-02-multica-credentials`
- **Independent deliverable:** native Git passthrough, HTTPS sync, and exact-key GitLab MR create/update operate through the existing Multica extension as the resolved root creator without persisting or exposing credentials.
- **Owned responsibility scope:** core `resources/git.py` and `internal/git_sync.py` extensions required for reusable plans, `packages/odcli-multica` Git/GitLab client and CLI modules, the small askpass helper, concrete GitLab `httpx` module, focused native-Git/fake-provider tests, and the narrow extension-package collection step in the existing `.github/workflows/ci.yml` `offline-tests` job. It consumes WP-02 identity values and may extend predecessor-owned extension files serially. It does not edit the core `commands/cli_parts/registration.py` registry.
- **Contract surface:** literal `--` raw transport in `odcli-multica`; child-only credential environment; existing SSH and exact-lease sync semantics; exact project/source/target MR key; bounded description file; created/updated result; no core-to-extension import.
- **DoD / evidence:** local Git needs no identity; remote URL/argv/config remain secret-free; native exit/stdio behavior holds; HTTPS sync retains stale-lease refusal; MR zero/one/many and provider-failure cases pass without a live GitLab service; `uv run pytest -q -o addopts='' packages/odcli-multica/tests` is collected by `offline-tests`; core installation remains Multica-free.
- **Parallel-safety rationale:** owns core Git paths, the extension package, and one additive `offline-tests` step after WP-02; no stage-2 sibling owns that workflow. It does not edit module, Caddy, monitor, central core registry, or UI files used by stage-2 siblings.

## WP-04-module-context

- **Stage:** 2
- **OpenSpec task coverage:** `4.1`–`4.4`
- **Direct `depends_on`:** `WP-01-project-binding-runtime`
- **Independent deliverable:** one read-only module context combines safe ordered filesystem modules with repository, Git-change, dependency, and installed-version facts while preserving partial availability.
- **Owned responsibility scope:** `resources/module.py`, its CLI adapter, on-demand Git/database collection helpers, and focused tests. It consumes addon repositories from WP-01 and the baseline selected checkout/environment context without editing their definitions.
- **Contract surface:** first-root precedence and shadows; repository/path provenance; file versus installed version; related Git changes; per-source availability/reason; no mutation or second index.
- **DoD / evidence:** duplicate names never merge; unsafe roots are rejected; Git/database failure does not hide filesystem facts; manifests are not executed; focused multi-repository/change/availability/no-mutation checks pass.
- **Parallel-safety rationale:** owns module-specific paths only and does not edit extension Git/GitLab or Caddy implementation files.

## WP-05-caddy-publication

- **Stage:** 2
- **OpenSpec task coverage:** `5.1`–`5.5`
- **Direct `depends_on`:** `WP-01-project-binding-runtime`
- **Independent deliverable:** project and environment runtimes gain stable idempotent HTTPS publish/unpublish through one locked, validated OdCLI-owned Caddy route file, with safe stop/restart/removal behavior.
- **Owned responsibility scope:** publication settings reader, one concrete publication resource, a self-contained core publication Click command module, route-file generator/lock, Caddy validate/reload process steps, environment removal hook, and focused tests. It reuses baseline environment ownership/runtime identities and adds no publication table or generic control plane. It does not edit the central registry.
- **Contract surface:** deterministic owner labels/URLs; exact ready backend; one complete owned route file; validate/reload/prior-byte restoration; HTTPS/Basic Auth/Odoo/panel routes; idempotent unpublish; removal cleanup gate; importable complete `publish` and `unpublish` Click commands for WP-06.
- **DoD / evidence:** stable distinct URLs; concurrent updates keep both routes; failed validation/reload preserves prior bytes and live routes; unknown Host/auth fail closed; stopped runtime is non-actionable; removal failure preserves caller-owned checkout bytes; outputs contain no secret; exported commands expose specified help/options in isolation.
- **Parallel-safety rationale:** owns publication modules and the narrow removal hook after WP-01 and exports complete commands without touching the registry. It does not edit extension Git/GitLab or module paths.

## WP-06-monitor-panel-verification

- **Stage:** 3
- **OpenSpec task coverage:** `6.1`–`6.6`, `7.1`–`7.3`
- **Direct `depends_on`:** `WP-03-git-gitlab`, `WP-04-module-context`, `WP-05-caddy-publication`
- **Independent deliverable:** the existing monitor/OpenAPI/generated client/panel exposes publication safely behind Caddy, documents all new leaves, and passes one integrated verification run.
- **Owned responsibility scope:** sole ownership of `src/odoo_instance_sdk/commands/cli_parts/registration.py`, canonical monitor snapshot/collector, FastAPI proxy policy, OpenAPI and generated TypeScript, existing React/Mantine views/tests, focused user/deployment/recovery docs, and final existing repo gates. It consumes completed extension Git/GitLab, module, and publication contracts and does not introduce new backend subsystems or test harnesses.
- **Contract surface:** one central core registry mapping only completed `publish` and `unpublish` Click objects; minimal publication state/URL/reason; separate local endpoint; exact trusted proxy/Host/effective origin; secure CSRF; local compatibility; relative generated client; Odoo-like views; server-supplied external URL only.
- **DoD / evidence:** core root help and lazy routing expose exactly one top-level `publish` and `unpublish` surface; the separate extension help exposes its Git/GitLab leaves; project/environment publication renders consistently; publication read failure is isolated; spoofed forwarding/unknown hosts fail; the test-only external POST proves the secure same-origin mutation boundary while the production external app still omits loopback pgAdmin; local mode passes; UI disables unavailable links and stays accessible/responsive; generated stale gate and existing repo gates pass once; optional smoke uses existing fixtures or reports missing prerequisites.
- **Parallel-safety rationale:** terminal convergence package and sole central-registry writer. All stage-2 feature writers finish first, so registry wiring, generated contracts, UI, docs, and integrated verification have no active sibling write conflicts.

## E2E Evidence Plan

E2E is required because live Multica identity, authenticated GitLab effects, a real Odoo runtime, and the browser panel cannot be proven by the repository's isolated tests alone. The local Mac uses a separate HTTP Caddy for transport smoke; its configuration, admin endpoint and route file are private test inputs and SHALL remain isolated from the primary Caddy. The production publisher emits HTTPS URLs, HTTPS forwarded headers and a complete Caddyfile reload. Therefore the HTTP smoke SHALL NOT claim to validate production `publish`/`unpublish`, HTTPS routing, secure external CSRF or its reload rollback. Those contracts retain the exact lower-level GitHub Actions coverage below. No local HTTPS/TLS service is a preflight condition. Validation, redaction, stale leases, malformed input, provider errors and unavailable sources remain lower-level checks. A full CLI sweep is out of scope.

### Coverage matrix

| Case ID | OpenSpec requirement/scenario | OpenAPI operation/schema (if any) | Local Python script | Independent agent steps | CI disposition/test/job | Assertions | Artifacts/images |
|---|---|---|---|---|---|---|---|
| `E2E-1` | Module context inventory/partial availability; root-human credentials; HTTPS sync; MR create/update | None | `tests/e2e/test_myl411_gitlab_flow.py::test_module_sync_and_mr_round_trip` | In a fresh shell, reproduce the four Git states, run public module/context/Git/MR commands, and inspect the remote branch and one MR without rerunning pytest | `local-only`: live Multica/GitLab are private-service prerequisites. Core checks remain in `offline-tests`; task `3.6` adds the missing `packages/odcli-multica/tests` command to that existing job | Provenance/dependencies and clean/staged/unstaged/untracked states; partial database failure; remote SHA; one MR IID updated in place; no secret/config mutation | GitLab MR: test scripts/fixtures and any repository-native functional commit created by the tested Git utility. Multica only: sanitized JSON outputs, provenance, and the module-help baseline/candidate render pair (2 PNGs); no GitLab UI image because MR links are authoritative |
| `E2E-2` | Atomic database/filestore binding; exact detached runtime; HTTP transport to Odoo and the local panel | Live: local `GET /api/v1/snapshot`, `getMonitorSnapshot`, `Snapshot`, `ProjectSummary`, `EnvironmentSnapshot`, `HttpError`. Production publication and external-mode schemas are lower-level contracts | `tests/e2e/test_myl411_http_proxy.py::test_local_http_proxy_and_panel` | In a fresh browser/profile and shell, restore and run the candidate, apply only test-owned routes to the separate HTTP Caddy, then inspect Odoo and local panel paths and the snapshot independently of pytest | `local-only`: private Odoo/Caddy/browser prerequisites. `offline-tests` covers product publication and monitor projection; `dashboard-tests` covers trusted proxy, secure external CSRF and generated types. Task `6.6` supplies the accepted external POST assertion | Paired binding equals ready runtime; test-owned HTTP routes reach only the selected Odoo and local monitor; login/assets/attachment/bus and panel SPA/read API work; Basic Auth and unknown Host reject; the local snapshot agrees with runtime identity. No HTTPS URL, production route, external-mode or secure-CSRF assertion is credited to this case | GitLab MR: shared test scripts/fixtures. Multica only: sanitized restore/runtime/snapshot output, HTTP smoke provenance, root-help render pair and local-panel screenshot pair (4 images) |
| `E2E-3` | Product candidate validation/reload rollback, stop/restart projection and idempotent unpublish | `GET /api/v1/snapshot`; `PublicationSnapshot` | No local live script: the production publisher cannot target the separate HTTP Caddy without changing its route scheme, forwarded headers and whole-file reload contract | Independently inspect the focused CI result and the generated production route/command assertions at the tested SHA; do not report this as a browser or live-Caddy walkthrough | `already covered` in `offline-tests`: `tests/unit/test_publication.py::{test_reload_failure_restores_prior_route_bytes,test_candidate_validation_failure_preserves_prior_route_bytes,test_publish_is_stable_and_unpublish_is_idempotent}`, `tests/unit/test_monitor_publication.py::test_publication_projection_keeps_local_endpoint_and_route_identity` | CI proves prior-byte restoration, failed reload and idempotency using the product command seam. The local HTTP smoke proves none of those production transitions | Multica: CI job link and tested SHA. No live-output or image claim |

### Exact delta-scenario disposition

`offline-tests` means `.github/workflows/ci.yml` job `offline-tests` running `make test`; `dashboard-tests` means its `make web-codegen-check`, `npm test`, and `pytest -m dashboard` steps. Task `3.6` extends `offline-tests` with the explicit extension-package command shown below. Each delta scenario is mapped here; grouped scenarios share the exact disposition shown.

| Delta scenario(s) | Exact live or CI disposition |
|---|---|
| `Prepare then run a main checkout`; `Catalogue backup restore succeeds`; `Restore binds database and filestore`; `Restored project is started`; `Detached project becomes ready` | `E2E-2`; lower-level regression: `offline-tests`, `tests/unit/internal/test_database_preparation.py::test_restore_coordinator_switches_default_only_after_restore`, `tests/unit/resources/test_instance_runtime.py::TestInstancePrefix::test_from_project_consumes_published_database_filestore_binding`, and `tests/unit/test_run_detached.py::test_detached_readiness_success_waits_and_keeps_runtime` |
| `Failed project preparation`; `Local archive restore fails`; `Download only preserves binding`; `Failure after database creation`; `Binding changes during preparation` | Lower-level sufficient: `offline-tests`, `tests/unit/internal/test_database_preparation.py::{test_restore_failure_retains_backup_and_does_not_write_manifest,test_preexisting_or_raced_restore_target_never_publishes_incomplete_binding,test_default_switch_updates_owned_files_and_compensates_both_on_write_failure}` |
| `Project selector with replace` | Lower-level sufficient: `offline-tests`, `tests/unit/internal/test_database_replacement.py::{test_replacement_success_publishes_new_backup_and_removes_rollback_filestore,test_replacement_nonzero_admin_reset_does_not_publish}` |
| `Publish selector conflict`; `Runtime is not ready` | Lower-level sufficient: `offline-tests`, `tests/unit/test_publication.py::{test_unpublish_selector_failure_emits_machine_output_envelope,test_runtime_target_rejects_stopped_before_route_mutation,test_runtime_target_rejects_stale_identity_before_route_mutation}` |
| `Existing Git subcommand remains stable`; `Local native Git command`; `Delimiter is absent`; `Remote host is ambiguous` | Lower-level sufficient: task `3.6`, `packages/odcli-multica/tests/test_gitlab.py::{test_git_passthrough_and_sync_scope_https_credentials_to_child,test_git_https_ambiguous_host_fails_before_child_process}` plus `packages/odcli-multica/tests/test_client.py::test_raw_git_credentials_require_the_actual_remote_command` |
| `MR description is not file-backed`; `Matching merge requests are ambiguous`; `GitLab rejects the request`; `Dry-run publication` | Lower-level sufficient: task `3.6`, `packages/odcli-multica/tests/test_gitlab.py::{test_description_file_rejects_bounded_negative_cases,test_publish_rejects_ambiguous_open_merge_requests,test_publish_provider_failure_is_bounded_and_redacted,test_mr_dry_run_plan_is_non_secret_and_does_not_call_provider}` |
| `Default monitor remains local`; `External monitor configuration is incomplete` | Lower-level sufficient: `offline-tests`, `tests/unit/test_cli_monitor.py::{test_monitor_headless_passes_flags_and_exits_clean,test_monitor_settings_require_explicit_external_mode}` |
| `Valid publication settings load`; `Secret-bearing setting is rendered`; `Invalid publication settings` | Lower-level sufficient: `offline-tests`, `tests/unit/test_publication.py::{test_settings_reject_plaintext_and_non_loopback_control,test_route_rendering_is_exact_host_basic_auth_and_secret_free}` |
| `Two projects share the publication backend`; `Two runtimes receive stable distinct URLs`; `Concurrent publications` | Lower-level sufficient: `offline-tests`, `tests/unit/test_publication.py::{test_monitor_route_is_reconciled_and_preserved_by_runtime_publication,test_two_concurrent_publications_retain_both_routes}` |
| `Child issue resolves its root creator`; `Root creator credentials resolve for one host`; `One resolution serves all consumers`; `HTTPS sync uses the root creator`; `Remote native Git command`; `Create a merge request`; `Update the unique merge request`; `Publish rebased feature branch` | `E2E-1`; deterministic boundaries additionally run under task `3.6`: `test_credentials.py::{test_root_creator_traversal_is_finite_and_workspace_bound,test_credential_mapping_uses_process_precedence_and_redacts_token}` and `test_gitlab.py::{test_publish_creates_one_exact_merge_request_without_exposing_token,test_publish_updates_the_unique_open_merge_request,test_git_passthrough_and_sync_scope_https_credentials_to_child}`; exact-lease behavior remains in `offline-tests`, `tests/integration/test_git_sync.py::test_sync_publishes_rewritten_head_with_exact_authoritative_lease` |
| `Invalid lineage fails closed`; `Credentials are unavailable`; `Child echoes credential material`; `Two users run Git concurrently`; `Plan and child output contain a token`; `HTTPS credentials are missing` | Lower-level sufficient: task `3.6`, `test_credentials.py::{test_root_creator_rejects_invalid_lineage,test_credentials_fail_closed_for_an_unmapped_host,test_two_root_creators_keep_private_tokens_isolated}` and `test_gitlab.py::test_publish_provider_failure_is_bounded_and_redacted` |
| `Rebase conflicts` | Lower-level sufficient: `offline-tests`, `tests/integration/test_git_sync.py::test_sync_conflict_preserves_state_and_recovery_guidance` |
| `File and installed module facts are combined`; `Database is unavailable` | `E2E-1`; lower-level regression: `offline-tests`, `tests/unit/test_module_resource.py::{test_context_combines_provenance_changes_and_partial_database_availability,test_context_preserves_filesystem_facts_when_database_probe_fails}` |
| `Same module exists in two repositories`; `Addon root escapes an allowed repository` | Lower-level sufficient: `offline-tests`, `tests/unit/test_module_resource.py::{test_context_uses_declared_repository_order_and_keeps_provenance,test_context_rejects_roots_outside_checkout_without_inspection}` |
| `Inspect a publication plan` | Lower-level sufficient: `offline-tests`, `tests/unit/test_publication.py::test_publish_cli_resolves_environment_selector_before_building_command` (dry-run plan) |
| `Publish a ready project runtime`; `Published Odoo browser flow`; `Unknown or unauthenticated host`; `Request arrives through the trusted Caddy proxy`; `Published environment is ready`; `Project-only snapshot`; `External URL reaches the generated client`; `Open a published main checkout`; `Panel is served below the external origin` | `E2E-2` proves only real Odoo/local-panel HTTP transport and runtime binding. Production HTTPS publication, external origin, proxy trust, stable external URL and unavailable-state semantics require `offline-tests` `tests/unit/test_publication.py` and `tests/unit/test_monitor_publication.py`, plus `dashboard-tests` `tests/unit/test_http_contract.py::{test_external_proxy_requires_trusted_forwarding_and_secure_csrf,test_snapshot_bytes_match_production_openapi_schema_and_are_not_cli_enveloped}` and `src/odoo_instance_sdk/web/src/App.test.tsx`; none receives a local live claim. |
| `Forwarded headers arrive directly` | Already covered: `dashboard-tests`, `tests/unit/test_http_contract.py::test_external_proxy_requires_trusted_forwarding_and_secure_csrf` rejects an untrusted or mismatched forwarded origin |
| `External state-changing request` | Current implementation/CI gap: the production external app intentionally omits its only loopback mutation, so a successful external POST cannot be inferred from omission or `GET /healthz`. Task `6.6` resolves the gap in `dashboard-tests` with `tests/unit/test_http_contract.py::test_external_same_origin_mutation_accepts_secure_csrf`, a test-only mounted existing mutation router configured for the effective HTTPS origin; it must accept the secure-cookie/token/same-origin POST and delegate exactly once without exposing pgAdmin in the production external app |
| `External browser tries a loopback-only action` | Already covered: `dashboard-tests`, `tests/unit/test_http_contract.py::{test_external_proxy_requires_trusted_forwarding_and_secure_csrf,test_pgadmin_browser_boundary_rejects_unsafe_requests_without_delegation,test_pgadmin_openapi_refs_and_success_bytes_are_production_typed}`. The local HTTP panel remains in local mode and does not verify external omission. |
| `Publication schema changes` | Already covered: `dashboard-tests`, `make web-codegen-check`, `tests/unit/test_openapi_export.py`, and `src/odoo_instance_sdk/web/src/generated.contract.test.ts` |
| `Publication is unavailable`; `Publication data cannot be read` | Lower-level sufficient: `offline-tests`, `tests/unit/test_monitor_publication.py::{test_publication_projection_isolated_when_route_state_cannot_be_read,test_missing_route_is_explicitly_unpublished}`; UI disabled-state assertion runs in `dashboard-tests`, `src/odoo_instance_sdk/web/src/App.test.tsx` |
| `Candidate configuration is rejected`; `Remove one route`; `Route exists but runtime stopped`; `Published runtime stops and restarts` | `E2E-3` is lower-level only: `offline-tests`, `tests/unit/test_publication.py::{test_reload_failure_restores_prior_route_bytes,test_candidate_validation_failure_preserves_prior_route_bytes,test_publish_is_stable_and_unpublish_is_idempotent}` and `tests/unit/test_monitor_publication.py::test_publication_projection_keeps_local_endpoint_and_route_identity`. No live HTTP proxy result is credited as product publication evidence. |
| `Environment deletion cannot remove its route` | Lower-level sufficient: `offline-tests`, `tests/unit/resources/test_environment_remove.py::TestEnvRemove::test_publication_cleanup_failure_preserves_caller_owned_checkout`; live deletion is excluded because it adds no assertion beyond the same fail-closed route transaction exercised by `E2E-3` |
| `Readiness times out`; `Persisted identity no longer matches` | Lower-level sufficient: `offline-tests`, `tests/unit/test_run_detached.py::{test_detached_readiness_timeout_clears_only_captured_identity_after_group_exit,test_detached_readiness_does_not_terminate_unrelated_process}` and `tests/unit/test_runtime_stop.py::test_stop_inaccessible_identity_fails_closed_and_retains_runtime` |

The two uncovered public-CI gaps are extension-package collection (task `3.6`, `WP-03`) and accepted external mutation-boundary coverage (task `6.6`, `WP-06`). Both extend existing jobs and fixtures; neither introduces a private service or a new framework.

### Reproducible stand and setup

Resolve `TEST_REPO_URL` and `GITLAB_HOST` only from the project description at execution time; never persist their values. `PRODUCT_SHA` is the exact candidate PR head. Derive `BASELINE_SHA` deterministically as the single merge base of `PRODUCT_SHA` and the freshly fetched target branch, assert it is an ancestor, and record both before any build. `TEST_BASE_SHA` and `TEST_CODE_SHA` are independent test-repository revisions; `FUNCTIONAL_SHA` exists only when a tested function produces a repository-native commit.

The test-code commit contains all executable verification inputs:

- `addons/myl411_e2e/{__init__.py,__manifest__.py,README.md}` and `addons/myl411_e2e/models/{__init__.py,marker.py}`;
- `tests/e2e/test_myl411_gitlab_flow.py`, `tests/e2e/test_myl411_http_proxy.py`, `tests/e2e/prepare_myl411_stand.py`, and `tests/e2e/prepare_myl411_http_caddy.py`;
- `tests/e2e/render_terminal.py`, `tests/e2e/capture_myl411_panel.py`, hash-locked `tests/e2e/requirements.lock`, pinned `tests/e2e/build-constraints.txt`, and `tests/e2e/fixtures/{terminal-font.ttf,terminal-font.LICENSE.txt,myl411-attachment.txt}`.

Use commit subject `test(e2e): add MYL-411 scenarios` and record `TEST_CODE_SHA`. If a tested function produces an addon change, translation, generated code/documentation, or a commit through the Git utility, preserve that result in a separate commit and record `FUNCTIONAL_SHA`; retain a utility-created commit unchanged instead of renaming it. Do not create a second commit when no repository-native functional result exists. `requirements.lock` pins Python 3.12-compatible `pytest`, `httpx`, `Pillow`, and `playwright` distributions with hashes. `build-constraints.txt` pins `uv_build==0.10.8`. The checked-in font makes text rendering independent of host fonts. The Playwright package and Chromium revision are locked together; browser installation and its executable checksum are recorded in Multica provenance.

```sh
set -euo pipefail
UVX_BOOTSTRAP_VERSION=0.13.0
UVX_BIN="$(command -v uvx)"
test -n "$UVX_BIN"
case "$UVX_BIN" in /*) ;; *) exit 1 ;; esac
test "$("$UVX_BIN" --version | awk 'NR == 1 {print $2}')" = "$UVX_BOOTSTRAP_VERSION"
shasum -a 256 "$UVX_BIN" > "$RUN_DIR/uvx.sha256"
UV_VERSION=0.10.8
uv_pinned() { "$UVX_BIN" --from "uv==$UV_VERSION" uv "$@"; }
test "$(uv_pinned --version | awk 'NR == 1 {print $2}')" = "$UV_VERSION"
git -C "$PRODUCT_REPO" fetch origin "$TARGET_BRANCH"
BASELINE_SHA="$(git -C "$PRODUCT_REPO" merge-base "$PRODUCT_SHA" "origin/$TARGET_BRANCH")"
test -n "$BASELINE_SHA"
git -C "$PRODUCT_REPO" merge-base --is-ancestor "$BASELINE_SHA" "$PRODUCT_SHA"
git -C "$PRODUCT_REPO" worktree add --detach "$PRODUCT_CHECKOUT" "$PRODUCT_SHA"
test "$(git -C "$PRODUCT_CHECKOUT" rev-parse HEAD)" = "$PRODUCT_SHA"
git clone "$TEST_REPO_URL" "$CHECKOUT"
TEST_BASE_SHA="$(git -C "$CHECKOUT" rev-parse HEAD)"
git -C "$CHECKOUT" switch -c "$BRANCH"
test "$(git -C "$CHECKOUT" rev-parse HEAD)" = "$TEST_BASE_SHA"
```

After committing the listed inputs, create a locked test environment and a separate extension runtime. Export candidate runtime dependencies from the candidate's frozen workspace lock, excluding workspace packages; install both locally built wheels with `--no-deps`, so the stale direct URL embedded in `odcli-multica` can never replace the candidate core wheel.

```sh
set -euo pipefail
TEST_CODE_SHA="$(git -C "$CHECKOUT" rev-parse HEAD)"
uv_pinned venv --python 3.12.13 "$TEST_VENV"
TEST_PYTHON="$TEST_VENV/bin/python"
uv_pinned pip sync --python "$TEST_PYTHON" --require-hashes "$CHECKOUT/tests/e2e/requirements.lock"
"$TEST_PYTHON" -m playwright install chromium
uv_pinned build --wheel --clear --build-constraints "$CHECKOUT/tests/e2e/build-constraints.txt" --out-dir "$RUN_DIR/dist" "$PRODUCT_CHECKOUT"
uv_pinned build --wheel --clear --build-constraints "$CHECKOUT/tests/e2e/build-constraints.txt" --out-dir "$RUN_DIR/dist-multica" "$PRODUCT_CHECKOUT/packages/odcli-multica"
uv_pinned export --project "$PRODUCT_CHECKOUT" --frozen --all-packages --no-dev --no-emit-workspace --output-file "$RUN_DIR/runtime-requirements.lock"
uv_pinned venv --python 3.12.13 "$MULTICA_VENV"
uv_pinned pip sync --python "$MULTICA_VENV/bin/python" "$RUN_DIR/runtime-requirements.lock"
uv_pinned pip install --python "$MULTICA_VENV/bin/python" --no-deps "$RUN_DIR"/dist/*.whl "$RUN_DIR"/dist-multica/*.whl
uv_pinned pip check --python "$MULTICA_VENV/bin/python"
shasum -a 256 "$RUN_DIR"/dist/*.whl "$RUN_DIR"/dist-multica/*.whl > "$RUN_DIR/wheels.sha256"
"$MULTICA_VENV/bin/python" -c 'import pathlib, sys, odoo_instance_sdk, odcli_multica; root=pathlib.Path(sys.prefix).resolve(); assert pathlib.Path(odoo_instance_sdk.__file__).resolve().is_relative_to(root); assert pathlib.Path(odcli_multica.__file__).resolve().is_relative_to(root)'
MULTICA_CLI="$MULTICA_VENV/bin/odcli-multica"
```

`prepare_myl411_stand.py` invokes the repository's existing `scripts/odcli_agent_tools/numbered_tool.py create` twice against the same `TEST_CODE_SHA` fixture: candidate `sdk_sha=PRODUCT_SHA` and baseline `sdk_sha=BASELINE_SHA`. It supplies separate absolute checkout roots, environment manifests, COPY databases, filestores, Odoo/monitor/control ports, and browser profiles. It fails unless both `inspect` results prove the exact SDK SHA, test checkout/revision, manifest/root, interpreter, lock digest, database, and non-overlapping ports.

```sh
set -euo pipefail
"$TEST_PYTHON" "$CHECKOUT/tests/e2e/prepare_myl411_stand.py" --numbered-tool "$PRODUCT_CHECKOUT/scripts/odcli_agent_tools/numbered_tool.py" --project "$CHECKOUT" --reference-checkout "$CHECKOUT" --reference-sha "$TEST_CODE_SHA" --candidate-sdk-sha "$PRODUCT_SHA" --baseline-sdk-sha "$BASELINE_SHA" --checkout-parent "$SLOT_CHECKOUT_PARENT" --odoo-executable "$ODOO_BIN" --ports-file "$PORTS_FILE" --output "$RUN_DIR/stand.json"
"$TEST_PYTHON" -c 'import json,sys; p=json.load(open(sys.argv[1])); assert p["candidate"]["sdk_sha"]==sys.argv[2]; assert p["baseline"]["sdk_sha"]==sys.argv[3]; assert p["candidate"]["checkout_sha"]==p["baseline"]["checkout_sha"]==sys.argv[4]; assert set(p["candidate"]["ports"]).isdisjoint(p["baseline"]["ports"])' "$RUN_DIR/stand.json" "$PRODUCT_SHA" "$BASELINE_SHA" "$TEST_CODE_SHA"
SLOT_LAUNCHER="$("$TEST_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["candidate"]["launcher"])' "$RUN_DIR/stand.json")"
BASELINE_SLOT_LAUNCHER="$("$TEST_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["baseline"]["launcher"])' "$RUN_DIR/stand.json")"
test -x "$SLOT_LAUNCHER"
test -x "$BASELINE_SLOT_LAUNCHER"
"$SLOT_LAUNCHER" --version
"$BASELINE_SLOT_LAUNCHER" --version
```

All core public commands below use `SLOT_LAUNCHER`; only extension commands use the separately verified `MULTICA_CLI`. Mandatory provenance is `PRODUCT_SHA`, `BASELINE_SHA`, `TEST_CODE_SHA`, parsed `uvx`/`uv` versions and the bootstrap executable checksum, both numbered manifests, wheel hashes, frozen lock digest, installed module roots, selected database/filestore/ports, Playwright/Chromium versions and checksum, and fixture/font checksums. Preflight SHALL confirm macOS arm64, CPython 3.12, the separate Caddy's healthy HTTP listener and loopback-only distinct admin endpoint, and that its active config/route file and process identity differ from the primary Caddy. The test script SHALL create and validate a complete test-owned HTTP Caddyfile, reload only the separate admin endpoint, and restore its prior bytes/config even after failure. It SHALL NOT call the product publisher: that command emits HTTPS sites and reloads the full owned file, so pointing it at this service would replace the HTTP listener. All listener/admin addresses, ports, host mappings, paths and credentials come from a private run manifest; no concrete stand value enters GitHub. Before live commands, verify a same-workspace Multica child issue/run ending at a human root creator; exact `MULTICA_PROJECT`, `ISSUE`, `RUN`, `REPO_URL`; owner-only host mapping and token key; token permission to fetch, push, and create or update the task MR; and an unclaimed source/target MR key. Credentials must never enter argv, output, Git config, artifacts, or commits.

### `E2E-1`: module context, HTTPS sync, and MR update

After the code commit, make four different paths observable: keep `__manifest__.py` clean, stage a temporary `models/marker.py` change, modify `README.md` unstaged, and create untracked `data/untracked.xml`. Run context, assert all four states, then restore the temporary changes and prove that the checkout is clean at `TEST_CODE_SHA` before sync. The temporary state exercise does not create a third commit.

```sh
set -euo pipefail
printf '%s\n' '# staged MYL-411 marker' >> "$CHECKOUT/addons/myl411_e2e/models/marker.py"
git -C "$CHECKOUT" add addons/myl411_e2e/models/marker.py
printf '%s\n' 'unstaged MYL-411 marker' >> "$CHECKOUT/addons/myl411_e2e/README.md"
mkdir -p "$CHECKOUT/addons/myl411_e2e/data"
printf '%s\n' '<odoo/>' > "$CHECKOUT/addons/myl411_e2e/data/untracked.xml"
git -C "$CHECKOUT" diff --quiet -- addons/myl411_e2e/__manifest__.py
"$SLOT_LAUNCHER" --project "$CHECKOUT" module context --format json > "$RUN_DIR/e2e-1-module-context.json"
"$MULTICA_CLI" git "$CHECKOUT" --project "$PROJECT" --multica-project "$MULTICA_PROJECT" --issue "$ISSUE" --run "$RUN" --repository-url "$REPO_URL" --format json -- status --short > "$RUN_DIR/e2e-1-git-status.json"
git -C "$CHECKOUT" restore --staged addons/myl411_e2e/models/marker.py
git -C "$CHECKOUT" restore addons/myl411_e2e/models/marker.py addons/myl411_e2e/README.md
git -C "$CHECKOUT" clean -f -- addons/myl411_e2e/data/untracked.xml
test -z "$(git -C "$CHECKOUT" status --porcelain)"
test "$(git -C "$CHECKOUT" rev-parse HEAD)" = "$TEST_CODE_SHA"
"$MULTICA_CLI" git sync --project "$PROJECT" --multica-project "$MULTICA_PROJECT" --issue "$ISSUE" --run "$RUN" --repository-url "$REPO_URL" "$CHECKOUT" --push --format json
"$MULTICA_CLI" gitlab mr publish "$CHECKOUT" --project "$PROJECT" --multica-project "$MULTICA_PROJECT" --issue "$ISSUE" --run "$RUN" --repository-url "$REPO_URL" --source "$BRANCH" --target "$TARGET" --title "$TITLE_CREATE" --description-file "$DESCRIPTION_CREATE" --format json > "$RUN_DIR/e2e-1-mr-create.json"
"$MULTICA_CLI" gitlab mr publish "$CHECKOUT" --project "$PROJECT" --multica-project "$MULTICA_PROJECT" --issue "$ISSUE" --run "$RUN" --repository-url "$REPO_URL" --source "$BRANCH" --target "$TARGET" --title "$TITLE_UPDATE" --description-file "$DESCRIPTION_UPDATE" --format json > "$RUN_DIR/e2e-1-mr-update.json"
"$TEST_PYTHON" -m pytest -q "$CHECKOUT/tests/e2e/test_myl411_gitlab_flow.py::test_module_sync_and_mr_round_trip"
test "$(git ls-remote "$TEST_REPO_URL" "refs/heads/$BRANCH" | cut -f1)" = "$TEST_CODE_SHA"
```

Every command and pytest exit `0`; create/update returns the same MR IID and URL. The fresh-agent walkthrough repeats the public actions without pytest and checks the remote and MR directly. For the required text-derived PNG pair, capture real non-TTY help text with identical `LC_ALL=C`, `NO_COLOR=1`, and `COLUMNS=120`, then render both files with the checked-in Pillow script and font. These are labelled renders, not terminal screenshots; ANSI color, TTY progress, and interactive state are explicitly outside this pair.

```sh
LC_ALL=C NO_COLOR=1 COLUMNS=120 "$BASELINE_SLOT_LAUNCHER" module --help > "$RUN_DIR/e2e-1-module-help-baseline.txt"
LC_ALL=C NO_COLOR=1 COLUMNS=120 "$SLOT_LAUNCHER" module --help > "$RUN_DIR/e2e-1-module-help-candidate.txt"
"$TEST_PYTHON" "$CHECKOUT/tests/e2e/render_terminal.py" --input "$RUN_DIR/e2e-1-module-help-baseline.txt" --font "$CHECKOUT/tests/e2e/fixtures/terminal-font.ttf" --columns 120 --label "$BASELINE_SHA" --output "$RUN_DIR/e2e-1-module-help-baseline.png"
"$TEST_PYTHON" "$CHECKOUT/tests/e2e/render_terminal.py" --input "$RUN_DIR/e2e-1-module-help-candidate.txt" --font "$CHECKOUT/tests/e2e/fixtures/terminal-font.ttf" --columns 120 --label "$PRODUCT_SHA" --output "$RUN_DIR/e2e-1-module-help-candidate.png"
```

### `E2E-2`: atomic binding, exact runtime, and isolated HTTP transport

```sh
set -euo pipefail
"$SLOT_LAUNCHER" --project "$CHECKOUT" db refresh --restore --no-input --format json > "$RUN_DIR/e2e-2-restore.json"
"$SLOT_LAUNCHER" --project "$CHECKOUT" run --detach --wait-ready --format json -- > "$RUN_DIR/e2e-2-run.json"
"$TEST_PYTHON" "$CHECKOUT/tests/e2e/prepare_myl411_stand.py" --start-local-monitors --stand "$RUN_DIR/stand.json"
"$TEST_PYTHON" "$CHECKOUT/tests/e2e/prepare_myl411_http_caddy.py" --stand "$RUN_DIR/stand.json" --private-config "$TEST_CADDY_CONFIG" --admin-url "$TEST_CADDY_ADMIN_URL" --candidate "$RUN_DIR/http-caddy.candidate" --apply
"$TEST_PYTHON" -m pytest -q "$CHECKOUT/tests/e2e/test_myl411_http_proxy.py::test_local_http_proxy_and_panel"
```

The monitor start action SHALL supervise both baseline and candidate in local mode and wait for their read endpoints before the HTTP route reload.

The monitor runs in local mode under the test project's foreground supervisor and must be ready before pytest. `prepare_myl411_http_caddy.py` reads the separate Caddy's existing full config and private listener/admin parameters; it SHALL preserve the health route and admin listener, add distinct test-owned HTTP Host routes for baseline and candidate Odoo/local monitors, validate the complete candidate with Caddy, then reload only `TEST_CADDY_ADMIN_URL`. It SHALL reject any path/process/admin identity matching the primary Caddy, preserve prior config bytes, and register a fail-safe restore/reload before mutation. No SDK publication settings or `publish` command are used. The test SHALL confirm restored database/filestore binding, ready runtime identity, authenticated Odoo login, redirects, assets, attachment and bus/WebSocket through HTTP, local panel SPA/read API, missing Basic Auth rejection and unknown Host non-routing. It SHALL assert the local snapshot against the runtime and SHALL NOT expect a production external URL or external-mode pgAdmin omission. The exact HTTPS route, forwarded-origin, external-mode and secure-CSRF contracts remain with the CI checks mapped above.

The fresh agent repeats restore/run and the test-owned HTTP route application in a reset stand, uses the separate baseline/candidate browser profiles from `stand.json`, and checks the JSON endpoint plus browser paths independently. Capture the root-help render pair with the same text method as `E2E-1`. Capture the actual local-panel screenshot pair with pinned Playwright/Chromium at `1440x900`, the same test fixture and locale, and a full database/filestore/browser reset between revisions:

```sh
LC_ALL=C NO_COLOR=1 COLUMNS=120 "$BASELINE_SLOT_LAUNCHER" --help > "$RUN_DIR/e2e-2-root-help-baseline.txt"
LC_ALL=C NO_COLOR=1 COLUMNS=120 "$SLOT_LAUNCHER" --help > "$RUN_DIR/e2e-2-root-help-candidate.txt"
"$TEST_PYTHON" "$CHECKOUT/tests/e2e/render_terminal.py" --input "$RUN_DIR/e2e-2-root-help-baseline.txt" --font "$CHECKOUT/tests/e2e/fixtures/terminal-font.ttf" --columns 120 --label "$BASELINE_SHA" --output "$RUN_DIR/e2e-2-root-help-baseline.png"
"$TEST_PYTHON" "$CHECKOUT/tests/e2e/render_terminal.py" --input "$RUN_DIR/e2e-2-root-help-candidate.txt" --font "$CHECKOUT/tests/e2e/fixtures/terminal-font.ttf" --columns 120 --label "$PRODUCT_SHA" --output "$RUN_DIR/e2e-2-root-help-candidate.png"
"$TEST_PYTHON" "$CHECKOUT/tests/e2e/capture_myl411_panel.py" --stand "$RUN_DIR/stand.json" --revision baseline --url "$BASELINE_PANEL_URL" --viewport 1440x900 --output "$RUN_DIR/e2e-2-panel-baseline.png"
"$TEST_PYTHON" "$CHECKOUT/tests/e2e/capture_myl411_panel.py" --stand "$RUN_DIR/stand.json" --revision candidate --url "$PANEL_URL" --viewport 1440x900 --output "$RUN_DIR/e2e-2-panel-candidate.png"
```

The capture script fails on revision/stand mismatch, browser console errors, missing ready selector, or unsanitized origin/identity. Do not capture Odoo pages because the proxied Odoo UI is unchanged.

### `E2E-3`: publication lifecycle disposition

The product's candidate validation, full-file reload, rollback, stop/restart publication projection and idempotent unpublish SHALL be verified by the exact `offline-tests` nodes in the matrix at the candidate SHA. The Tester SHALL inspect the CI job result and relevant generated route/command assertions independently and report the absence of live product-publication evidence. The isolated HTTP Caddy is a transport fixture, not the OdCLI-owned route file. No fault is injected into either Caddy admin endpoint during local E2E, and no HTTPS/TLS readiness is required.

### Test-repository publication, Multica evidence, and cleanup

Push `TEST_CODE_SHA` and update the same task MR. When a repository-native functional result exists, push its separate `FUNCTIONAL_SHA` after the test-code commit; otherwise the MR contains only the test scripts and required fixtures. GitLab links are `/-/commit/$TEST_CODE_SHA`, optional `/-/commit/$FUNCTIONAL_SHA`, the applicable `/-/compare/$TEST_BASE_SHA...<last-functional-or-test-sha>`, `/-/blob/<revision>/<functional-or-test-path>`, and `/-/merge_requests/$MR_IID`. The GitLab MR must not contain captured PNG/text output, run or diagnostic JSON, provenance, command transcripts, exit codes, hashes, assertion results, environment dumps, binaries, videos, or decorative reports. JSON belongs in GitLab only when JSON generation is itself the tested repository-native function.

The final Multica evidence comment contains the internal MR/commit/diff/blob links, `PRODUCT_SHA`, `TEST_CODE_SHA`, optional `FUNCTIONAL_SHA`, exact commands and exit codes, and summarized assertions. Attach the sanitized run outputs/diagnostic JSON, provenance and hashes needed for verification, plus all six required PNGs. Post with `--content-file`, request JSON output, then read the created comment through the CLI and verify every required link and attachment ID. A missing revision, command result, required link, output/provenance attachment, or required image makes verification incomplete.

Cleanup is fail-closed: stop dependent cases after any failure; restore the separate HTTP Caddy's prior complete config and reload it through the verified test admin endpoint, confirming the preflight health route and original admin listener still work; stop the captured runtime and local monitor; then run `"$TEST_PYTHON" "$CHECKOUT/tests/e2e/prepare_myl411_stand.py" --cleanup --stand "$RUN_DIR/stand.json"` and require exit `0` plus absence of both numbered manifests, roots, disposable databases, filestores, ports, and browser profiles. Never mutate or reload the primary Caddy. Remove only run-owned temporary credentials, the run directory, local test worktrees, and other disposable stand state after that assertion. Preserve the task MR, remote branch, test scripts/fixtures, and every functional commit for later inspection. Closing, merging, or deleting the MR or branch is outside E2E cleanup.

## Estimation Evidence and Assumptions

- Evidence inspected on the approved base: merged `packages/odcli-multica` models/client/CLI/tests, public typed `multica-py` issue parent/creator/workspace member surfaces, core adoption schema/planning/execution/cleanup and acceptance tests, current `RuntimeView`, `internal/dbprep`, atomic project manifest writer, project/environment runtime identity, `ModuleResource`, `GitResource`/exact-lease sync, dotenv/redaction, monitor/msgspec/OpenAPI/generated TypeScript/React flow, FastAPI Host/CSRF boundary, dependency metadata, and existing test harnesses.
- Already satisfied and excluded from remaining effort: dependency/package creation, typed checkout/daemon compatibility, task context verification, COPY preparation delegation, caller-owned checkout adoption/persistence, non-destructive cleanup, and their existing focused/integration acceptance tests.
- The estimate counts each remaining focused check with its owning implementation row and counts central CLI registration, shared documentation/generated artifacts, and repository gates only in the terminal row. No blanket testing/review percentage, new acceptance harness, trace matrix, publication database, provider framework, or repeated full-gate pass is included.
- Existing database preparation, detached runtime, process execution/redaction, Git exact-lease, safe module parsing, monitor/OpenAPI generation, React/Mantine, and disposable Odoo fixtures are reused as observed.
- The local E2E supplies a separate HTTP Caddy with a private test admin endpoint and valid test Host mappings. HTTPS/TLS is outside local preflight; production publication semantics are covered by the named repository checks. External waiting is excluded from active developer-hours.
- Tests were not run to manufacture timing evidence. The estimate uses the inspected source/spec snapshot and is uncalibrated because comparable execution history was not supplied.
- The estimate is invalidated by changing the pinned Multica public contract, adding other forges/proxies, DNS automation, public Caddy administration, pgAdmin publication, automatic merge, arbitrary addon roots, a generic integration framework, or a second publication store.
