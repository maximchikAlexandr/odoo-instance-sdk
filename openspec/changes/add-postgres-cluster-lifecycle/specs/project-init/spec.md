## MODIFIED Requirements

### Requirement: `ProjectConfig` public type

`ProjectConfig` MUST оставаться `msgspec.Struct` с `frozen=True` и предоставлять `ProjectConfig.load(project_path: str | Path) -> ProjectConfig`, читающую `.odcli/project.toml`.

Минимальные поля (существующие) plus новое optional `postgres: PostgresProjectConfig | None = None`.

`PostgresProjectConfig` MUST быть `msgspec.Struct` с `frozen=True, kw_only=True` со следующими полями:

- `mode: Literal["external", "compose"] = "external"`
- `image: str | None = None` — compose only
- `port: int | None = None` — compose only; `None` = allocate free loopback port at `init`
- `user: str | None = None` — compose only; default = source `db_user` or `"odoo"`

`ProjectConfig.to_manifest()` MUST писать `[postgres]` section только если поля не-default (т.е. для `mode="external"` без других полей — section опущена для backward compat).

Manifest MUST NOT содержать пароль или любые secret-like keys (через существующий `assert_no_secrets`).

#### Scenario: Manifest with postgres compose section

- **WHEN** `ProjectConfig` with `postgres=PostgresProjectConfig(mode="compose", image="pgvector/pgvector:pg16", port=5468, user="odoo")` is rendered
- **THEN** `to_manifest()` outputs `[postgres]` with `mode`, `image`, `port`, `user` but no `password`

#### Scenario: Legacy manifest without postgres section

- **WHEN** `ProjectConfig.load("/repo")` on a manifest without `[postgres]`
- **THEN** `config.postgres is None` (treated as external via source config downstream)

#### Scenario: Round-trip preserves postgres section

- **WHEN** `ProjectConfig` with `postgres=PostgresProjectConfig(mode="compose", ...)` is written and reloaded
- **THEN** reloaded `config.postgres` equals the original

#### Scenario: External mode omits postgres section by default

- **WHEN** `ProjectConfig` with `postgres=PostgresProjectConfig(mode="external")` (defaults) is rendered
- **THEN** `to_manifest()` omits `[postgres]` section (backward compat)

#### Scenario: Load existing manifest

- **WHEN** `ProjectConfig.load("/path/to/repo")` вызывается с существующим `.odcli/project.toml`
- **THEN** возвращается `ProjectConfig` с полями из manifest (включая optional `postgres`)

#### Scenario: Missing manifest

- **WHEN** `ProjectConfig.load("/path/to/repo")` вызывается без `.odcli/project.toml`
- **THEN** поднимается typed error с подсказкой `odcli init`

### Requirement: `odcli init` postgres options

`odcli init` MUST принимать опции:

```text
--postgres [external|compose]   default: external
--postgres-image IMAGE          compose only; required with --no-input
--postgres-port PORT            compose only; omitted = allocate free loopback port
--postgres-user USER            compose only; default: source db_user or "odoo"
```

Interactive init MUST prompts только для unresolved mode-specific values. `--no-input` MUST forbid prompts и MUST require `--postgres-image` для `compose` mode.

`--dry-run --json` MUST сообщать resolved non-secret plan including `postgres` section (mode, image, port, user, allocated_port flag) без записи файлов.

`init` MUST NOT запускать `docker compose up`. `init` MUST NOT создавать compose artifacts directory (создаётся lazily при первом `up`).

Для `compose` без `--postgres-port` — аллоцировать free loopback port через `probe_address` и persist в manifest. Для `external` без `--postgres-*` — `[postgres]` section опущена (backward compat).

Idempotency comparison MUST учитывать `[postgres]` section.

#### Scenario: Compose no-input requires image

- **WHEN** `odcli init --no-input --postgres compose` without `--postgres-image`
- **THEN** command fails with stable error listing missing `--postgres-image`

#### Scenario: Compose allocates free port

- **WHEN** `odcli init --postgres compose --postgres-image pgvector/pgvector:pg16` without `--postgres-port`
- **THEN** a free loopback port is allocated and persisted in manifest

#### Scenario: Compose with explicit port

- **WHEN** `odcli init --postgres compose --postgres-image pgvector/pgvector:pg16 --postgres-port 5468`
- **THEN** manifest contains `port = 5468`

#### Scenario: Compose user defaults to source db_user

- **WHEN** `odcli init --postgres compose --postgres-image ... --config odoo.conf` where `odoo.conf` has `db_user=alice`
- **THEN** manifest `user = "alice"`

#### Scenario: Compose user defaults to odoo without source config

- **WHEN** `odcli init --postgres compose --postgres-image ...` without `--postgres-user` and no source config
- **THEN** manifest `user = "odoo"`

#### Scenario: External default omits postgres section

- **WHEN** `odcli init --odoo-bin ... --config ...` without `--postgres` flags
- **THEN** manifest has no `[postgres]` section (default external)

#### Scenario: Dry-run reports postgres plan

- **WHEN** `odcli init --dry-run --json --postgres compose --postgres-image ...` is run
- **THEN** JSON envelope contains `postgres` with `mode`, `image`, `port`, `user`, `allocated_port` and no secrets; files not written

#### Scenario: Init does not start Docker

- **WHEN** `odcli init --postgres compose --postgres-image ...` completes successfully
- **THEN** no `docker compose up` is invoked, no artifacts directory is created

#### Scenario: Idempotent re-init with same postgres section

- **WHEN** `odcli init` runs with options identical to existing manifest (including `[postgres]`)
- **THEN** no-op, manifest unchanged