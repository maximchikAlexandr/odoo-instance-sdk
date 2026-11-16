# Design

## Context

OdCLI already starts Docker workloads through `internal.proc` and writes Odoo through one captured shell script. GreenMail 2.1.15 exposes `POST /api/user`, `DELETE /api/user/{email}`, `GET /api/service/readiness`, and only a global `POST /api/mail/purge` plus `POST /api/service/reset`. There is no per-message purge.

## Goals / Non-Goals

**Goals:**

- One loopback GreenMail container per OS user, labeled and locked.
- Database-scoped SMTP and IMAP setup with undo and dry-run.
- Host and Docker runtimes get an address that is reachable from that runtime.
- Secrets stay in mode `0600` files and out of plans, stdout, and JSON.

**Non-Goals:**

- CLI commands for send, read, wait, or purge.
- Installing `fetchmail`.
- Proving SPF, DKIM, DMARC, or delivery through a real provider.
- Calling GreenMail purge or reset.

## Decisions

1. **Image and ports.** Pin `greenmail/standalone:2.1.15`. Override `GREENMAIL_OPTS` to `-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0` so authentication stays enabled. Publish only `127.0.0.1` for SMTP `3025`, IMAP `3143`, and API `8080`. The container name and network are `odcli-greenmail-<uid>`. Inside that network the DNS alias remains `odcli-greenmail`.
2. **Ownership.** Container and network labels are `odcli.mail.managed=true` and `odcli.mail.owner=<uid>`. `mail down` removes the container only when both labels match. A foreign name is an error.
3. **Idempotent up.** The command snapshot includes inspect, network create, remove, run, and readiness. Execution skips create/run when inspect shows an owned running loopback container. An owned container published beyond loopback is recreated.
4. **Runtime address.** The configure snapshot includes a read-only `docker ps` for the instance HTTP port. Execution checks host processes first. A host runtime stores `127.0.0.1` and the published ports, and the Odoo shell probes them before any write. The plan records a host-process check and an attach action. A single container publishing the instance HTTP port, other than this user's GreenMail, makes the follow-up `docker network connect` and `python3` probe dynamic process steps, because the container id does not exist at planning time. Those steps use the user's network, the `odcli-greenmail` alias, and container ports `3025` and `3143`. The undo snapshot key includes the database host and port. No running runtime means the host placement used by `odcli run`. Dry-run shows the discovery step and does not connect or probe.
5. **Database switch.** One shell transaction checks `fetchmail` and a concrete inbound model (`discuss.channel`, then `mail.channel`) before writes. It stores only server ids and `active` flags in a mode `0600` snapshot, deactivates every other `ir.mail_server` and `fetchmail.server`, and upserts `odcli_mail.greenmail_smtp` and `odcli_mail.greenmail_imap`. `smtp_debug` is forced off. The mailbox password is read from the credentials file inside the shell and is never part of `result`. Undo restores `active` and unlinks the managed rows. It does not include Docker steps.
6. **Mailbox isolation.** The configured mailbox is `odcli-<database>@example.test`. Its password is HMAC-SHA256 of a user secret and the address. Extra runs use `mail_recipient(project, run_id)`. Cleanup of one mailbox is `DELETE /api/user/{email}`. Global purge and reset are not implemented.
7. **Errors.** Docker or API rejection is `mail_connection`. Readiness past the deadline while the container was started is `mail_timeout`. Missing `fetchmail` is `mail_fetchmail_missing`.
8. **Output spelling.** Status uses the existing `--format json` envelope. `--json` stays rejected by the output contract.

## Risks / Trade-offs

- GreenMail keeps mail in memory, so `mail down` discards every user's test mail. That matches the global command and is why tests must not call it.
- Mailboxes on one server are not a confidentiality boundary. Only synthetic addresses are supported.
- A Docker probe needs `python3` in the Odoo container. If it is absent, configure fails before the database write.
- The snapshot is the baseline from the configure that found no managed servers. Repeating configure does not move that baseline.

## Migration Plan

No stored data exists yet. Rollback is removing the CLI group and stopping the labeled container with `mail down`.

## Open Questions

None. The issue left the SMTP takeover and cleanup mechanism to implementation; both are fixed above.
