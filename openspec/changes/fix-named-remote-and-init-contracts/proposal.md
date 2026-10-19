## Why

Named remote registration currently requires a database even though refresh already knows how to select the sole exposed database. The same gap reaches init, process inventory, COPY planning, generated local credentials, HTTP allocation, restore publication, and PostgreSQL image consent, so a project can be configured successfully yet fail before the existing safe workflows can run.

## What Changes

- Make a named remote database optional in the typed model, manifest, `remote add`/`remote update`, process inventory, and both supported `init --remote` forms while keeping explicit nonblank names authoritative.
- Reuse the existing database-list resolver during refresh: select one database, reject zero/multiple/unavailable results before backup, retain named-source credentials and provenance, and return an actionable typed error when COPY planning still lacks a selected database.
- Preserve all existing named remotes across re-init except names explicitly added or updated; keep registration and dry-run offline and inside the existing immutable init snapshot.
- Constrain automatic environment HTTP allocation to 8000–9000 inclusive, try the valid preferred candidate (or 8069) first, scan the full range once, and leave explicit ports exact.
- Ensure Compose init creates, preserves, or repairs an owner-only generated `admin_passwd` without accepting or exposing the secret through CLI, manifest, plans, output, errors, logs, or fingerprints.
- Capture generated-config Git tracking and repository identity checks in restore planning and consume those exact observations before default publication.
- Integrate existing PostgreSQL OCI digest resolution and per-project approval into init: `--yes` approves the selected digest, interactive Rich mode asks when needed, matching approvals are reused, and dry-run/external PostgreSQL remain side-effect free.
- Keep the pinned developer hotfix verifier compatible with uv's exact trusted `git+https://...git@SHA` provenance while rejecting foreign or mismatched sources.
- Add focused model, CLI, init, allocator, refresh/COPY, restore-publication, process-inventory, image-consent, and compatibility regression coverage; update user documentation for the public forms.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-database-preparation`: named sources may omit a database and defer safe resolution to refresh; COPY and restore publication failure contracts become explicit.
- `project-init`: init accepts optional-database named remotes, maintains source sets, manages a local generated master password, and captures PostgreSQL image consent in its immutable operation.
- `development-environment`: automatic HTTP allocation uses the complete bounded range while explicit requests remain exact.
- `database-restore`: generated-config Git safety observations are captured before execution and consumed before binding publication.
- `postgres-cluster`: init may resolve and approve the exact Compose image digest through the existing approval store while preserving fail-closed startup.
- `cli-odcli`: public remote/init syntax, dry-run behavior, machine output, inventory compatibility, and consent behavior reflect these contracts.

## Impact

The change affects the project manifest model and serializer, named-remote and init CLI adapters, project init/config generation, database preparation and COPY source selection, checkout port allocation, restore publication preflight, PostgreSQL image approval orchestration, process-inventory regressions, the pinned developer fix tool, README examples, and focused unit/integration-style tests. It adds no dependency, service, schema migration, network discovery path, public runner abstraction, or secret-bearing CLI/config field; existing manifests with explicit remote databases remain valid.
