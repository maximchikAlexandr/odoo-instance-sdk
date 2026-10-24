## Why

`odcli init` durably registers a project, but the public SDK and CLI provide no inverse operation. Users therefore cannot remove a finished project from the catalogue and monitor without manually editing SDK state, and they have no safe, ownership-aware way to erase all local resources that belong exclusively to that project.

## What Changes

- Add a public typed project-removal SDK operation and one root `odcli remove` command, also reachable as the short alias `odcli rm`, with mutually exclusive `--project PATH` and `--project-id ID` selectors.
- Make the default operation forget the project without deleting source, configuration, secrets, databases, backups, environments, or other retained resources; preserve their internal ownership and refuse when active managed resources would become unaccounted for.
- Add `--purge` to capture and execute an immutable, resumable removal plan that first stops owned runtimes and removes publication, then deletes only resources whose exclusive SDK ownership is proven. Shared, external, remote, and tool-managed resources are retained and reported.
- Require an exact confirmation token containing the full project name and full `project_id` for purge. Interactive and `--confirm-project` confirmation are supported; `--yes` alone is insufficient. Dry-run is non-mutating and renders the same bounded plan.
- Keep a failed or partial purge registered, return completed and remaining actions, and allow a later invocation to resume safely. Forgetting occurs only after every required purge action has succeeded or reached an explicitly retained terminal disposition.
- Permit lookup by full `project_id` when the original project directory is absent, and make repeated forget/purge calls deterministic and idempotent.
- Reactivate the same catalogue identity on a later successful `odcli init`, without creating a duplicate ownership record.

## Capabilities

### New Capabilities

- `project-removal`: Public project selection, immutable removal planning, exact confirmation, forget and purge execution, ownership decisions, partial-failure reporting, retry, and typed results.

### Modified Capabilities

- `project-init`: Initialization reactivates a forgotten project under its existing canonical identity and preserves retained ownership records.
- `environment-monitor`: Project registration state controls catalogue/monitor visibility without discarding retained ownership metadata.
- `local-resource-lifecycle`: The existing inventory supplies the complete project purge projection and explicit delete/retain ownership decisions.
- `cli-odcli`: The root command surface, context rules, SDK-first leaf inventory, confirmation behavior, dry-run, and bounded machine output include canonical `remove` with the `rm` alias.

## Impact

- Existing SQLite catalogue `projects` schema and migration/equivalence tests; no parallel catalogue or ownership store.
- A new public project lifecycle resource, frozen JSON-safe plan/result models, lazy package exports, and `OdooClient` facade wiring.
- Root Click registration and the test-owned `PUBLIC_LEAF_CASES` inventory, output envelopes, Rich rendering, help, and documentation.
- Existing runtime stop, publication removal, environment cleanup, guarded database/filestore deletion, backup deletion, Compose container/volume cleanup, resource inventory, locking, redaction, and process-boundary components.
- Focused unit/integration/CLI/architecture tests plus an isolated local E2E walkthrough against the project test repository; no production data removal during planning.
