## Context

`_prepare_checkout()` already resolves all mutable inputs into a frozen `_CheckoutPlan` before execution. `do_checkout()` later writes the generated configuration with `generate_config(...)`, but it currently omits the optional `data_dir`; `render_config()` therefore preserves a source value when present and emits none for a legacy Compose source config that lacks it. The project-init path already defines `project_owned_data_dir(project_root)` and validates project-owned paths with `verify_project_owned_data_dir(...)`.

The resulting gap is observable in `shared` mode: the database remains the project database, but Odoo can choose a different or invalid filestore root while checkout still finalizes the catalogue row as `READY`. The same configuration-generation path is used before both shared and copy database handling.

Constraints are to preserve the immutable checkout snapshot, existing process and catalogue boundaries, atomic owner-only generated-config writes, external-source behavior, and current database/filestore ownership rules.

## Goals / Non-Goals

**Goals:**

- Resolve and validate the managed Compose filestore binding before checkout mutation.
- Carry that resolved value through the existing immutable plan and apply it in the one existing generated-config write.
- Cover shared and copy configuration generation, external-source preservation, and failure before `READY` at the public resource boundary.

**Non-Goals:**

- Creating a filestore service, resolver hierarchy, backup pool, copy operation, or migration.
- Creating the filestore directory during checkout or changing project initialization.
- Changing external PostgreSQL behavior, database selection, removal semantics, public APIs, CLI options, catalogue schema, or process execution.

## Decisions

### D1: Resolve the binding during checkout planning

In `_prepare_checkout()`, inspect the already-loaded `ProjectConfig`. If `project.postgres` exists and `project.postgres.mode == "compose"`, compute `project_owned_data_dir(repo_root)` and pass it through `verify_project_owned_data_dir(repo_root, ..., require_exists=False)`. Store the resulting absolute `Path` as an optional field on frozen `_CheckoutPlan`. For every other project configuration, store `None`.

This keeps preview and execution on one captured input set, rejects an unsafe symlink or containment escape before the catalogue is opened, and reuses the existing project-init ownership definition. `require_exists=False` is deliberate: checkout binds configuration to the canonical project path but does not create or adopt storage.

Alternative considered: recompute the path inside `do_checkout()`. Rejected because execution would rebuild a filesystem-sensitive input outside the immutable plan.

### D2: Use the existing generated-config override

Pass `plan.data_dir` to the existing `generate_config(...)` call. `render_config()` already overwrites `data_dir` only when the argument is non-null; therefore managed Compose projects receive the verified canonical binding in both database modes, while external projects continue to preserve an explicit source value unchanged.

Alternative considered: mutate the parsed source configuration or add a dedicated filestore resolver. Rejected because both duplicate behavior already present in `generate_config()` and expand the change surface.

### D3: Prove behavior at the public resource boundary

Extend `tests/unit/resources/test_environment_checkout.py` with focused cases that invoke public checkout behavior:

- managed Compose + legacy source config without `data_dir` in shared mode writes the canonical project-owned path and reaches `READY`;
- the existing copy checkout path receives the same planned binding in its generated config;
- a non-Compose project with explicit source `data_dir` preserves it;
- a symlinked canonical managed path escaping the project fails during planning, before any environment row can become `READY`.

Reuse current fixtures, source-config parsing, and existing copy-operation fakes. Do not add a parallel test harness.

Alternative considered: test `render_config()` only. Rejected because it would not prove project classification, plan capture, or fail-before-readiness behavior.

## Risks / Trade-offs

- A legacy Compose project may not yet have a physical filestore directory → validation permits an absent final directory while still rejecting existing non-directories and symlink escapes; checkout itself remains non-creating.
- A Compose manifest with an explicit external `data_dir` will now use the canonical project-owned binding → this is intentional because Compose mode is the authoritative self-contained ownership declaration.
- Adding one private plan field touches planning fixtures that construct `_CheckoutPlan` directly → update only those constructors and keep the field private; no public model migration is required.

## Migration Plan

1. Add the optional resolved `data_dir` to `_CheckoutPlan` and resolve it during `_prepare_checkout()`.
2. Feed the captured value to `generate_config(...)`.
3. Add focused public-boundary regressions and run the repository's targeted checkout tests plus standard pull-request checks.

No persisted data migration is required. Rollback is a code revert; existing generated configurations remain valid files and are not rewritten automatically.

## Open Questions

None.
