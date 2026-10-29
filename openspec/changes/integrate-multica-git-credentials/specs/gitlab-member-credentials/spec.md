## MODIFIED Requirements

### Requirement: Protected pre-clone credential files

OdCLI SHALL read non-secret GitLab mappings by default from `get_user_root()/credentials/gitlab-credentials.toml` and legacy token values from the unified owner-only `get_user_root()/.env`, both available without a project or repository and honoring `ODCLI_USER_ROOT`. A supplied `mapping_path` SHALL replace the default legacy mapping path, while a supplied `environment_path` SHALL replace all default legacy token files. Each mapping SHALL contain exactly one person's Multica account `user_id` from `multica workspace member list --output json`, normalized HTTPS GitLab host, login, token environment-key name, and the provider user ID required for cache-owner verification. The membership-row `id`, agent IDs, and squad IDs SHALL NOT be accepted as substitutes for that `user_id`. Every consulted directory/file SHALL be owned by and accessible only to the current user; malformed permissions, duplicate member/host mappings, missing values, or ambiguous entries SHALL fail closed. OdCLI SHALL NOT inspect or use ambient Git credential helpers, credential stores, URL userinfo, or another member's mapping.

Credential resolution SHALL select an exact encrypted member/host record first. Only exact encrypted-record absence SHALL continue to the legacy mapping selected above, then choose the token from the first applicable source in this order: supplied process environment, supplied `environment_path`, unified user dotenv, compatibility credential dotenv. A project `.odcli` mapping or dotenv SHALL have no implicit priority and SHALL be consulted only when its path is supplied explicitly. The former `get_user_root()/credentials/.env` SHALL be a compatibility-only source when the encrypted store and higher-precedence values do not resolve the selected token key. An old-only value SHALL be accepted with a non-secret deprecation diagnostic. When both default user files contain the selected key, equal nonblank values SHALL use the unified file with that diagnostic, while differing nonblank values SHALL fail without revealing either value. Explicit path overrides SHALL not combine with the corresponding default compatibility files.

#### Scenario: Resolve one selected member before clone

- **WHEN** `--member-id` names a UUID with one valid mapping for the normalized host and its token key has a non-empty value at the highest applicable source
- **THEN** OdCLI resolves one private credential snapshot without requiring a destination manifest, issue creator, runtime owner, current Git identity, or project-local credential copy

#### Scenario: User-root-only credential resolves

- **WHEN** the encrypted store is exactly absent, mapping and token exist only below the effective `ODCLI_USER_ROOT`, and the selected project has no credential files
- **THEN** OdCLI resolves the selected member and host through the user-root files

#### Scenario: Explicit paths replace legacy defaults

- **WHEN** explicit mapping and environment paths are supplied and no encrypted record exists
- **THEN** OdCLI reads only those legacy files plus the supplied process environment and does not merge project or default user credential files

#### Scenario: Encrypted record overrides legacy values

- **WHEN** a valid exact encrypted record and conflicting explicit or default legacy values exist
- **THEN** OdCLI uses only the encrypted record

#### Scenario: Legacy token path transitions safely

- **WHEN** only the former credential dotenv defines the selected token key
- **THEN** OdCLI uses it for compatibility, emits path-only migration guidance to `<ODCLI_USER_ROOT>/.env`, and keeps the token secret

#### Scenario: Legacy and unified token values conflict

- **WHEN** fallback reaches the default user tier and the unified and former credential dotenv files contain different nonblank values for the selected key
- **THEN** OdCLI fails before a Git child or GitLab request and does not choose either value

#### Scenario: Credential files are not protected

- **WHEN** any consulted mapping, dotenv file, or containing directory is writable or readable beyond the current owner
- **THEN** OdCLI fails before a Git child or GitLab request starts and reports only the affected path and permission requirement

#### Scenario: Ambient identity is available

- **WHEN** no exact selected-member mapping exists but system or repository Git credentials are configured
- **THEN** OdCLI rejects the operation without invoking those credentials

### Requirement: GitLab token owner verification

For ordinary credentialed commands, OdCLI SHALL call the selected host's authenticated GitLab `GET /api/v4/user` endpoint before the first Git invocation or mutating GitLab request, require the returned username to equal the configured login, and capture a stable GitLab user ID plus usable commit name/email identity. In native cache-read mode, OdCLI SHALL instead call `GET /api/v4/personal_access_tokens/self` and require an active token whose provider user ID equals the mapping and whose complete standard scope set is exactly `read_repository`; cache mode SHALL NOT require commit identity. Authentication, host, owner, identity, state, or scope mismatch SHALL fail before Git or mutation. Public plans/results/errors SHALL expose only non-secret member, host, login, provider user identity, selection source, and cache-read validation state.

#### Scenario: Token belongs to configured login

- **WHEN** GitLab authenticates an ordinary task token and returns the configured login with usable commit identity
- **THEN** the same immutable verified identity is used for that command's Git author/committer or GitLab request context

#### Scenario: Cache token is read-only

- **WHEN** token self-information proves the configured provider owner, active state, and exact `read_repository` scope
- **THEN** the helper may return that credential for native cache clone/fetch without collecting commit identity

#### Scenario: Token belongs to another user

- **WHEN** authenticated owner evidence differs from the configured login or provider user ID
- **THEN** OdCLI exits non-zero before clone, fetch, commit, push, upload, or merge-request mutation

#### Scenario: Token identity lacks commit fields

- **WHEN** a commit is requested and the verified GitLab identity cannot supply a valid name and email or API-confirmed noreply address
- **THEN** OdCLI rejects the commit before launching Git and does not fall back to machine Git configuration

## ADDED Requirements

### Requirement: Stable standard Git credential helper entrypoint

The core package SHALL install `git-credential-odcli` as a thin executable over the existing standard Git credential-helper implementation. It SHALL accept only a bounded `get` request, require an exact HTTPS host and explicit member/purpose inputs, return only protocol username/password fields on success, write sanitized non-secret diagnostics only to stderr, and terminate. It SHALL NOT implement a second resolver, persist credentials, approve/reject/store credentials, or run as a service.

#### Scenario: Git invokes the packaged helper

- **WHEN** Git requests credentials for the configured exact host through `credential.helper=odcli`
- **THEN** the executable delegates to the shared resolver and writes one protocol response before exiting

#### Scenario: Unsupported helper operation

- **WHEN** Git requests `store`, `erase`, an unknown action, a different host, or malformed input
- **THEN** the helper returns no credential, exits non-zero, and emits no secret
