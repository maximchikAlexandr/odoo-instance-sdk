## Context

The current dashboard is intentionally local and small, but `internal/serve.py` now owns port/Host policy, FastAPI construction, snapshot locking/serialization, errors, health, static assets, browser boot, and optional imports. `web/src/api.ts` manually mirrors the complete `msgspec` snapshot tree and performs the single direct fetch. The second frontend operation—open the selected environment database in one shared local pgAdmin—turns those shortcuts into a contract drift and security risk.

The implementation starts from the post-MYL-55 observability boundary: `EnvironmentMonitor.snapshot()` is the canonical read model at schema v2 with required `observed_port` and `artifacts`; `EnvironmentResource` and existing `DatabaseResource`/`PostgresCluster` paths own environment, database, and Compose behavior. MYL-55 must be merged or rebased before implementation. This change preserves its v2 fields, removed-row behavior, CLI envelope v1, and CLI projections, must not reuse CLI envelopes for HTTP, and adds only required `pgadmin` as schema v3.

Official pgAdmin container behavior supports local single-user operation through exact `PGADMIN_CONFIG_SERVER_MODE=False`, declarative `/pgadmin4/servers.json`, replacement on startup through exact `PGADMIN_REPLACE_SERVERS_ON_STARTUP=True`, `PGADMIN_DEFAULT_PASSWORD_FILE`, and `PGPASS_FILE`. The container also receives deterministic non-secret `PGADMIN_DEFAULT_EMAIL=odoo-instance-sdk@localhost.invalid`; the generated administrator password remains required for image bootstrap but server mode does not present a login form or expose that secret to the user. Server JSON requires `Name`, `Group`, `Port`, `Username`, `MaintenanceDB`, and a host/service field and cannot import passwords. Those supported mechanisms make the returned root URL directly usable without API/UI credentials; no UI deep-link contract is needed.

## Goals / Non-Goals

**Goals:**

- Keep one typed Python model/result boundary from SDK resources through FastAPI/OpenAPI to generated React calls.
- Make OpenAPI and TypeScript output deterministic, checked in, and stale-checked in CI.
- Add a safe backend-only resolution path for one user-level pgAdmin container across SDK-owned Compose projects.
- Preserve loopback-only dashboard behavior, lazy optional imports, current CLI boot, and direct `msgspec` response bytes.
- Give the implementer exact schema names, operation IDs, error/status mappings, lifecycle states, and verification points.

**Non-Goals:**

- Remote/external PostgreSQL, multi-user pgAdmin, authentication/TLS, cloud/container-provider abstractions, or a generic HTTP/DI framework.
- CLI envelopes over HTTP, Pydantic DTO copies, Axios, hooks libraries, validation libraries in React, generated mocks, custom generator templates, or npm publication.
- Undocumented pgAdmin object-browser deep links, persistent SDK database registries, arbitrary user-supplied endpoints, or automatic database mutation.

## Decisions

### D1: Two HTTP modules and a compatibility shim

Create `src/odoo_instance_sdk/http/app.py` and `http/monitor.py`. `app.py` owns existing socket/port helpers, exact loopback Host middleware, `create_app`, `/healthz`, UI/headless composition, static mount, browser behavior, and `run_server`. `monitor.py` builds one concrete `APIRouter` from the app-scoped `EnvironmentMonitor` plus the existing app-scoped environment resource. It owns the snapshot lock, typed request decode, direct `msgspec` encode, and exception-to-HTTP mapping.

Keep `internal/serve.py` as a thin compatibility re-export during this change so existing tests/importers and `odcli monitor` remain valid; the CLI may continue importing that shim. FastAPI imports remain inside app/router factory functions. A concrete callable may be injected for route tests and schema-only export, but there is no registry, container, or public provider interface.

Alternative: keep adding routes to `internal/serve.py`. Rejected because composition and API contracts are already independent responsibilities, and code generation requires a deterministic router surface. Alternative: a generic service/DI layer. Rejected because there are only two operations and existing resources already define the application boundary.

### D2: Public `msgspec` types are the only schema source

Add public frozen models/enums in `models.py`: `PgAdminEligibilityState`, `PgAdminEligibility`, `PgAdminOpenState`, `PgAdminOpenResult`, `PgAdminOpenRequest`, `HttpErrorCode`, and `HttpError`. Starting from MYL-55 snapshot v2, extend `EnvironmentSnapshot` with the single required field `pgadmin: PgAdminEligibility` and bump `Snapshot.schema_version` from 2 to 3. Required MYL-55 fields `observed_port` and `artifacts`, their collection semantics, removed-row behavior, and every earlier field remain unchanged. Keep the direct JSON representation—no success wrapper. `HttpErrorCode` includes exact `invalid_request`, `monitor_snapshot_failed`, `environment_not_found`, `pgadmin_not_eligible`, `database_not_found`, and `pgadmin_unavailable` values.

Add one internal schema adapter in `http/monitor.py` (or a sibling private helper only if the file becomes unwieldy). It calls `msgspec.json.schema_components` for the exact route model set, moves definitions into `components.schemas`, and rewrites local definition references to `#/components/schemas/...`. Route `responses` reference those component names; a small custom `app.openapi` merger adds the generated components to FastAPI's base document without changing unrelated FastAPI schemas. Cache the final OpenAPI dict per app as FastAPI already does.

The production response path remains `msgspec.json.encode` into `Response`. Tests resolve the operation response `$ref` from the actual `app.openapi()` document and validate production bytes with one test-only JSON Schema validator; they do not validate a separately generated schema.

Alternative: duplicate models as Pydantic response models. Rejected because it creates the second Python DTO hierarchy forbidden by the issue. Alternative: hand-author OpenAPI JSON. Rejected because it can drift from runtime serialization.

### D3: Eligibility is a monitor projection; mutation stays on `EnvironmentResource`

The monitor already joins each environment to its project cluster. After cluster collection it computes `PgAdminEligibility` in fixed precedence: non-ready lifecycle, unresolved database, non-Compose/unowned cluster, non-healthy cluster, else eligible. It does not query the database or mutate Docker while polling. React uses only this state for button availability and explanation.

Add `EnvironmentResource.open_pgadmin(selector)` as the public application operation. It resolves the catalog environment and generated config, recomputes the same security-critical preconditions (UI eligibility is advisory), constructs the existing configured instance, and calls existing `DatabaseResource.exists(database)` before any pgAdmin file or Docker mutation. Confirmed absence maps to `database_not_found`; inconclusive configuration/transport maps to a sanitized unavailable error. The app constructs one existing client/environment resource for its lifetime; the client's fallback executable is irrelevant to this operation because the selected environment's generated configuration and existing psql/Docker helpers are authoritative.

Alternative: mutate through `EnvironmentMonitor`. Rejected because its existing contract is read-only. Alternative: implement resolution in the FastAPI route. Rejected because it would make HTTP the owner of catalog/database/Docker orchestration and leave no reusable typed Python operation.

### D4: A small internal pgAdmin lifecycle helper under one global lock

`EnvironmentResource.open_pgadmin` delegates only container/file mechanics to `internal/pgadmin.py`; this is a module of functions/data private to the resource, not a public service hierarchy. Add user-global paths under the SDK data/state roots for:

- one lock, deterministic container name, chosen port, and a non-secret configuration fingerprint containing a credential revision HMAC;
- persistent pgAdmin data;
- atomic `servers.json` containing one selected server definition;
- existing-or-generated administrator password, `.pgpass`, and a private per-user random HMAC key, held in a private directory with validated containment/type/ownership/mode/ACL for the pinned runtime UID.

Use an exact constant `docker.io/dpage/pgadmin4@sha256:<reviewed digest>` and its documented runtime UID `5050`. Every create/recreate supplies exactly `PGADMIN_CONFIG_SERVER_MODE=False`, `PGADMIN_REPLACE_SERVERS_ON_STARTUP=True`, deterministic `PGADMIN_DEFAULT_EMAIL=odoo-instance-sdk@localhost.invalid`, `PGADMIN_DEFAULT_PASSWORD_FILE=/run/odoo-instance-sdk/pgadmin-admin-password`, and `PGPASS_FILE=/run/odoo-instance-sdk/pgpass`. The two host files are mounted read-only at those exact paths; `/pgadmin4/servers.json` is mounted read-only and persistent data is mounted read-write at `/var/lib/pgadmin`. Docker argv/environment metadata contains only these non-secret literals and host/container paths—never password contents. Bind only `127.0.0.1:<port>:<container-port>`, mount no Docker socket, apply stable SDK labels for ownership/fingerprint, and reject a same-named container lacking the ownership label.

On Linux, create paths as the invoking user without `sudo` and use one exact POSIX ACL layout. Private traversal parents are mode `0710` with `user::rwx,user:5050:--x,group::---,mask::--x,other::---`. Credential/pass/configuration files are mode `0640` with `user::rw-,user:5050:r--,group::---,mask::r--,other::---`; the per-user HMAC key is mode `0600` and owner-only, is never mounted into Docker, and is validated as a regular file. The persistent data directory is mode `0770` with access ACL `user::rwx,user:5050:rwx,group::---,mask::rwx,other::---` and the corresponding default ACL for owner, UID 5050, empty owning group, mask, and other; files created by UID 5050 remain writable there. The group-class mode bits represent the POSIX ACL mask, not owning-group access. Validate numeric UID, invoking-user ownership of prepared host paths, exact mode/ACL entries and masks, regular-file type, containment, and mount read-only/read-write flags before Docker mutation. Missing ACL support, an ineffective or extra ACL entry, symlink, or group/other effective access fails closed with the sanitized unavailable error. Do not `chown` host paths to root/5050 or grant the owning group/world access.

Under the global lock, inspect the selected Compose container/network identity, generate a server entry with the selected database as both `MaintenanceDB` and `DBRestriction`, and connect the pgAdmin container only to the required user-defined Compose network. Use the globally unique backend-resolved PostgreSQL container name/address visible on that network; never use browser host/port input. On first use, create/start with the required startup settings so server mode opens without login and the server is imported, then return `started`. If labels/config/network and required startup settings already match, return `reused` without recreation. Matching environment validation treats image-inherited entries as additive but requires every SDK variable/value and rejects conflicting duplicates. The fingerprint is an HMAC-SHA256 over the public backend identity and selected credential using the private per-user key, so same-backend password rotation is not reused and public identity plus a candidate password cannot derive the label. Otherwise atomically rewrite configuration and recreate/restart the same named container with `PGADMIN_REPLACE_SERVERS_ON_STARTUP=True`, wait until the root is ready, refresh the persistent `/var/lib/pgadmin/.pgpass` from the selected read-only mount through an ID-scoped ownership-checked command as UID 5050 with mode `0600`, and verify the replacement server is effective before returning `reconfigured`. Reconfiguration therefore changes the selected database even with persistent `/var/lib/pgadmin`; accumulating a second SDK catalog is intentionally avoided. The bootstrap administrator password is reused after first creation, while the target-specific escaped PostgreSQL passfile is atomically replaced when the selected server credential changes. Keep file/ACL preparation, container/reconciliation, and readiness probing in separate private modules; `open_pgadmin_lifecycle` is the sole orchestration entry point.

Wait for the loopback pgAdmin HTTP root with a bounded readiness probe before returning. Transient connection/startup failures retry at a bounded interval until the monotonic deadline; login pages, unsafe redirects, non-success/invalid responses, and other security-invalid responses fail immediately. Any partial create/reconfigure failure is cleaned up when safely SDK-owned and reported with a fixed sanitized error; existing secrets and user data remain for retry.

Alternative: one pgAdmin per project. Rejected by the shared user-level requirement. Alternative: host-published PostgreSQL endpoints passed from React. Rejected because credentials/endpoints must be backend-resolved and loopback port reachability differs inside containers. Alternative: Docker socket inside pgAdmin. Rejected as unnecessary privilege.

### D5: Explicit API names and fixed error mapping

Use exact operation IDs `getMonitorSnapshot` and `openPgAdmin`. `PgAdminOpenRequest` has only `environment_id`; decode raw bytes with `msgspec.json.decode(..., type=PgAdminOpenRequest, strict=True)` and forbidden unknown fields so FastAPI does not synthesize a Pydantic model. The state-changing route requires an explicit exact same-origin `Origin`, rejects cross-site Fetch Metadata, requires `application/json`, and uses a per-session double-submit CSRF cookie/header before decoding or delegating. Malformed JSON, a missing or non-string `environment_id`, and any unknown field all map identically to status `422` and the named `HttpError` payload `{"code":"invalid_request","message":"invalid request"}`. The route catches only decode/validation failures for this mapping and does not call the injected/public opener. Known resource exceptions map to the documented `HttpError` code/status pairs. Unhandled monitor/pgAdmin exceptions map to fixed 500/503 messages after logging only sanitized diagnostics. Successful responses are `Snapshot` and `PgAdminOpenResult` directly.

The UI route exists only when `headless=False`; constructing either mode does no integration work. `create_app` gains one private/internal static-assets composition keyword, defaulting to the current runtime behavior: runtime UI composition validates and mounts packaged `web/dist`, while `static_assets=False` registers the identical UI API routes without checking or mounting the SPA. Schema export uses `headless=False`, `static_assets=False`, an injected monitor, and a no-side-effect typed opener. This seam performs no source-tree/dist access, browser or socket action, Docker inspection, credential/config creation, or public resource construction; it is not a new public service/provider abstraction.

Alternative: register the route in headless mode but reject at runtime. Rejected because the requirement makes route absence itself the security boundary.

### D6: Canonical export followed by a narrow generator adoption gate

Add a Python export script that imports `create_app`, calls `create_app(headless=False, static_assets=False, monitor=<schema stub>, pgadmin_opener=<typed no-op>).openapi()`, recursively verifies that forbidden machine-local values are absent, and writes repository `openapi.json` as UTF-8 JSON with `sort_keys=True`, fixed separators/indentation, and one trailing newline via atomic replace. It does not inspect `web/dist`, start Uvicorn, bind a port, open a browser, construct runtime resources, touch Docker, or create credentials/configuration.

Exact-pin `@hey-api/openapi-ts` in `web/package.json`/lockfile. Use its supported models + Fetch client + services/operations output without custom templates, hooks, mocks, or Axios. Generated files live only in `web/src/generated/`, use the tool's generated header (which must include `@generated`), and expose flat callable operations based on explicit operation IDs. Run the adoption gate once during implementation: compile, assert exported operation/type names and header, scan forbidden outputs, then run generation twice and compare bytes. If any gate fails, replace only with exact-pinned `openapi-typescript` + `openapi-fetch`; do not keep both paths or evaluate more tools.

Alternative: retain handwritten interfaces because the API is small. Rejected because the issue deliberately uses the second endpoint as the threshold for codegen. Alternative: generate at application runtime or publish an npm package. Rejected because this is a local packaged dashboard.

### D7: One handwritten runtime client configuration and deterministic checks

Replace `web/src/api.ts` with a small `client-config.ts` that sets the generated Fetch client's relative base URL. `App.tsx` imports generated types/operations. Snapshot polling preserves the current serialized two-second cadence. Each environment card renders pgAdmin state; an eligible click calls `openPgAdmin`, opens only the returned loopback root URL with `noopener,noreferrer`, and displays typed/sanitized known failures. The UI never derives eligibility from cluster fields.

`make web-codegen` runs export then frontend generation. `make web-codegen-check` uses a temporary working copy/output directory, runs export/generation twice, compares both runs to each other and to committed `openapi.json`/`generated/`, and exits non-zero without rewriting developer files. In a clean checkout CI installs the Python dashboard/test dependencies, runs `npm ci` (generator available but no `dist` required), then `make web-codegen-check`, then frontend tests and `npm run build`; backend contract/package tests follow without changing the generated tree. Package validation confirms canonical OpenAPI/generated sources do not accidentally ship as runtime assets unless already intended by package rules.

Alternative: run generation in place then use `git diff`. Rejected because it can overwrite unrelated developer edits and is less reliable in a dirty worktree.

### D8: Verification layers

Backend unit tests cover module compatibility/lazy imports, operation IDs/components, headless route absence, malformed/missing/wrong-type/unknown-field request mapping with opener-not-called assertions, direct msgspec bytes, error sanitization/status mapping, monitor eligibility precedence, database preflight ordering, lock concurrency, ownership labels, HMAC/loopback/socket constraints, exact single-user/email/password-file/replace-server startup settings, Linux UID 5050 modes/ACLs/mount flags and fail-closed cases, secret-free argv/environment metadata/logs, private-key rotation/non-derivability, active passfile refresh, readiness refusal/deadline/security-invalid paths, short-password acceptance, password-rotation recreation, and fresh/reused/cross-project-reconfigured paths with fake Docker subprocesses. The disposable Docker smoke runs passwordless `SELECT 1` from the pgAdmin container before, after cross-project reconfiguration, and after same-backend rotation; it checks active passfile updates and that the old credential fails. `tests/unit/test_cli_characterization.py::test_discovered_public_methods` changes by exactly one expected entry, `EnvironmentResource.open_pgadmin`; every other canonical public method remains identical to the MYL-55 baseline.

Contract tests validate serialized representative success/error values against the exact OpenAPI responses. Export tests run twice with hostile temporary paths/environment values and assert byte identity/no leaks. Frontend tests cover generated-client polling, disabled reasons, success URL open, action failure, and no duplicate click while pending. Existing backend, frontend build/tests, package tests, and core-only import tests remain required.

## Risks / Trade-offs

- [FastAPI does not natively consume msgspec response models] → Keep one narrow schema adapter, validate all references, and contract-test production bytes against the published schema.
- [pgAdmin image or generator changes output across releases] → Pin the image by digest and generator by exact npm version/lockfile; byte-compare two runs and committed output.
- [Container networking differs across Docker Desktop/Linux] → Use SDK-owned user-defined Compose network identity discovered through existing Docker CLI helpers, test argv/identity resolution, and include one disposable Docker smoke case.
- [Reconfiguration interrupts an already open pgAdmin session] → Reuse when fingerprints match; only recreate under the global lock when the selected backend changes, preserving the pgAdmin data directory.
- [Snapshot schema v3 affects strict CLI/HTTP JSON consumers] → Treat MYL-55 v2 as mandatory baseline, add only required `pgadmin`, preserve `observed_port`, `artifacts`, all earlier fields/semantics and CLI envelope v1, and update shared v3 fixtures plus CLI/HTTP compatibility tests together.
- [Private host files and persistent data must work for official UID 5050 without sudo] → Keep invoking-user ownership and exact ACL-aware `0710`/`0640`/`0770` modes with empty owning-group/other entries, grant only named UID 5050 access/default ACLs, validate masks and mount flags before mutation, and fail closed instead of chowning or granting group/world effective access.
- [Persistent pgAdmin state can ignore a changed declarative server] → Require `PGADMIN_REPLACE_SERVERS_ON_STARTUP=True` on every create/recreate and verify the effective selected database before returning `reconfigured`.
- [MYL-55 is not yet on `main`] → Rebase/merge it before implementation, retain its complete snapshot v2 and canonical public-surface table, then make only the deliberate v3/`open_pgadmin` additions in this scope.

## Migration Plan

1. Before implementation, update `main`, merge/rebase MYL-55, and rebase `feat/MYL-58-typed-http-codegen`; verify the push remote is SSH.
2. Land typed models/eligibility and HTTP schema contract first while preserving `internal.serve` compatibility.
3. Add public pgAdmin resource operation and secured lifecycle behind the UI-only route.
4. Export canonical OpenAPI, pass the Hey API adoption gate (or the fixed fallback), commit generated SDK, then migrate React.
5. Enable `web-codegen-check` in CI after committed generated output is current and run the full backend/frontend/package suite.

Rollback is a normal commit revert: `internal.serve` compatibility and existing snapshot route remain isolated, and removing the UI-only pgAdmin route/codegen consumer does not require catalog migration. A leftover SDK-owned pgAdmin container/data directory is user-level operational state; document its explicit stop/removal command, but never delete it automatically on rollback.

## Open Questions

None. The implementation must record the reviewed pgAdmin image digest and exact generator version in source/lockfiles, but choosing newer values does not alter the contract above.
