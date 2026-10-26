# Secret Store Specification

## Purpose

Define owner-only encrypted credential storage, administrator authentication, write-only secret management, and fail-closed credential resolution.

## Requirements

### Requirement: Owner-only single-administrator configuration

OdCLI SHALL authenticate exactly one administrator through Starlette Admin `AuthProvider` and signed `SessionMiddleware`. The administrator password hash and stable session-signing key SHALL be read from one owner-only regular configuration file below the user config root. The service SHALL reject missing, blank, malformed, symlinked, non-regular, group/world-accessible, or unsupported password-hash configuration without creating a default credential or disclosing either value.

#### Scenario: Valid administrator signs in and out

- **WHEN** the configured password verifies against the stored supported hash
- **THEN** Starlette Admin SHALL establish an authenticated signed session and SHALL invalidate it on logout

#### Scenario: Invalid or unsafe configuration fails closed

- **WHEN** the administrator configuration is absent, malformed, unsafe, or contains a blank/unsupported value
- **THEN** Admin startup SHALL fail with bounded recovery guidance and SHALL NOT create or log a credential

### Requirement: Encrypted secret store and key lifecycle

OdCLI SHALL store all managed secrets in one SQLCipher database below `ODCLI_USER_ROOT` and SHALL keep its random key in a separate owner-only regular file below the user config root. `ODCLI_SECRET_KEY_FILE` SHALL optionally select a key-file path and SHALL NOT contain raw key material. New directories, database files, and key files SHALL be created atomically with owner-only permissions. The encrypted store SHALL never be opened through a plaintext SQLite driver.

Read-only lookup SHALL treat the absence of both store and key as an empty encrypted store. A database without its key, a key paired with a missing previously initialized database, a wrong key, corrupt schema, unsafe file, or unreadable store SHALL fail closed without recreation or legacy fallback.

#### Scenario: First Admin write initializes the store

- **WHEN** neither encrypted database nor key exists and an authenticated administrator creates the first valid secret
- **THEN** OdCLI SHALL atomically create a random key and encrypted schema with owner-only permissions before committing the record

#### Scenario: Lost or wrong key blocks use

- **WHEN** the encrypted database exists but the key is missing, wrong, unsafe, or unreadable
- **THEN** Admin and secret-consuming operations SHALL return a bounded store error and SHALL NOT recreate the store or inspect legacy secrets

#### Scenario: Empty store preserves compatibility

- **WHEN** neither database nor key exists during a secret lookup
- **THEN** the lookup SHALL return exact record absence so the caller may continue through its existing legacy chain

### Requirement: Remote Odoo secrets use normalized URL identity

The encrypted store SHALL contain at most one remote Odoo record per canonical normalized base URL. The record SHALL contain a nonblank master password and SHALL NOT contain a database name, named-source identifier, or remote discovery result. Normalization SHALL reject credential-bearing, non-HTTP(S), fragmented, or otherwise invalid URLs using the existing URL boundary.

#### Scenario: Named sources share a server secret

- **WHEN** two named sources normalize to the same remote base URL while using different or absent databases
- **THEN** both SHALL resolve the same encrypted master-password record without changing either database selection

#### Scenario: Secret record cannot select a database

- **WHEN** an administrator creates or edits a remote Odoo secret
- **THEN** no database field SHALL be accepted, stored, returned, or used by remote database resolution

### Requirement: GitLab secrets use member and host identity

The encrypted store SHALL contain at most one GitLab credential record for each normalized opaque member ID and lowercase HTTPS host pair. Each record SHALL contain a nonblank login and token and SHALL receive one automatically generated stable internal token key. The token key SHALL NOT be supplied by the administrator or change when an unrelated field is edited.

#### Scenario: Same member on different hosts is independent

- **WHEN** one member ID has credentials for two normalized GitLab hosts
- **THEN** lookup and editing SHALL treat the records independently by the complete pair

#### Scenario: Token key is generated internally

- **WHEN** a valid GitLab record is created
- **THEN** OdCLI SHALL generate its internal token key and SHALL NOT expose or accept that key in the Admin form

### Requirement: Stored secret fields are write-only

Admin list, detail, form-load, validation, response, log, exception, repr, OpenAPI, and generated-client projections SHALL NOT reveal stored passwords, tokens, ciphertext, key material, secret length, or reversible placeholders. Creation SHALL require nonblank secret inputs. On edit, a blank secret input SHALL retain the stored value and a nonblank input SHALL replace it.

#### Scenario: Existing record is displayed safely

- **WHEN** an administrator opens a secret list, detail, or edit form
- **THEN** the response SHALL contain only safe identity/metadata fields and an empty write-only secret control

#### Scenario: Blank edit retains secret

- **WHEN** a valid edit changes metadata and submits a blank password or token field
- **THEN** the existing secret value SHALL remain unchanged without being read back to the browser

### Requirement: Secret edits reject stale revisions

Every secret record SHALL carry an integer revision. Update and delete SHALL compare the submitted revision within the same SQLCipher transaction and SHALL reject a stale revision as a conflict without overwriting or deleting the newer record. Transactions for different identities SHALL remain independently committable and bounded busy/lock failures SHALL not trigger legacy fallback.

#### Scenario: Concurrent sessions edit one record

- **WHEN** two authenticated sessions load one revision and the first update commits before the second
- **THEN** the second update SHALL return a reload-and-retry conflict and the first value SHALL remain authoritative

#### Scenario: Concurrent sessions edit different records

- **WHEN** two sessions update distinct secret identities
- **THEN** both valid transactions SHALL commit without treating either as a stale update

### Requirement: Encrypted records precede legacy credential lookup

Remote Odoo credential resolution SHALL query by normalized source URL and GitLab credential resolution SHALL query by normalized member ID and host before invoking their existing environment/`.env` chains. Only exact record absence SHALL delegate to the unchanged legacy order. A present but blank/invalid record, wrong key, unavailable dependency, corrupt/unreadable store, duplicate identity, or lookup error SHALL fail the operation without fallback. Returned credentials and errors SHALL remain redacted.

#### Scenario: Encrypted record wins

- **WHEN** a matching valid encrypted record and legacy values both exist
- **THEN** the operation SHALL use only the encrypted record

#### Scenario: Missing record uses unchanged legacy order

- **WHEN** the encrypted store opens successfully but has no matching identity, or no store/key exists
- **THEN** the caller SHALL resolve credentials through the exact pre-change environment and `.env` precedence

#### Scenario: Broken matching record never falls through

- **WHEN** a matching record exists but cannot produce every required nonblank credential value
- **THEN** the operation SHALL fail with a bounded typed error and SHALL NOT read a legacy value

### Requirement: Safe Admin projections carry opaque route identity

Each safe remote Odoo and GitLab projection SHALL expose a deterministic `admin_id` derived only from normalized public identity and current positive revision. It SHALL round-trip through one exact bounded decoder and SHALL contain or derive from no password, token, ciphertext, key material, internal token key, or secret length. It SHALL remain an Admin transport identity, not a persisted column or credential lookup key.

#### Scenario: Projection identity round-trips

- **WHEN** a safe secret projection is listed and its `admin_id` is decoded by the matching Admin view
- **THEN** the original normalized identity and current revision are recovered exactly without reading secret material

#### Scenario: Admin identity is not persisted

- **WHEN** the encrypted store schema and committed record are inspected
- **THEN** no new `admin_id` column or duplicate identity authority exists

#### Scenario: Safe representation remains secret-free

- **WHEN** list, detail, form-load, validation, response, log, exception, repr, OpenAPI, or generated-client output contains a safe secret projection
- **THEN** `admin_id` reveals no stored secret or secret-derived metadata and the existing write-only-field contract remains intact

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
