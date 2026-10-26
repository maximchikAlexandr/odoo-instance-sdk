## MODIFIED Requirements

### Requirement: Protected pre-clone credential files

OdCLI SHALL read non-secret GitLab mappings from `get_user_root()/credentials/gitlab-credentials.toml` and legacy token values from the unified owner-only `get_user_root()/.env`, both available without a project or repository. When a selected project exists, its `.odcli/.env` SHALL override the user file, and an explicit process value SHALL override both. Each mapping SHALL contain exactly one Multica member UUID, normalized HTTPS GitLab host, login, and token environment-key name. Every consulted directory/file SHALL be owned by and accessible only to the current user; malformed permissions, duplicate member/host mappings, missing values, or ambiguous entries SHALL fail closed. OdCLI SHALL NOT inspect or use ambient Git credential helpers, credential stores, URL userinfo, or another member's mapping.

The former `get_user_root()/credentials/.env` SHALL be a compatibility-only source when the encrypted store and higher-precedence process/project values do not resolve the selected token key. An old-only value SHALL be accepted with a non-secret deprecation diagnostic. When both user files contain the selected key, equal nonblank values SHALL use the unified file with that diagnostic, while differing nonblank values SHALL fail without revealing either value. An explicit `environment_path` override SHALL remain authoritative and SHALL not combine with default compatibility files.

#### Scenario: Resolve one selected member before clone
- **WHEN** `--member-id` names a UUID with one valid mapping for the normalized host and its token key has a non-empty value at the highest applicable process, project, or unified-user tier
- **THEN** OdCLI resolves one private credential snapshot without requiring a destination manifest, issue creator, runtime owner, or current Git identity

#### Scenario: Project token overrides user token
- **WHEN** the selected project and unified user dotenv both define the mapped token key and the process does not
- **THEN** the project value is used and neither value is exposed or propagated outside the credential transport

#### Scenario: Legacy token path transitions safely
- **WHEN** only the former credential dotenv defines the selected token key
- **THEN** OdCLI uses it for compatibility, emits path-only migration guidance to `<ODCLI_USER_ROOT>/.env`, and keeps the token secret

#### Scenario: Legacy and unified token values conflict
- **WHEN** fallback reaches the user tier and the unified and former credential dotenv files contain different nonblank values for the selected key
- **THEN** OdCLI fails before a Git child or GitLab request and does not choose either value

#### Scenario: Credential files are not protected
- **WHEN** any consulted mapping, dotenv file, or containing directory is writable or readable beyond the current owner
- **THEN** OdCLI fails before a Git child or GitLab request starts and reports only the affected path and permission requirement

#### Scenario: Ambient identity is available
- **WHEN** no exact selected-member mapping exists but system or repository Git credentials are configured
- **THEN** OdCLI rejects the operation without invoking those credentials

