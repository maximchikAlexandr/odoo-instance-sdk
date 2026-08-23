## ADDED Requirements

### Requirement: `odcli postgres` command group

`odcli` MUST предоставлять command group `postgres` с подкомандами:

```text
odcli postgres status [--json]
odcli postgres up [--wait-timeout SECONDS]
odcli postgres stop [--timeout SECONDS]
odcli postgres approve-image --image-digest REPOSITORY@sha256:DIGEST [--timeout SECONDS] [--json]
```

Все три MUST использовать existing project resolution rules (`resolve_project_path`) — без project argument внутри initialized project или registered worktree.

`status` MUST быть read-only (не меняет cluster state). `status` MUST NOT вызывать Docker в external mode (только TCP probe). `--json` output: JSON envelope v1 с `state`, `mode`, `owned`, `endpoint` (redacted).

`up` MUST быть idempotent. Для managed (compose) cluster — вызывает `PostgresCluster.ensure_running(timeout)` (Compose `up --detach --wait`). Для external cluster — только reachability check (вызывает `status()`), не вызывает Docker. `--wait-timeout SECONDS` переходит в `ensure_running(timeout=...)`.

`stop` MUST быть allowed только для SDK-owned (compose) cluster. Для external — typed error, exit 1. `--timeout SECONDS` переходит в `stop(timeout=...)`. `stop` MUST preserves container data/volume (никогда `down -v`).

JSON envelope v1 MUST остаться (`emit_json_envelope`/`fail`). Entry point `odoo_instance_sdk.cli:cli` MUST сохраниться.

`postgres` group MUST NOT дублировать preflight, который уже делает `OdooInstance` перед spawn Odoo. Команды `run`/`shell`/`eval`/`exec`/`module`/`translations` не вызывают `postgres up` явно — preflight в `OdooInstance` обрабатывает readiness.

`approve-image` MUST resolve the manifest reference through Docker within its bounded `--timeout`, require `--image-digest` to exactly equal the OCI RepoDigest, and persist the approval outside the repository. Human and JSON responses MUST show the exact reference and digest. `up` and Odoo preflight MUST fail closed until approval exists and MUST re-resolve the image at every start.

#### Scenario: Status inside initialized project

- **WHEN** `odcli postgres status` runs inside a project with `[postgres] mode="compose"`
- **THEN** output reports `state`, `mode`, `owned`, `endpoint` without starting/stopping cluster

#### Scenario: Status JSON envelope

- **WHEN** `odcli postgres status --json` runs
- **THEN** JSON envelope v1 with `result` containing `state`, `mode`, `owned`, `endpoint` (no password)

#### Scenario: Status external does not invoke Docker

- **WHEN** `odcli postgres status` on external mode
- **THEN** only TCP probe is performed, no `docker compose` invocation

#### Scenario: Up compose starts cluster

- **WHEN** `odcli postgres up --wait-timeout 60` on compose mode with `STOPPED` cluster
- **THEN** runs `docker compose up --detach --wait`, polls until healthy, exits 0

#### Scenario: Up external checks reachability only

- **WHEN** `odcli postgres up` on external mode with reachable endpoint
- **THEN** no Docker invocation, exits 0

#### Scenario: Up external unreachable fails

- **WHEN** `odcli postgres up` on external mode with unreachable endpoint
- **THEN** exits 1 with typed `PostgresClusterUnreachableError` message

#### Scenario: Stop compose preserves volume

- **WHEN** `odcli postgres stop --timeout 30` on a running compose cluster
- **THEN** runs `docker compose stop`, named volume persists, exits 0

#### Scenario: Stop external fails

- **WHEN** `odcli postgres stop` on external mode
- **THEN** exits 1 with `PostgresClusterNotOwnedError` message

#### Scenario: Commands resolve project without --project

- **WHEN** `odcli postgres status` runs inside an initialized project
- **THEN** project is resolved via existing two-rule context, no `--project` required

### Requirement: `init` wires `--postgres*` options

`odcli init` MUST принимать `--postgres`, `--postgres-image`, `--postgres-port`, `--postgres-user` (см. `project-init` spec). `init` MUST NOT создавать compose artifacts directory. `init` MUST NOT запускать Docker. Existing init flow (interactive prompts, `--no-input`, `--dry-run --json`, idempotency, VS Code import) MUST оставаться без breaking changes — новые опции интегрируются в existing provenance tracking и `ProjectConfig` construction.

#### Scenario: Init with postgres and vscode import

- **WHEN** `odcli init --from-vscode launch.json --postgres compose --postgres-image ...` runs
- **THEN** both VS Code import and postgres section are persisted; provenance records both sources

#### Scenario: Init provenance records postgres option

- **WHEN** `odcli init --postgres compose --postgres-image ... --dry-run --json` runs
- **THEN** provenance includes `option` entry for `postgres`
