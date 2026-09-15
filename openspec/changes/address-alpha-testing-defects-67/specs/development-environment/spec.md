## MODIFIED Requirements

### Requirement: `env list`

`env list` MUST provide the following invocations and output rules:

```bash
odcli env list
odcli env list --all
odcli env list --format rich|json|toon
```

The command SHALL project one frozen `CheckoutInventory` model for Rich, JSON, and TOON. The main checkout of each selected project SHALL appear as the first typed row of its group with `kind = main | environment`, a stable `project_id`, and a nullable `environment_id`. The main checkout SHALL NOT be modelled as a synthetic environment.

The base row SHALL contain only working identity and state: kind/name and project; branch, short SHA, and canonical worktree path; commits ahead of the base branch plus added and deleted lines; a compact Odoo status `running | stopped | unavailable` without PID or metrics; and the bound database/DB mode when applicable.

Rich SHALL NOT show `OBSERVED`, `ODOO_PID`, `CPU`, `RAM`, `SIZE`, or detailed process/artifact columns; those values live in `odcli ps`. Rich SHALL remain a readable `Table` with headers and checkout rows on both normal and compact terminal widths. Quick reconciliation columns (`OBSERVED`, port state, owned backup) SHALL NOT appear in `env list`; they are available in `odcli ps` and `odcli doctor`.

По умолчанию скрываются только `removed`; `failed` и `cleanup_failed` видны.

`--all-projects` (CLI-level flag, см. `cli-odcli` spec) читает durable global registry из любой directory и не требует project context.

`--all` — include `removed` environments (по умолчанию скрыты).

`CheckoutInventory` SHALL be built from one canonical `EnvironmentMonitor.snapshot()` per sample plus Git facts of the main checkout. A separate monitor or collector SHALL NOT be added. The raw `EnvironmentMonitor.snapshot()` SHALL remain the canonical source for `odcli ps`, the Python SDK, FastAPI, and the dashboard.

#### Scenario: Default hides removed

- **WHEN** `env list` без `--all`
- **THEN** `removed` environments скрыты, `failed`/`cleanup_failed` видны

#### Scenario: Main checkout is the first row

- **WHEN** `env list` runs for a project with one environment
- **THEN** the first row has `kind=main` and no synthetic environment is created

#### Scenario: Rich drops process columns

- **WHEN** `env list` renders a Rich table
- **THEN** the columns `OBSERVED`, `ODOO_PID`, `CPU`, `RAM`, and `SIZE` are absent

#### Scenario: Stopped checkout stays visible

- **WHEN** `env list` runs and the main checkout's Odoo is stopped
- **THEN** the main checkout row remains visible with `stopped` status and no PID

#### Scenario: Reconciliation detects missing worktree

- **WHEN** `env list` runs for an environment where the worktree is missing
- **THEN** the `CheckoutInventory` row reflects the missing worktree in its compact status without a separate `OBSERVED` column

#### Scenario: OBSERVED reflects live socket.bind

- **WHEN** `env list` runs for an environment with an allocated port and `socket.bind((http_interface, http_port))` succeeds
- **THEN** the compact Odoo status reflects the live port state without a dedicated `OBSERVED` column

#### Scenario: Reconciliation detects missing generated config

- **WHEN** `env list` runs for an environment where the generated `odoo.conf` is missing
- **THEN** the `CheckoutInventory` row reflects the missing config in its compact status

#### Scenario: Reconciliation detects missing owned backup

- **WHEN** `env list` runs for a copy environment where the owned backup file is missing
- **THEN** the `CheckoutInventory` row reflects the missing backup in its compact status

#### Scenario: Reconciliation detects missing Python or lock

- **WHEN** `env list` runs for an environment where the recorded Python path does not exist or `requirements.lock` is missing
- **THEN** the `CheckoutInventory` row reflects the missing Python/lock in its compact status

### Requirement: `env remove`

```bash
odcli env remove <environment-id> --dry-run
odcli env remove <environment-id> --yes
odcli env remove <env-id-1> <env-id-2> --dry-run
odcli env remove <env-id-1> <env-id-2> --yes
```

Перед изменениями показать план и выполнить полный preflight. Без `--yes` требуется Click confirmation.

`env remove` SHALL accept variadic positional arguments (full UUIDs or selectors). For each explicit target, the persisted repository, Git common dir, worktree, and PostgreSQL cluster SHALL be resolved independently; the project/cluster of the current cwd SHALL NOT be applied to the whole set. A call without a positional argument SHALL preserve the existing cwd semantics for exactly one environment.

Before the first destructive action, the command SHALL resolve all targets and perform planning preflight. An unknown, ambiguous, or duplicate target SHALL abort with no changes. After successful preflight, Rich SHALL show one confirmation listing all sanitized targets; machine modes without `--yes` SHALL change nothing and SHALL emit `confirmation_required`. Execution SHALL run single-target commands sequentially in argument order with per-target execution-time revalidation. A per-target failure SHALL continue remaining prepared targets and return per-target success/failure with a non-zero exit code.

Default cleanup matrix (per target):

| Artifact | `shared` | `copy` |
|---|---:|---:|
| Generated config | delete | delete |
| Requirements lock | delete | delete |
| Python venv | delete iff owned | delete iff owned |
| Owned Git worktree | remove | remove |
| Source DB | never | never |
| Target DB | n/a | drop |
| Environment backup | n/a | delete |
| Git branch | keep | keep |
| Audit rows | keep | keep |

Safety rules:

- сначала проверить, что worktree чистый; dirty worktree блокирует удаление;
- любой занятый reserved address блокирует удаление как ownership-unknown; занятость определяется через `socket.bind((http_interface, http_port))`; HTTP health check служит только диагностикой, не доказательством ownership; responsive Odoo на address не доказывает, что он принадлежит этому environment;
- drop target DB (copy mode only) MUST быть только для `copy` environment с совпавшими cluster identity, target DB и recorded restore/backup ownership; после drop MUST проверять postcondition `exists(target_db) is False`; если postcondition fails — `cleanup_failed` с причиной;
- использовать `git worktree remove`, не recursive filesystem deletion;
- generated lock удалять по recorded environment path; Python venv — только при `python_environment_owned=true` и containment внутри environment root;
- reused project venv (`owned=false`) никогда не изменять во время remove;
- не использовать Git force и не удалять branch;
- shared source DB не удаляется ни при каких flags;
- `BackupResource.delete()` используется только для recorded environment-owned backup;
- отсутствие уже удалённого owned artifact считается идемпотентным успехом и записывается в audit;
- частичная ошибка оставляет `cleanup_failed` с точной причиной; повторный `remove` продолжает с оставшихся owned artifacts;
- `removed` ставится только после подтверждения отсутствия всех owned artifacts;
- final empty environment directory удаляется, SQLite rows остаются.

Bulk prune, автоматическое удаление по возрасту и `--force` для грязных worktrees не входят в scope. A generic bulk SDK, parallel deletion, or new orchestration hierarchy SHALL NOT be added.

#### Scenario: Dirty worktree blocks remove

- **WHEN** `env remove` для environment с dirty worktree
- **THEN** remove блокируется, error

#### Scenario: Occupied port blocks remove

- **WHEN** `env remove` и `socket.bind((http_interface, http_port))` fails (port occupied)
- **THEN** remove блокируется как ownership-unknown; HTTP response только diagnostic

#### Scenario: Shared source DB never dropped

- **WHEN** `env remove` для `shared` environment
- **THEN** source DB не удаляется ни при каких flags

#### Scenario: Drop postcondition checked

- **WHEN** `env remove` для `copy` environment, target DB drop succeeds
- **THEN** postcondition `exists(target_db) is False` verified; if fails → `cleanup_failed`

#### Scenario: Drop refused on cluster identity mismatch

- **WHEN** `env remove` для `copy` environment, но target DB cluster identity не совпадает с recorded (e.g. DB moved to different cluster) OR no recorded restore/backup ownership
- **THEN** drop refused, `cleanup_failed` с причиной; target DB не удаляется

#### Scenario: Idempotent missing artifact

- **WHEN** `env remove` и owned artifact уже отсутствует
- **THEN** считается идемпотентным успехом, записывается в audit

#### Scenario: Partial failure → cleanup_failed

- **WHEN** `env remove` частично fails (e.g. worktree remove error)
- **THEN** state `cleanup_failed`, повторный `remove` продолжает с оставшихся artifacts

#### Scenario: Multiple UUIDs resolved independently

- **WHEN** `env remove UUID1 UUID2` runs with UUIDs from different projects
- **THEN** each target's repository and cluster context is resolved separately

#### Scenario: No arguments preserves cwd semantics

- **WHEN** `env remove` runs from inside an exact registered worktree
- **THEN** it resolves exactly that environment

#### Scenario: Planning preflight aborts before mutation

- **WHEN** one target in a multi-target call is unknown
- **THEN** the command aborts with no changes and a sanitized error

## ADDED Requirements

### Requirement: CheckoutInventory with main checkout row

`EnvironmentResource` SHALL expose a `CheckoutInventory` projection that includes the main checkout of each selected project as the first typed row with `kind = main | environment`, a stable `project_id`, and a nullable `environment_id`. The main checkout SHALL NOT be modelled as a synthetic environment. The base row SHALL contain working identity and state: kind/name and project; branch, short SHA, and canonical worktree path; commits ahead of the base branch plus added and deleted lines; compact Odoo status `running | stopped | unavailable` without PID or metrics; and bound database/DB mode when applicable.

`CheckoutInventory` SHALL be built from one canonical `EnvironmentMonitor.snapshot()` per sample plus Git facts of the main checkout. A separate monitor or collector SHALL NOT be added. The raw `EnvironmentMonitor.snapshot()` SHALL remain the canonical source for `odcli ps`, the Python SDK, FastAPI, and the dashboard.

#### Scenario: Main checkout is the first row

- **WHEN** `CheckoutInventory` is built for a project with one environment
- **THEN** the first row has `kind=main` and no synthetic environment is created

#### Scenario: Stopped checkout stays visible

- **WHEN** the main checkout's Odoo is stopped
- **THEN** the main checkout row remains visible with status `stopped` and no PID