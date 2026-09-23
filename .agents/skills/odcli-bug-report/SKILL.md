---
name: odcli-bug-report
description: Prepare, review, and submit a reproducible OdCLI bug report through the public CLI/SDK without bypassing safeguards.
---

# OdCLI bug report

Use this skill when OdCLI behaviour blocks or misleads work and a reproducible report is the correct next step.

## Workflow

1. Detect the problem and capture the smallest failing scenario.
2. Check requirements and obvious duplicate issues in the target repository.
3. Run `odcli bug-report init --title "<one line>" --kind bug|enhancement|tech-debt`.
4. Fill `report.md` honestly. Mark unknown Odoo/PostgreSQL values as unknown. Separate facts from hypotheses.
5. Preview locally with `odcli bug-report submit <REPORT_ID> --dry-run --format json`.
6. Run an independent review using the reviewer prompt reference. The skill writes `reviews/N.json`; OdCLI validates only.
7. After approval for the current payload hash, run `odcli bug-report submit <REPORT_ID>`.
8. Report the issue URL or the structured blockers, then return to the main task.

## Constraints

- Use the CLI/SDK boundary only. Do not edit internal catalog SQLite, bypass locks, or invent shell wrappers around `gh`.
- Read applicable SDK rules and project constraints before proposing a solution direction.
- Working from another Odoo repository does not exempt the report from SDK rules.
- If SDK sources or docs are unavailable for substantive verification, request the missing context and do not claim conformance.
- Do not add `--skip-review`, `--force`, or hidden emergency CLI flags.
- Duplicate GitHub issues are handled in the skill flow; OdCLI does not auto-create duplicates.

## Simplicity criteria

Apply Ponytail when available. When Ponytail is unavailable, apply the same written criteria:

- Reuse an existing mechanism instead of adding a parallel one.
- Limit the change to the confirmed cause.
- Compare against a smaller fix before proposing a broader refactor.
- Do not add a universal abstraction for one case.

## Emergency unblock

Normal mode ends with a verified bug report. It does not require implementing a fix in the user's project.

Emergency unblock applies only when all of the following hold:

- A confirmed OdCLI bug directly blocks the agent's main work.
- OdCLI provides no supported safe workaround without changing the work's requirements.
- Manual unblock would require direct SQLite/internal-catalog edits, bypassing the public CLI/SDK, or other actions that could break ownership, provenance, runtime identity, or state consistency.

When emergency unblock applies:

1. Finish the reviewed bug report first.
2. Work in the OdCLI repository on a separate feature branch from the current target branch.
3. Limit the diff to the confirmed root cause and preserve safety contracts.
4. Add a regression test through the public CLI/SDK boundary.
5. Run the relevant mandatory gates, publish the branch when authorized, and open a PR linked to the report.
6. Report branch/PR URL, check results, and whether the main work can safely resume.

Do not merge, release, or install the fix into the user's environment without separate permission. If checkout, publish, or PR creation is unavailable, keep a ready local commit or patch and report the missing capability instead of applying a risky manual state edit.

If a supported safe workaround exists, or manual unblock would not require bypassing public boundaries, emergency unblock does not apply. Report the bug normally.

## References

- Reviewer prompt: [reviewer-prompt.md](reviewer-prompt.md)
- Draft layout and validation: `odoo_instance_sdk.bug_report` and `odoo_instance_sdk.internal.bug_report`
