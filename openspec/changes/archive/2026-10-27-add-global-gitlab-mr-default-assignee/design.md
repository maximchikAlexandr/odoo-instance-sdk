## Context

The implemented MYL-565 baseline stores a project-scoped default at `gitlab.mr.default_assignee:https://<normalized-host>/<normalized-project-path>`. During `odcli-multica gitlab mr publish`, a nonblank explicit `--assignee` skips catalog lookup; otherwise the client reads that one key, validates its JSON scalar, and passes it into the existing exact-user/member/provider verification path.

The requested follow-up adds one workspace-wide fallback without changing the settings table, Admin CRUD, GitLab transport, or published result model. Planning is based on `f5c6a03f77d74cd462fa9532e5ac8fe710fd92c5`, the exact implemented MYL-565 head; implementation must use that revision or a descendant containing the same contracts.

## Goals / Non-Goals

**Goals:**

- Accept one exact global setting key: `gitlab.mr.default_assignee`.
- Select the first applicable value in the fixed order: nonblank explicit CLI override, exact project key, global key, then no assignee.
- Apply the existing value validation, exact GitLab user resolution, project membership check, provider mutation, and response verification unchanged.
- Make the lookup order inspectable in dry-run/help and cover every precedence/failure branch with focused tests.

**Non-Goals:**

- Per-host, group, wildcard, pattern, inheritance, or multiple-assignee configuration.
- Changes to core `odcli git mr create`, reviewer semantics, GitLab credential selection, catalog schema, Admin UI structure, or the completed MYL-565 ledger.
- A new assignee-source enum value or a migration of existing project-specific rows.

## Decisions

### 1. Use one exact global key and the existing typed value contract

The global key SHALL be exactly `gitlab.mr.default_assignee`. Catalog validation will classify both that key and the existing scoped-key family as MR assignee settings, so both accept only a positive integer GitLab user ID or a nonblank exact username without whitespace.

Alternative: introduce `gitlab.mr.default_assignee:global` or a wildcard URL. Rejected because it creates an invented scope syntax and complicates validation without serving the requested single fallback.

### 2. Resolve sequentially and stop at the first present source

The resolver will retain the explicit override short-circuit. Without an override it will read the exact project key first, read the global key only when the project row is absent, and otherwise select no assignee. A present but invalid higher-priority value fails before GitLab mutation; it never falls through to a lower-priority value.

Alternative: read both settings and merge them. Rejected because there is one scalar result, and extra reads weaken the observable precedence contract. Alternative: fall back after validation/provider failure. Rejected because silently selecting a different person is unsafe.

### 3. Reuse the existing publication and output contracts

Either catalog source continues to pass `assignee_source="default"` into the existing publisher. This preserves the frozen result schema while the dry-run projection records the project key, global key, and precedence. CLI help and operator documentation explain that explicit input overrides both defaults and that project-specific configuration overrides global configuration.

Alternative: add `project_default` and `global_default` result enum values. Rejected because the requester needs selection behavior, not a public result-schema expansion; focused resolver tests can prove the source chosen.

### 4. Ponytail full gate

No new subsystem is necessary. The existing alternatives are the catalog key validator, `MulticaOdooClient.publish_merge_request_command()` resolver, immutable execution plan, focused pytest suites, and operator README. Decision: extend those exact seams only; do not add a generic configuration hierarchy, resolver abstraction, migration, transport, or E2E harness.

## Risks / Trade-offs

- **A malformed project value could be masked by the global value** → stop on any present invalid higher-priority value and assert that the global key was not read.
- **A scoped value could leak across projects** → retain the normalized exact host/project key and test that unrelated scoped rows are ignored while the global fallback remains eligible.
- **Dry-run could imply a resolved value without reading runtime state** → expose only the ordered candidate keys and deferred resolution, never a fabricated selected user.
- **The unmerged baseline could drift before implementation** → require implementation from `f5c6a03f…` or a descendant and revalidate the existing MYL-565 contracts before editing.
- **Using `default` for both catalog sources loses source detail in final output** → accept this compatibility trade-off; precedence is inspectable in the plan and deterministic tests, and no consumer-visible schema change was requested.

## Migration Plan

No schema or data migration is required. Deploy the validator, resolver, tests, help, and documentation together. Existing scoped rows continue to win unchanged; installations without the global row retain current behavior. Rollback ignores the global row and restores scoped-only lookup without data loss.

## Open Questions

None. Key spelling, precedence, fail-closed behavior, compatibility boundary, and verification strategy are fixed.
