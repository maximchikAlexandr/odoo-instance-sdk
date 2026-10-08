## Context

The current `origin/main` baseline already provides most low-level mechanics that this change must reuse:

- `commands/context.py` resolves project and environment owners into one `RuntimeView`; `db refresh` and `db restore` already converge on `EnvironmentResource.refresh_database_command()` and can run a bounded auxiliary Database Manager for a stopped project.
- `internal/dbprep/` validates sources, restores databases, retains failure artifacts, and atomically changes `ProjectConfig.default_source_database`; `resources/instance/runtime.py` consumes that field for project launches.
- detached launches persist project/environment runtime identity and implement bounded readiness and safe stop semantics.
- `ModuleResource` safely parses ordered addon roots and `GitResource` already implements commit/check/absorb/sync through `internal/proc`, but module facts are single-worktree/filesystem-centric and sync rejects HTTPS publication.
- `EnvironmentMonitor` owns the canonical typed snapshot, FastAPI exports it through one msgspec/OpenAPI bridge, and the React client is generated from that schema. The server currently enforces loopback Host/bind rules and the UI opens local runtime URLs.
- the canonical user root, owner-only project dotenv loader, central process redaction, catalog migrations, and output inventory provide the required safety foundations.

The repository does not contain a merged Multica checkout-adoption/identity integration or a Caddy/GitLab control plane. The planning attachments describe earlier research against a different snapshot; this design uses the current base `c1e57b79f39e529a50c25818134c06309384ee23` and treats unmerged branches as evidence, not as available implementation.

## Goals / Non-Goals

**Goals:**

- Make database preparation, runtime launch/status/stop, module context, Git/GitLab, and publication independently composable through typed SDK operations and CLI leaves.
- Keep one owner-neutral project/environment runtime path and one database preparation path.
- Resolve all GitLab authority from the human creator of the root Multica issue, with no caller-provided user identity or machine-account fallback.
- Publish stable HTTPS addresses through an OdCLI-owned Caddy aggregate without risking existing routes on a failed update.
- Preserve the canonical monitor/OpenAPI/TypeScript data path and make external panel access explicit and fail-closed.
- Keep secrets out of argv, remote URLs, persisted public state, plans, fingerprints, logs, and errors.

**Non-Goals:**

- Skill orchestration, LLM analysis, automatic task-status updates, or a monolithic “prepare everything” command.
- Creating worktrees during adoption, taking ownership of Multica checkout files, or updating adopted code outside the credential-aware Git wrapper.
- Managing arbitrary Caddy/Nginx configuration, DNS zones, certificates outside Caddy, public Caddy administration, or unrestricted on-demand TLS.
- Publishing pgAdmin, bypassing Odoo authentication/authorization, or using Basic Auth as application-level authorization.
- Supporting non-GitLab forge APIs, SSH credential substitution, automatic MR merge, force push without an exact lease, or automatic retry of non-idempotent provider calls.
- Executing addon manifests or maintaining a second module index.

## Decisions

### 1. Extend the existing owner-neutral seams

New bounded operations attach to existing resources instead of adding a workflow service. Project and environment selectors continue to resolve through `ResolvedContext`/`RuntimeView`; database work continues through `refresh_database_command()`; runtime work continues through `OdooInstance`; module and Git work extend their concrete resources; monitor remains the only snapshot authority.

Alternative considered: an `odcli-multica prepare` command coordinating refresh, run, publish, and MR. Rejected because the issue requires independently callable steps and such a coordinator would duplicate compensation, output, and process boundaries.

### 2. Keep the project database/filestore binding in one atomically replaced manifest

Extend `ProjectConfig` with a managed filestore binding paired with `default_source_database`. Preparation captures the current manifest identity, performs all existing restore/postcondition stages, validates the restored filestore as a contained non-symlink path, and writes both fields in one existing atomic manifest replacement. Download-only does not write either field. Execution re-reads the manifest under the preparation lock before replacement, so a concurrent successful binding produces a stale-plan failure rather than lost update.

Project runtime construction uses only this pair for the main checkout: the database becomes `StartConfig.db_name` and the contained filestore parent becomes the effective `data_dir`/filestore source. The source `odoo.conf` remains immutable. Existing retained-artifact behavior applies if binding publication fails after restore.

Alternative considered: a new catalog-only `project_bindings` table. Rejected because the current project default is already manifest-backed and used by runtime/doctor/PostgreSQL paths; a second authority would require reconciliation and could split database from filestore.

### 3. Represent adoption as an externally owned environment

Add a concrete `adopt_command()` beside existing checkout operations. It captures canonical worktree, Git common directory, branch/HEAD, Multica binding, project configuration, database/runtime inputs, and stores `checkout_owner = "multica"` plus the immutable external checkout identity in the environment catalog. Adoption runs no Git or database/runtime mutation.

Lifecycle code branches only at filesystem ownership boundaries: runtime, database, module, monitor, publication, and credential-aware Git reuse the normal owner-neutral path; generic sync and worktree deletion are prohibited. Removal may clean OdCLI-owned generated config, database, publication, and catalog state only after it proves runtime/route cleanup, but never modifies the external checkout.

Alternative considered: treating a Multica checkout as a main project only. Rejected because multiple task checkouts require stable environment identities, database/runtime isolation, monitor rows, and independent publication while remaining externally owned.

### 4. Expand module context without weakening containment

Add ordered `addon_repositories` declarations to project configuration. A root is eligible only when it is contained by the selected main/adopted checkout or one declared canonical repository. `ModuleResource` keeps first-root precedence and safe `ast.literal_eval`; the new context result enriches each candidate with repository identity and Git change facts from the existing Git process boundary.

Installed state is queried read-only from the selected ready database through the existing Odoo shell/transport seam and represented independently from filesystem presence/version. Component availability is an enum/result per source, so a database failure cannot masquerade as “not installed.” No cache or provider registry is added.

Alternative considered: recursively scanning parent directories or accepting arbitrary absolute addon roots. Rejected because it breaks the current traversal/symlink boundary and can mix client repositories.

### 5. Put Multica/GitLab identity in a concrete optional integration

Ship an `odcli-multica` optional integration module/extra in this distribution, lazily imported only by adoption or credential-requiring Git/GitLab operations. It pins a compatible public `multica-py` release and uses its typed checkout/project/issue/workspace resources. The resolver requires one checkout binding, walks parents with a visited set and workspace invariant, and accepts only a human root creator.

Project manifest configuration stores non-secret rows keyed by Multica user ID: exact normalized GitLab host, login, and a reserved dotenv token-key name. The existing owner-only `.odcli/.env` parser supplies values with process precedence. One private frozen context is shared by Git and MR construction; public models retain only safe identifiers.

Alternative considered: accept `--user-id`, use the issue assignee, or let Git's global helper decide. Rejected because each violates the explicit root-creator attribution and isolation contract.

### 6. Use a process-local Git credential helper

For an allowed HTTPS GitLab host, the command snapshot sets `GIT_TERMINAL_PROMPT=0` and a packaged non-interactive `GIT_ASKPASS` helper only for that child. Login and token are private environment values covered by central redaction. The origin URL and Git configs remain unchanged. Before injection, a read-only resolver derives exactly one remote host from explicit URL arguments or named repository remotes; an ambiguous/unmapped host fails.

`GitResource.passthrough_command()` prepends only `git -C <root>` and the exact post-delimiter arguments. Local commands skip identity resolution. Remote commands, `ls-remote`, fetch, and push share the same credential snapshot. Existing sync rebase/check/exact-lease transitions stay intact; only the SSH-only precondition becomes SSH-or-proven-HTTPS.

Alternative considered: embedding the token in the remote URL or running `git credential approve`. Rejected because both leak/persist credentials and couple concurrent users.

### 7. Make GitLab MR publication idempotent by exact key

Use the existing bounded `httpx` transport family with a GitLab adapter. The lookup key is `(host, project, source_branch, target_branch, state=open)`. Zero matches leads to create; one leads to update; more than one fails. The UTF-8 description file is opened without following symlinks, bounded, fingerprinted during planning, and revalidated before mutation. The canonical Multica issue link is appended once. Assignee input resolves to exactly one GitLab user before mutation.

Create is not retried automatically. Update revalidates the unique MR identity immediately before the request. Results expose provider ID/URL and `created|updated`, never response headers or credential material.

Alternative considered: match by title or update the newest MR. Rejected because both can mutate the wrong review.

### 8. Use one locked OdCLI-owned Caddy aggregate

Add owner-only `~/.odcli/config/publication.toml` and an OdCLI-owned generated aggregate below the canonical user root. Settings contain the normalized domain suffix, Caddy executable/control target, panel label, Basic Auth username/password hash, and trusted proxy addresses. Plaintext passwords are invalid.

Catalog migration adds publication rows keyed by `(owner_kind, owner_id)` with route identity, external URL, captured local endpoint/runtime identity, desired/observed state, sanitized last error, and timestamps. A single publication lock serializes changes. Mutation reads current rows, constructs a deterministic complete candidate for OdCLI routes, writes a private temporary file, runs `caddy validate`, then `caddy reload` through `internal/proc`. Only after reload succeeds are the aggregate and catalog state atomically published. Failed validation/reload leaves the prior authoritative file and rows unchanged. The configured Caddy instance must dedicate that aggregate to OdCLI; unowned ingress files are never edited.

Stable host labels are derived from already stable project/environment IDs and remain within one DNS label. Routes enforce HTTPS, hashed Basic Auth, explicit Host match, standard proxy headers, Odoo HTTP/static/download paths, and the Odoo 19 bus/WebSocket path. No catch-all route exists.

Alternative considered: mutate Caddy's live JSON tree per route. Rejected because partial multi-call updates complicate rollback and ownership. Alternative Nginx support is excluded because the issue selects Caddy and the attached research identifies Caddy's simpler dedicated-node TLS path.

### 9. Preserve route identity across stop, clean it before environment deletion

Stop does not remove a route row; monitor combines the saved publication with the exact runtime identity and reports `backend_unavailable`. Republish after restart updates the backend while retaining the external URL. Explicit unpublish removes the route and row idempotently.

Environment removal inserts route cleanup before destructive environment finalization. Failure leaves the environment retryable as `cleanup_failed`; the externally owned checkout remains untouched. No missing-directory heuristic removes a project route.

Alternative considered: automatically unpublish on every stop. Rejected because it destroys stable availability identity and creates unnecessary Caddy churn during normal restarts.

### 10. Add a strict proxy mode rather than relaxing loopback checks

`create_app()` receives a typed proxy policy. Local mode keeps current loopback bind and Host middleware. External mode binds only the configured proxy-facing local interface, checks the direct peer against exact trusted addresses, accepts only configured panel Host, and trusts forwarded scheme/host only from that peer. The CSRF cookie becomes `Secure`; same-origin comparison uses the validated effective HTTPS origin. Caddy remains the Basic Auth boundary.

The canonical snapshot gains one `PublicationSnapshot` used by project and environment rows. Local `RuntimeMetrics.http_url` stays separate. OpenAPI/codegen propagate the type to React. The UI never synthesizes URLs: `Open Odoo` uses an available external URL or is disabled with the typed reason. External mode disables loopback-only pgAdmin. Styling changes remain within the existing Mantine application but use compact Odoo-like navigation, table/list density, status pills, colors, typography, and controls.

Alternative considered: accept arbitrary Host/Forwarded headers or replace local URLs with external ones. Rejected because the former enables host-header/proxy spoofing and the latter loses operational observability.

### 11. Keep migrations and compatibility additive

Catalog migrations add adoption ownership/evidence and publication rows without changing existing environment/runtime identifiers. Legacy managed environments default to SDK-owned; absence of publication rows means `unpublished`. Project manifests without the new filestore/addon/mapping settings continue to load, but project run after a newly restored binding requires the paired safe filestore. Existing SSH sync, local monitor, environment checkout, project restore, and generated API fields remain compatible.

Verification includes unit matrices for lineage/credential failures, secret canaries, Git host parsing and concurrent users, MR create/update/ambiguity, project binding stale/failure paths, adoption non-ownership, module partial availability, publication rollback/concurrency, proxy spoofing/CSRF, schema/codegen determinism, UI behavior, and catalog migrations. Integration tests use fake Multica/GitLab/Caddy boundaries plus native Git HTTPS-helper fixtures; opt-in acceptance covers disposable Odoo 19 login/assets/redirect/attachment/bus behavior through Caddy.

## Risks / Trade-offs

- **[Public `multica-py` surface differs from the research snapshot]** → Pin one compatible release, isolate calls in the concrete integration adapter, and test missing/ambiguous parent/creator cases without private HTTP fallback.
- **[Token leaks through Git prompts or provider diagnostics]** → Use private environment snapshots, central secret-canary redaction, non-interactive helpers, bounded outputs, and architecture tests forbidding token-bearing URLs/argv/config writes.
- **[Cross-user Git concurrency]** → Never mutate process-global environment or Git configuration; each command carries an immutable child-only environment and tests simultaneous users.
- **[Caddy reload disrupts working routes]** → Serialize, validate a complete deterministic candidate, reload before persistent publication, and retain the last known-good aggregate/rows on every failure.
- **[DNS/TLS is externally unavailable]** → Treat route application, DNS reachability, certificate readiness, and backend readiness as distinct typed states; do not claim availability from file generation alone.
- **[Proxy trust is misconfigured]** → Reject incomplete/wildcard trust settings and direct untrusted peers; local mode remains the default.
- **[Project binding spans manifest, restored DB, and filestore]** → Hold the existing preparation lock, atomically replace paired fields only after all postconditions, and surface retained artifacts without rolling back the old binding.
- **[Adopted checkout cleanup deletes caller data]** → Persist external ownership, centralize the removal guard, and test that no worktree Git/file mutation is planned.
- **[Large cross-cutting implementation conflicts]** → Delivery packages isolate foundation, identity/Git, runtime/publication, module context, and panel surfaces with shared model/schema predecessors and explicit file ownership.

## Migration Plan

1. Add compatible configuration/model/catalog migrations and preserve legacy defaults.
2. Implement project binding and adoption foundations with migration and failure-recovery tests.
3. Add concrete Multica identity/credential resolution, Git passthrough/HTTPS sync, and GitLab MR publication behind the optional integration extra.
4. Add publication persistence, Caddy candidate/reload boundary, lifecycle reconciliation, and focused integration tests.
5. Extend canonical monitor/OpenAPI/generated TypeScript, then update trusted proxy mode and React presentation.
6. Run format, lint, strict typing, architecture inventory, unit/integration/dashboard/codegen/package gates, strict OpenSpec validation, and opt-in Caddy/Odoo acceptance where prerequisites exist.

Rollback disables external monitor mode, unpublishes owned routes through the recorded owner set, restores the last known-good Caddy aggregate, and deploys the prior package. Additive catalog columns/tables and manifest fields remain readable/ignorable; restored databases and caller-owned checkouts are never deleted merely by package rollback.

## Open Questions

None. DNS delegation, a dedicated reachable Caddy instance, valid TLS issuance prerequisites, and operator-supplied user/credential mappings are deployment prerequisites; their absence is a typed runtime failure, not an implementation choice.
