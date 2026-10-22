## Context

The current code already captures a checkout `data_dir`, owns an immutable process plan, journals COPY recovery, verifies GitLab member identities, resolves Multica root creators, and coordinates recoverable uv-tool replacement. The defects come from incomplete composition across those boundaries rather than missing infrastructure:

- checkout writes `plan.data_dir` to the final environment config, but `_do_copy_restore()` and `_copy_auxiliary_session()` reconstruct their Odoo instance from the source config, which may omit that binding;
- commit generation still wraps a configured URL in angle brackets even though validation already accepts both historical forms;
- self-update snapshots and verifies only the core uv tool, while an independently installed companion can retain its own older SDK distribution;
- `GitResource.commit_command()` and the CLI currently treat any `member_id` as generic mode and therefore require `repository`, although the initialized-project path already has a verified identity adapter.

The implementation must preserve the single process boundary, immutable preview/execution parity, source filestore contents, primary Odoo availability, credential secrecy, update recovery evidence, and compatibility with existing commit history.

## Goals / Non-Goals

**Goals:**

- Make the captured target `data_dir` authoritative for COPY restore and readiness in ordinary and adopted checkouts.
- Keep source backup reads and target restore writes separated by database name without changing source files or the running primary.
- Generate bare ticket URLs while accepting old history and trailers.
- Treat an already-installed companion plus its embedded SDK as one optional self-update composition and verify actual launchers and VCS revisions.
- Apply a verified human GitLab author/committer to project-aware commits through the existing member and Multica lineage contracts.
- Preserve exact dry-run/execution snapshots and secret-free public projections.

**Non-Goals:**

- No credential-helper redesign, arbitrary metadata identity, machine-account fallback, new update framework, new process runner, new filestore copier, catalogue migration, or live Odoo E2E execution.
- No installation of `odcli-multica` into a core-only setup and no implementation of the broader credential-helper scope tracked by GitHub #168.
- No rewriting of historical commit messages or removal of angle-bracket compatibility from history validation.

## Decisions

### 1. Reuse the captured checkout `data_dir` throughout COPY

The immutable checkout plan remains the single source for the verified target `data_dir`. Checkout will materialize an environment-owned auxiliary configuration derived from the source configuration with that captured binding and the already allocated helper port, then use it for the helper that serves backup and restore. The source database is read at `filestore/<source_db>` and the restore writes `filestore/<target_db>` under the same verified root; no source file is renamed, copied in place, or deleted. The generated environment configuration continues to use the same captured binding.

Alternative considered: move filestore bytes in the HTTP client after restore. Rejected because `/web/database/restore` is the server-side writer and post-hoc copying would duplicate archive validation, ownership, and cleanup rules.

### 2. Add a target attachment postcondition before finalization

After database existence and neutralization succeed, a bounded read-only query will enumerate non-empty `ir_attachment.store_fname` values in the target database. Each normalized relative name must resolve to a regular file contained under `<data_dir>/filestore/<target_db>`. Zero file-backed attachments is success. Missing, escaping, symlinked, or non-regular targets fail before `_finalize_environment_checkout()` and flow through the existing COPY journal, cleanup, and borrowed-backup retention rules.

Alternative considered: assert only that the target filestore directory exists. Rejected because the reported failure has a valid directory but missing attachment content.

### 3. Change generation, not historical acceptance, for ticket URLs

`_commit_context_from_results()` will append `\n\n<url>` as `\n\nurl`. The existing validator retains both bare and angle-bracketed second paragraphs and standard `Co-authored-by` trailers. Preview and execution keep the exact same captured message.

Alternative considered: tighten validation to bare URLs. Rejected because that would invalidate existing repository history.

### 4. Model the companion as optional installed update composition

Inspect will look for a protected, unambiguous `odcli-multica` uv-tool receipt and launcher. Absence means the existing core-only path. Presence captures the companion direct requirement, tool environment, launcher, its package VCS revision, and the VCS revision of `odoo-instance-sdk` installed inside that environment. The frozen plan keeps the existing core install step and adds one companion install step pinned to the same target revision with an explicit target-revision SDK requirement.

Snapshot, journal, rollback, recovery argv, and incomplete-state reporting cover both installed tools when the companion is present. Before the first install, Rich output identifies both affected utilities. Verification executes both captured launchers and inspects distribution provenance inside each environment; equal version strings are insufficient. Commit/cleanup occurs only when core, companion, and companion-embedded SDK all match the target. Any partial failure follows the existing rollback-or-`update_incomplete` contract.

Alternative considered: update only the companion package. Rejected because its isolated uv-tool environment can retain the old SDK dependency. Alternative considered: install the companion whenever missing. Rejected because core-only composition is an explicit compatibility contract.

### 5. Keep project-aware commit policy and add verified identity input

CLI validation will allow `member_id` without `repo` only when normal project/environment context resolves the initialized Odoo worktree. `repo + member_id` remains generic/explicit-repository mode, `repo` without `member_id` remains invalid, and `tag` remains valid only for project-aware mode.

For project-aware SDK calls, the resource resolves the bound worktree repository host, calls the existing `explicit_commit_environment(member_id, host)`, and captures only verified name/email in `GIT_AUTHOR_*` and `GIT_COMMITTER_*` on the existing `git.commit` process step. The public dry-run exposes those safe identity fields and no token. The same staged tree, message, environment, and step are executed without replanning.

The Multica companion obtains the member UUID only from `client.context()` followed by `root_creator()`, revalidating parent lineage, workspace/project identity, and human creator status. It passes that UUID to project-aware commit; assignee, agent identity, arbitrary metadata, numeric GitLab ID, and machine identity are never candidates.

Alternative considered: use repository `user.name`/`user.email`. Rejected because it is mutable ambient state and is not proof of the selected member. Alternative considered: reuse remote credential injection alone. Rejected because askpass authorizes transport but does not define local commit identity.

### 6. Ponytail full gate

No new subsystem is necessary. Filestore handling reuses the checkout plan and COPY journal; attachment verification reuses the existing PostgreSQL/process boundary; identity reuses `explicit_commit_environment`, `git_frontier.credentials()`, and root-creator lineage; update coordination extends the existing provenance, frozen-step, snapshot, journal, verify, and recovery flow. All proposed abstractions are additions to those existing records and helpers, not parallel frameworks.

## Risks / Trade-offs

- **Attachment enumeration can be large** → use a bounded streaming/query result with normalized relative names and fail closed if the result cannot be checked completely.
- **One helper serves source backup and target restore** → bind one verified data root and distinct source/target database directories; regression tests assert source bytes remain unchanged.
- **Companion update can fail after core replacement** → snapshot both compositions first and retain journal/recovery evidence until both revisions verify; never collapse partial success into `updated`.
- **uv receipt layouts can be absent or ambiguous** → treat an absent companion launcher as core-only, but fail preflight when a present launcher lacks unambiguous protected provenance.
- **Safe identity fields are public in dry-run** → expose only verified name/email, never token, helper payload, or private environment values; retain central redaction.
- **Root lineage can change between planning and execution** → re-run the existing typed lineage checks before constructing/executing the commit identity snapshot and fail closed on drift.

## Migration Plan

No persistent schema migration is required. Existing commit history remains valid. Existing core-only uv-tool installations follow their current update flow; companion-aware state is recorded only when a companion installation is discovered. An interrupted coordinated update resumes or rolls back through the extended versioned journal and snapshot, retaining backward-readable core fields and adding optional companion fields.

## Open Questions

None. The authoritative behaviors and failure modes are fixed by this design and the delta specifications.
