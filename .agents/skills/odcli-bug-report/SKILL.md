---
name: odcli-bug-report
description: Prepare, review, and submit a reproducible OdCLI bug report through the public CLI/SDK without bypassing safeguards.
---

# OdCLI bug report

Use this skill when OdCLI behaviour blocks or misleads work, or a capability needed by an agent is missing. A missing useful capability is a bug-reportable gap, not a reason to silently bypass the CLI.

## Workflow

1. Detect the problem and capture the smallest failing scenario.
2. Check requirements and obvious duplicate issues in the target repository.
3. Run `odcli bug-report init --title "<one line>" --kind bug|enhancement|tech-debt`.
4. Fill `report.md` honestly. Mark unknown Odoo/PostgreSQL values as unknown. Separate facts from hypotheses.
5. Preview locally with `odcli bug-report submit <REPORT_ID> --dry-run --format json`.
6. Run a separate reviewer using the reviewer prompt reference (the `odcli-autonomous-work` skill offers an optional CLI wrapper). The reviewer verdict is written to `reviews/N.json`; OdCLI validates it.
7. After approval for the current payload hash, run `odcli bug-report submit <REPORT_ID>`.
8. Report the issue URL or the structured blockers, then return to the main task through a verified safe workaround if one exists.

## Constraints

- Use the CLI/SDK boundary only. Do not edit internal catalog SQLite, bypass locks, or invent shell wrappers around `gh`.
- Read applicable SDK rules and project constraints before proposing a solution direction.
- Working from another Odoo repository does not exempt the report from SDK rules.
- If SDK sources or docs are unavailable for substantive verification, request the missing context and do not claim conformance.
- Do not add `--skip-review`, `--force`, or hidden emergency CLI flags.
- Duplicate GitHub issues are handled in the skill flow; OdCLI does not auto-create duplicates.
- A requested convenience that is not yet supported counts as a reportable capability gap. Do not present it as a regression or invent a reproduction of a command that never existed.

## Simplicity criteria

Apply Ponytail when available. When Ponytail is unavailable, apply the same written criteria:

- Reuse an existing mechanism instead of adding a parallel one.
- Limit the change to the confirmed cause.
- Compare against a smaller fix before proposing a broader refactor.
- Do not add a universal abstraction for one case.

## Emergency unblock

Normal mode ends with a verified bug report. It does not require implementing a fix in the user's project.

Emergency unblock applies only when all of the following hold:

- A confirmed OdCLI defect or missing capability directly blocks the agent's main work.
- OdCLI provides no supported safe workaround without changing the work's requirements.

When emergency unblock applies:

1. Finish the reviewed bug report first.
2. Work in the OdCLI repository on a separate feature branch from the current target branch.
3. Limit the diff to the confirmed root cause and preserve safety contracts.
4. Add a regression test through the public CLI/SDK boundary.
5. Run the relevant mandatory gates, publish the branch when authorized, and open a PR whose body contains `Fixes #<report issue number>` when it targets the default branch and fully resolves the report.
6. Test a pinned fix revision through `odcli-autonomous-work` only after its shared-`~/.odcli` compatibility gate. Keep the canonical `odcli` untouched. Report branch/PR URL, check results, and whether the main work can safely resume.

Do not merge or release without separate permission. The isolated test-tool installation is allowed only after the compatibility gate; an incompatible or unverified change to shared `~/.odcli` blocks use and is escalated to the user. If checkout, publish, or PR creation is unavailable, keep a ready local commit or patch and report the missing capability instead of applying a risky manual state edit.

If a supported safe workaround exists, emergency unblock does not apply. Report the bug normally and continue through the reviewed workaround. Never unblock by directly editing SQLite/internal catalog or bypassing the public CLI/SDK.

## References

- Reviewer prompt: [reviewer-prompt.md](reviewer-prompt.md)
- Draft layout and validation: `odoo_instance_sdk.bug_report` and `odoo_instance_sdk.internal.bug_report`
