## Why

PR #155 implements GitHub #154 but its review found one structural regression, stale requirements, missing issue-scoped planning artifacts, and tests whose control flow obscures the acceptance contracts. This change records the bounded closure work while preserving the already-reviewed remote resolution, init, restore, and hot-fix behavior.

## What Changes

- Extract PostgreSQL image consent capture/execution from the central project-init coordinator without changing ordering, plans, failure behavior, or consent semantics.
- Document optional remote databases, both supported init forms, offline registration/update behavior, COPY refusal and recovery, generated local credentials, captured Git validation, and image consent in issue-scoped OpenSpec artifacts.
- Document matching and mismatched repository/SHA-pinned hot-fix provenance in the numbered-tool requirements.
- Specify both accepted hot-fix provenance forms: the canonical `.git` URL with a separate exact lowercase `vcs_info.commit_id`, and the pinned `git+https://...odoo-instance-sdk.git@<same-sha>` URL with the same identity; reject foreign, mismatched, suffixed, or malformed sources before coordinator invocation.
- Specify deterministic HTTP allocation as `8000..9000` inclusive with preferred/`8069` first, a complete no-duplicate exhaustion scan, and exact explicit-port probing.
- Specify that `odcli ps` loads and renders a named remote without a database without mutation while retaining rejection of invalid explicit database values.
- Rewrite the eight D/F test methods into focused A–C tests while retaining every listed acceptance and regression scenario.
- Remove the format-dependent assertion branch from the one-command image approval matrix while retaining Rich/TOON/JSON success, no-prompt, exact-digest, and `0600` checks.
- Correct the production line-limit and test typing failures.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-odcli`: optional named-remote database grammar, offline registration, both remote init forms, and no-database COPY refusal/recovery.
- `project-init`: optional database resolution, generated credential and image-consent contracts, and captured Git validation.
- `project-database-preparation`: remote database selection and COPY refusal/recovery behavior.
- `database-restore`: captured Git validation before default publication.
- `numbered-odcli-slots`: accepted repository/SHA-pinned hot-fix provenance and refusal cases.
- `postgres-cluster`: image digest consent and stable local credential behavior.

## Impact

Implementation is limited to the existing PR branch: the project-init coordinator and a focused internal init-phase helper, the listed unit tests, the primary OpenSpec specs, and this issue-scoped change's proposal/design/tasks/delta specs. No dependency, public API, executable, or external E2E scope changes are required.
