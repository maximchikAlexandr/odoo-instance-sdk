## ADDED Requirements

### Requirement: Canonical user publication settings
The client SHALL load publication settings from one owner-only file below the canonical user config root using the repository's existing configuration conventions. The settings SHALL cover normalized domain suffix, Caddy executable/control/owned-route-file locations, Basic Auth username and password hash, panel label, and exact trusted proxy addresses. Loading SHALL not create defaults silently; malformed permissions, duplicate keys, unsafe paths, or invalid host/network values SHALL fail with sanitized diagnostics.

#### Scenario: Valid publication settings load
- **WHEN** the owner-only settings file contains valid normalized values
- **THEN** SDK publication and external monitor operations receive the same immutable settings snapshot

#### Scenario: Secret-bearing setting is rendered
- **WHEN** settings are represented in a result, error, repr, plan, or log
- **THEN** the password hash and private control details are redacted while safe domain/host facts remain visible

### Requirement: Publication settings remain user-global and project bindings remain project-scoped
Static domain and Caddy control settings SHALL live only in canonical user configuration. Published owner routes SHALL live only in the OdCLI-owned route file, while runtime/database bindings retain their existing stable project/environment identities. Publication data SHALL NOT be duplicated in catalog tables, repository manifests, or caller-owned Multica worktrees.

#### Scenario: Two projects share the publication backend
- **WHEN** two initialized projects use the same user settings
- **THEN** they share the Caddy settings and owned route file but retain distinct owner routes and project bindings
