## REMOVED Requirements

### Requirement: Configured remote test source and secret boundary

**Reason**: Separate origin approval is retired under the trusted project-manifest model. The replacement below preserves source/password selection and transport safeguards while deliberately replacing origin-approval scenarios.

**Migration**: Keep project URLs and password variables. Existing origin approval variables are ignored and may be removed manually; no secret-file rewrite is required.

## ADDED Requirements

### Requirement: Trusted project source and password selection

The workflow SHALL resolve the remote URL/database from the explicitly selected named entry, or from legacy `[test_instance]` when no name is supplied. Named-only projects without a selector SHALL fail; unknown names SHALL never fall back. Legacy preparation SHALL read `ODCLI_TEST_MASTER_PASSWORD`; named preparation SHALL read the source-specific password described below. Passwords SHALL be passed only to the selected remote instance and SHALL NOT appear in manifest, catalog, results, exceptions, argv, logs or fingerprints. Missing configuration or missing/empty secrets SHALL fail before network or local mutation.

Project-configured remote URLs SHALL be trusted for named and legacy sources. Separate origin approval SHALL NOT be required. `ODCLI_TEST_INSTANCE_ORIGIN_PINS` and `ODCLI_REMOTE_<NAME>_ORIGIN` SHALL be treated as legacy no-op variables: their absence, emptiness, mismatched or malformed values SHALL NOT affect source selection or block execution. Init and diagnostics SHALL NOT generate or request them. Existing dotenv files SHALL NOT be rewritten to remove them; normal dotenv syntax validation remains applicable.

URL normalization and validation, TLS verification and the existing once-per-process cleartext-secret warning for non-loopback HTTP SHALL remain. Automatic redirects SHALL remain disabled; any redirect response SHALL fail without replaying a password-bearing request. Generic direct backup calls SHALL retain their current contract.

#### Scenario: Configured source needs no origin approval

- **WHEN** a named or legacy project source has a valid URL and required password but no origin variable
- **THEN** preparation proceeds without a separate origin approval check

#### Scenario: Legacy origin values are ignored

- **WHEN** old origin variables are present with empty, mismatched or invalid-origin values in otherwise valid dotenv or process environment
- **THEN** the SDK uses the project URL and selected password exactly as if those variables were absent

#### Scenario: Configured HTTP retains its warning

- **WHEN** a trusted project source uses non-loopback HTTP
- **THEN** preparation emits the existing cleartext-secret warning before sending the password and keeps the password redacted

`source_branch` in typed options SHALL override `test_instance.git_branch`. The branch value is declarative provenance; the SDK SHALL NOT query or infer Git state from the remote Odoo instance. The result SHALL identify the branch origin as `explicit`, `configured`, or `unknown` without exposing secrets.

#### Scenario: Explicit branch override

- **WHEN** `[test_instance].git_branch="develop"` and refresh supplies `source_branch="release/19"`
- **THEN** the backup records `release/19` and the result reports branch origin `explicit`

#### Scenario: Missing remote password

- **WHEN** legacy refresh is requested without a non-empty `ODCLI_TEST_MASTER_PASSWORD`
- **THEN** it fails before HTTP, catalog, PostgreSQL, or manifest mutation and no secret value appears in the error

For a named source, the SDK SHALL derive only `ODCLI_REMOTE_<UPPER_NAME>_MASTER_PASSWORD`. Process environment SHALL override the existing owner-only project dotenv per key; worktrees SHALL resolve the registered project root. An empty process override SHALL fail. The selected project URL SHALL be trusted without a separate origin approval variable. URL validation, TLS verification, existing transport warnings and canonicalization SHALL apply. Redirect responses SHALL fail without follow-up requests. Every key matching a valid named-source password form SHALL be stripped from all child-process environments, including local Odoo, and all diagnostic projections.

Named-source branch provenance SHALL come from its configured branch or explicit override using the same declared/unknown semantics as legacy preparation. Results and backup catalog provenance SHALL retain nullable historical source name, normalized origin, database and declared branch; configuration edits or removal SHALL NOT rewrite historical provenance.

#### Scenario: Two passwords remain separate

- **WHEN** lab and staging are configured with different credentials
- **THEN** a staging request uses only the staging password, and neither password reaches a local child process or output

#### Scenario: Source URL changed before planning

- **WHEN** a new command is planned after the selected project URL changes to another valid URL
- **THEN** it uses that configured URL without consulting legacy origin variables
- **AND** changing the profile after planning still causes the existing stale-plan rejection

#### Scenario: Cross-origin redirect

- **WHEN** a configured endpoint redirects to another origin
- **THEN** no password-bearing request is forwarded to that other origin

#### Scenario: Worktree and process override

- **WHEN** preparation runs from a registered worktree and a staging password is present in both project dotenv and process environment
- **THEN** the process value wins, the same canonical project dotenv is used, and an empty override is rejected

#### Scenario: Named source omitted or misspelled

- **WHEN** a named-only project omits the selector or a caller supplies an unknown name
- **THEN** preparation fails with available names and does not select the first profile

### Requirement: Named-source preparation reuses existing behavior

An explicit named source SHALL use the current download, validation, local restore, neutralization, audit and failure-retention behavior. Manual refresh with restore SHALL retain its existing atomic default switch; download-only SHALL leave the default unchanged. Freshness/coalescing SHALL compare canonical project, source origin, database and branch, not merely source name or database name. Captured profile changes SHALL cause a stale-plan failure before mutation.

#### Scenario: Refresh staging

- **WHEN** a caller selects staging for manual refresh with restore
- **THEN** it downloads staging, creates a new local target through existing preparation, and switches the project default only after all requested postconditions succeed

#### Scenario: Lab result cannot satisfy staging

- **WHEN** concurrent requests select lab and staging with an identically named remote database
- **THEN** a completed lab result is not reused as the staging result

### Requirement: Diagnostics explain source readiness without hidden backup

Existing public diagnostics SHALL accept an optional named source and report configuration, password presence, Git ref and local restore prerequisites without exposing values. Offline diagnostics SHALL make no network request. Checks SHALL NOT create a backup just to test credentials; authentication SHALL be reported unverified until actually demonstrated.

#### Scenario: Missing staging secret

- **WHEN** source diagnostics find a missing password
- **THEN** they report the missing variable name and corrective action without downloading data or exposing another secret
