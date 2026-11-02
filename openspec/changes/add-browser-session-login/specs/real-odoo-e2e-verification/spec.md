## ADDED Requirements

### Requirement: Real browser-login verification uses isolated disposable resources

Verification SHALL use the exact product revision in a fresh numbered `odcli-N` checkout with its own copied database, port, project profile, and compatible Odoo 19 filesystem session store. It SHALL exercise the actual panel, Caddy publication, instance-host consume, SDK child process, Odoo session store, browser cookie, `/odoo`, and `/web/session/get_session_info` boundaries. Cleanup SHALL remove the disposable slot/database/service while preserving the evidence branch/MR/commits and SHALL NOT close, merge, or delete them without separate authorization.

#### Scenario: Two users receive distinct real authorization

- **WHEN** an independent Tester opens as two eligible fixture users with deliberately different access rights using clean browser state
- **THEN** safe session-info evidence confirms the expected UID/database for each and a real protected action demonstrates the rights difference
- **AND** neither session is `SUPERUSER_ID` or `su=True`

#### Scenario: Replay and trust failures are live

- **WHEN** the Tester replays/expires a ticket or changes Origin, host, runtime, database, publication, or eligibility in an isolated scenario
- **THEN** the live boundary refuses it without creating a second session, leaking credentials, or starting/reconfiguring the runtime

### Requirement: Browser-login evidence excludes credentials

Tester evidence SHALL record product/test revisions, safe commands and exit codes, response classifications, safe UID/database observations, sanitized audit outcomes, assertions, cleanup, and required images. It SHALL NOT record ticket, SID, session token, password/API key, Cookie/Set-Cookie value, secret-file content, private hostname, or personal data. Images and raw diagnostic artifacts SHALL be attached only to Multica and SHALL NOT be committed unless they are actual outputs of the tested product required by the repository contract.

#### Scenario: Secret scan covers every evidence projection

- **WHEN** automated and manual evidence is finalized
- **THEN** assertions prove credentials are absent from Git, MR content, URLs, logs, plans, recordings, audit, screenshots/renders, and Multica explanatory text
- **AND** any secret-bearing capture is rejected rather than redacted after publication
