## Context

`LocalArchiveRestoreSource` is intentionally ZIP-only, but `capture_selected_backup_restore()` currently assigns `BackupFormat.ZIP` to every local file and immediately calls `validate_zip()`. A PostgreSQL custom dump therefore reaches `zipfile.is_zipfile()` and becomes `BackupCorruptError("backup archive is corrupt")`, even though its content has the stable `PGDMP` signature and the main specification already excludes native dumps from this source.

The local capture boundary already opens the source with `O_NOFOLLOW`, verifies regular-file identity, streams a SHA-256 digest, and rechecks the descriptor identity. Later materialization repeats identity and digest checks before creating a private snapshot. The format decision must reuse that trust boundary, remain bounded and path-redacted, and must not invoke `pg_restore`; the process-backed dump validator remains reserved for catalogued `BackupFormat.DUMP` inputs.

## Goals / Non-Goals

**Goals:**

- Classify local restore input from a small prefix obtained during the existing verified read.
- Produce stable, distinct typed diagnostics for a recognized PostgreSQL custom dump, malformed ZIP, and unknown binary content.
- Preserve the supported Odoo ZIP path, immutable evidence, no-follow checks, bounded ZIP validation, source preservation, and catalogue DUMP behavior.
- Verify the behavior at the internal policy/capture boundary and the public JSON dry-run boundary, including absence of mutation and path leakage.

**Non-Goals:**

- Restoring a caller-owned PostgreSQL dump or adding format conversion.
- Using filename extensions, external `file`, `pg_restore`, or any new dependency for classification.
- Changing public restore-source types, catalogue formats, persistent schemas, plan topology, or successful ZIP restore semantics.

## Decisions

### 1. Capture classification evidence during the verified read

The verified-file helper SHALL retain only the bounded leading bytes required for classification while it hashes the source through its existing no-follow descriptor. It SHALL return that prefix with the stable identity and digest evidence. Local-source capture SHALL classify only this captured prefix; it SHALL NOT reopen the caller path merely to probe format.

This ties classification to the same file identity and bytes that produced the digest and avoids a new time-of-check/time-of-use window. A separate path reopen was rejected because an attacker or concurrent writer could substitute content between hashing and classification. Reading the entire file into memory was rejected because the existing streaming boundary is deliberately bounded.

### 2. Use a pure three-way local-format probe

A small internal typed probe in the backup-validation boundary SHALL map evidence to `odoo_zip`, `postgres_custom_dump`, or `unknown`. `PGDMP` is the authoritative PostgreSQL custom-format signature. Recognized ZIP-family signatures SHALL route to the existing `validate_zip()` implementation, which remains responsible for central-directory, member, CRC, safety, operator-limit, and disk checks. The probe SHALL use no filename, extension, subprocess, environment lookup, or mutable global state.

Calling `validate_dump()` for `PGDMP` was rejected because local native dump restore is out of scope, `pg_restore` compatibility varies by dump version, and the required error is unsupported format rather than dump validity. Sending every non-ZIP input through ZIP validation was rejected because it recreates the misleading diagnosis.

### 3. Represent format failures as typed backup policy errors

The exception boundary SHALL add two `BackupPolicyError` subclasses: one with code `backup_unsupported_format` for a recognized PostgreSQL custom dump and one with code `backup_unknown_format` for unrecognized content. Messages and details SHALL contain only a stable format label, never paths, captured bytes, filenames, or source content. The command's existing `getattr(exc, "code", None)` transport SHALL carry these codes into JSON/TOON/Rich envelopes without command-specific branching.

A generic `ConfigError` was rejected because the machine-readable CLI would fall back to `db_restore_failed`, losing the distinction required by the public contract. Reusing `backup_corrupt` was rejected because recognized unsupported and unknown content are not evidence that a ZIP is corrupt.

### 4. Reject before ZIP validation and all effects

For a local source, format classification SHALL run immediately after verified identity/hash capture and before `validate_zip()`, snapshot-directory creation, process execution, or mutation planning. `odoo_zip` proceeds unchanged. The other two outcomes raise their typed errors. The catalogued `Backup` branch continues to select its declared `BackupFormat`, including the existing process-backed native dump validation path.

This placement preserves existing successful behavior while ensuring that `--dry-run` reports the format error without `pg_restore`, database activity, snapshot creation, catalogue writes, or project-default changes.

### 5. Verify policy and public transport as one regression matrix

Parameterized internal tests SHALL cover a valid Odoo ZIP, `PGDMP` content, a ZIP-family prefix with malformed structure, and unknown bytes. They SHALL assert typed code/message, source preservation, path redaction, and no snapshot. Parameterized public CLI tests SHALL drive `odcli db restore --file ... --dry-run --format json` through the real local capture boundary while replacing later database-capable collaborators with fail-fast sentinels; the valid ZIP case SHALL reach a sanitized plan, and rejected cases SHALL return their exact codes before any sentinel can mutate or spawn.

## Risks / Trade-offs

- [A damaged PostgreSQL dump whose leading signature is destroyed becomes unknown rather than recognized unsupported] → This is accurate for the available evidence and remains fail-closed; no validity claim is made.
- [A file with a forged `PGDMP` prefix is labelled PostgreSQL custom dump without full validation] → The diagnostic deliberately identifies the recognized format signature, not archive validity; no native restore is attempted.
- [ZIP has multiple legal leading signatures and malformed inputs may be ambiguous] → Centralize the explicit recognized ZIP-family signature set in the pure probe and keep all structural decisions in `validate_zip()`.
- [Changing verified-file return data can disturb internal tests] → Keep the new evidence type internal, update the single production call site and focused helper tests, and preserve the later identity/digest revalidation contract.

## Migration Plan

No data or configuration migration is required. Release the typed errors and classifier atomically with their regression tests. Rollback is a normal code revert; persisted catalogue and restore records are unaffected because every new rejection precedes writes.

## Open Questions

None. The accepted behavior, signatures, stable codes, ordering, and non-goals are fully specified.
