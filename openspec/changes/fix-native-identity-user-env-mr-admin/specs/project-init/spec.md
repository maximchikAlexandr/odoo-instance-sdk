## ADDED Requirements

### Requirement: Unified owner-level OdCLI environment
OdCLI SHALL treat `<ODCLI_USER_ROOT>/.env` (default `~/.odcli/.env`) as the single recommended user dotenv. It SHALL use the same deterministic non-shell grammar, immutable mapping, owner-only regular-file checks, symlink rejection, sanitized path/line diagnostics, and secret classifier as the project dotenv. For a named key, precedence SHALL be explicit process environment, selected project `.odcli/.env`, then user `.env`; a missing selected project or file SHALL skip only that tier. User values SHALL NOT mutate `os.environ` or be broadly inherited by child processes.

#### Scenario: Three-tier precedence
- **WHEN** one key exists in process, project, and user sources
- **THEN** the process value wins; without it the project value wins; without either the user value wins

#### Scenario: User root override
- **WHEN** `ODCLI_USER_ROOT` selects an absolute owner-controlled directory
- **THEN** the user dotenv is resolved exactly as `<selected-root>/.env` and no home or ancestor dotenv is searched

#### Scenario: Unsafe user dotenv
- **WHEN** the user dotenv is symlinked, non-regular, not owned by the current user, group/world accessible, malformed, or invalid UTF-8
- **THEN** resolution fails before mutation or child creation with sanitized path/line guidance and no key or value disclosure

#### Scenario: Purpose-limited user secret
- **WHEN** the user dotenv contains `ODCLI_ADMIN_PASSWORD`, a mapped legacy GitLab token, and unrelated values
- **THEN** only the named resolver receives the selected secret and Git, PostgreSQL, Docker, package, editor, browser, generic, and unrelated Odoo children receive none of those user values
