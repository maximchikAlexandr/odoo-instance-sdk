## ADDED Requirements

### Requirement: Canonical user publication settings
The client SHALL expose a frozen typed, non-plaintext publication settings model loaded from one owner-only file below the canonical user config root. The model SHALL cover normalized domain suffix, Caddy executable/control/owned-config locations, Basic Auth username and password hash, panel label, and exact trusted proxy addresses. Loading SHALL not create defaults silently; malformed permissions, duplicate keys, unsafe paths, or invalid host/network values SHALL fail with sanitized diagnostics.

#### Scenario: Valid publication settings load
- **WHEN** the owner-only settings file contains valid normalized values
- **THEN** SDK publication and external monitor operations receive the same immutable settings snapshot

#### Scenario: Secret-bearing setting is rendered
- **WHEN** settings are represented in a result, error, repr, plan, or log
- **THEN** the password hash and private control details are redacted while safe domain/host facts remain visible

### Requirement: Publication settings remain user-global and project bindings remain project-scoped
Static domain and Caddy control settings SHALL live only in canonical user configuration. Published owner routes and runtime/database bindings SHALL retain their existing stable project/environment identities in catalog state and SHALL NOT be copied into repository manifests or caller-owned Multica worktrees.

#### Scenario: Two projects share the publication backend
- **WHEN** two initialized projects use the same user settings
- **THEN** they share the Caddy control configuration but persist distinct owner routes and project bindings
