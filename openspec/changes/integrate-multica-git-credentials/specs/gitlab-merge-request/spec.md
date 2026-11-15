## MODIFIED Requirements

### Requirement: Existing merge-request publication uses the selected task member

The existing `odcli-multica` merge-request command and CLI SHALL accept optional `member_id` and consume the same existing selected credential snapshot as other task Git operations. An explicit member SHALL change only the exact-host credential used for provider lookup and mutation. Existing issue-link, exact-project, unique-MR, assignee-precedence, bounded-HTTP, non-idempotent retry, result, and redaction contracts SHALL remain unchanged.

#### Scenario: Explicit member publishes the merge request

- **WHEN** publication receives a valid explicit human workspace member
- **THEN** existing lookup and mutation use that member's exact-host credential and report only safe explicit-selection metadata

#### Scenario: Explicit member credential is unavailable

- **WHEN** that member lacks a valid exact-host credential
- **THEN** publication fails before provider lookup or mutation without using the root creator, cache account, or another member
