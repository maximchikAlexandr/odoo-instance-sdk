# Catalog Settings Specification

## Purpose

Define typed, non-secret catalog settings and authenticated revision-safe management, including host/project-scoped merge-request assignee defaults.

## Requirements

### Requirement: Canonical non-secret catalog settings
The existing catalog SHALL persist non-secret settings in one `settings` table keyed by a unique nonblank string. Every value SHALL be valid canonical JSON, stored as compact UTF-8 with deterministic object-key order, and round-trip as a JSON scalar, array, or object without coercion. Each row SHALL carry a positive revision plus immutable creation and automatically updated modification timestamps. Secret-like keys, non-finite numbers, duplicate object keys, malformed JSON, and values beyond the catalog's bounded input size SHALL be rejected before mutation.

#### Scenario: Scalar and structured settings round-trip
- **WHEN** an administrator stores a JSON string, number, boolean, null, array, or object under an allowed unique key
- **THEN** catalog lookup returns the same JSON type and value from its canonical serialization

#### Scenario: Invalid or secret-like setting fails
- **WHEN** a key is classified as secret-bearing or its value is malformed, ambiguous, non-finite, or oversized JSON
- **THEN** the catalog rejects the write without changing an existing row or exposing the submitted value in diagnostics

### Requirement: Authenticated Admin settings management
The existing Starlette Admin SHALL expose a small settings list/create/edit/delete view under the existing administrator authorization. Key and JSON value SHALL be the only business inputs. Revision and timestamps SHALL be read-only metadata, and edit/delete SHALL compare the revision-bound route identity in the same catalog transaction so stale operations fail without overwriting newer data. Validation errors SHALL be attached to the key or value field.

#### Scenario: Administrator changes a setting
- **WHEN** an authenticated administrator creates or edits a valid key and JSON value
- **THEN** exactly one row is committed, its revision/timestamp advance as applicable, and the returned Admin route identifies the current revision

#### Scenario: Concurrent setting edit is stale
- **WHEN** two sessions load one setting revision and the first session commits an edit
- **THEN** the second edit or delete fails with reload-and-retry guidance and preserves the first result

### Requirement: Host/project-scoped MR assignee setting
The default merge-request assignee SHALL use the exact key `gitlab.mr.default_assignee:https://<normalized-host>/<normalized-project-path>`. Its canonical JSON value SHALL be either one positive GitLab numeric user ID or one nonblank exact username without whitespace; booleans, null, non-positive numbers, display names, arrays, and objects SHALL be invalid. Deleting the row SHALL disable the default for only that exact host/project.

#### Scenario: Valid numeric or username assignee
- **WHEN** Admin stores a positive integer or exact username at the canonical key for one normalized GitLab host/project
- **THEN** the setting is available only to that exact publication scope and preserves its integer-or-string type

#### Scenario: Invalid assignee setting
- **WHEN** Admin receives another JSON type, an empty/whitespace username, a display name, or a non-positive number for an MR assignee key
- **THEN** it reports a value-field validation error and leaves the prior setting unchanged
