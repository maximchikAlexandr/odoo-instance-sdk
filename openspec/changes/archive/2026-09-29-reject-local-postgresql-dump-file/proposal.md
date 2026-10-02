## Why

`odcli db restore --file` currently treats every caller-owned file as an Odoo ZIP, so a recognizable PostgreSQL custom dump is misreported as a corrupt ZIP. The command must preserve its ZIP-only contract while identifying unsupported or unknown input accurately before any database mutation.

## What Changes

- Classify a caller-owned local restore file from bounded, verified content before ZIP validation.
- Reject a recognized PostgreSQL custom dump with a stable typed unsupported-format diagnostic; do not route it to native dump restore.
- Keep malformed ZIP and unknown binary failures fail-closed and distinguish their diagnostics from a recognized unsupported dump.
- Preserve valid Odoo ZIP behavior, source immutability, path redaction, bounded validation, and the existing catalogue-backup DUMP path.
- Add public CLI dry-run regression coverage for valid ZIP, recognized PostgreSQL dump, malformed ZIP, and unknown bytes, proving that rejected inputs cause no database mutation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-restore`: refine caller-owned local archive preflight so supported ZIPs, malformed ZIPs, recognized PostgreSQL dumps, and unknown binary content receive distinct fail-closed outcomes.

## Impact

- Affected implementation: typed backup policy errors, bounded backup-format probing, and local restore capture/preflight in `src/odoo_instance_sdk/internal/`.
- Affected verification: internal validation/capture tests and the public `odcli db restore --file ... --dry-run --format json` boundary.
- Public restore capabilities remain unchanged: `LocalArchiveRestoreSource` accepts Odoo ZIP only, and native dump restore remains available only for catalogued backups.
- No new dependency, migration, persistent data, or production process launch is introduced.
