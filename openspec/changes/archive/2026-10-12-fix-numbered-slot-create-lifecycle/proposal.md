## Why

The private numbered-slot workflow cannot provision a real isolated Binta checkout: adoption rejects transport-equivalent Git origins, and named-remote COPY restoration can bypass the bounded local auxiliary Database Manager so the required local target database is never created. This blocks safe parallel E2E execution and leaves the already-specified isolation contract unfulfilled.

## What Changes

- Compare configured and caller-owned Git remotes through one strict, transport-aware repository-identity normalizer so equivalent `git@host:path` and `ssh://git@host/path` origins match while different repositories and unsafe URL forms still fail closed.
- Apply the existing bounded auxiliary Database Manager lifecycle to every COPY checkout restore target, including named-remote and retained-backup sources; only backup acquisition may contact a named remote.
- Preserve the current immutable plan, single before/after PostgreSQL probe pair, COPY journal, compensation, secret-redaction, and selective numbered cleanup contracts.
- Add focused unit/contract coverage plus a real local numbered-slot lifecycle check for `create -> list -> inspect -> remove`, including database, filestore, checkout, launcher, neighbor, and failure-cleanup evidence.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `development-environment`: Define canonical Git repository identity comparison for adoption and require named-remote/retained-backup COPY adoption to restore through the local project runtime and cluster.
- `database-restore`: Clarify that the bounded auxiliary Database Manager lifecycle protects the restore target for every COPY source kind, while remote access remains limited to backup acquisition.
- `numbered-odcli-slots`: Require a complete numbered create/list/inspect/remove lifecycle with transport-equivalent origins and local COPY resource isolation.

## Impact

- Affected implementation: Git remote identity handling in `internal/git_worktree.py` and `resources/environment/checkout_api.py`; COPY auxiliary-session selection and restore orchestration in `resources/environment/checkout.py` and existing checkout tests.
- Affected private integration: `scripts/odcli_agent_tools/numbered_tool.py` contract and the Binta test-repository adapter that supplies concrete project inputs; no new public CLI command or option.
- Affected verification: focused adoption/restore tests, existing architecture and output-plan checks, GitHub CI suites, and a Mac local E2E against the test repository URL configured by the project.
- Dependencies and storage schemas remain unchanged; secrets, private hostnames, concrete ports, evidence logs, diagnostic JSON, and screenshots remain outside Git.
