## Why

A restore can finish importing its database and filestore but fail during administrator reset or default publication. The current public retry path deletes that proven retained target and imports the archive again, so operators need a supported way to finish only the remaining work while the previous project default stays active.

## What Changes

- Distinguish a proven completed import with pending post-restore work from an import that did not complete; retain only the minimal secret-free checkpoint needed to make that distinction.
- Extend the existing `odcli db restore` leaf with a mutually exclusive completion mode for one exact project-owned incomplete target. Completion reuses the preparation lock, target ownership/provenance checks, administrator-reset operation, and atomic default switch without reading or importing the archive again.
- Permit only explicitly named module upgrades as an optional pre-reset repair step; never infer modules or run `-u all`.
- Report the exact target, completed checkpoint, pending step, unchanged prior default, and sanitized failure in Rich/JSON/TOON output.
- Keep ordinary successful restore, destructive incomplete-restore retry, and foreign/unproven database behavior unchanged.
- Document the inspect-first completion sequence and add focused catalogue, SDK, CLI, concurrency, and real-Odoo regression coverage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-database-preparation`: Record and safely complete proven post-import restore work without reimporting or publishing an unproven target.
- `cli-odcli`: Expose completion through the existing `db restore` leaf with stable, redacted, inspectable output and explicit module selection.

## Impact

The change affects restore checkpoint persistence in the backup catalogue, database-preparation planning/execution, typed restore results and failure projection, the `db restore` CLI callback/help, existing module-update composition, documentation, and focused unit/integration/real-Odoo tests. It adds no service, daemon, workflow engine, dependency, automatic broad module upgrade, or second restore backend.
