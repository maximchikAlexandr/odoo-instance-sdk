## Delivery Contract

- **Task key:** `MYL-409`
- **Change:** `extend-odcli-automation-primitives`
- **Approved base:** `origin/main@c1e57b79f39e529a50c25818134c06309384ee23`
- **Planning branch:** `feat/MYL-409-extend-odcli-automation-primitives-v3`
- **Graph revision:** `MYL-409-GR3`
- **Delivery mode:** `dag`
- **Sizing source:** authoritative `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` properties on the root planning issue; numerical totals are intentionally not duplicated in artifacts
- **Topology decision:** the weighted property remains above the multi-WP threshold and stages 1 and 2 each contain genuinely independent write scopes
- **Estimate basis:** remaining active developer-hours for one experienced developer familiar with the repository and stack, without AI acceleration; each row includes its focused checks and likely repair, shared integration/verification is counted once
- **Confidence:** medium and uncalibrated; existing database/runtime/module/Git/monitor/UI seams are directly reusable, while public `multica-py`, Caddy/Odoo proxy behavior, and external trusted-proxy acceptance remain bounded integration risks
- **Ponytail constraint:** use existing concrete resources, process/HTTP boundaries, configuration conventions, generated contracts, and test harnesses; do not add a coordinator, plugin/provider registry, publication database, second module index, optional integration distribution, new acceptance harness, or duplicate verification layer

Any product scope, public contract, persistence owner, external dependency, direct dependency edge, owned responsibility, or approved-base change requires a new graph revision. Operational status, assignee, WIP, and stage promotion do not change this artifact.

## DAG and Stages

```text
WP-01 ─┬──────────> WP-04 ─┐
       └──────────> WP-05 ─┤
WP-02 ─┬─> WP-03 ──────────┤
       ├─> WP-04 ──────────┤
       └─> WP-05 ──────────┴─> WP-06
```

Direct edge set: `WP-01→WP-04`, `WP-01→WP-05`, `WP-02→WP-03`, `WP-02→WP-04`, `WP-02→WP-05`, `WP-03→WP-06`, `WP-04→WP-06`, `WP-05→WP-06`.

Required child stage mapping:

| Work package | Stage |
|---|---:|
| `WP-01-project-binding-runtime` | 1 |
| `WP-02-multica-adoption-identity` | 1 |
| `WP-03-git-gitlab` | 2 |
| `WP-04-module-context` | 2 |
| `WP-05-caddy-publication` | 2 |
| `WP-06-monitor-panel-verification` | 3 |

Stages are consecutive topological layers. Stage 1 permits project binding/runtime and Multica adoption/identity concurrently because they edit disjoint project/runtime versus environment/integration paths. Stage 2 permits Git/GitLab, module context, and Caddy publication concurrently after their required contexts exist. WIP is operational and is not encoded as an edge.

The direct edge set is unchanged after ownership correction: `WP-03` and `WP-05` remain genuinely independent feature packages and neither writes the central CLI registry. Each exports its complete Click command/group object. Their existing common successor `WP-06` is the sole owner of `src/odoo_instance_sdk/commands/cli_parts/registration.py` and wires both completed surfaces during convergence. This single-owner contract removes the shared-file conflict without inventing a dependency between unrelated GitLab and publication behavior.

## Task Coverage

Every OpenSpec task is owned exactly once:

| OpenSpec tasks | Work package |
|---|---|
| `1.1`–`1.4` | `WP-01-project-binding-runtime` |
| `2.1`–`2.5` | `WP-02-multica-adoption-identity` |
| `3.1`–`3.5` | `WP-03-git-gitlab` |
| `4.1`–`4.4` | `WP-04-module-context` |
| `5.1`–`5.5` | `WP-05-caddy-publication` |
| `6.1`–`6.5`, `7.1`–`7.3` | `WP-06-monitor-panel-verification` |

## WP-01-project-binding-runtime

- **Stage:** 1
- **OpenSpec task coverage:** `1.1`–`1.4`
- **Direct `depends_on`:** none
- **Independent deliverable:** a main checkout can publish one paired database/filestore binding through the existing project manifest and run/status/stop the exact detached runtime that consumes it.
- **Owned responsibility scope:** `project.py` fields/serialization, existing `internal/dbprep/` project publication, restore projections, project runtime construction/identity/readiness, and directly related focused tests. Critical shared files are `src/odoo_instance_sdk/project.py`, project-facing preparation/materialization code, `commands/db.py`, project runtime modules, and their tests. It also defines ordered addon repositories consumed read-only by WP-04.
- **Contract surface:** backward-compatible managed filestore and addon repositories; atomic stale-checked database/filestore publication; complete bounded restore outcome; exact project runtime/readiness identity.
- **DoD / evidence:** old/new manifests round-trip; download-only and every failed/stale preparation preserve the prior pair; successful restore feeds the exact project run; focused ready/timeout/stale identity checks and existing project/environment compatibility tests pass.
- **Parallel-safety rationale:** it owns project manifest, preparation, and project runtime paths. WP-02 owns environment catalogue/adoption and the Multica adapter, so stage-1 writes do not overlap.

## WP-02-multica-adoption-identity

- **Stage:** 1
- **OpenSpec task coverage:** `2.1`–`2.5`
- **Direct `depends_on`:** none
- **Independent deliverable:** one existing Multica checkout is registered as externally owned without mutation, and one concrete adapter resolves its root human creator plus exact host-scoped GitLab credential context.
- **Owned responsibility scope:** the additive environment ownership field/migration, `EnvironmentResource` adoption and filesystem ownership guards, `commands/env.py`, one in-package Multica adapter, owner-only mapping reader, public `multica-py` dependency metadata, and focused tests. Critical shared files are environment catalogue/schema/migration/accessors, adoption/removal/sync code, dependency metadata, integration module, and their tests.
- **Contract surface:** one external checkout identity; `checkout_owner=multica`; no worktree creation/deletion/generic sync; finite same-workspace lineage; human root creator; exact user/host/login/token-key mapping; private per-command credential value and safe public identifiers.
- **DoD / evidence:** valid adoption has no Git/database/runtime/filesystem effects; removal leaves checkout bytes/Git state untouched; missing/ambiguous/cyclic/cross-workspace/non-human lineage and credential gaps fail before remote/mutation; two users remain isolated; one secret-canary check passes.
- **Parallel-safety rationale:** it does not edit project manifest/dbprep/project-runtime paths owned by WP-01. Shared process redaction is reused rather than redesigned.

## WP-03-git-gitlab

- **Stage:** 2
- **OpenSpec task coverage:** `3.1`–`3.5`
- **Direct `depends_on`:** `WP-02-multica-adoption-identity`
- **Independent deliverable:** native Git passthrough, HTTPS sync, and exact-key GitLab MR create/update operate as the resolved root creator without persisting or exposing credentials.
- **Owned responsibility scope:** `resources/git.py`, `internal/git_sync.py`, `commands/git.py`, a self-contained GitLab Click group/leaf module, the small askpass helper, concrete GitLab `httpx` module, and focused native-Git/fake-provider tests. It consumes WP-02 identity values without editing adoption/mapping code. It does not edit the central `commands/cli_parts/registration.py` registry.
- **Contract surface:** literal `--` raw transport; child-only credential environment; existing SSH and exact-lease sync semantics; exact project/source/target MR key; bounded description file; created/updated result; one importable, fully constructed top-level `gitlab` Click group for WP-06 to register without adapting its callbacks.
- **DoD / evidence:** local Git needs no identity; remote URL/argv/config remain secret-free; native exit/stdio behavior holds; HTTPS sync retains stale-lease refusal; MR zero/one/many and provider-failure cases pass without a live GitLab service; the exported group exposes the specified help and `mr publish` leaf in isolation.
- **Parallel-safety rationale:** owns only Git/GitLab paths and tests and exports its Click group without touching the central registry; it does not edit module, Caddy, monitor, or UI files used by stage-2 siblings.

## WP-04-module-context

- **Stage:** 2
- **OpenSpec task coverage:** `4.1`–`4.4`
- **Direct `depends_on`:** `WP-01-project-binding-runtime`, `WP-02-multica-adoption-identity`
- **Independent deliverable:** one read-only module context combines safe ordered filesystem modules with repository, Git-change, dependency, and installed-version facts while preserving partial availability.
- **Owned responsibility scope:** `resources/module.py`, its CLI adapter, on-demand Git/database collection helpers, and focused tests. It consumes addon repositories and selected owner context without editing their definitions.
- **Contract surface:** first-root precedence and shadows; repository/path provenance; file versus installed version; related Git changes; per-source availability/reason; no mutation or second index.
- **DoD / evidence:** duplicate names never merge; unsafe roots are rejected; Git/database failure does not hide filesystem facts; manifests are not executed; focused multi-repository/change/availability/no-mutation checks pass.
- **Parallel-safety rationale:** owns module-specific paths only and does not edit Git/GitLab or Caddy implementation files.

## WP-05-caddy-publication

- **Stage:** 2
- **OpenSpec task coverage:** `5.1`–`5.5`
- **Direct `depends_on`:** `WP-01-project-binding-runtime`, `WP-02-multica-adoption-identity`
- **Independent deliverable:** project and environment runtimes gain stable idempotent HTTPS publish/unpublish through one locked, validated OdCLI-owned Caddy route file, with safe stop/restart/removal behavior.
- **Owned responsibility scope:** publication settings reader, one concrete publication resource, a self-contained publication Click command module, route-file generator/lock, Caddy validate/reload process steps, environment removal hook, and focused tests. It reuses existing runtime/catalog owner identities and adds no publication table or generic control plane. It does not edit the central `commands/cli_parts/registration.py` registry.
- **Contract surface:** deterministic owner labels/URLs; exact ready backend; one complete owned route file; validate/reload/prior-byte restoration; HTTPS/Basic Auth/Odoo/panel routes; idempotent unpublish; removal cleanup gate; importable, fully constructed top-level `publish` and `unpublish` Click commands for WP-06 to register without adapting their callbacks.
- **DoD / evidence:** stable distinct URLs; concurrent updates keep both routes; failed validation/reload preserves prior bytes and live routes; unknown Host/auth fail closed; stopped runtime is non-actionable; removal failure preserves the adopted checkout; outputs contain no secret; exported commands expose the specified help and options in isolation.
- **Parallel-safety rationale:** owns publication modules and the narrow removal hook after WP-02 and exports complete Click commands without touching the central registry. It does not edit Git or module paths; monitor/UI and central registration consume its contracts only in WP-06.

## WP-06-monitor-panel-verification

- **Stage:** 3
- **OpenSpec task coverage:** `6.1`–`6.5`, `7.1`–`7.3`
- **Direct `depends_on`:** `WP-03-git-gitlab`, `WP-04-module-context`, `WP-05-caddy-publication`
- **Independent deliverable:** the existing monitor/OpenAPI/generated client/panel exposes publication safely behind Caddy, documents all new leaves, and passes one integrated verification run.
- **Owned responsibility scope:** sole ownership of `src/odoo_instance_sdk/commands/cli_parts/registration.py`, canonical monitor snapshot/collector, FastAPI proxy policy, OpenAPI and generated TypeScript, existing React/Mantine views/tests, focused user/deployment/recovery docs, and final existing repo gates. It consumes completed Git/module/publication contracts and does not introduce new backend subsystems or test harnesses.
- **Contract surface:** one central registry mapping the completed `gitlab`, `publish`, and `unpublish` Click objects without callback adaptation; minimal publication state/URL/reason; separate local endpoint; exact trusted proxy/Host/effective origin; secure CSRF; local compatibility; relative generated client; Odoo-like views; server-supplied external URL only.
- **DoD / evidence:** root help and lazy routing expose exactly one top-level `gitlab`, `publish`, and `unpublish` surface backed by the predecessor exports; project/environment publication renders consistently; publication read failure is isolated; spoofed forwarding/unknown hosts fail; external same-origin mutation and local mode pass; UI disables unavailable links and stays accessible/responsive; generated stale gate and existing repo gates pass once; optional smoke uses existing fixtures or reports missing prerequisites.
- **Parallel-safety rationale:** terminal convergence package and sole central-registry writer. All stage-2 feature writers finish first, so registry wiring, generated contracts, UI, docs, and integrated verification have no active sibling write conflicts.

## Estimation Evidence and Assumptions

- Evidence inspected: current `RuntimeView`, `internal/dbprep`, atomic project manifest writer, project/environment runtime identity, environment catalogue/removal, `ModuleResource`, `GitResource`/exact-lease sync, dotenv/redaction, monitor/msgspec/OpenAPI/generated TypeScript/React flow, FastAPI Host/CSRF boundary, dependency metadata, existing test harnesses, and the planning attachments.
- The estimate counts each focused check with its owning implementation row and counts central CLI registration, shared documentation/generated artifacts, and repository gates only in the terminal row. No blanket testing/review percentage, new acceptance harness, trace matrix, publication database, provider framework, or repeated full-gate pass is included.
- Existing database preparation, detached runtime, process execution/redaction, Git exact-lease, safe module parsing, monitor/OpenAPI generation, React/Mantine, and disposable Odoo fixtures are reused as observed.
- Public `multica-py` must expose the required typed traversal. If it does not, the missing public API becomes a separate upstream change rather than private HTTP work inside this estimate.
- Deployment supplies DNS/TLS prerequisites, a dedicated local Caddy instance including the owned route file, and valid mappings. External waiting is excluded from active developer-hours.
- Tests were not run to manufacture timing evidence. The estimate uses the inspected source/spec snapshot and is uncalibrated because comparable execution history was not supplied.
- The estimate is invalidated by adding other forges/proxies, DNS automation, public Caddy administration, pgAdmin publication, automatic merge, arbitrary addon roots, a generic integration framework, or a second publication store.
