# gitlab-member-credentials Specification

## Purpose
Define owner-protected GitLab credential selection, authenticated member identity verification, and secret-safe delivery for repository workflows.
## Requirements
### Requirement: Protected pre-clone credential files

OdCLI SHALL read non-secret GitLab mappings from `get_user_root()/credentials/gitlab-credentials.toml` and token values from `get_user_root()/credentials/.env`, both available without a project or repository. Each mapping SHALL contain exactly one Multica member UUID, normalized HTTPS GitLab host, login, and token environment-key name. The directory and files SHALL be owner-only; malformed permissions, duplicate member/host mappings, missing values, or ambiguous entries SHALL fail closed. The selected token key MAY be overridden by the process environment, but OdCLI SHALL NOT inspect or use ambient Git credential helpers, credential stores, URL userinfo, or another member's mapping.

#### Scenario: Resolve one selected member before clone
- **WHEN** `--member-id` names a UUID with one valid mapping for the normalized host and the configured token key has a non-empty effective value
- **THEN** OdCLI resolves one private credential snapshot without requiring a destination, project manifest, issue creator, runtime owner, or current Git identity

#### Scenario: Credential files are not protected
- **WHEN** either credential file or its directory is writable or readable beyond the current owner
- **THEN** OdCLI fails before a Git child or GitLab request starts and reports only the affected path and permission requirement

#### Scenario: Ambient identity is available
- **WHEN** no exact selected-member mapping exists but system or repository Git credentials are configured
- **THEN** OdCLI rejects the operation without invoking those credentials

### Requirement: GitLab token owner verification

For every credentialed command, OdCLI SHALL call the selected host's authenticated GitLab `GET /api/v4/user` endpoint before the first Git invocation or mutating GitLab request, SHALL require the returned username to equal the configured login, and SHALL capture a stable GitLab user ID plus usable commit name/email identity. Authentication, host, owner, or identity mismatch SHALL fail before Git or mutation. Public plans/results/errors SHALL expose only non-secret member, host, login, and GitLab user identity fields.

#### Scenario: Token belongs to configured login
- **WHEN** GitLab authenticates the token and returns the configured login with usable commit identity
- **THEN** the same immutable verified identity is used for that command's Git author/committer or GitLab request context

#### Scenario: Token belongs to another user
- **WHEN** the authenticated GitLab username differs from the configured login
- **THEN** OdCLI exits non-zero before clone, fetch, commit, push, upload, or merge-request mutation

#### Scenario: Token identity lacks commit fields
- **WHEN** a commit is requested and the verified GitLab identity cannot supply a valid name and email or API-confirmed noreply address
- **THEN** OdCLI rejects the commit before launching Git and does not fall back to machine Git configuration

### Requirement: Secret-safe per-command delivery

OdCLI SHALL bind each private credential snapshot to one exact normalized HTTPS host and deliver it only through the existing private askpass/HTTP authorization boundary. Tokens SHALL NOT appear in URL userinfo, argv, Git configuration, public environment projections, stdin projections, output, logs, errors, fingerprints, or persisted state. Recursive submodule operations SHALL resolve all required host mappings for the same member before the first Git child and SHALL isolate concurrent members.

#### Scenario: Two members use one host concurrently
- **WHEN** two commands select different member UUIDs for the same GitLab host
- **THEN** each child/request receives only its selected token snapshot and neither changes global or repository credential configuration

#### Scenario: Child echoes secret material
- **WHEN** Git, askpass, or GitLab returns text containing a selected token or authorization header
- **THEN** every public observation and diagnostic replaces the secret before publication
