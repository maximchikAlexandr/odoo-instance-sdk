## 1. Typed disk-inspection contract

- [x] 1.1 Add a dedicated backup-policy exception for restore filesystem inspection failures, with a stable code, sanitized requested/inspection path details, and consistent public SDK export.
- [x] 1.2 Add focused exception-contract coverage proving the new failure remains distinct from measured `backup_insufficient_disk` results.

## 2. Shared restore preflight

- [x] 2.1 Resolve the configured restore destination without creating it, walk missing components to the nearest existing directory, and reject existing non-directory components or traversal failures with the typed inspection error.
- [x] 2.2 Run `shutil.disk_usage()` only on the selected existing directory, translate inspection errors truthfully, and preserve the current free-space reserve calculation and measured insufficient-capacity result.
- [x] 2.3 Propagate the inspection failure through ZIP validation and both restore-preflight entry points without weakening operator limits, bounded CRC validation, archive safety checks, or existing caller behavior.

## 3. Regression verification

- [x] 3.1 Extend focused backup-validation tests for existing and nested-missing destinations, no-directory-creation behavior, non-directory ancestors, injected resolution/traversal/disk-usage errors, backup-directory fallback, and genuine insufficient capacity; parametrize repeated matrices.
- [x] 3.2 Add public SDK restore command-construction coverage using a structurally valid local ZIP and missing nested `data_dir`, proving successful planning and no filesystem mutation without live Odoo or PostgreSQL.
- [x] 3.3 Add public machine-readable CLI dry-run coverage for missing-versus-existing `data_dir` parity and actionable inspection failure, asserting exactly one failure envelope and no service contact.
- [x] 3.4 Run the focused restore/backup-validation tests, repository lint and type checks, and the standard offline test suite; record commands and results for review.
