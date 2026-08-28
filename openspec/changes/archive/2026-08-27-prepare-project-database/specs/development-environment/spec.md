## ADDED Requirements

### Requirement: Checkout backup provenance comparison

Before checkout creates a catalog row, worktree, config, Python environment, backup, database, or process, `EnvironmentResource` SHALL compare the effective base ref against the source branch of the backup audit row mapped to the effective source database through `restores.backup_id`. The lookup SHALL NOT require the archive to be available, readable, or in a restorable state. The typed comparison SHALL be `matched` when normalized ref texts are equal, `mismatched` when both are known and unequal, and `unknown` only when the mapping/backup audit row is absent or its recorded branch is null.

A known mismatch SHALL abort before any mutation in shared and copy modes. Checkout SHALL not automatically reset the administrator.

#### Scenario: Known branch matches

- **WHEN** effective base and mapped backup source branch are both `release/19`
- **THEN** checkout planning reports `matched` and may continue

#### Scenario: Known mismatch aborts

- **WHEN** effective base is `release/19` and mapped backup branch is `develop`
- **THEN** checkout fails before catalog/worktree/database mutation with both non-secret refs reported

#### Scenario: Unavailable archive retains known mismatch

- **WHEN** the mapped backup records `release/18`, the effective base is `release/19`, and the archive was deleted
- **THEN** provenance is `mismatched` and checkout rejects it rather than applying the legacy-unknown exception; freshness separately reports unavailable

### Requirement: Legacy unknown provenance policy

Unknown provenance SHALL be accepted only when the checkout call supplies an explicit source database (`EnvironmentCheckoutOptions.source_database` / `--source-db`). Accepted unknown provenance SHALL emit a stable warning that branch compatibility cannot be verified and identify the explicit source database. A source database inferred from project default or config SHALL not satisfy this exception; checkout SHALL fail with guidance to pass `--source-db` or refresh a provenance-bearing backup.

#### Scenario: Explicit legacy source accepted

- **WHEN** a legacy mapped backup has no branch and checkout explicitly supplies its source database
- **THEN** checkout reports `unknown`, emits the compatibility warning, and may continue

#### Scenario: Inferred legacy source rejected

- **WHEN** the same legacy database is selected only from `default_source_database`
- **THEN** checkout fails before mutation with guidance to opt in explicitly or refresh

### Requirement: Checkout freshness preparation ordering

Checkout SHALL evaluate `refresh_after_hours` only after project/source/base resolution. When configured and the current default is stale, missing an available mapped backup, or missing its file, checkout SHALL invoke the shared project preparation workflow with restore enabled before producing the final checkout plan. The workflow SHALL complete any required download, restore, and default switch before checkout re-resolves the source database, provenance, target name, and remaining plan.

Checkout SHALL not create its environment row or owned artifacts until preparation succeeds. When freshness is not configured, checkout SHALL not refresh based on age. Provenance validation remains mandatory either way.

#### Scenario: Stale default refreshed before plan

- **WHEN** the mapped backup age reaches the configured threshold
- **THEN** preparation creates and selects a fresh local default before checkout calculates its final source/target and creates artifacts

#### Scenario: Preparation failure leaves checkout untouched

- **WHEN** required refresh restores a database but its post-restore step fails
- **THEN** checkout creates no environment/worktree artifacts, the prior default remains selected, and preparation retains its database/mapping for diagnosis

### Requirement: Checkout dry-run reports the same provenance decision

Public additive `EnvironmentResource.plan_checkout()` SHALL return a secret-free `EnvironmentCheckoutPlan` and resolve the same effective base, source database, audit provenance, legacy-unknown rule, and availability-aware freshness state as execution using read-only catalog access. It SHALL report whether preparation would download/restore/switch the default and then report the resulting checkout intent without performing network, catalog migration/write, manifest write, database, filesystem, Git, Python, lock-held mutation, or admin reset. Additive `checkout_with_plan()` SHALL return `EnvironmentCheckoutResult` containing the realized environment and final public plan recalculated after preparation. Canonical `checkout()` SHALL delegate to that method and continue returning only `DevelopmentEnvironment` as required by the main spec. Private `_CheckoutPlan` SHALL remain internal execution state and SHALL NOT be serialized or exposed by either public model.

The public plan SHALL expose only name, branch, effective base, database mode/source/target, create-or-reuse Python mode, typed provenance, typed freshness, prospective preparation actions, and warnings. It SHALL NOT expose parsed config values, passwords, paths, executable/argv data, or prospective identifiers.

Dry-run is an observation, not a reservation; real checkout SHALL recheck everything after acquiring the preparation lock.

#### Scenario: Dry-run known mismatch

- **WHEN** dry-run observes a known source/base mismatch
- **THEN** it returns the same rejection as execution and performs no mutation

#### Scenario: Dry-run stale plan

- **WHEN** dry-run observes a stale current default with a compatible configured test branch
- **THEN** it reports the refresh/restore/default-switch steps and subsequent checkout intent without executing them

#### Scenario: Canonical checkout remains compatible

- **WHEN** an SDK caller uses `EnvironmentResource.checkout()` after this change
- **THEN** it receives `DevelopmentEnvironment`, while `commands/env.py` may use additive `checkout_with_plan()` for the same execution plus a secret-free report

## MODIFIED Requirements

### Requirement: `EnvironmentCheckoutOptions` public type

`EnvironmentCheckoutOptions` MUST быть `msgspec.Struct` с `frozen=True`:

- `base_ref: str | None = None`
- `name: str | None = None`
- `config_path: Path | None = None`
- `db_mode: EnvironmentDatabaseMode = EnvironmentDatabaseMode.SHARED`
- `source_database: str | None = None`
- `target_database: str | None = None`
- `odoo_bin: Path | None = None`
- `python: str | Path | None = None`
- `create_venv: bool = False`
- `http_port: int | None = None`

`base_ref` is the explicit per-call override; when it is `None`, checkout SHALL use `ProjectConfig.default_base_ref`, then `HEAD`. `source_database is not None` SHALL record explicit caller intent for the legacy-unknown provenance exception even when it equals the configured project default.

`create_venv` default `false` и не может прийти из project manifest, VS Code profile или cwd inference: только explicit `--create-venv` текущего checkout.

#### Scenario: Default shared checkout

- **WHEN** `EnvironmentCheckoutOptions()` используется без изменений and the project has no default base
- **THEN** `db_mode=SHARED`, `create_venv=False`, and effective base is `HEAD`

#### Scenario: Explicit source records legacy opt-in

- **WHEN** options explicitly contain `source_database="legacy_db"`
- **THEN** checkout may apply the warned unknown-provenance exception for that exact database
