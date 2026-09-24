# bug-report Specification

## Purpose
TBD - created by archiving change address-alpha-testing-defects-74. Update Purpose after archive.

## Requirements

### Requirement: `odcli bug-report init` creates a local draft

`odcli bug-report init --title TEXT --kind bug|enhancement|tech-debt [--format rich|json|toon]` SHALL create a UUID `REPORT_ID`, a directory `get_user_root()/bug-reports/<REPORT_ID>/`, and the files `report.md`, `metadata.json`, and an empty `reviews/` directory. The SDK SHALL expose `bug_report_init_command()` returning `Command[BugReportInitResult]`. `--dry-run` SHALL emit the plan without creating files or running processes. `PUBLIC_LEAF_CASES` SHALL set `sdk_primitive=bug_report_init_command`. The directory SHALL be `0700` and the files `0600` on POSIX.

`init` SHALL work offline from any cwd, including outside an initialized Odoo project and without GitHub connectivity or authorization. Unknown Odoo/PostgreSQL versions SHALL be recorded as unknown, not required. The command SHALL return the `REPORT_ID` and absolute paths to the directory and `report.md`.

`report.md` SHALL be a template with a title and the sections: context (installed OdCLI version and VCS SHA if available, OS/Python, applicable Odoo/PostgreSQL versions, unknown values explicitly marked), reproduction or scenario, actual vs expected behaviour with the expectation's basis, evidence (separated facts from hypotheses), minimal sufficient solution direction with bounds, verifiable acceptance criteria and a regression scenario, and applicable project constraints and change consequences. Unfilled required sections SHALL block submission. `metadata.json` SHALL hold the ID, creation date, kind, target repository/labels, and submission/URL fields managed by the SDK.

`init` SHALL accept `--dry-run` and SHALL NOT create files or run processes in that mode. The command SHALL follow the shared output contract, exit codes, and redaction rules. `cli_only_reason` SHALL NOT be used.

#### Scenario: init creates a draft offline

- **WHEN** `odcli bug-report init --title "stop does not stop foreground run" --kind bug --format json` runs outside an Odoo project and without network
- **THEN** a UUID draft directory is created under the bug-reports root with `report.md`, `metadata.json`, and `reviews/`, and the result returns the `REPORT_ID` and absolute paths

#### Scenario: init dry-run creates nothing

- **WHEN** `odcli bug-report init --title "..." --kind bug --dry-run` runs
- **THEN** no file or directory is created and no process is started

#### Scenario: repeated init does not overwrite

- **WHEN** `odcli bug-report init` runs twice with the same title
- **THEN** two different drafts with different `REPORT_ID`s are created and the first draft is unchanged

#### Scenario: path traversal is rejected

- **WHEN** a path traversal or symlink substitution targets the bug-reports root
- **THEN** `init` rejects it and no file is created outside the root

### Requirement: `odcli bug-report submit` validates and publishes a draft

`odcli bug-report submit <REPORT_ID> [--dry-run] [--format rich|json|toon]` SHALL validate the report structure, required sections, size (`report.md` at most 262144 bytes), and secret redaction before any external action. The SDK SHALL expose `bug_report_submit_command()`. `--dry-run` SHALL NOT write, send, invoke a reviewer, or call `gh`. Submit SHALL be allowed only when the highest `reviews/N.json` with N in {1,2,3} has `verdict=approved` and SHA-256 matching title+body+repo+labels. There SHALL be no `--skip-review` or `--force`. Default repo `maximchikAlexandr/odoo-instance-sdk` unless `get_config_root()/user.toml` `[bug_report].repository` overrides it. `[bug_report]` SHALL NOT override labels. GitHub labels SHALL be `["alpha-testing"]` only (`--kind` is `metadata.kind` plus a `Kind:` line in `report.md`). If the create outcome is uncertain after timeout/failure, the command SHALL return `submit_outcome_unknown` and SHALL NOT blindly re-POST. `--dry-run` SHALL return `report_valid` (structure/size/redaction) and `submit_ready` (valid plus approved hash) as separate booleans plus `report_errors` and `submit_blockers`. The skill SHALL write `reviews/N.json`; CLI SHALL only validate those files. Lock `get_locks_dir()/bug-report-{REPORT_ID}.lock`. Expression SHALL NOT appear in that lock, the submit-intent ActionStep, or the `gh` ProcessStep. Before the `gh` ProcessStep, an ActionStep SHALL record submit intent in `metadata.json`. `gh` argv SHALL be `("gh", "issue", "create", "--repo", <repo>, "--title", <title>, "--label", "alpha-testing", "--body-file", "-")` with the body on stdin through `internal/proc`, `shell=False`. The child SHALL inherit `GH_TOKEN`; OdCLI SHALL NOT copy it into config, draft, or argv. Duplicate GitHub issues SHALL NOT be auto-created; the skill handles showing a found duplicate. Emergency unblock SHALL NOT be a CLI flag.

Normal `submit` SHALL check structure, review, payload-hash immutability, and the attempt limit before external actions. On success it SHALL store the issue URL/number and return it with the `REPORT_ID`. Re-submitting an unchanged already-published report SHALL return the stored URL. Changing the title, body, repo, or labels after approval SHALL invalidate the approval and require a new review within the same limit. The publishable body SHALL include a stable technical marker `REPORT_ID` that participates in preview and review.

`gh` SHALL be invoked through `internal/proc` with `shell=False` and `--body-file -` (body on stdin); no shell interpolation of the body. Multiline text with special characters SHALL be delivered exactly. A local lock SHALL protect against concurrent submit of one `REPORT_ID`. The command SHALL NOT declare exactly-once: GitHub create and local record are not a shared transaction. If the create outcome is uncertain after timeout/failure, the command SHALL return `submit_outcome_unknown` and SHALL NOT blindly re-POST; it SHALL re-check the actual issue by stored URL/ID or the `REPORT_ID` marker. A local write failure SHALL NOT cause a blind re-POST.

Errors of missing `gh`, authorization, rights, or labels SHALL save the draft and return a typed result. The target repository and labels SHALL be configured once in trusted user config and fixed in the draft; subsequent config changes SHALL NOT redirect an already-approved report without a new review. The repository SHALL NOT be inferred from the current Odoo project Git remote. Secrets SHALL NOT appear in argv, output, plans, or hashes.

#### Scenario: dry-run shows payload and hash

- **WHEN** `odcli bug-report submit <ID> --dry-run --format json` runs on a filled draft
- **THEN** the result shows the target repo, title, exact body, labels `["alpha-testing"]`, SHA-256, `report_valid`, `submit_ready`, `report_errors`, and `submit_blockers`, and no file is written, no `gh` is invoked, and no reviewer is contacted

#### Scenario: submit without review is blocked

- **WHEN** `odcli bug-report submit <ID>` runs on a filled but unreviewed draft
- **THEN** no `gh` call occurs and the result reports missing approval as a structured reason

#### Scenario: successful submit stores URL

- **WHEN** `odcli bug-report submit <ID>` runs on an approved draft
- **THEN** `gh` is invoked through `internal/proc` with `shell=False` and `--body-file -`, the issue URL/number is stored, and the result returns the `REPORT_ID` and URL

#### Scenario: repeat returns stored URL

- **WHEN** `odcli bug-report submit <ID>` runs again on an unchanged published draft
- **THEN** the stored URL is returned and no new issue is created

#### Scenario: changed text invalidates approval

- **WHEN** the title, body, repo, or labels change after approval
- **THEN** a new review is required within the same limit before submit

#### Scenario: timeout re-checks instead of blind re-POST

- **WHEN** `gh` times out or the network outcome is uncertain
- **THEN** the command re-checks the actual issue by stored URL/ID or `REPORT_ID` marker and does not blindly re-POST

#### Scenario: multiline body is delivered exactly

- **WHEN** `submit` sends a multiline body with special characters
- **THEN** it is delivered exactly via `--body-file -` through stdin without shell interpolation

#### Scenario: concurrent submit is blocked

- **WHEN** two concurrent `submit` calls target the same `REPORT_ID`
- **THEN** the local lock prevents double submission

### Requirement: Up to three independent reviewer rounds

A submission SHALL be allowed when the last completed `reviews/N.json` is `approved` for the current payload hash. Round 1 approval is enough. At most three completed `changes_requested` rounds then stop without publish. The skill SHALL write `reviews/N.json`. OdCLI SHALL NOT embed an LLM and SHALL NOT write review files. Each `reviews/<N>.json` SHALL contain `round`, `reviewed_payload_sha256`, `reviewer_session_ref` (real id or `null`), `verdict`, and `findings`.

After `changes_requested`, the author edits `report.md`, repeats local validation, and passes the new revision to a reviewer. After the third refusal, the command SHALL stop, report the `REPORT_ID`, path, and unresolved questions to the user, and SHALL NOT publish. The limit SHALL NOT reset on resumption in another session. A reviewer launch failure without a verdict SHALL NOT count as a substantive review and SHALL NOT be substituted with approval.

#### Scenario: approval on first review

- **WHEN** the first reviewer returns `approved`
- **THEN** the report is ready for submission

#### Scenario: approval after correction

- **WHEN** the first reviewer returns `changes_requested` and the second returns `approved` after edits
- **THEN** the report is ready for submission

#### Scenario: three refusals stop the flow

- **WHEN** three reviews return `changes_requested`
- **THEN** the command stops, reports the unresolved questions, and does not publish

#### Scenario: reviewer launch failure does not count

- **WHEN** a reviewer launch fails without producing a verdict
- **THEN** it does not count as a substantive review and is not substituted with approval

### Requirement: Portable `odcli-bug-report` agent skill

A portable skill `odcli-bug-report` SHALL live under `.agents/skills/` with `name` and `description` frontmatter. It SHALL be runtime-agnostic and SHALL NOT bind to a single subagent API. The main `SKILL.md` SHALL contain the short sequence: detect problem → check requirements/obvious duplicate → `init` → fill report → local preview → independent review with the limited cycle → `submit` → report URL or block → return to the main task. A reviewer prompt reference SHALL be offloaded to one reference file if it reduces main context. The schema of report/review SHALL match the CLI implementation; a second validator or template SHALL NOT be created.

Before proposing a solution, the agent and reviewer SHALL read the applicable SDK rules and the project constraints where the failure appeared. Working from another Odoo repository SHALL NOT exempt from SDK rules. If SDK sources/docs are unavailable for substantive verification, the agent SHALL explicitly request the needed context and SHALL NOT claim conformance. The skill SHALL apply Ponytail criteria when Ponytail is available and SHALL work without Ponytail by applying the written criteria: reuse existing mechanism, limit change to the cause, compare with a smaller fix, and do not add a universal abstraction for one case.

The skill SHALL NOT require the author to pre-implement a fix, run a large audit, prove an unknown cause, or rewrite architecture. A quality reproducible bug report with honest uncertainty is acceptable. The skill SHALL NOT justify disabling safeguards or violating contracts for brevity.

#### Scenario: skill is runtime-agnostic

- **WHEN** the skill is read
- **THEN** it does not bind to a single subagent API and works with Codex, Multica, OpenCode, or another runtime

#### Scenario: skill works without Ponytail

- **WHEN** Ponytail is not available
- **THEN** the skill applies the written simplicity criteria and remains operational

### Requirement: Emergency unblock of main work

The normal skill mode ends with a verified bug report and does NOT require the discovering agent to implement a fix. An emergency unblock applies ONLY when all of the following hold: a confirmed OdCLI bug directly blocks the agent's main work, OdCLI provides no supported safe workaround to continue without changing the work's requirements, and manual unblock would require direct SQLite/internal-catalog edits, bypassing the public CLI/SDK, or other actions that could break ownership/provenance/runtime-identity/state consistency.

In that case the agent SHALL NOT apply a dangerous workaround to the user's project and SHALL NOT leave the work blocked by a single bug report. After preparing and independently reviewing the report, the agent SHALL perform a minimal fix in the OdCLI repository: work from the current target branch in a separate feature branch, limit the diff to the confirmed root cause, preserve safety contracts, add a regression test reproducing the block via the public CLI/SDK boundary, run the relevant mandatory gates, publish the feature branch with write-access, create a PR with appropriate rights, link it to the bug report, and report the branch/PR URL, check results, and whether the main work can safely resume. The agent SHALL NOT merge, release, or install the fix into the user's environment without separate permission.

If checkout, publish, or PR is unavailable due to missing sources, authorization, or rights, the agent SHALL keep a ready local commit/patch, report the missing capability, and SHALL NOT substitute the fix with a risky manual state edit. The blockage SHALL NOT expand the fix beyond the cause or bypass independent review.

#### Scenario: emergency unblock produces a minimal PR

- **WHEN** all emergency-unblock conditions hold
- **THEN** the agent opens a minimal PR linked to the bug report, preserves safety contracts, and reports whether the main work can resume

#### Scenario: no dangerous workaround

- **WHEN** a supported safe workaround exists or manual unblock would not require bypassing public boundaries
- **THEN** the emergency unblock does not apply and the agent reports the bug normally
