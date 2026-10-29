## Why

OdCLI previews mutating operations but often hides the user-owned files and directories that will be created, replaced, moved, or deleted, while long machine-mode operations, help discovery, and restore completion also omit information automation needs to make safe decisions. GitHub #183 and the owner-approved DX/UX extension require one truthful, redacted contract across Rich, JSON, and TOON without adding a filesystem tracer, a second command registry, or a second execution plan.

## What Changes

- Standardize structured user-visible filesystem consequences inside the existing immutable `ExecutionPlan`/`ActionStep.details`, render them in Rich dry-run output, and preserve the same data in JSON/TOON.
- Populate those consequences for translation export, project initialization, VS Code generation, environment create/checkout/sync/removal, database restore/refresh/drop, backup deletion, bug-report, self-update, Git, and Odoo execution paths, including honest conditional or unknown effects.
- Emit framed, redacted progress events for clone, backup download, and restore on stderr in the selected machine format while preserving exactly one final JSON/TOON document on stdout.
- Project JSON/TOON help directly from the live Click command graph, including commands, arguments, options, types, required/default state, and descriptions, with no second registry.
- Extend restore results with explicit `passed`, `failed`, or `not_checked` neutralization and filestore-verification outcomes and unique expected/found/missing file counts.
- Add parametrized compatibility, redaction, no-mutation, plan/execution parity, progress-framing, help-schema, restore-verification, and real-boundary verification coverage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `command-execution`: Define structured filesystem effects in the existing immutable action plan and machine-readable progress framing over existing lifecycle events.
- `cli-odcli`: Expose consistent Rich/JSON/TOON dry-run consequences, machine-readable help, and machine progress stream separation.
- `translation-export`: Publish the exact planned `.po`/`.pot` destination and safe create/replace sequence, including ISO-name uncertainty and optional validation.
- `database-restore`: Report neutralization and filestore verification independently with explicit states and unique-file counts.

## Impact

- Public plan JSON gains only additive, JSON-safe values within existing action `details`; existing fields and fingerprints remain compatible after canonical regeneration.
- Affected implementation spans `execution.py`, `internal/proc/`, `commands/output.py`, `internal/output_rich.py`, Click command/group composition, translation/project/environment/database/backup/bug-report/self-update/Git/Odoo operation planners, and restore result models.
- Existing `PUBLIC_LEAF_CASES`, unit/integration tests, real-Odoo CI fixtures, documentation, and generated command inventory require coordinated updates; no runtime dependency, database migration, OpenAPI operation, or new background service is introduced.
- JSON/TOON final stdout remains one document; consumers that opt into normal machine-mode execution may additionally drain the documented progress stream on stderr.
