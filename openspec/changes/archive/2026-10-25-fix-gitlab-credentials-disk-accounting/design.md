## Context

The core SDK already owns the encrypted-first GitLab resolver in `internal/gitlab_credentials.py`, including exact member/host selection, secret-store failure semantics, legacy fallback, and redacted `GitLabCredentialContext`. The Multica bridge aliases the same public credential models but currently reconstructs a project-only mapping/token lookup in `MulticaOdooClient._resolve_credentials`, causing the encrypted-only BNT-9 failure.

The monitor already computes detailed environment storage and a checkout-only `ProjectStorageFootprint`; it also has the catalogue ownership, PostgreSQL-size, filestore-containment, bounded directory probes, recording boundary, and 15-second cache needed for a project aggregate. The React card receives project and environment storage through the generated OpenAPI client but renders a generic `Resources` detail row.

Constraints include fail-closed secret handling, immutable/redacted command plans, no new subprocess seam, public frozen snapshot models, partial-data isolation, generated-client consistency, and no local E2E at the user's explicit request.

## Goals / Non-Goals

**Goals:**

- Make encrypted-store-only credentials work for bridge Git push/sync/passthrough and MR publication while retaining the exact legacy fallback.
- Produce one honest project disk total with a backup subtotal, deterministic de-duplication, and partial completeness.
- Show Disk beside CPU/RAM on running and stopped cards.
- Cover the change with focused Python, API/codegen, and dashboard tests.

**Non-Goals:**

- Change secret-store schema, Admin UI, credential provisioning, Git identity verification, or askpass transport.
- Attribute shared database/backup storage proportionally to individual environments.
- Report physical PostgreSQL cluster overhead, reclaimable host bytes, storage history, quotas, or cleanup controls.
- Add a second storage collector, cache, credential abstraction, test framework, or live E2E workflow.

## Decisions

### Reuse the core resolver at the bridge boundary

`MulticaOdooClient._resolve_credentials` will first resolve the verified root creator and normalized host, then delegate to core `resolve_gitlab_credential`. It will pass the bridge's selected legacy mapping path, project `.odcli/.env` path, and supplied process environment so exact encrypted-record absence retains current bridge behavior. Core and bridge already share `GitLabCredentialContext`, so no adapter or duplicate secret model is needed. Core typed credential failures will be translated to the bridge's existing sanitized context error without including the token or secret-store internals.

Alternative: duplicate the `SecretStore.lookup_gitlab_member` branch inside `odcli-multica`. Rejected because it would create a second precedence/failure policy and repeat security-sensitive logic.

### Use one project aggregate rather than new per-environment ownership rules

The project card will own the inclusive total requested by GitHub #176. The existing environment footprints remain unchanged and useful on worktree cards; the project collector will separately form an aggregate from project-owned catalogue and plan inputs. This avoids inventing allocation rules for a shared database or backup while still making scope explicit in the UI.

Filesystem inputs will be canonicalized and contained duplicates collapsed before bounded measurement. Backup rows are limited to available records owned by the project and de-duplicated by resolved file identity. Related databases are de-duplicated by normalized cluster endpoint plus database name, and filestores by validated canonical path. Known values are summed even when another component fails; completeness is independent of the total.

Alternative: add the Docker volume metric to every project. Rejected because volume usage includes shared PostgreSQL overhead and databases outside the selected project's logical scope, so it cannot satisfy attribution or no-double-counting requirements.

### Add only the backup subtotal to the public project DTO

`ProjectStorageFootprint` gains `backup_bytes` because it is the one requested subtotal that the dashboard must render. `total_bytes` and `complete` retain their meanings, expanded to the declared project aggregate. Internal component values stay implementation details; adding every component to the API would enlarge the compatibility surface without a user-facing need. The OpenAPI document and generated TypeScript client will be regenerated through the established repository command.

Alternative: introduce a new disk-breakdown model. Rejected as unnecessary API surface for one displayed subtotal.

### Keep Disk independent of runtime liveness

The card metric row will always render Disk from storage state. CPU and RAM keep their existing live-runtime guards. Project cards add the formatted backup subtotal and `project total` scope; environment cards use the existing environment total. The detail-row `Resources` markup is removed.

Alternative: show Disk only while the process runs. Rejected because storage persists independently of process state and the issue explicitly requires stopped-instance visibility.

### Ponytail full subsystem gate

No new subsystem is necessary. Credential resolution stays in the existing core resolver; storage collection stays in `EnvironmentMonitor`; ownership comes from `BackupCatalog`; PostgreSQL and filestore measurement reuse existing helpers; and presentation remains in the current React card. Each proposed addition is either a field on an existing DTO or composition inside an existing boundary, so all candidate new abstractions are removed from the design.

## Risks / Trade-offs

- [Project aggregation can become slower as worktrees, backups, and databases grow] → retain the existing bounded probes and 15-second project cache, de-duplicate before measurement, and expose incomplete data instead of blocking indefinitely.
- [A catalogue row can point to a missing or changed backup file] → validate the actual file during the sample, preserve known totals, and mark the aggregate incomplete.
- [Database or filestore discovery can fail independently] → isolate each component, never substitute cluster-volume bytes, and keep `complete=False` with available subtotals.
- [Generated client drift can break dashboard typing] → update the model/OpenAPI contract and regenerate the checked TypeScript client in the same implementation task, then run codegen and dashboard tests.
- [Credential test fixtures currently use non-UUID member placeholders] → update only affected fixtures to valid deterministic UUIDs because the shared resolver correctly enforces the production member-ID contract.

## Migration Plan

No persisted data migration is required. Implement the credential delegation and tests, then the monitor model/collector and generated client, then the card presentation. The additive `backup_bytes` field changes the snapshot schema contract and therefore requires coordinated server/client regeneration in the same release. Rollback is a normal code revert; encrypted records and catalogue data are unchanged.

## Open Questions

None. The aggregate fallback explicitly permitted by GitHub #176 is selected, and local E2E is excluded by the root issue's user decision.
