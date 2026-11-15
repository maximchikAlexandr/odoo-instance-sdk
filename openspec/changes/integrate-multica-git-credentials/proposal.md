## Why

The repository already has the member credential resolver, a standard Git credential helper, verified root-creator traversal, execution-time revalidation, and one credential snapshot shared by raw Git, sync, and merge-request publication. The remaining GitHub #168 gap is to connect those paths for native Multica cache reads and an optional explicit human member, without project-local credential copies or a second selection subsystem.

## What Changes

- Expose the existing short-lived helper through a stable executable if packaging does not already provide one, and add a cache-read purpose that validates the configured account and an active token whose complete standard scope set is exactly `read_repository`.
- Configure native cache Git children through process-local daemon-generation environment; replacement uses the existing stop → validate → start lifecycle and introduces no hot-rotation product API.
- Extend the existing `RootCreatorContext` and execution revalidation path minimally so raw Git, sync, and merge-request publication can select an optional explicit current human workspace member; omission preserves root-creator behavior.
- Stop `odcli-multica` from synthesizing project `.odcli` credential defaults. Existing encrypted-first resolution, `ODCLI_USER_ROOT`, legacy conflict checks, and explicit overrides remain authoritative.
- Add only focused tests and one extension of the existing live workflow for cache identity/scope, explicit override failures, user-root-only credentials, two-account isolation, and one integrated path.

## Capabilities

### New Capabilities

- `multica-git-cache-credentials`: Process-local native-cache authentication through the existing helper and resolver.

### Modified Capabilities

- `gitlab-member-credentials`: Add cache-purpose validation and stable helper packaging while retaining existing storage and precedence.
- `multica-user-credentials`: Add an optional explicit human member to the existing root-creator context and remove implicit project credential defaults from bridge calls.
- `multica-environment-binding`: Thread the optional member through existing command builders and revalidation.
- `gitlab-merge-request`: Let the existing publication path consume the selected member without changing MR semantics.

## Impact

- Narrow changes in the existing core helper/identity modules and packaging, plus the existing `packages/odcli-multica` context, client, command, CLI, tests, and docs.
- Additive `member_id`/`--member-id` inputs and a stable helper executable where missing.
- No new credential store, provider-ID persistence, selector/result subsystem, Admin model, database migration, OAuth service, credential daemon, rotation state machine, or global account switch.
