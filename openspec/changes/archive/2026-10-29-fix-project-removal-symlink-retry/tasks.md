## 1. Filesystem purge safety

- [x] 1.1 Narrow the nested-symlink rejection to registered source-root deletion while preserving top-level symlink, canonical containment, source provenance, unknown-content, clean-worktree, and deletion postcondition guards.
- [x] 1.2 Add parameterized unit regressions for nested file and directory symlinks in an owned non-source target, proving the owned tree is removed and every external referent is unchanged.
- [x] 1.3 Retain focused unit coverage showing top-level target symlinks and nested source-root symlinks remain inert.

## 2. Missing-runtime purge recovery

- [x] 2.1 Reuse the loaded project manifest, generated configuration, active cluster claim, owned credential, and existing instance/cluster primitives to capture removal-only database and Compose observations when the primary Python runtime is absent.
- [x] 2.2 Keep the recovery path read-only during planning and fail closed for missing or changed manifest, database, cluster, credential, Docker identity, or exclusive-use evidence.
- [x] 2.3 Add integration coverage for a full-id purge retry with an already-absent primary `.venv`, including completed remaining cleanup, no environment recreation/execution, truthful partial results, and inert negative evidence cases.

## 3. Product verification

- [x] 3.1 Extend the existing source-backed real-Odoo project-removal CI scenario to exercise an OdCLI-created primary venv containing nested interpreter symlinks and assert purge preserves an external referent.
- [x] 3.2 Run the focused filesystem and project-removal unit/integration suites, repository lint and type checks, and the existing CI jobs selected by the repository without adding a test framework or committed runtime evidence.
- [x] 3.3 Verify strict OpenSpec validation and confirm public SDK signatures, CLI syntax, bounded result schemas, dependencies, and catalogue schema remain unchanged.
