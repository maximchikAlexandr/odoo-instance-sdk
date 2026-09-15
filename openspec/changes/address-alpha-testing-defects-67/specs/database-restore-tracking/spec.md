## MODIFIED Requirements

### Requirement: Schema v0 → v2 миграция

The first Alembic revision SHALL create `restores` and `database_events` with their indexes as part of the complete current catalogue schema. Sequential `PRAGMA user_version` v0→v2 steps SHALL NOT remain as a production migration ledger. Existing backup rows SHALL be preserved when a known alpha catalogue is stamped. All `CREATE TABLE` and `CREATE INDEX` in that revision SHALL be the current schema, not a historical partial upgrade.

#### Scenario: Существующая инсталляция v0

- **WHEN** a known alpha catalogue that historically started as `user_version = 0` is stamped
- **THEN** таблицы `restores` и `database_events` exist, existing backup rows are unchanged, and no `PRAGMA user_version` step runs

#### Scenario: Повторное открытие v2-каталога

- **WHEN** catalog opens already stamped at the first Alembic revision
- **THEN** schema не модифицируется, no-op
