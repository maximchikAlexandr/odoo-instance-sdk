## Why

Administrators can currently configure a default GitLab merge-request assignee only by repeating the same value for every normalized host/project key. A workspace-wide fallback is needed so one setting covers all GitLab projects while preserving project overrides and explicit command intent.

## What Changes

- Accept `gitlab.mr.default_assignee` as the global catalog key, with the same positive numeric user ID or exact username value contract as project-scoped assignee settings.
- Resolve assignment in this fixed order: nonblank explicit CLI `--assignee`, exact normalized host/project setting, global setting, then no automatic assignee.
- Keep invalid selected values fail-closed; do not skip an invalid higher-priority value to use a lower-priority value.
- Keep core `odcli git mr create`, GitLab reviewer behavior, catalog storage, and provider-side member validation unchanged.
- Document and test the fallback and precedence contract without adding a new service, schema migration, or configuration model.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `catalog-settings`: Allow and validate the exact global MR assignee key alongside existing host/project-scoped keys.
- `gitlab-merge-request`: Add the global default fallback after the exact project setting and before unassigned publication.

## Impact

The change is limited to catalog setting-key validation, the existing `odcli-multica gitlab mr publish` setting resolver and dry-run description, focused catalog/client/output tests, and operator documentation. It depends on the catalog-settings and MR-assignment implementation already present at baseline `f5c6a03f77d74cd462fa9532e5ac8fe710fd92c5`; no database migration, new dependency, production GitLab mutation during verification, or change to the completed MYL-565 implementation ledger is required.
