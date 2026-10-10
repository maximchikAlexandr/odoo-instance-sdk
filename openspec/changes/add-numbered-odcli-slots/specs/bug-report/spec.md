## MODIFIED Requirements

### Requirement: Emergency unblock of main work

The normal skill mode ends with a verified bug report and does NOT require the discovering agent to implement a fix. An emergency unblock applies when a confirmed OdCLI defect or missing capability directly blocks the main work and no reviewed supported safe workaround preserves that work's requirements. A direct SQLite/internal-catalog edit or bypass of the public CLI/SDK SHALL NOT count as a safe workaround.

In that case the agent SHALL NOT apply a dangerous workaround to the user's project and SHALL NOT leave the work blocked by a single bug report. After preparing and independently reviewing the report, the agent SHALL perform a minimal fix in the OdCLI repository: work from the current target branch in a separate feature branch, limit the diff to the confirmed cause, preserve safety contracts, add a regression test for a broken operation or an acceptance scenario for a missing capability through the public CLI/SDK boundary, run the relevant mandatory gates, publish the feature branch with write-access, create a PR with appropriate rights, and include `Fixes #N` in the PR body only when the PR targets the default branch and fully resolves that GitHub issue. The agent SHALL report the branch/PR URL, check results, and whether the main work can safely resume. The agent SHALL NOT merge or release the fix without separate permission.

To continue before merge, the agent MAY use a pinned `odcli-fix-<issue-number>` test command for its reviewed exact revision. The skill wrapper SHALL verify only hot-fix-specific repository/SHA/PR/issue/reviewer linkage and compatibility of external project, Odoo, PostgreSQL, database, filestore, Docker, or port resources that remain shared. It SHALL delegate uv installation, installed provenance, explicit-root capability, manifest, launcher rendering, lifecycle locking, inspection, and removal to the same package-owned lifecycle used by numbered tools. The wrapper SHALL NOT contain a second implementation of those mechanics. The launcher SHALL select fresh isolated `~/.odcli-fix-<issue-number>` without copying or accessing canonical, legacy, numbered, or neighboring hot-fix state. Catalogue-schema compatibility with isolated roots SHALL NOT be required. Unknown root confinement or unsafe shared external resources SHALL block use and be escalated. Each command SHALL warn with its exact SHA and isolated root before forwarding arguments.

Direct `odcli-fix-<issue-number> update` SHALL fail before update planning or effects. Skill-managed canonical `odcli update` SHALL authorize retirement only when the linked PR is merged into the default branch, the installed canonical revision contains that merge, the linked issue is closed, and no unsafe local branch/worktree would be lost. It SHALL then delegate idle-lock, deterministic identity, and selective deletion checks to the shared lifecycle. Eligible retirement SHALL delete only that hot fix's launcher, uv layout, manager metadata, lock after release, safe branch, and isolated state root. A direct user-run `odcli update` SHALL NOT be claimed to perform skill-managed cleanup.

If checkout, publish, or PR is unavailable due to missing sources, authorization, or rights, the agent SHALL keep a ready local commit/patch, report the missing capability, and SHALL NOT substitute the fix with a risky manual state edit. The blockage SHALL NOT expand the fix beyond the cause or bypass independent review.

#### Scenario: emergency unblock produces a minimal PR

- **WHEN** all emergency-unblock conditions hold
- **THEN** the agent opens a minimal PR linked to the bug report, preserves safety contracts, and may resume only through a reviewed compatible pinned test command while awaiting the user's merge decision

#### Scenario: no dangerous workaround

- **WHEN** a reviewed supported safe workaround preserves the main task's requirements
- **THEN** the emergency unblock does not apply and the agent reports the bug normally

#### Scenario: concurrent hotfix revisions use isolated catalogues

- **WHEN** two agents need different unmerged OdCLI fixes concurrently
- **THEN** each uses its reviewed pinned `odcli-fix-<issue-number>` command with its own `~/.odcli-fix-<issue-number>`, and neither accesses canonical, numbered, or neighboring hot-fix state

#### Scenario: one merged hot fix is retired selectively

- **WHEN** two hot fixes exist and exactly one idle hot fix satisfies every merge, ancestry, issue, branch, identity, and lock gate
- **THEN** skill-managed reconciliation removes only that hot fix and its isolated root while preserving every other installation and state root
