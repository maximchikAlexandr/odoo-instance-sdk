## MODIFIED Requirements

### Requirement: GitLab credential identity is verified at use time

OdCLI SHALL continue to resolve the exact member and normalized HTTPS host through the existing encrypted-first resolver and SHALL verify the configured login at use time. For cache-read purpose only, it SHALL reuse that owner verification's runtime provider ID and compare it with one bounded personal-access-token self-information response. It SHALL NOT persist provider identity or change credential records, the secret-store schema, or the Admin model.

#### Scenario: Cache identity and token owner agree

- **WHEN** the configured login authenticates to one runtime provider ID and the active token self-information reports that same ID with exactly the standard `read_repository` scope
- **THEN** the helper returns that exact-host credential for cache clone/fetch

#### Scenario: Cache identity cannot be proved

- **WHEN** login ownership, token ownership, active state, or the complete scope set is missing, incompatible, or mismatched
- **THEN** the helper returns no credential and does not try another record or member

### Requirement: Existing resolver precedence remains authoritative

The bridge SHALL call the existing resolver without synthesizing project-local mapping or dotenv defaults. Encrypted-first lookup, the effective user root including `ODCLI_USER_ROOT`, supplied process environment, explicit legacy paths, and existing compatibility/conflict behavior SHALL remain authoritative. Project-local credentials SHALL be read only when a caller passes their paths explicitly.

#### Scenario: Only user-root credentials exist

- **WHEN** the selected member and host have valid credentials under the effective user root and no project `.odcli` credential copies exist
- **THEN** the bridge resolves the credential through the existing resolver

#### Scenario: Explicit legacy path is supplied

- **WHEN** encrypted lookup is absent and the caller supplies an explicit mapping or environment path
- **THEN** the existing explicit-path precedence applies without merging an implicit project file

## ADDED Requirements

### Requirement: Existing standard helper has a stable installed entrypoint

The package SHALL expose `git-credential-odcli` as a thin installed entrypoint over the existing finite helper implementation if no equivalent installed entrypoint exists. It SHALL accept only bounded standard `get` input, require exact host/member/purpose in cache mode, emit only protocol fields on success, and emit only sanitized diagnostics on failure. It SHALL NOT implement another resolver, store/erase behavior, persistence, or a service.

#### Scenario: Installed Git invokes the helper

- **WHEN** Git invokes `credential.helper=odcli` for the configured exact HTTPS host
- **THEN** the entrypoint delegates once to the existing helper/resolver and exits after one response

#### Scenario: Helper request is unsupported

- **WHEN** the action, input, purpose, member, or host is absent, malformed, or different from configuration
- **THEN** the helper exits non-zero without returning or logging a secret
