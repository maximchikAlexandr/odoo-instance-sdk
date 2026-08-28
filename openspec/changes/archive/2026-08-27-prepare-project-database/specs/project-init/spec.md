## ADDED Requirements

### Requirement: Project database preparation configuration

`ProjectConfig` SHALL add:

- `default_base_ref: str | None = None` under `[project]`;
- `refresh_after_hours: float | None = None` under `[project]`, finite and strictly greater than zero;
- `test_instance: TestInstanceProjectConfig | None = None` from a top-level `[test_instance]` table.

`TestInstanceProjectConfig` SHALL be a frozen, keyword-only `msgspec.Struct` with required non-empty `base_url` and `database`, plus optional non-empty `git_branch`. URL normalization SHALL use the existing base-URL rules. The table SHALL never accept a master-password/password/secret field. Unknown keys SHALL fail closed.

#### Scenario: Preparation config round-trip

- **WHEN** a manifest contains `default_base_ref`, `refresh_after_hours`, and `[test_instance]` URL/database/branch
- **THEN** `ProjectConfig.load()` validates them and `to_manifest()` round-trips the same non-secret values

#### Scenario: Legacy project remains valid

- **WHEN** a legacy manifest omits all preparation settings
- **THEN** it loads with `None` defaults and existing project behavior remains available

#### Scenario: Secret-like test key rejected

- **WHEN** `[test_instance]` contains `master_password` or another unknown key
- **THEN** loading/writing fails and the value is not echoed

### Requirement: Effective checkout base precedence

The project manifest's `default_base_ref` SHALL be the checkout default only when the current checkout call does not supply an explicit base. Explicit `EnvironmentCheckoutOptions.base_ref` / CLI `--base` SHALL take precedence. If both are absent, the existing `HEAD` fallback SHALL remain.

#### Scenario: Explicit base wins

- **WHEN** project default is `develop` and checkout supplies `--base release/19`
- **THEN** effective base is `release/19`

#### Scenario: Manifest default applies

- **WHEN** checkout supplies no base and project default is `develop`
- **THEN** effective base is `develop`

### Requirement: Atomic default database updates preserve manifest intent

Database preparation SHALL update only `default_source_database` through the existing atomic, secret-checked manifest writer. It SHALL preserve preparation, PostgreSQL, runtime, and all unrelated project fields. The write SHALL occur under the project preparation lock after reloading the current file and detecting conflicting relevant edits.

#### Scenario: Refresh switches one field

- **WHEN** a restored preparation completes successfully
- **THEN** the manifest is atomically replaced with only the default database changed and all other settings preserved
