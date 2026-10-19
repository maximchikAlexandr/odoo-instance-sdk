## 1. Align contracts and planning artifacts

- [x] 1.1 Update the primary CLI, project-init, preparation, restore, postgres, and numbered-tool requirements for optional remote databases, both init forms, COPY refusal/recovery, credentials, captured Git checks, image consent, and pinned hot-fix provenance.
- [x] 1.2 Create and validate the issue-scoped #154 proposal, design, task list, and delta specs without modifying the unrelated active change.
- [x] 1.3 Specify canonical direct and pinned hot-fix provenance with the same exact lowercase `vcs_info.commit_id`, including rejection of foreign, mismatched, malformed, and suffixed forms.
- [x] 1.4 Specify bounded HTTP allocation (`8000..9000`, preferred/`8069` order, complete scan/exhaustion, and exact explicit ports) and align the primary and issue-scoped specs.
- [x] 1.5 Specify `ps` success/non-mutation for a remote without a database and rejection of invalid explicit database values.

## 2. Extract the init phase

- [x] 2.1 Move image-consent capture/execution and its prepared steps into a focused internal helper while preserving step IDs, ordering, dry-run, approval, and failure behavior.
- [x] 2.2 Add or adjust focused image-consent coverage and keep `project_init.py` below the repository's 1,000-line limit.

## 3. Rewrite the required tests

- [x] 3.1 Split the restore tracking guard cases into explicit success and error/root-drift tests.
- [x] 3.2 Split HTTP allocation free/occupied cases while preserving the exact single-probe assertion.
- [x] 3.3 Split image-consent first-approval/already-approved and accept/decline scenarios without conditional expected assertions.
- [x] 3.4 Split named-remote init parsing, repeatability/secret-free behavior, and additive update coverage.
- [x] 3.5 Split named preparation configured-database, singleton-discovery, and three-error cases while retaining secret/provenance and no-backup assertions.
- [x] 3.6 Split hot-fix canonical/pinned source setup from bootstrap argv assertions.
- [x] 3.7 Split optional-remote add, dry-run non-mutation, persistence/list round-trip, and update-preserves-database tests.
- [x] 3.8 Narrow the image-test process step type so strict mypy accepts `cwd` and `environment` access.
- [x] 3.9 Rework the one-command image approval matrix without format-dependent control flow while retaining Rich/TOON/JSON success, no-prompt, exact-digest, and `0600` coverage.

## 4. Verify and deliver

- [x] 4.1 Run the identical curated focused suite before and after with line and branch coverage for the affected production modules, proving no scenario or comparable coverage is lost.
- [x] 4.2 Run focused regression suites, `openspec validate --all --strict`, `openspec-verify-change`, `make lint`, and `make types`.
- [x] 4.3 Review the bounded diff, commit the bounded Conventional Commit sequence, push normally to `feat/remote-database-auto-resolution`, and record exact SHA, ancestry, paths, evidence, and exit codes.
