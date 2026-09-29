## Context

Restore archive validation computes the uncompressed ZIP size and routes disk-capacity checks through `preflight_restore_disk_space()` in `internal/backup_validation.py`. The shared helper is used by local-archive restore, catalogue restore, COPY environment validation, and restore source binding. It currently resolves the configured destination and calls `shutil.disk_usage()` on that exact path. Any `OSError`, including the expected `FileNotFoundError` for a new `data_dir`, is converted into a failed `RestoreDiskPreflight` with zero available bytes and the `backup_insufficient_disk` code.

The resulting failure is factually wrong: capacity was not measured. It also blocks immutable command construction and therefore public `db restore --dry-run` before any Odoo or PostgreSQL interaction. The solution must preserve archive bounds, typed error transport, the current `max(1 GiB, 10% of free space)` reserve policy, and side-effect-free planning.

## Goals / Non-Goals

**Goals:**

- Inspect the filesystem that will contain a missing configured destination by using its nearest existing directory ancestor.
- Distinguish a successful low-capacity measurement from failure to resolve, traverse, validate, or inspect the destination path.
- Keep dry-run and command construction free of directory-creation side effects.
- Apply the corrected behavior once in the shared preflight so every existing restore caller receives it.
- Verify the shared helper and the public command-construction path without requiring Odoo or PostgreSQL services.

**Non-Goals:**

- Changing the reserve formula, archive size calculation, CRC bound, operator limit, or ZIP safety policy.
- Creating `data_dir`, changing Odoo configuration, or probing a database service during planning.
- Introducing a storage abstraction, new dependency, configuration switch, retry policy, or catalog migration.
- Reworking restore execution, locking, ownership, cleanup, or public command topology.

## Decisions

### 1. Resolve one inspection directory inside the shared disk preflight

The preflight SHALL start from the configured destination (or the existing backup-directory fallback when no `data_dir` is supplied), resolve it without creating it, and inspect path components upward until it finds the nearest existing entry. A `FileNotFoundError` means only that the search must continue at the parent. The first existing entry MUST be a directory; an existing non-directory in the path is an invalid destination and MUST fail inspection. Once selected, that directory is the sole argument to `shutil.disk_usage()`.

This keeps the change at the common root-cause seam and reuses the operating system’s filesystem accounting. It also preserves mount-boundary correctness: the nearest existing ancestor is the filesystem on which the missing child would be created.

Alternatives considered:

- Create the destination before measuring: rejected because it mutates state during dry-run and can leave partial configuration behind.
- Always inspect the project root, current directory, or backup directory: rejected because those paths may be on a different filesystem from the configured destination.
- Add caller-specific fallbacks: rejected because the same shared helper has multiple restore callers and duplicated workarounds would drift.

### 2. Fail closed with a dedicated typed inspection error

Only `FileNotFoundError` while walking upward is treated as “keep searching.” Resolution failures, permission errors, I/O errors, an existing non-directory component, an impossible root traversal, and `disk_usage()` failures SHALL produce a dedicated backup-policy inspection failure with a stable code and sanitized details identifying the requested path, the attempted inspection path when known, and the operating-system reason. They SHALL NOT produce `backup_insufficient_disk`, zero measured capacity, or a successful preflight.

The error SHALL flow through the existing restore command-construction failure transport, so JSON output remains a single typed failure envelope and human output remains actionable. A genuinely completed capacity measurement continues to use `BackupInsufficientDiskError` when the existing reserve policy is not met.

Alternatives considered:

- Reuse `BackupInsufficientDiskError`: rejected because it preserves the false claim the issue is fixing.
- Swallow inspection failures and continue: rejected because archive extraction without a capacity bound weakens an existing safeguard.
- Reuse the generic `BackupValidationUnavailableError`: rejected because a stable backup-policy subtype and structured path details make the new failure distinguishable without changing the meaning of the existing executable-validator error.

### 3. Preserve the existing capacity model and archive-validation order

After an inspection directory is obtained, the preflight SHALL retain the current free-space lookup and reserve calculation. `validate_zip()` SHALL continue to enforce operator limits, disk limits, and bounded CRC work in the existing order. The only semantic split is between a measured insufficient-capacity result and an unmeasured inspection exception.

No new abstraction is needed: a small private path-selection helper, the existing preflight value object for measured outcomes, and one typed exception are sufficient.

### 4. Test the common seam and one public end-to-end planning route

Focused unit tests SHALL cover nested missing destinations, existing destinations, a non-directory ancestor, traversal/inspection `OSError`, genuine insufficient free space, and the invariant that no missing directory is created. Existing parametrization conventions SHALL be used for repeated failure cases.

A regression test SHALL construct or invoke the public local-archive restore dry-run path with a structurally valid ZIP and configured missing nested `data_dir`. It SHALL assert successful planning, unchanged filesystem state, and parity with the existing-directory case. The same public boundary SHALL demonstrate actionable failure when filesystem inspection is forced to fail. Existing fixtures and command fakes SHALL be reused; no live Odoo or PostgreSQL process is part of the test.

## Risks / Trade-offs

- [Race: a path component changes between ancestor selection and `disk_usage()`] → Treat the resulting inspection error as a typed fail-closed outcome; planning does not create or lock filesystem paths.
- [Symlink resolution or platform-specific path errors] → Use `Path.resolve(strict=False)` plus explicit error translation, and cover injected `OSError` paths without assuming one platform-specific message.
- [Public error surface grows by one exception type/code] → Keep it under the existing backup-policy hierarchy, export it consistently with sibling backup-policy errors, and assert the stable code/details.
- [The later restore may create the path on a different mount than the measured ancestor if the system mount topology changes after planning] → This is an inherent time-of-check limitation; existing immutable planning and execution safeguards remain unchanged, and no speculative mount-management mechanism is introduced.

## Migration Plan

No data or configuration migration is required. Ship the shared preflight and regression tests together. Rollback is the single OpenSpec implementation commit/revert; it does not require cleanup because planning creates no directories and changes no persisted state.

## Open Questions

None. The GitHub report, current call graph, and existing reserve/error contracts determine the behavior.
