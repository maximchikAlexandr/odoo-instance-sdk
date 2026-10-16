## Why

OdCLI cannot currently authenticate GitLab work as an explicitly selected Multica member or operate on an arbitrary GitLab repository before `odcli init`. That prevents one task from safely changing independent repositories/submodules and publishing their merge requests, while `odcli update` also drops the installed `dashboard` extra and breaks the monitor after restart.

## What Changes

- Add owner-only, machine-local GitLab credential configuration keyed by exact `(Multica member ID, GitLab HTTPS host)`, with token ownership verified through the GitLab API before any Git or mutation request.
- Extend core `odcli git` with generic-repository `clone`, `commit`, and `sync` operations that accept explicit `--member-id`/`--repo`, work without an OdCLI project, protect credentials across Git and submodule children, and retain Odoo-specific commit policy only for initialized Odoo contexts.
- Add core `odcli git mr create` for the exact `--repo`, including UTF-8 Markdown descriptions and optional GitLab project-upload images that are idempotently referenced without committing the image.
- Preserve safe clone retry/submodule initialization without resetting the checked-out branch, and keep superproject gitlink updates explicit rather than hidden in submodule sync.
- Preserve the installed optional extras, including `[dashboard]`, through `odcli update`; verify the restarted monitor and API remain available and that built UI assets ship in the installed target.
- Reconcile the README command inventory and examples with the actual CLI, including the new GitLab workflows.

## Capabilities

### New Capabilities

- `gitlab-member-credentials`: Secure local credential storage, exact member/host selection, GitLab token-owner verification, and secret-safe child/request delivery.
- `generic-gitlab-repository-workflow`: Clone, commit, synchronize, and submodule behavior for any allowed GitLab repository independent of OdCLI project registration.

### Modified Capabilities

- `odoo-git-workflow`: Add explicit generic `--repo` operation while preserving Odoo commit conventions only for initialized Odoo projects.
- `gitlab-merge-request`: Add the core `odcli git mr create` contract, exact repository selection, description files, image upload, and idempotent image references.
- `command-execution`: Apply frozen process/action planning, credential isolation, and redaction to clone, generic Git, submodule, and GitLab upload/MR effects.
- `self-update`: Preserve the installed optional-extra composition and verify dashboard/monitor viability after replacement.
- `packaging`: Make built dashboard assets and installed dashboard dependencies part of update regression coverage.
- `cli-odcli`: Add the new command paths/options to the public CLI surface and documented inventory.

## Impact

The change affects core Git CLI/resource/model code, the shared process and HTTP action boundaries, local configuration paths and parsers, GitLab transport currently implemented in `packages/odcli-multica`, self-update provenance/install planning, packaging tests, CLI output inventories, README examples, and security/integration tests. It adds no GitHub workflow, no implicit `odcli init`, no hidden superproject gitlink commit, and no standalone `push` command.
