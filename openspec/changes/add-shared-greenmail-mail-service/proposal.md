# Proposal

## Why

Local Odoo projects need a shared, user-owned SMTP and IMAP sink so several test runs can prove outbound and inbound mail without leaking messages to a real provider or mixing results in one mailbox. GitHub #199 asks for that service and for database-scoped setup that can be undone.

## What Changes

- Add `odcli mail up`, `mail status`, `mail down`, and `mail configure` / `mail configure --undo`.
- Run one loopback-published GreenMail container per OS user, with an owner lock and an owner label so `mail down` cannot remove a foreign container.
- Point only the selected Odoo database at that service, in one shell transaction, and restore the previous server active flags on undo.
- Document SMTP and IMAP examples that take endpoints from `odcli mail status --format json`. The repository output contract uses `--format json` rather than the issue's `--json` spelling.

## Capabilities

### New Capabilities

- `mail-test-service`: user-level GreenMail lifecycle, per-database mail configuration, secret handling, and isolated test mailboxes.

### Modified Capabilities

- `cli-odcli`: the Click surface gains the mail commands as thin adapters over the public SDK primitives.

## Impact

- New SDK resource and CLI group, plus the public CLI leaf inventory, README, and changelog.
- Docker image `greenmail/standalone:2.1.15` and the Odoo shell are used only through the existing process boundary.
- No new third-party Python dependency. `mail up` and `mail down` do not write an Odoo database.
