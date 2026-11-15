## ADDED Requirements

### Requirement: Selected-user browser verification uses one isolated disposable slot

Verification SHALL use the exact candidate revision in a fresh numbered `odcli-N` checkout with its own copied database, port, and supported Odoo 19 filesystem session store. One fixture helper SHALL seed/reset/inspect/cleanup two synthetic eligible users. The walkthrough SHALL use the real panel, Caddy consume route, SDK child process, Odoo session store, browser cookie, `/odoo`, session-info, neighboring instance host, and logout. Cleanup SHALL remove the disposable slot/database while preserving the evidence branch/MR/commit.

#### Scenario: Deliberately selected user opens Odoo

- **WHEN** an independent Tester chooses one of two fixture users in a clean browser
- **THEN** safe session-info confirms the selected non-superuser UID/database, the neighboring instance host remains unauthenticated, and normal logout invalidates the selected session

### Requirement: Browser-login evidence contains no credentials

Evidence SHALL record exact product/test revisions, commands/exit codes, safe UID/database observations, assertions, required panel/login/logout images, and cleanup/read-back. It SHALL NOT contain handoff, SID, session token, Cookie/Set-Cookie value, password/API key, private hostname, personal data, or secret-file content. Images and diagnostics SHALL remain Multica attachments rather than Git content.

#### Scenario: Evidence is finalized

- **WHEN** automatic and manual evidence is published
- **THEN** credential absence and attachment read-back are verified and any credential-bearing capture blocks acceptance
