# Merge-conflict webhook setup

The `Notify merge conflicts` workflow runs after each push to `main`, checks every
open pull request targeting `main`, and sends one JSON batch when GitHub reports
one or more pull requests as unmergeable.

## Required secret

Create a repository Actions secret named `MULTICA_CONFLICT_WEBHOOK_URL`. Its
value is the complete HTTPS webhook URL issued by the receiving automation.
The URL value must never be added to a workflow, repository file, pull request,
issue, comment, commit message, or log.

Set it in **Settings → Secrets and variables → Actions**, or without placing the
value on the command line:

```bash
gh secret set MULTICA_CONFLICT_WEBHOOK_URL \
  --repo maximchikAlexandr/odoo-instance-sdk
```

The command reads the value from standard input. The repository contains only
the secret's name; GitHub injects its value into the workflow environment.

## Receiver contract

The receiver must accept an HTTPS `POST` with JSON. One delivery represents one
`main` SHA and contains the repository, base branch/SHA, GitHub run id, and a
list of conflicting pull requests with their number, URL, title, draft state,
head branch/SHA, and base branch/SHA.

`Idempotency-Key` is `merge-conflicts:<repository>:<main-sha>`. The receiver
should deduplicate on that key and re-check live pull-request state before
starting work, because a developer may resolve a conflict before delivery is
processed.

## Permissions and security

The workflow has only `contents: read` and `pull-requests: read`. Protect changes
to `.github/workflows/**`, rotate the receiver URL after any suspected exposure,
and inspect Actions logs after initial setup to confirm that the value is never
printed. A failed delivery reports only an HTTP status or a generic network
error; it deliberately omits the secret URL.

## Verification

After the secret is configured, merge a harmless test branch while another open
pull request is intentionally conflicting. The workflow should report one
delivery. Re-run with all open pull requests mergeable; it should finish with
`No open pull requests conflict with main.` and send nothing.
