## ADDED Requirements

### Requirement: Restore reports independent verification outcomes

Every completed restore result SHALL report neutralization and filestore verification independently with `status` equal to `passed`, `failed`, or `not_checked` and a sanitized reason when not passed. Filestore verification SHALL compare distinct safe `ir_attachment.store_fname` identities with files under the captured managed filestore and SHALL report non-negative counts for expected, found, and missing unique files. Database existence, restore transport success, or administrator-password reset SHALL NOT imply either check passed, and no attachment content or unsafe path SHALL be exposed.

#### Scenario: Neutralization and filestore both pass
- **WHEN** neutralization completes with its postcondition and every distinct stored attachment file exists in the captured filestore
- **THEN** both outcomes are `passed`, expected equals found, missing is zero, and the restore may report verified success under existing publication rules

#### Scenario: Filestore is incomplete
- **WHEN** one or more distinct attachment store files are absent after import
- **THEN** filestore verification is `failed`, expected/found/missing counts identify the concrete mismatch, and successful password reset or database existence does not mask it

#### Scenario: Check is not performed
- **WHEN** a supported restore path does not run neutralization or cannot apply filestore verification by contract
- **THEN** that outcome is `not_checked` with a stable reason and is not represented as passed

#### Scenario: Duplicate attachment rows
- **WHEN** multiple `ir_attachment` rows reference the same safe stored file
- **THEN** expected, found, and missing counts operate on the unique file identity once

#### Scenario: Unsafe attachment identity
- **WHEN** an attachment store name is absolute, escapes the filestore root, or is otherwise unsafe
- **THEN** verification fails closed with a sanitized reason and never reads outside the captured filestore

#### Scenario: Verification query fails
- **WHEN** the bound database cannot return the attachment inventory after restore
- **THEN** the applicable check is `failed`, the result does not claim verified completion, and existing compensation/retention evidence remains authoritative
