## Context

R2 is based on `main` at `7e29e50a0de123565c46f9f566ab67cca0963955`. That revision already has more of the requested foundation than the first plan credited:

- `write_project_generated_config()` and `internal/generated_config.py` create a mode-`0600` `.odcli/odoo.conf`, with or without a source config, before Compose bootstrap.
- `generated_config_needs_repair()` and existing init handling independently repair a missing or stale generated config without rewriting an unchanged manifest.
- Managed checkout/sync already plan uv venv, compile, install/sync, interpreter selection, locks, pin retention, and worktree rebasing.
- `_resolve_checkout_dependency_inputs()` and adjacent helpers already select explicit and Odoo requirements, but do not yet discover requirements recursively from configured addon roots or provide the primary-checkout operation.

The large intervening main diff primarily adds GitLab/Git workflows, build/packaging behavior, and process runtime-environment materialization. It does not add the requested manifest fields, recursive discovery, or `prepare-venv`. The fixed uv argv in this change does not need the new dynamic-process environment abstraction.

## Goals / Non-Goals

**Goals:**

- Store and render ordered addon roots and server-wide modules through existing init/config behavior.
- Use one deterministic dependency-input selection for primary and managed checkouts.
- Prepare or refresh the primary checkout venv through a public immutable SDK operation and transport-only CLI.
- Preserve managed-environment ownership, hash-lock, pin, locking, and retained-lock behavior.

**Non-Goals:**

- Rebuilding the existing init transaction, generated-config lifecycle, uv executor, process boundary, or configuration parser.
- Modeling the primary checkout as a `DevelopmentEnvironment` or adding a resolver/provider/facade/cache.
- Adopting the new dynamic process-environment mechanism for fixed uv commands.
- Changing external-PostgreSQL init, `run`, or `shell` dependency behavior.

## Decisions

### Extend the existing manifest and generated-config repair inputs

Add optional ordered tuple fields `addon_roots` and `server_wide_modules` to `ProjectConfig`. Addon roots are repository-relative, contained after resolution, and first-occurrence deduplicated; module names are nonblank and comma-free. Re-init omission retains a stored tuple, while one or more supplied option values replace that tuple. No separate clear flag or settings object is introduced.

The existing config renderer converts addon roots to absolute paths and overlays `addons_path` and `server_wide_modules` after reading an optional source config. Existing source preservation, database/network/data/log/secret overlays, atomic mode-`0600` writes, confirmation, bootstrap ordering, and external-PostgreSQL behavior remain unchanged. The existing repair fingerprint/content comparison includes the two stored values.

### Complete and share the existing dependency-input helper

Extend/consolidate the current neutral checkout-planning helper instead of creating a public resolver. For each configured addon root, walk deterministically and identify module directories by `__manifest__.py`; consider `requirements.txt` in the module and immediate parent. Then consider the configured Odoo-bin sibling and explicit project requirement inputs. Resolve and canonicalize candidates, retain first occurrence, reject a missing explicit file with the existing typed-error style, and ignore absent discovered candidates.

Managed checkout and sync rebase repository-local inputs to their worktree before invoking the shared selection. Existing uv compile/install or sync argv, hash-lock bypass, ownership rules, locking, pins, and retained-lock behavior are not redesigned.

### Add one primary-checkout preparation operation

Add a narrow public immutable command builder and convenience operation beside project initialization. Relative venv paths resolve from the project root. A missing venv adds the existing-style `uv venv` prepared step; both missing and existing venvs use the platform-correct interpreter and the shared dependency inputs. Non-empty inputs use the existing joint compile/install mechanics; an empty set only creates or validates the venv. The manifest interpreter is written atomically only after successful steps, and dry-run executes no mutation.

The primary checkout is not inserted into the environment catalog and no new resource facade is introduced. The implementation may factor the minimum private helper needed to reuse existing uv argv without moving unrelated environment lifecycle code.

### Keep CLI and verification incremental

Click callbacks transport repeated init values and `prepare-venv` inputs to public SDK operations and reuse current preview, confirmation, execution, error, and Rich/JSON/TOON output paths. Canonical command/output inventories are extended in place. Focused tests cover new branches; inherited output, security, process, and packaging suites are run rather than duplicated.

One local E2E script covers the only combined real-process gap: initialized config values plus a real primary uv environment. Existing GitHub real-Odoo smoke remains the real generated-config consumption boundary; it is extended only where needed to assert the new config keys.

### Ponytail full R2 gate

| Proposed part | Necessity | Existing alternative | Minimal decision |
| --- | --- | --- | --- |
| Addon roots and server-wide modules | Stable config and discovery inputs are absent | `addon_repositories` owns checkouts; `default_run_args` is opaque | Two optional tuples and repeated init options; omit retains, supplied values replace |
| Generated config values | Odoo must consume the new inputs | Generator, secure writer, repair, confirmation, and bootstrap ordering already exist | Add two overlays and repair inputs only |
| Recursive dependency discovery | Current helpers omit addon-module requirements and are duplicated at call sites | Existing checkout planning/rebase helpers | Complete one private helper and reuse it; no resolver subsystem |
| Primary venv preparation | No public primary-checkout equivalent exists | Managed-environment uv steps already implement most mechanics | One narrow public operation reusing steps/locks; no catalog row or facade |
| CLI and verification | New public behavior must be reachable and regression-safe | Existing registration, output inventories, CI, and real-Odoo smoke | Extend those surfaces and add one combined local script |
| Dynamic process environments from new main | Not needed for fixed uv argv | Existing prepared-process boundary already suffices | Do not adopt it |

R1 abstractions removed after the main audit: no new two-artifact init transaction, no explicit-empty-list protocol, no separate discovery subsystem, no six-package DAG, and no pair of overlapping local E2E scripts.

## Risks / Trade-offs

- Recursive walking can be expensive; it is bounded to validated configured roots, deterministic, and filters on two filenames.
- Shared parent requirements can repeat; canonical first-occurrence deduplication makes the input stable.
- Existing and project venv interpreters differ by platform; use the repository's platform-aware executable conventions and test POSIX/Windows projections.
- Primary preparation reuses internal uv mechanics whose current home is managed-environment code; factor only the common argv/step portion and keep ownership/catalog policy in the existing resource.
- The current real-Odoo CI fixture may not model the private test repository layout; local E2E supplies that layout while ordinary artifacts remain in Git and runtime evidence remains in Multica.

## Migration Plan

The fields are optional, so old manifests remain valid. Existing projects gain them only when init receives values. Managed behavior changes only in the unhashed discovery branch. A project interpreter is stored only after successful primary preparation. Rollback can ignore the optional fields and command; generated config and venvs remain normal project resources and are not automatically deleted.

## Open Questions

None. Concrete Binta/test-repository paths remain caller inputs rather than product defaults.
