# Tasks

## 1. Manifest and doctor

- [x] 1.1 Add optional `OdooLoggingConfig` to `ProjectConfig` load and write, and verify a unit test round-trips `[logging.odoo]` and rejects a blank handler without writing the section when it is absent.
- [x] 1.2 Add the doctor `logging` check facts for `structured`, `level`, and `handlers`, and verify a doctor JSON test shows disabled settings when the section is absent and the configured values when it is present.

## 2. Parser, cursor, and query

- [x] 2.1 Add the Odoo 19 parser and `LogEvent` model, and verify tests cover a parsed line, a traceback continuation, and an unparsed line.
- [x] 2.2 Add the generation index and opaque cursor codec, and verify tests cover restart, filtered-record advance, equal timestamps, retained rotation, and the four cursor errors without paths.
- [x] 2.3 Add the bounded scan with a frozen window, and verify a test that appends during pagination keeps the new record outside `--until-cursor`.

## 3. Runs and marks

- [x] 3.1 Add structured run directory creation to the existing foreground and detached command snapshots, and verify a dry-run test shows one run-specific `--logfile` without creating the directory while an execute test creates modes `0700` and `0600` and non-secret metadata.
- [x] 3.2 Add `current` and `last` run selection plus `iter_log_events()`, and verify the raw `iter_logs()` tests still pass when the manifest section is absent.
- [x] 3.3 Add atomic environment-scoped marks and their command for create, replace, and delete, and verify tests cover the before/after boundary, `mark_already_exists`, `mark_not_found`, mode `0600`, and a dry-run that writes nothing.

## 4. CLI, cleanup, and accounting

- [x] 4.1 Turn `odcli logs` into a group with search, cursor, and mark leaves, register them once in `PUBLIC_LEAF_CASES`, and verify the bare command still tails raw text and search JSON contains the page fields.
- [x] 4.2 Emit JSONL events and checkpoints from search, and verify a follow checkpoint resumes and Ctrl+C exits 130.
- [x] 4.3 Include owned run logs in environment `other_files_bytes` and delete only those owned directories during environment removal, and verify a removal test leaves an unrelated directory in place.
- [x] 4.4 Run the focused pytest modules for manifest, doctor, logs, run launch, and CLI leaves, and verify they pass.
