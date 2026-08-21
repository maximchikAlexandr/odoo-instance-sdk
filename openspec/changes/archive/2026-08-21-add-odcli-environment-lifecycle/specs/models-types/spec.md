## ADDED Requirements

### Requirement: `StartConfig.from_odoo_config(path)` records actual path

`StartConfig.from_odoo_config(path)` MUST устанавливать `config_path` в фактический `path` (приведённый к строке), а не ждать option `config_path` внутри файла.

Если файл содержит option `config_path`, actual `path` argument MUST иметь приоритет — значение из файла игнорируется для `config_path` field.

`_build_cli_args()` MUST передавать ровно один `--config <config_path>`. SDK MUST NOT добавлять второй временный config только из-за `db_password`, если persistent generated conf уже имеет права `0600`.

#### Scenario: config_path set to actual path

- **WHEN** `StartConfig.from_odoo_config("/worktree/odoo.conf")` вызывается
- **THEN** `config_path == "/worktree/odoo.conf"` (or `str(Path("/worktree/odoo.conf"))`), regardless of `config_path` option inside file

#### Scenario: File config_path ignored

- **WHEN** `StartConfig.from_odoo_config("/worktree/odoo.conf")` и файл содержит `config_path = /other/path`
- **THEN** `config_path` field = actual `path` argument, NOT `/other/path`

#### Scenario: Single --config in argv

- **WHEN** `_build_cli_args()` builds argv для persistent `0600` generated conf
- **THEN** ровно один `--config <path>`, no second temp config from `db_password`

#### Scenario: No temp config for 0600 persistent conf

- **WHEN** generated conf has `0600` permissions and `db_password` is set
- **THEN** `db_password` flows through the persistent config; no second temp config created