## ADDED Requirements

### Requirement: Atomic identity-changing secret edits
Authenticated Admin edits SHALL allow all business fields: `base_url` and write-only `master_password` for Remote Odoo; `member_id`, `host`, `login`, and write-only `token` for GitLab members. Revision, `admin_id`, `created_at`, `updated_at`, internal token key, and other technical fields SHALL NOT be editable form inputs. The revision embedded in the current opaque route identity SHALL be the compare-and-swap token. A blank secret SHALL retain the stored secret and a nonblank secret SHALL replace it.

Changing a normalized base URL or normalized member/host pair SHALL execute as one primary-key-changing transaction after format, uniqueness, and expected-revision checks. The transaction SHALL preserve `created_at`, advance revision/`updated_at` once, retain the stable internal GitLab token key, and leave neither a duplicate nor partial old/new record. The returned projection SHALL use the new revision-bound route identity; stale old routes SHALL resolve as absent/conflict without exposing secret data.

#### Scenario: Remote URL changes without password replacement
- **WHEN** an administrator edits `base_url`, submits a blank password, and the expected revision is current
- **THEN** one record exists under the normalized new URL with the prior password and creation time, advanced revision/update time, and a new Admin route

#### Scenario: GitLab identity and business values change together
- **WHEN** an administrator changes `member_id`, `host`, `login`, and token in one valid current-revision edit
- **THEN** one record exists under the normalized new pair with the new login/token, unchanged internal token key/creation time, and one advanced revision

#### Scenario: Identity collision rolls back
- **WHEN** a proposed normalized URL or member/host pair already belongs to another record
- **THEN** the edit reports a field-specific uniqueness error and both original records remain unchanged

#### Scenario: Concurrent identity edit is stale
- **WHEN** another session commits any edit after the revision-bound edit route was loaded
- **THEN** the stale update fails with reload-and-retry guidance and does not create, delete, or overwrite a record

#### Scenario: Old route stops resolving
- **WHEN** a successful edit changes a record identity
- **THEN** Admin redirects to the new detail/edit route and the old identity/revision route refuses predictably

