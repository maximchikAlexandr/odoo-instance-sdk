## Context

`ProjectConfig` already owns project discovery defaults and safe TOML round-tripping. `init_project_command()` already captures one immutable plan, `internal/generated_config.py` already preserves and overlays Odoo config values, and managed environments already compile/install dependencies through uv and the shared process boundary. The missing behavior is wiring these existing mechanisms around two new project inputs and exposing the same dependency selection to the primary checkout.

The design must preserve the repository's SDK-first CLI rule, typed bounded output, immutable/redacted command plans, atomic secret-bearing config writes, and the existing init confirmation and Compose/bootstrap lifecycle. The generated project config must exist before database bootstrap because bootstrap consumes the resolved Odoo runtime configuration.

## Goals / Non-Goals

**Goals:**

- Make init produce a complete project `.odcli/odoo.conf` with or without a source config.
- Persist deterministic addon-root and server-wide-module settings without secrets.
- Provide one reusable dependency-input discovery path for primary and managed environments.
- Add an SDK-first primary-checkout venv preparation command with honest dry-run output.
- Preserve current managed-environment ownership, lock, pinning, and retained-lock behavior.

**Non-Goals:**

- Installing Odoo, selecting Binta-specific directory layouts, or embedding bootstrap-skill policy in this repository.
- Adding a package resolver, lockfile format, process runner, config parser, or venv abstraction.
- Changing `run` or `shell` to synchronize dependencies automatically.
- Persisting database passwords or other secrets in `project.toml`.

## Decisions

### Extend `ProjectConfig` and the existing init transaction

Add tuple fields for `addon_roots` and `server_wide_modules`. Addon roots are repository-relative, validated with the existing project-path containment pattern, deduplicated by canonical path while retaining first occurrence, and serialized in order. Server-wide modules retain first occurrence and reject blank/comma-containing names so config serialization remains unambiguous.

Omission during re-init means inherit the stored tuple; an explicit tuple, including empty, replaces it. The init plan treats `project.toml` and `.odcli/odoo.conf` as one resolved artifact set under the existing manifest lock and confirmation decision. Config rendering is planned before Compose/bootstrap follow-up steps and atomic writes occur in that order.

Alternative: encode addon roots in `addon_repositories` or `default_run_args`. Rejected because repositories are checkout ownership inputs and raw run args cannot safely drive dependency discovery or stable config generation.

### Extend the existing config generator with explicit overlays

`render_config()` receives resolved addon paths and server-wide modules in addition to its current database/network/runtime values. It starts from the optional source config, rebases source-owned paths as today, then overlays project-owned values. Init resolves secrets and connection details through the current dotenv/project/Compose configuration path and passes them only to the mode-`0600` config writer; they never enter the manifest or public projection.

Alternative: build a second init-only template. Rejected because it would diverge from environment config adaptation and duplicate preservation, rebasing, atomic-write, and permission rules.

### Extract dependency-input discovery, not dependency execution

A focused internal discovery function accepts a `ProjectConfig` plus an execution root. It walks each addon root deterministically, identifies module directories by `__manifest__.py`, considers module and immediate-parent `requirements.txt`, then the Odoo-bin sibling and explicit inputs. It resolves each candidate, validates explicit files, deduplicates canonical paths, and returns an immutable ordered tuple suitable for public-plan projection.

Environment checkout and sync call that function after rebasing repository-local paths to their worktree. The current uv argv construction, ownership-specific install/sync choice, lock serialization, and failure handling remain the single execution implementation.

Alternative: have each caller discover requirements independently. Rejected because identical project state could then produce different dependency sets and drift behavior.

### Add a project preparation SDK operation beside init

Expose a public `prepare_project_venv_command()`/convenience operation using the same `Command` and prepared-step boundary as init and environment sync. Relative venv paths resolve from the project root; absolute paths remain explicit. Planning determines whether `uv venv` is needed, the platform-specific interpreter path, the ordered inputs, compile/install argv, and the final manifest update. Execution validates the target, serializes uv writes on the canonical venv path, runs prepared steps, and writes the updated immutable `ProjectConfig` only after success.

The operation uses install semantics for the explicitly selected primary venv; managed owned environments retain exact sync semantics. An empty input set still validates or creates the venv but skips compile/install.

Alternative: model the primary checkout as a `DevelopmentEnvironment` catalog row. Rejected because the primary checkout is already represented by the project manifest and introducing ownership/catalog lifecycle would broaden cleanup and identity semantics beyond the request.

### Keep CLI callbacks transport-only

Register repeatable init flags and top-level `prepare-venv` through existing CLI registration. Callbacks resolve Click-only concerns, request the public SDK command, and use the current preview/confirmation/execution/output path. Characterization and output inventories gain the new leaf and dry-run metadata; no CLI-local filesystem walk or uv argv is introduced.

Alternative: call uv directly in the callback. Rejected by the SDK-first and shared-process contracts and would make dry-run incomplete.

### Ponytail full subsystem audit

No new subsystem is necessary. The required behaviors fit the existing `ProjectConfig`, init command, generated-config renderer, environment dependency sync, process executor, and output envelope. The design keeps one new focused discovery helper and one public operation; separate config, runner, resolver, catalog, and venv layers are removed from consideration.

## Risks / Trade-offs

- [Recursive discovery can traverse large addon trees] → restrict traversal to configured contained roots, use deterministic filesystem walking, and inspect only `__manifest__.py`/`requirements.txt` names.
- [Parent requirements can be shared by many modules] → canonical-path first-occurrence deduplication ensures each file is compiled once.
- [Init can fail between two artifact writes] → hold the manifest lock, pre-render and validate both targets, write atomically, and retain the existing precise retry/confirmation semantics; bootstrap never starts until both are valid.
- [An existing external venv may not match the requested selector] → validate its interpreter and treat the existing venv path as authoritative; the selector is used only for creation.
- [Source config values may conflict with project values] → document and test that resolved project/runtime overlays win while unrelated source settings are preserved.
- [Cross-platform interpreter layout differs] → use the existing environment executable helper or a shared platform-aware equivalent rather than hardcoding only `bin/python`.

## Migration Plan

The new manifest fields are optional, so existing manifests load unchanged. Existing projects acquire fields only through explicit init input and acquire the project interpreter after successful `prepare-venv`. Managed environment calls switch to the shared discovery result without catalog migration. Rollback can ignore the optional fields and stop exposing the command; generated config and existing venvs remain ordinary project resources and are not deleted automatically.

## Open Questions

None. Binta-specific concrete paths remain wrapper inputs, as required by scope.
