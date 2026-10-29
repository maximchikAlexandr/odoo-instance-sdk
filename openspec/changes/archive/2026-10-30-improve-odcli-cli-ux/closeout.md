# Completion evidence

Implementation and four-profile review accepted commit
`b6b0bb6347d70dc3125ce4358f61acfff75475f2` against baseline
`f0918da2d36b59c35cf0a949efc58744d1efb7f9`.
Review round 3 closed all findings without a waiver. The 32 reviewed test
methods/helpers received A=24, B=6, C=2, D=0, F=0. Comparable production line
and branch coverage did not decline. The local unchanged AST-inventory timeout
remains a recorded limitation; the complete exact-head CI passed that inventory.

## Task traceability

| Tasks | Accepted evidence |
| --- | --- |
| 1.1, 1.2 | Public missing-TICKET diagnostic, native stderr compatibility, machine usage envelopes, output contract tests, UX-02. |
| 1.3 | Invalid UTF-8 and real filesystem permission denial under VCS uv-tool installs, sanitized Rich/JSON/TOON, UX-01. |
| 2.1, 2.2 | Catalogue-first identity, one project-scoped snapshot, focused call-count/scope tests and public selectors, UX-03. |
| 2.3, 2.4 | Fresh-interpreter import boundaries, public exports and installed metadata observations, UX-04. Timing is informational. |
| 3.1, 3.2 | Command-tree short help, example parsing, project-bound public dry-run and literal passthrough, UX-06. |
| 4.1, 4.2, 4.3 | README/changelog, retained characterization suites, installed artifact coverage and complete mapped CI. |

## CI and independent acceptance

[GitHub Actions run 40938806677](https://github.com/maximchikAlexandr/odoo-instance-sdk/actions/runs/40938806677)
completed successfully at the reviewed commit. Offline tests, Linux/macOS
compatibility, package, Windows preparation, lint/types, dashboard, OSV and
smoke checks passed. No new dependency or CI framework was introduced.

E2E attempt 2 completed all 80 phases across baseline/candidate in 219.030 seconds;
six JSON/strict-TOON pairs matched. Independent manual acceptance passed
UX-01/02/03/04/06. Four before/after console-text renders for UX-01/06 were
attached to the internal evidence record and inspected. They are text renders,
not terminal screenshots. Raw command evidence remains authoritative.

Attempt 1 stopped at an installation prerequisite: file/source installations
cannot exercise the VCS-only self-update path. It is retained as incomplete,
not a product failure or a passed run. Attempt 2 used genuine isolated Git
uv-tool installations of the exact baseline/candidate commits. Total E2E
attempts: 2; product fixes during E2E: 0.

Both attempts used disposable data. The primary OdCLI executable, its live
catalogue and existing service resources were not executed, opened, migrated
or changed. Permission-denial fixtures were restored and owned working roots
cleaned; isolated installations and external evidence were retained.

## Evidence limitations and subsequent changes

Private test-repository and attachment URLs are intentionally absent from this
public document. The available interface cannot safely attach the four images
to GitHub; they remain in the internal evidence record. No image, video or raw
run report is committed to the repository.

Only documentation/OpenSpec completion follows the tested executable commit.
Its source and test content must remain byte-identical through final history
preparation. Final CI must be bound to the final published commit; a previous
green run does not establish that result. No merge is authorized here.
