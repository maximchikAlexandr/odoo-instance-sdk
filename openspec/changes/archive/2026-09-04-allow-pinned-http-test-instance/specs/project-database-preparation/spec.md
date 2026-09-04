## MODIFIED Requirements

### Requirement: Configured remote test source and secret boundary

The workflow SHALL resolve the remote base URL and database only from `[test_instance]` in the project manifest. The remote database master password SHALL be read only from `ODCLI_TEST_MASTER_PASSWORD` at execution time, SHALL be passed only to the remote `OdooInstance`, and SHALL NOT be persisted in the manifest, catalog, result, exception, argv, or logs. Missing configuration or a missing/empty environment secret SHALL fail before network or local mutation.

Repository-selected preparation flows SHALL require an external exact-origin approval for every non-loopback test-instance origin before preparation work begins. `ODCLI_TEST_INSTANCE_ORIGIN_PINS` SHALL be the sole approval channel and SHALL contain one comma-separated list of non-secret exact origins. Entries SHALL be compared after the existing canonical-origin normalization (lowercase scheme/host and effective port); paths, queries, fragments, wildcards, and host-only values SHALL not broaden approval. Loopback origins SHALL not require a pin. A non-loopback HTTP origin SHALL be permitted only when its canonical exact origin is pinned and SHALL emit the existing once-per-process cleartext-secret warning before sending a master password. Transport and approval SHALL be checked before the preparation lock, PostgreSQL readiness, database-manager access, HTTP, catalog mutation, or manifest mutation. The approval variable SHALL not be persisted. Generic direct `DatabaseResource.backup()` calls SHALL retain their existing contract and SHALL not require this repository-selected approval.

#### Scenario: Operator approves a repository-selected origin

- **WHEN** the operator exports `ODCLI_TEST_INSTANCE_ORIGIN_PINS="https://odoo-test.example:443"` and runs project refresh against `[test_instance].base_url = "https://odoo-test.example"`
- **THEN** the canonical exact-origin check accepts the remote source without exposing or persisting a password; an unpinned or differently ported origin fails before preparation mutation

#### Scenario: Operator approves a repository-selected HTTP origin

- **WHEN** the operator pins the exact canonical origin `http://odoo-test.example:8069` and runs project refresh against that HTTP test instance
- **THEN** preparation is allowed, the cleartext-secret warning is emitted before the password-bearing request, and the password remains redacted from every output and persisted artifact

#### Scenario: Unpinned HTTP origin remains rejected

- **WHEN** a repository selects a non-loopback HTTP origin that is absent from `ODCLI_TEST_INSTANCE_ORIGIN_PINS`
- **THEN** preparation fails before network or local mutation

`source_branch` in typed options SHALL override `test_instance.git_branch`. The branch value is declarative provenance; the SDK SHALL NOT query or infer Git state from the remote Odoo instance. The result SHALL identify the branch origin as `explicit`, `configured`, or `unknown` without exposing secrets.

#### Scenario: Explicit branch override

- **WHEN** `[test_instance].git_branch="develop"` and refresh supplies `source_branch="release/19"`
- **THEN** the backup records `release/19` and the result reports branch origin `explicit`

#### Scenario: Missing remote password

- **WHEN** refresh is requested without a non-empty `ODCLI_TEST_MASTER_PASSWORD`
- **THEN** it fails before HTTP, catalog, PostgreSQL, or manifest mutation and no secret value appears in the error
