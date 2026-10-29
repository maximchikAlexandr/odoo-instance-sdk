## Why

Native Multica cache fetches and task-checkout Git/GitLab operations do not yet share one explicit, verified credential-selection contract. This leaves cold-cache authentication, per-member overrides, and user-root-only legacy credentials either unsupported or dependent on manual wrappers and project-local files.

## What Changes

- Publish the existing short-lived Git credential helper through a stable executable and define a fail-closed, host-scoped cache-read mode for native Multica clone/fetch, including cold caches and background fetches.
- Select one explicitly configured cache credential whose runtime provider identity and permissions are verified as read-only, and document stop → validate → restart for replacing that daemon-generation credential.
- Add one shared typed member-selection input to credential-dependent `odcli-multica` SDK operations and `--member-id` to native Git passthrough, `git sync`, and merge-request publication. An explicit member wins over automatic root-creator selection but never bypasses verified checkout, issue, workspace, repository, or host binding.
- Make user-root credential storage the default for every `odcli-multica` credential path, including `ODCLI_USER_ROOT`, encrypted records, and legacy mapping/token files; retain explicit path/process overrides with one documented precedence.
- Report the chosen non-secret identity and selection source in inspectable plans/results while preserving command-scoped secret delivery and redaction.
- Add focused unit, integration, packaging, CI, and isolated live verification for cache routing, two-member concurrency, negative routing, user-root precedence, CLI/SDK parity, and secret absence.

## Capabilities

### New Capabilities

- `multica-git-cache-credentials`: Stable helper integration and read-only credential routing for native Multica shared-cache clone/fetch operations.

### Modified Capabilities

- `gitlab-member-credentials`: Define user-root defaults, explicit override precedence, read-only cache-token verification, stable helper packaging, and secret-safe identity observability.
- `multica-user-credentials`: Add explicit verified member selection alongside automatic root-creator selection and require all bridge consumers to use the same shared selection/resolution path.
- `multica-environment-binding`: Preserve verified checkout/task/repository binding when an explicit member is selected and expose the selection through typed SDK and CLI contracts.
- `gitlab-merge-request`: Accept the shared explicit member selection for `odcli-multica` publication without changing assignee semantics or core `odcli git mr create`.

## Impact

- Affected implementation: `src/odoo_instance_sdk/internal/git_credential_helper.py`, the existing runtime GitLab identity validation and packaging metadata, and `packages/odcli-multica` client, models, CLI, Git execution, tests, and documentation.
- Affected public surface: additive typed `member_id` inputs, additive `--member-id` options, a stable helper executable, and safe identity fields in plans/results.
- Native Multica integration uses process-local Git configuration and a quiescent daemon restart; it does not persist tokens in remotes, Git config, checkout bindings, or a new credential store.
- No credential-record/Admin-model migration, database migration, rotation API/state machine, new dependency, OAuth service, long-running credential process, or global account switch is introduced.
