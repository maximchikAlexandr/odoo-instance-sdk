## Delivery Contract

- **Task key:** `MYL-409`
- **Change:** `extend-odcli-automation-primitives`
- **Approved base:** `origin/main@c1e57b79f39e529a50c25818134c06309384ee23`
- **Planning branch:** `feat/MYL-409-extend-odcli-automation-primitives`
- **Graph revision:** `MYL-409-GR1`
- **Delivery mode:** `dag`
- **Sizing source:** authoritative `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` custom properties on the root planning issue; numerical totals are intentionally not duplicated in repository artifacts
- **Topology decision:** the authoritative weighted property is above the multi-WP threshold and the graph has real independent frontiers at stages 2 and 3
- **Estimate basis:** remaining work for one experienced developer familiar with Python, Click, msgspec, Git, GitLab, Caddy, FastAPI, React/TypeScript, SQLite migrations, and this repository, without AI acceleration; active developer-hours include investigation, implementation, tests, review fixes, and attended verification, while external queues and unavailable infrastructure are excluded
- **Confidence:** medium-low and uncalibrated; existing database/runtime/module/Git/monitor seams are inspectable, while the public `multica-py` compatibility surface, host credential isolation, externally owned checkout migration, Caddy/TLS topology, Odoo proxy acceptance, and cross-language generated-contract work create material but bounded uncertainty

Any product-scope, public-contract, persistence-owner, external dependency, direct dependency edge, owned responsibility, or approved-base change requires a new graph revision. Operational status, assignee, WIP, and stage promotion do not change this artifact.

## DAG and Stages

```text
WP-01 ─┬─> WP-02 ───────────────┐
       └─> WP-03 ─┬─> WP-04 ────┼──────────────┐
                  ├─> WP-05 ────┼──────────────┤
                  └─> WP-06 ─> WP-07 ──────────┴─> WP-08
                        ^
                        └──────── WP-02
```

Direct edge set: `WP-01→WP-02`, `WP-01→WP-03`, `WP-03→WP-04`, `WP-03→WP-05`, `WP-02→WP-06`, `WP-03→WP-06`, `WP-06→WP-07`, `WP-04→WP-08`, `WP-05→WP-08`, `WP-07→WP-08`.

Required child stage mapping:

| Work package | Stage |
|---|---:|
| `WP-01-foundations` | 1 |
| `WP-02-project-binding-runtime` | 2 |
| `WP-03-multica-adoption-identity` | 2 |
| `WP-04-git-gitlab` | 3 |
| `WP-05-module-context` | 3 |
| `WP-06-caddy-publication` | 3 |
| `WP-07-monitor-http-contracts` | 4 |
| `WP-08-panel-docs-verification` | 5 |

Stages are consecutive topological layers. Stage 2 permits `WP-02` and `WP-03` concurrently; after both predecessors needed by publication close, stage 3 permits `WP-04`, `WP-05`, and `WP-06` concurrently. WIP limits are operational and are not encoded as DAG edges.

## Task Coverage

Every OpenSpec task is owned exactly once:

| OpenSpec tasks | Work package |
|---|---|
| `1.1`–`1.5` | `WP-01-foundations` |
| `2.1`–`2.6` | `WP-02-project-binding-runtime` |
| `3.1`–`3.5`, `4.1`–`4.4` | `WP-03-multica-adoption-identity` |
| `5.1`–`5.6` | `WP-04-git-gitlab` |
| `6.1`–`6.5` | `WP-05-module-context` |
| `7.1`–`7.6` | `WP-06-caddy-publication` |
| `8.1`–`8.4` | `WP-07-monitor-http-contracts` |
| `9.1`–`9.3`, `10.1`–`10.4` | `WP-08-panel-docs-verification` |

## WP-01-foundations

- **Stage:** 1
- **OpenSpec task coverage:** `1.1`–`1.5`
- **Direct `depends_on`:** none
- **Independent deliverable:** backward-compatible configuration, frozen public/private models, additive catalog migrations, publication persistence primitives, and central redaction/projection contracts that every domain package can consume without editing shared foundations concurrently.
- **Owned responsibility scope:** `project.py` configuration parsing/serialization; canonical publication settings loader and paths; new model modules and public exports; catalog schema/migrations/accessors for adoption/publication; process redaction and plan projection; CLI/public architecture inventories and directly related unit/migration/security fixtures. Critical shared files include `src/odoo_instance_sdk/project.py`, `config.py`, `internal/paths.py`, `internal/proc/redaction.py`, `storage/catalog_schema.py`, `storage/catalog_migrations/`, package export modules, and their focused tests.
- **Contract surface:** paired project database/filestore fields; ordered addon repositories; user-to-GitLab non-secret mapping; owner-only publication settings; `checkout_owner`; publication rows keyed by stable owner; safe public projections and private credential-bearing model boundaries. Later packages consume these fields and models but do not redefine or relocate them.
- **DoD / evidence:** old and new manifests round-trip; invalid permissions/hosts/paths/mappings fail safely; fresh and prior catalogues migrate to equivalent heads with existing identities preserved; legacy environments become SDK-owned and missing publication rows mean unpublished; secret canaries are absent from repr/plans/fingerprints/errors; format, focused lint/type, migration, architecture, and public-surface tests pass.
- **Parallel-safety rationale:** sole stage-1 owner of shared configuration, schema, exports, redaction, and inventory surfaces. No sibling runs concurrently.

## WP-02-project-binding-runtime

- **Stage:** 2
- **OpenSpec task coverage:** `2.1`–`2.6`
- **Direct `depends_on`:** `WP-01-foundations`
- **Independent deliverable:** a main checkout can independently refresh/restore into one atomic database/filestore binding and start, wait, inspect, and stop the exact project-owned detached runtime that consumes it.
- **Owned responsibility scope:** existing `internal/dbprep/` preparation/materialization steps; database restore result and CLI adapters; project runtime construction, detached readiness/identity/status/stop; project doctor checks; focused preparation/runtime/CLI tests and fixtures. It consumes WP-01 project fields/models and does not edit their definitions. Critical files include `internal/dbprep/materialize.py`, `materialize_steps.py`, source/restore helpers, `commands/db.py`, `resources/instance/runtime.py`, runtime identity modules, and related tests.
- **Contract surface:** captured prior binding; atomic paired publication after postconditions; stale compare-before-write; complete restore result/failure context; project runtime requires bound database/filestore; persisted exact runtime/readiness identity; project and environment selector separation.
- **DoD / evidence:** download-only leaves binding unchanged; successful remote/catalog/local restore hands the exact database/filestore to project run; every post-restore failure retains prior binding and names retained artifacts safely; concurrent publication fails stale; readiness success/exit/timeout/cleanup/PID-reuse matrices pass; existing environment restore/runtime behavior remains green.
- **Parallel-safety rationale:** may run beside WP-03 because it owns database/preparation/project-runtime paths while WP-03 owns Multica integration and environment adoption paths. Shared types/schema were frozen by WP-01.

## WP-03-multica-adoption-identity

- **Stage:** 2
- **OpenSpec task coverage:** `3.1`–`3.5`, `4.1`–`4.4`
- **Direct `depends_on`:** `WP-01-foundations`
- **Independent deliverable:** a caller-owned Multica checkout can be registered without filesystem/Git mutation, and the integration resolves its root human creator plus exact host-scoped GitLab credential context through public `multica-py` and the existing owner-only dotenv.
- **Owned responsibility scope:** optional `odcli-multica` package/extra and dependency pin; concrete Multica adapter; environment adoption planning/execution/CLI; external-owner lifecycle guards; root-lineage and credential mapping; environment catalog adoption accessors supplied by WP-01; focused fake-Multica, configuration, concurrency, removal, containment, and secret tests. Critical files include new integration modules, `resources/environment/` adoption/cleanup seams, `commands/env/`, optional dependency metadata, and dedicated tests.
- **Contract surface:** exactly one checkout/workspace/issue binding; finite same-workspace parent traversal; human root creator; user/host/login/token-key mapping; private immutable credential context; adopted environment with `checkout_owner=multica`; no generic sync/worktree deletion; no user-ID CLI parameter or machine fallback.
- **DoD / evidence:** missing/ambiguous/cyclic/cross-workspace/non-human cases fail before remote/mutation; dotenv permissions and process precedence behave as existing policy; two concurrent users remain isolated; adoption plans no Git/database/runtime effects; removal never modifies the external checkout; core/help work without the optional extra; all values are sanitized.
- **Parallel-safety rationale:** disjoint from WP-02's database/project-runtime files. It is the sole owner of the integration and adoption paths; later Git, module, and publication packages depend on its stable owner/identity contracts.

## WP-04-git-gitlab

- **Stage:** 3
- **OpenSpec task coverage:** `5.1`–`5.6`
- **Direct `depends_on`:** `WP-03-multica-adoption-identity`
- **Independent deliverable:** exact native Git passthrough, HTTPS/token-backed safe sync, and idempotent GitLab MR create/update all operate as the root issue creator without persisting or disclosing credentials.
- **Owned responsibility scope:** `resources/git.py`, `internal/git_sync.py`, Git credential-helper/remote-host modules, `commands/git.py`, new GitLab HTTP/resource/command modules, file snapshot helper use, and dedicated unit/integration fake-provider/native-Git tests. Shared models, exports, redaction, and integration context come from WP-01/WP-03 and are consumed read-only.
- **Contract surface:** `passthrough_command()` raw transport; literal `--`; child-only `GIT_ASKPASS`; SSH compatibility; proven HTTPS host; existing rebase/check/exact-lease transitions; MR exact match key; bounded UTF-8 description identity; issue-link/assignee resolution; typed create/update result.
- **DoD / evidence:** local Git bypasses credentials; remote argv/URL/config remain secret-free; raw stdio/signal/exit parity holds; concurrent users are isolated; dirty/protected/upstream/rebase/stale-lease behavior remains; MR zero/one/many and provider failures pass without live GitLab; no non-idempotent create retry.
- **Parallel-safety rationale:** can run beside WP-05 and WP-06 because it exclusively owns Git/GitLab modules and tests. It does not edit module, publication, monitor, or UI files.

## WP-05-module-context

- **Stage:** 3
- **OpenSpec task coverage:** `6.1`–`6.5`
- **Direct `depends_on`:** `WP-03-multica-adoption-identity`
- **Independent deliverable:** one read-only typed context joins safe ordered multi-repository filesystem modules, dependency paths, installed versions, repository provenance, and related Git changes with explicit partial availability.
- **Owned responsibility scope:** `resources/module.py`, module command adapter, module-context collection helpers, and focused multi-repository/database/Git availability tests. It consumes project repository declarations and adopted owner context but does not edit configuration, integration, or Git credential modules.
- **Contract surface:** preserved first-root precedence; allowed repository containment; shadow identities; file versus installed version; committed/staged/unstaged/untracked relation; per-source availability and bounded reason; inspectable read-only command.
- **DoD / evidence:** duplicate technical names never merge; unsafe/unregistered roots are not inspected; missing database is unavailable rather than uninstalled; Git failure does not hide filesystem facts; manifests are never executed; no install/update/Git/file mutation occurs; existing module catalogue/update tests remain green.
- **Parallel-safety rationale:** owns only module-specific production/tests and is independent of WP-04 Git implementation and WP-06 publication files. Repository Git facts use the shared process contract without modifying GitResource.

## WP-06-caddy-publication

- **Stage:** 3
- **OpenSpec task coverage:** `7.1`–`7.6`
- **Direct `depends_on`:** `WP-02-project-binding-runtime`, `WP-03-multica-adoption-identity`
- **Independent deliverable:** project and environment runtimes gain stable idempotent HTTPS publish/unpublish through a locked validated OdCLI-owned Caddy aggregate, with lifecycle-safe stop/restart/removal reconciliation.
- **Owned responsibility scope:** new publication resource/planning/persistence adapter; Caddy candidate generator and process boundary; publication CLI; publication lock; route cleanup integration in environment removal after WP-03; runtime publication reconciliation after WP-02; focused Caddy fake/process/config/concurrency/lifecycle tests and service documentation fragments. It consumes WP-01 settings/models/catalog and does not edit monitor/HTTP/React code.
- **Contract surface:** stable host/route IDs; exact runtime readiness/endpoint; deterministic complete aggregate; validate then reload then persist; last-known-good rollback; HTTPS/Basic Auth/headers/Odoo HTTP/assets/attachments/bus routes; panel route input; idempotent unpublish; backend-unavailable state; cleanup-failed removal.
- **DoD / evidence:** two owners cannot collide or lose routes; validation/reload failures preserve prior bytes/rows/routes; unknown Host/invalid auth never proxies; stopped/stale runtime cannot claim availability; republish retains URL; removal failure is retryable and preserves adopted worktree; plans and logs contain no auth hash/private control secret.
- **Parallel-safety rationale:** can run beside WP-04/WP-05 because it owns new publication modules and only touches environment cleanup/runtime seams after their stage-2 owners have completed. It does not modify Git/module paths.

## WP-07-monitor-http-contracts

- **Stage:** 4
- **OpenSpec task coverage:** `8.1`–`8.4`
- **Direct `depends_on`:** `WP-06-caddy-publication`
- **Independent deliverable:** the canonical monitor, FastAPI/OpenAPI, and generated TypeScript contracts expose publication for project/environment runtimes and safely serve the panel behind one explicitly trusted Caddy proxy while preserving default loopback mode.
- **Owned responsibility scope:** monitor models/collector/planning; `http/app.py`, `http/monitor.py`, serve/monitor CLI adapter; `openapi.json`; generated TypeScript SDK; HTTP/monitor/schema/codegen tests. It consumes publication rows/resource APIs and does not edit publication mutation, Git, module, or React presentation files.
- **Contract surface:** one `PublicationSnapshot`; separate local/external URLs; available/unpublished/backend-unavailable/error; component isolation; exact trusted peers/Hosts/forwarded origin; secure external CSRF; local-mode compatibility; external pgAdmin disabled; deterministic OpenAPI/codegen.
- **DoD / evidence:** project-only and environment snapshots serialize identically across monitor/API/generated client; publication failure does not hide runtime facts; forwarded-header spoofing and unknown hosts fail; external same-origin mutation passes only with secure CSRF; local mode retains current behavior; generated stale gate is clean.
- **Parallel-safety rationale:** convergence package follows publication. It may start only after WP-06 because publication schema/lookup semantics must be stable; no stage-4 sibling exists.

## WP-08-panel-docs-verification

- **Stage:** 5
- **OpenSpec task coverage:** `9.1`–`9.3`, `10.1`–`10.4`
- **Direct `depends_on`:** `WP-04-git-gitlab`, `WP-05-module-context`, `WP-07-monitor-http-contracts`
- **Independent deliverable:** an Odoo-like external-safe panel plus complete user/SDK/deployment/recovery documentation and repository-wide verification of every preceding capability on the exact integrated lineage.
- **Owned responsibility scope:** handwritten React/Mantine source and UI tests; README, changelog, Python SDK and deployment/security documentation; requirement/test trace; final CLI/public/architecture inventories; repository-wide verification evidence; opt-in Caddy/Odoo acceptance harness and directly related fixtures. Generated TypeScript remains owned by completed WP-07 and is consumed unchanged unless its authoritative schema requires a planning revision.
- **Contract surface:** compact Odoo-style project/environment views; server-supplied external URL only; disabled action plus typed reason; relative API/assets; external pgAdmin behavior; documented configuration/commands/recovery; final acceptance trace.
- **DoD / evidence:** UI tests cover project/environment available and unavailable states, no synthesized localhost/domain fallback, relative client and accessibility/responsiveness; docs contain no secret values; every delta requirement/scenario maps to passing evidence; format, Ruff, strict mypy, unit/integration/dashboard, OpenAPI/codegen, packaging, architecture and secret audits pass; opt-in Caddy/Odoo checks run when prerequisites exist or are reported as unavailable without false success.
- **Parallel-safety rationale:** terminal integration package. Direct predecessors cover every stage-3 branch and WP-07 carries WP-06/WP-02 transitively; no feature sibling writes while final verification and documentation converge.

## Estimation Evidence and Assumptions

- Evidence inspected: current project/environment selector and `RuntimeView`; common `internal/dbprep` restore/materialization pipeline and atomic project manifest write; project runtime and persisted launch identity; environment catalog/migrations and cleanup; safe `ModuleResource`; `GitResource` plus sync state machine; project dotenv and central process redaction; canonical monitor/msgspec/OpenAPI/generated TypeScript/React flow; FastAPI loopback Host/CSRF boundary; canonical user paths; repository quality and architecture inventories; current and archived nearby OpenSpec packages; both planning attachments.
- The estimate covers all implementation tasks, additive migrations, compatibility repair, focused/unit/integration/dashboard/package tests, required documentation, review-fix iterations, and attended verification. It does not count unspecified DNS/provider approval queues, certificate issuance waiting, human review delay, or unavailable opt-in infrastructure.
- Shared foundations are counted once in WP-01; domain rows do not add blanket test/review percentages because their estimates already include required tests and likely repair.
- Existing database preparation, detached runtime, process execution/redaction, Git exact-lease, monitor/OpenAPI, and React generation seams are assumed reusable as observed.
- The public `multica-py` release must expose sufficient typed checkout/issue/workspace traversal. If it lacks that public capability, the package requires a separate upstream planning change rather than private HTTP access.
- Deployment supplies wildcard or per-host DNS/TLS prerequisites, a dedicated reachable Caddy instance whose OdCLI aggregate is safe to own, and valid root-user credential mappings. Missing prerequisites block acceptance deployment but do not authorize a different proxy/security design.
- Main uncertainty is coupled across identity/credential secrecy, catalog adoption/publication migration, project binding/failure retention, Caddy rollback/TLS/WebSocket acceptance, and trusted-proxy/CSRF behavior. These risks are represented in the authoritative scenario properties rather than a separate contingency multiplier.
- Tests were not run to manufacture estimate timing evidence; source, tests, configuration, local history, and complete OpenSpec artifacts were inspected. The estimate is uncalibrated because no comparable execution history was supplied.
- The estimate is invalidated by adding non-GitLab forges, Nginx support, DNS management, public Caddy administration, pgAdmin publication, automatic merge, legacy checkout adoption without Multica proof, arbitrary addon roots, or a monolithic skill orchestrator.
