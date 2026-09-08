## Context

GitHub #59 combines defects observed in one imported Compose project with cross-command presentation regressions. The approved base already has the needed primitives: immutable `ExecutionPlan`/`ProcessStep`, the shared CLI envelope and Rich projection, `generate_config()`, a single SQLite catalogue, `DevelopmentEnvironment.worktree_path` and `base_ref`, `human_bytes`-style formatting, the context resolver, and the shared address probe. The base also contains catalogue migration v14 that rebuilds the two environment child tables with correct foreign keys, but its behavioral v7-to-current insertion regression still belongs in this scope.

The implementation spans `cli.py`, `commands/`, `internal/`, `resources/`, storage migrations, tests, and README. Repository rules require all launches to remain behind `internal/proc`, public plans to stay frozen and redacted, and bounded output to keep one typed pipeline. This design therefore extends existing seams and does not introduce a second renderer, catalogue, resolver, runner, or configuration model.

## Goals / Non-Goals

**Goals:**

- Make a confirmed imported Compose initialization immediately usable without editing imported files or the repository root.
- Make project selection, endpoint selection, module-update success, Git baselines, and port availability truthful and deterministic.
- Make Rich output readable while preserving machine schemas, exact integer values, redaction, and execution parity.
- Expose stored worktree paths only at the local CLI boundary.
- Cover the whole issue through shared parameterized contracts plus focused root-cause regressions.

**Non-Goals:**

- No setup wizard, manifest merge engine, `env cd`, nested shell, second metrics pass, new port setting, new renderer framework, or new public resource.
- No local path in monitor/FastAPI/dashboard schemas.
- No Git fetch/pull, arbitrary retry delay, uncommitted-diff metric, install/uninstall module flow, or change to JSON/TOON payload schemas except the explicit CLI-only worktree field and new `env path` result.
- No production implementation in this planning change.

## Decisions

### 1. Treat init confirmation as a gate, not a merge mode

Add `--yes` beside `--no-input` and `--dry-run`. Existing-manifest handling first computes and validates the complete replacement, keeps identical input as a no-op, then permits a non-identical atomic replacement only after interactive confirmation or `--yes`. Dry-run returns before writes regardless of confirmation. This is smaller and deterministic compared with a field-wise merge engine, which is explicitly outside scope.

### 2. Generate one project runtime config through the existing generator

For Compose projects, place the effective config under `.odcli` and call the existing generated-config function with the repository root as both source checkout and current worktree context. Overlay owned-cluster host/port/user/password and the preferred HTTP port after reading the imported source options. Read the database password from the existing owner-only Compose password file, pass it only through private execution/config state, and keep it in the common redaction secret set. Project runtime resolution chooses this generated config; external-mode projects retain their existing source-config path.

The alternative of copying and editing the imported config is rejected because it destroys provenance and repeats config logic. Storing the password in `project.toml` is rejected because the manifest is a public planning surface.

### 3. Keep ignore ownership inside `.odcli`

Replace root `.gitignore` mutation with an `.odcli/.gitignore` rule covering the local secret file. The initialization transaction owns this local file and writes it atomically with the existing path/symlink safety posture. Existing root ignore content is never opened for writing. This uses native Git ignore nesting and removes the need for repository-level cleanup.

### 4. Centralize endpoint precedence in the existing project runtime resolver

Compute the effective project HTTP port once where `CliRuntimeContext`/project instance state is constructed: explicit command override, `preferred_http_port`, effective Odoo config, then Odoo default. All project-bound consumers, including restore postconditions, use that resolved state. Environment-bound commands continue using their recorded/generated environment config. Updating each command independently is rejected because it would recreate the observed drift.

### 5. Join CLI path data by stable environment ID

Keep the canonical monitor snapshot free of local paths. For one-shot and watch `env list`, read the existing catalogue environment projection in the same CLI coordination path and map `environment.id` to stored `worktree_path`; reuse each monitor snapshot rather than collect metrics again. Rich adds `WORKTREE`; machine output adds the field only in the CLI result graph, not in the `Snapshot` model.

`env path` reuses the existing positional name/UUID and cwd worktree resolver, validates the catalogue row is active and its stored path is an existing directory, and uses a command-specific plain Rich success emission that suppresses the generic completion line. JSON/TOON use the normal envelope. Directory discovery by SDK layout and `env cd` are rejected as respectively brittle and impossible to apply to the parent shell.

### 6. Use existing ownership keys for catalogue scope

Resolve the canonical project identity once through the existing context/provenance helpers and pass it into backup/resource catalogue queries. `--all-projects` passes no project predicate and records explicit global provenance. Outside a project, absence of the flag is an error. Database inventory stays on its already bound cluster. This avoids post-query filtering and prevents unrelated pages from consuming the limit/cursor before project filtering.

### 7. Repair Rich presentation at the common boundary and thin leaf renderers

Retain one output document and one renderer selection. The shared Rich plan projection prints semantic goal/targets/preconditions and then every process step's already-redacted `display` in execution order. It does not rebuild argv. Leaf renderers use existing Rich tables/panels and shared byte formatting; list renderers have one owner so callbacks do not print a table before the common emitter prints another. A parameterized inventory covers bounded command families and the exact list-command set.

Raw JSON as a primary Rich view and a generic renderer class hierarchy are rejected. Contained pretty JSON remains allowed only for nested values whose structure would otherwise be lost.

### 8. Make progress identity originate in captured steps

Use each plan step's stable ID and existing description/target metadata when the run context creates start/progress/completion/failure events. The shared observer formats those fields, elapsed time, reliable units, and process exit status. Command callbacks do not invent labels. This fixes refresh and restore and prevents the same anonymous event class across other long bounded operations.

### 9. Fix module update at source construction and postcondition

Build the module-name JSON once and decode it inside the generated Odoo shell source, matching `_module_list_source()`. Reject an empty tuple before command construction. After upgrade, compare the requested frozen set with returned mapped names and fail on any missing item before the success envelope is built. A CLI test with a canned successful payload is insufficient; the regression inspects/executes the generated source semantics and covers an empty recordset.

### 10. Thread recorded `base_ref` through both Git collectors

Include each catalogue row's `base_ref` in bounded probe construction, direct collection, cache identity, and `GitActivity.default_branch` compatibility output. Resolve its upstream first and local ref second, then compute merge base, ahead/behind, and committed numstat exactly as today. Both collectors share the result decoder. Missing baseline or ancestry remains orphan; missing `main` is irrelevant.

### 11. Match the address probe to the next server bind

Set `SO_REUSEADDR` on the probe socket before bind. This is the smallest platform-native fix for closed accepted connections while a live listener still prevents bind. Preserve the short timeout and `UNKNOWN` mapping. A broad retry loop or unconditional delay is rejected because it slows every test and can hide real ownership races.

### 12. Retain migration v14 and prove behavior

Do not add a second migration version when v14 is present. Add a v7 fixture whose child foreign keys were rewritten to the staging table, migrate through current, inspect both references, preserve rows, and insert a new `environment_events` row with foreign keys enabled. Fresh-schema coverage asserts both references directly. If implementation is rebased onto a base lacking v14, the same table-rebuild algorithm becomes the next sequential version.

## Risks / Trade-offs

- [Generated config contains an owned database password] → keep it in an owner-only `.odcli` file, source the password from the existing secret artifact, and include it in common redaction/fingerprint exclusion tests.
- [CLI-only path enrichment could drift from snapshot ordering] → join exclusively by stable UUID and fail a missing active catalogue match rather than positionally pairing records.
- [Whole-CLI Rich audits can become brittle snapshots] → assert semantic invariants with a parameterized leaf inventory, reserving exact-output assertions for `env path` and single-table ownership.
- [Base refs can contain characters meaningful to Git revision syntax] → pass them only as argv values through existing validation and resolve exact recorded refs without shell interpretation.
- [SO_REUSEADDR differs across operating systems] → test both the recently closed accepted-connection case and a concurrently live listener; retain `UNKNOWN` for unsupported socket failures.
- [Concurrent work on the output boundary may overlap files] → implement against the approved base in this feature branch, preserve the single shared emitter architecture, and resolve later integration at the contract level without duplicating helpers.

## Migration Plan

1. Land focused characterization and regression tests for all reproduced failures.
2. Apply the catalogue regression to existing v14 without changing current schema version on this base.
3. Implement init/config/ignore and shared endpoint resolution, then catalogue scoping and path access.
4. Fix module source/postcondition, Git baseline propagation, and address probing.
5. Update common progress and Rich projections, then simplify affected leaf renderers and documentation.
6. Run strict OpenSpec validation and the repository's format, lint, type, unit, architecture, and documentation checks. No data rollback is needed for the retained v14 migration; code rollback leaves already repaired catalogues valid.

## Open Questions

None. GitHub #59 and the approved-base implementation provide all externally observable choices required for implementation.
