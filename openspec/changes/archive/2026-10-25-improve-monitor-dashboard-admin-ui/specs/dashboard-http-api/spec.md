## ADDED Requirements

### Requirement: Built-in Admin navigation names every registered entity

Authenticated Admin SHALL register exactly the existing five model views directly with Starlette Admin: Backups, Incomplete operations, Environment history, Remote Odoo secrets, and GitLab member secrets. Each SHALL define a nonblank built-in `menu_label` and icon. Navigation SHALL use the built-in sidebar and default index; it SHALL NOT add a custom dashboard, template, navigation component, ORM layer, or sixth entity.

#### Scenario: Authenticated sidebar lists all entities

- **WHEN** an authenticated administrator opens `/admin/`
- **THEN** the built-in sidebar and default index contain distinct links/panels for all five entities with human-readable labels

#### Scenario: Existing authorization still guards navigation

- **WHEN** an unauthenticated client requests the Admin root or any of the five view routes
- **THEN** the existing login redirect or denial occurs before catalogue or secret data is returned

### Requirement: Secret Admin routes use complete safe record identity

Remote Odoo and GitLab secret views SHALL use a hidden safe `admin_id` as Starlette Admin `pk_attr`. It SHALL encode the complete normalized public identity and current positive revision for exact list/detail/edit/delete routing: member ID plus host for GitLab, and base URL for remote Odoo. It SHALL contain no secret or secret-derived metadata and SHALL be excluded from list, detail, create, edit, import, and export fields.

#### Scenario: Created GitLab secret opens from its list row

- **WHEN** an administrator creates a GitLab secret and follows its built-in list-row detail action
- **THEN** the view resolves the exact `(member_id, host)` record and renders only safe identity/metadata fields

#### Scenario: Same member on two hosts routes independently

- **WHEN** two GitLab records share a member ID on different normalized hosts
- **THEN** each list/detail/edit/delete URL resolves only its own host-qualified record

#### Scenario: Revision changes after edit

- **WHEN** a valid secret edit increments the record revision
- **THEN** subsequent built-in navigation uses the new `admin_id`, while an old identity is rejected as stale or not found without overwriting newer data

#### Scenario: Invalid identity fails closed

- **WHEN** a secret detail, edit, or delete route receives malformed, incomplete, or non-positive-revision identity
- **THEN** the request returns bounded not-found/conflict behavior and performs no secret-store mutation
