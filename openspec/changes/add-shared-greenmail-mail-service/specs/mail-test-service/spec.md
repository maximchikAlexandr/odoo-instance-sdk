## ADDED Requirements

### Requirement: User-owned GreenMail lifecycle

OdCLI MUST expose public commands `mail_up_command`, `mail_status_command`, and `mail_down_command`. `mail up` MUST start or reuse one GreenMail container for the OS user and MUST be idempotent. A second `mail up` MUST NOT create a second container. State and the user secret MUST live under the OdCLI user root with directory mode `0700` and secret file mode `0600`. Concurrent `mail up` calls MUST take one user-level exclusive lock. `mail down` MUST remove only a container labeled as this user's managed GreenMail and MUST NOT change an Odoo database. `mail up` and `mail down` MUST NOT call GreenMail `/api/mail/purge` or `/api/service/reset`. Published SMTP, IMAP, and API ports MUST bind to loopback by default. The pinned image MUST be `greenmail/standalone:2.1.15`.

#### Scenario: Repeated up reuses the service

- **WHEN** GreenMail is already running for this user and `mail up` is invoked again
- **THEN** the command reports the running service and does not start another container

#### Scenario: Down ignores a foreign container

- **WHEN** a container uses the managed name but lacks this user's owner label
- **THEN** `mail down` fails without removing it

#### Scenario: Status is machine-readable and secret-free

- **WHEN** `odcli mail status --format json` runs
- **THEN** the document includes SMTP, IMAP, and API endpoints and states that provider delivery, SPF, DKIM, and DMARC are not validated
- **AND** the document does not include a mailbox password

### Requirement: Selected database mail configuration

`mail configure` MUST resolve the Odoo instance with the same project and environment selectors as `odcli run`. It MUST refuse before any database write when the database identity is ambiguous. It MUST ensure GreenMail is running, create or reuse one IMAP mailbox for that database, and verify SMTP and IMAP from the selected runtime. A host runtime MUST use the loopback published ports. A Docker runtime MUST use the GreenMail container DNS name and container ports, because `127.0.0.1` inside that container is not the host. If `fetchmail` is not installed, the command MUST fail with a clear error and MUST NOT install the module or leave a partial mail setup. The database change MUST be one Odoo shell transaction that deactivates other outgoing and incoming servers and upserts the managed servers without duplicates. A repeated configure MUST NOT create a second managed server and MUST NOT report success while another active server can still send. `--dry-run` MUST show the plan and MUST NOT start the write. `--undo` MUST restore only the selected database's previous server active flags and remove the managed servers. Undo MUST NOT stop GreenMail or edit another instance.

#### Scenario: Missing fetchmail writes nothing

- **WHEN** the selected database does not have `fetchmail` installed
- **THEN** configure fails before creating or updating `ir.mail_server` and `fetchmail.server`

#### Scenario: Repeat configure is idempotent

- **WHEN** configure succeeds and is invoked again for the same database
- **THEN** the managed SMTP and IMAP servers are updated in place and other servers stay inactive

#### Scenario: Undo restores the selected database only

- **WHEN** configure --undo runs after a successful configure
- **THEN** the previous server active flags are restored and GreenMail is left running

### Requirement: Isolated test mailboxes

Each configured database MUST use its own mailbox. Additional test runs MUST be able to address `<project>-<run-id>@example.test` without reading another run's mailbox. Mailbox passwords MUST be repeatable for the same address, stored only in an owner-only file, and omitted from stdout, JSON, and logs. Cleanup guidance MUST name `DELETE /api/user/{email}` for one mailbox and MUST forbid global purge and service reset. The README MUST include one SMTP example and one IMAP example that read ports from `odcli mail status --format json`. The IMAP example MUST require a separate Odoo processing check and MUST NOT treat presence in GreenMail as success.

#### Scenario: Two runs do not share a recipient

- **WHEN** two run identifiers are requested for one project
- **THEN** the resulting recipient addresses differ

#### Scenario: Connection and timeout failures differ

- **WHEN** GreenMail cannot be reached immediately, or it stays unready until the deadline
- **THEN** the errors use distinct codes `mail_connection` and `mail_timeout`
