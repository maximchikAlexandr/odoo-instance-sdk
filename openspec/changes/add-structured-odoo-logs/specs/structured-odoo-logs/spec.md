# Spec Delta

## Purpose

Structured Odoo run logs give agents a typed, resumable view of one environment-owned Odoo 19 logfile without a collector, index, or second log store.

## ADDED Requirements

### Requirement: Odoo logfile events
The SDK SHALL parse the standard Odoo 19 file line `timestamp pid level dbname logger: message` into one immutable event with schema version 1, source `odoo`, UTC timestamp, level, message, project id, environment id, run id, pid, database, logger, `parsed`, attributes, and an opaque cursor. A continuation or traceback line SHALL attach to the preceding event. A line that does not match SHALL be returned with `parsed = false` and its original text. The parser SHALL NOT interpret SQL, RPC payloads, or other message content, and SHALL NOT rewrite or claim to redact the source message.

#### Scenario: Parsed Odoo line
- **WHEN** a logfile line matches the Odoo 19 file prefix
- **THEN** the event exposes the timestamp, pid, level, database, logger, and message separately and `parsed` is true

#### Scenario: Traceback stays with the event
- **WHEN** a matching line is followed by traceback lines that do not match the prefix
- **THEN** those lines belong to the same event and the next matching line starts a new event

#### Scenario: Unparsed line is retained
- **WHEN** a line does not match the prefix and is not a continuation
- **THEN** the scan yields one event with `parsed = false` and the original text

### Requirement: Run selection
Structured log reads SHALL accept `current`, `last`, or an explicit run id for the bound environment. `current` SHALL be the verified active run. `last` SHALL be the run with the latest start timestamp. An unknown run id SHALL fail without creating a file. PostgreSQL events, cluster scope, and multi-source marks are not part of this capability.

#### Scenario: Current run
- **WHEN** a structured read asks for `current` and one verified run is active
- **THEN** events come only from that run

#### Scenario: Last run
- **WHEN** a structured read asks for `last`
- **THEN** events come from the run with the latest start timestamp in that environment

#### Scenario: Unknown run
- **WHEN** a structured read names a run id that is not an owned run of the bound environment
- **THEN** the read fails and no logfile or directory is created

### Requirement: Opaque cursors
A cursor SHALL be an opaque, versioned, URL-safe token for a position between source records. It SHALL keep its meaning after the CLI restarts, SHALL be bound to the project, environment, and run, and SHALL NOT be a timestamp, line number, path, public byte offset, or event id. `--after-cursor` SHALL start strictly after that position. The scan cursor SHALL advance over every examined record, including records excluded by filters. Order SHALL follow log generation and position within that generation, not timestamp. Normal rotation SHALL keep a cursor valid while its generation is retained. A deleted generation SHALL fail as `cursor_expired` and include `earliest_cursor` when a later retained generation is known. An unexpected truncation or replacement SHALL fail as `source_reset`. A malformed token SHALL fail as `cursor_invalid`. A token for another project, environment, or run SHALL fail as `cursor_wrong_instance`. None of these errors SHALL include a filesystem path, and none SHALL silently continue from another position.

#### Scenario: Cursor survives restart
- **WHEN** a cursor is created and a later process searches with `--after-cursor`
- **THEN** the search starts strictly after the original position

#### Scenario: Filtered records still advance the cursor
- **WHEN** a search level filter skips records inside the scanned range
- **THEN** the returned next cursor is past those skipped records

#### Scenario: Equal timestamps do not drop records
- **WHEN** two records share a timestamp and a cursor is taken between them
- **THEN** the later read returns the second record once and does not return the first

#### Scenario: Rotation keeps a retained generation
- **WHEN** the active logfile is rotated and the referenced generation file is still present
- **THEN** a cursor into that generation still reads it

#### Scenario: Distinct cursor failures
- **WHEN** the token is malformed, belongs to another instance, names a deleted generation, or the generation was reset
- **THEN** the failure codes are `cursor_invalid`, `cursor_wrong_instance`, `cursor_expired`, and `source_reset` respectively, with no path and no fallback read

### Requirement: Stable search window
The first bounded search SHALL fix `window_end_cursor` at the high-water boundary observed when the search starts. A continuation SHALL honor `--until-cursor` so records appended after that boundary do not enter the page. `next_cursor` SHALL move through the scanned range. `has_more` SHALL be true only when source records remain before `window_end_cursor`. `complete` SHALL be true only when the scan reached that boundary. The bounded envelope SHALL contain `events`, `next_cursor`, `has_more`, `window_end_cursor`, and `complete`.

#### Scenario: New records stay outside the window
- **WHEN** a bounded search returns a window and new records are appended before the next page
- **THEN** the next page that passes the same `--until-cursor` excludes those new records

#### Scenario: Limit leaves a remainder
- **WHEN** the limit is reached before the window boundary
- **THEN** `has_more` is true, `complete` is false, and `next_cursor` is the position after the scanned records

### Requirement: Search filters
Structured search SHALL accept only a case-insensitive positional text substring, repeatable exact levels, a logger prefix, a database name, RFC 3339 `--since` and `--until`, one of `--after-cursor` or `--after-mark`, optional `--until-cursor`, `--limit`, and `--follow`. It SHALL reject regular expressions, boolean queries, SQL-like syntax, saved searches, and ranking.

#### Scenario: Substring and level
- **WHEN** a search supplies a text substring and one exact level
- **THEN** only events whose message contains that substring without case sensitivity and whose level matches are returned

#### Scenario: Conflicting resume selectors
- **WHEN** a search supplies both `--after-cursor` and `--after-mark`
- **THEN** the command fails as a usage error before reading the logfile

### Requirement: Named marks
A mark SHALL be a named persisted cursor unique within one environment. Creating it SHALL first read the selected logfile through the current high-water boundary and finish the open multiline event. Replacement SHALL require an explicit replace flag; otherwise an existing name fails as `mark_already_exists`. A missing name fails as `mark_not_found`. Stored metadata SHALL contain the name, opaque cursor, creation time, and environment and run identity, and SHALL NOT contain secrets or local paths. The write SHALL be atomic and private to the environment artifact directory. Mark operations SHALL NOT create a database or table. Automatic marks for module or test commands are out of scope.

#### Scenario: Mark separates later events
- **WHEN** a mark is created and later matching events are appended
- **THEN** a search after that mark returns only events after the stored boundary

#### Scenario: Replace is explicit
- **WHEN** a mark name already exists and create is called without replace
- **THEN** the call fails with `mark_already_exists` and the stored cursor is unchanged

#### Scenario: Mark file is private
- **WHEN** a mark is created
- **THEN** its file mode is `0600` and a reader can show, list, and delete it by name

### Requirement: JSONL checkpoints
JSONL SHALL emit one event or checkpoint record per line. A follow stream SHALL periodically emit a checkpoint record that can resume the same run. Interrupting follow with Ctrl+C SHALL exit 130. A bounded JSONL export SHALL be a valid snapshot of that page.

#### Scenario: Follow checkpoint resumes
- **WHEN** a JSONL follow stream emits a checkpoint and a later search uses that cursor
- **THEN** the later search starts strictly after the checkpoint position

#### Scenario: Follow interrupt
- **WHEN** the operator interrupts a JSONL follow with Ctrl+C
- **THEN** the process exits 130
