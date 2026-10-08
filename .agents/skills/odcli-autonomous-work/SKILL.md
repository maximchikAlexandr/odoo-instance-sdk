---
name: odcli-autonomous-work
description: Coordinate autonomous OdCLI use when an agent encounters a defect or missing capability; triage, verify, report, or test a compatible hotfix without bypassing the public CLI.
---

# Autonomous OdCLI work

Use the installed `odcli` for the main task. Treat a broken operation **and** a missing useful capability as a potential bug. Do not replace an available public operation with direct catalog edits or an undocumented shortcut.

## Decision

1. Record the smallest concrete failure or capability gap, its effect on the current task, and a proposed supported workaround. Check whether an existing GitHub issue already covers it.
2. Use `scripts/reviewer.py triage <case-file>` to ask a separate read-only Codex session whether the issue is real, whether the workaround preserves the task's requirements and OdCLI state, and whether work can continue. Its verdict is evidence, not permission to merge or migrate data. If Codex is unavailable, stop at the review gate; do not self-approve.
3. If the verdict is `no_issue`, follow the corrected supported usage. If `report_only`, report the gap without pausing the main task. If `workaround`, continue through the verified workaround and report the defect/gap. Both report paths use `odcli-bug-report`. If `blocked`, use `odcli-bug-report`, then prepare the smallest hotfix in an OdCLI feature branch and PR. The main task waits only when neither a verified workaround nor a compatible test tool can unblock it.
4. For the report's second review gate, run `scripts/reviewer.py review <report-id>`. The wrapper uses the existing OdCLI payload hash and review-file helper. Submit only after `approved` for the current hash. `scripts/reviewer.py ask <session-id> <question-file>` continues a reviewer conversation when clarification is needed; a follow-up does not itself approve a changed report.

## Hotfix use

- A fix branch is never merged by this skill. Publish a PR with `Fixes #N` in its body for a fully fixed GitHub issue targeting the default branch; the user's merge permission remains the gate.
- Multiple agents may run distinct pinned fix revisions as `odcli-fix-<issue-number>`. They share the **same** `~/.odcli`; do not copy it. Review the exact commit with `scripts/reviewer.py compatibility <full-sha>` and verify catalog/storage format, migrations, locking and concurrent operations used by other agents. If compatibility is unknown or negative, do not install/run the fix tool: escalate the blocker to the user.
- Save the compatibility result with `scripts/reviewer.py --output <review-json> compatibility <full-sha>`, then install only that reviewed revision with `scripts/fix_tool.py install <issue-number> <full-sha> --pr <pr-number> --review <review-json>`. Its shim warns on every invocation and then forwards arguments to that revision's `odcli`. Never confuse it with canonical `odcli` or install over the canonical uv tool.
- For agent-driven updates use `scripts/fix_tool.py update`, which runs canonical `odcli update` and then reconciles fix tools. Cleanup is allowed only when the linked GitHub PR is merged into the default branch and the installed revision contains that merge, and no local unmerged/checked-out branch would be lost. A closed issue alone is insufficient. If GitHub or ancestry cannot be checked, keep the fix tool and tell the user why. A direct user-run `odcli update` does not invoke this skill-only cleanup.

## Boundaries

The reviewer wrapper needs a locally authenticated `codex` executable; fix-tool management needs `uv` and authenticated `gh`. None is a new project runtime dependency. Use `--model` on the reviewer wrapper when a stronger locally available model is desired. It injects additional developer instructions, not a replacement system prompt. Review runs read-only; the wrapper alone writes the validated review verdict. Do not claim the separate session proves model independence. Do not edit `~/.odcli` except through OdCLI's supported operations and the dedicated fix-tool registry/shims.
