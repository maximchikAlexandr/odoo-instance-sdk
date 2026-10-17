## Context

The approved base is updated `main` at merge commit `884aa66c711078879f9e540256f3896eaf0b6a39`, which incorporates PR #152 and provides `prepare_venv_command()` / `prepare_venv()`, trusted provenance, staged lock publication, deterministic dependency discovery, platform-specific interpreter selection, and `odcli prepare-venv`. It also provides `init_project_command()`, whose captured order is manifest/config persistence followed by Compose startup and Odoo `tmp` bootstrap.

Today the two commands cannot simply be called one after another inside the init callback. `prepare_venv_command()` loads an existing manifest while building its immutable plan, but a fresh init has no manifest yet; building it later during execution would introduce unplanned process/action effects. Existing-manifest handling also exits early for an external-project no-op, and Compose bootstrap captures its Python prefix before a prepared interpreter exists.

The change therefore needs one pre-captured composite command while retaining the standalone preparation operation as the single owner of venv behavior. Repository rules require every spawned process to remain in `internal/proc`, every public plan to stay frozen and redacted, and the CLI callback to remain transport-only.

## Goals / Non-Goals

**Goals:**

- Make `init --venv PATH` an opt-in composition of existing initialization and primary-venv preparation.
- Capture all manifest, venv, dependency, persistence, Compose, and bootstrap steps before execution in their actual order.
- Make the concrete venv interpreter the persisted project Python and the Python used by later Odoo bootstrap.
- Define fresh, repeat, repair, dry-run, failure, and recovery states without weakening the standalone operation.
- Preserve current behavior byte-for-byte at the public contract level when no venv is requested.

**Non-Goals:**

- Automatically create a venv when `--venv` is omitted or infer a default venv path.
- Replace or hide `prepare-venv`, recreate an existing venv when its source selector changes, or add a delete/rollback operation for venv contents.
- Redesign init completeness, generated config, Compose, dependency discovery, uv commands, provenance, or lock ownership.
- Add a venv resource, catalog row, background job, plugin, dependency, or new test framework.

## Decisions

### Compose one captured init command from the existing preparation owner

Factor the minimum private planning/execution seam out of `prepare_venv_command()`. It accepts an already-resolved `ProjectConfig`, project identity, requested Python selector, and venv path, and returns the same prepared steps plus the same locked persistence behavior. The standalone public command loads and snapshots the existing manifest exactly as today, then uses that seam. `init_project_command()` uses it with the effective config known at planning time, without loading a not-yet-created manifest.

The public init SDK gains only an optional venv input; omitted input does not construct preparation state. CLI adds only `--venv PATH`. No second implementation or public composition type is introduced.

Alternative rejected: execute `prepare_venv_command()` from inside init after writing the manifest. That would build process argv and action steps during execution, so dry-run and the public plan would not describe the real effects.

### Resolve preparation inputs before mutation and execute them in lifecycle order

When venv is requested, command construction SHALL resolve and validate the effective Python selection, canonical venv/interpreter path, current venv existence, dependency inputs, staged lock path, and all prepared actions. The effective Python comes from the existing init resolution path (explicit `--python`, imported/discovered value, or a safely preserved existing value). If no effective Python exists, planning fails before any write and tells the caller to provide one; no new implicit interpreter default is added.

The plan order is:

1. existing read-only remote-name resolution when applicable;
2. write or verify the requested manifest/config inputs;
3. create-if-missing and validate the venv;
4. compile/sync dependencies when inputs are non-empty;
5. stage and publish venv origin, concrete interpreter, and dependency lock through the existing preparation transaction;
6. start/verify Compose when selected;
7. bootstrap and verify Odoo `tmp` through the concrete prepared interpreter.

The bootstrap prefix is captured from the planned concrete venv interpreter, not from the pre-preparation manifest value. Dry-run exposes this entire redacted sequence and performs none of it.

Alternative rejected: prepare before writing the manifest. Dependency discovery and persistence are defined by project inputs, so this would make preparation operate on stale or absent configuration and violate the approved lifecycle order.

### Treat `--python` as a creation selector and the venv interpreter as runtime truth

For a missing target, the resolved init Python is passed unchanged to `uv venv --python`. For an existing valid target, the established `prepare-venv` contract refreshes it without recreation; a different `--python` therefore does not replace that environment's Python. The validated platform-specific interpreter inside the selected venv is the value persisted and passed to bootstrap. Users who need a different Python runtime select a missing venv path or deliberately replace the environment outside this operation, then use init or standalone recovery.

On repeated init, the existing trusted-origin rule may preserve the concrete interpreter for the same original request. An explicit changed Python request remains a manifest change and follows the existing confirmation/`--yes` rules before the existing venv is refreshed. Provenance records both requested selector and actual interpreter, so output does not claim that an existing environment was recreated.

Alternative rejected: silently delete/recreate an existing venv when the selector changes. It would be destructive, expand rollback scope, and contradict standalone refresh semantics.

### Preserve successful boundaries and expose honest partial state

The manifest/config write is the first durable phase. Preparation retains its current atomic persistence boundary: origin, manifest interpreter, and dependency lock are published only after create/validate/compile/sync succeeds, and a publication failure restores their previous bytes. Process effects inside a venv are not claimed to be reversible.

- A failure before preparation publication leaves the initialized manifest with its resolved source Python and no new trusted origin/interpreter/lock publication. A newly created or partially refreshed venv may remain and is recoverable by repeating `init --venv` or running `prepare-venv`.
- A successful preparation followed by Compose/bootstrap failure keeps the concrete interpreter, trusted origin, and published lock. An identical retry verifies/refreshes preparation, then resumes Compose/bootstrap.
- A damaged existing venv fails validation before compile/sync or persistence. The manifest is not advanced to that interpreter.
- Empty dependency input sets still create/validate and persist the venv, with no compile/sync/lock-publish steps.
- A failure before the manifest phase leaves no init artifacts, as today.

This is phase-consistent recovery rather than a global transaction. It preserves useful successful work and never reports bootstrap success early.

Alternative rejected: roll back or delete the venv on a later failure. Existing environments may be caller-owned, dependency installation is not generally reversible, and deletion would weaken path and data safety.

### Route existing-manifest outcomes through the composite command when opted in

Without `--venv`, existing no-op/repair/Compose-resume branches remain unchanged. With `--venv`, an identical manifest no longer returns before command construction: init captures a read-only manifest verification action, preparation refresh, and any required Compose/bootstrap steps. Generated-config repair remains before preparation. Non-identical manifests retain the current prompt/`--yes`/headless refusal rules.

The Rich result distinguishes initialized, repaired, refreshed, and resumed outcomes; JSON and TOON retain the established envelope and add only the venv result/plan data required by the opt-in request. No callback executes uv or builds argv.

### Ponytail full gate

| Proposed subsystem or surface | Necessity | Existing alternative | Decision |
| --- | --- | --- | --- |
| Reusable primary-venv planner/executor seam | Required to pre-capture fresh-init effects while retaining one behavior owner | Calling the public standalone command during execution would build an unplanned command | Keep one private seam extracted from current code |
| New `init --venv` input | Required opt-in contract | Unconditional/default venv creation changes existing init | Keep one optional path only |
| Composite/global transaction | Not required; init and preparation already have meaningful durable boundaries | Existing manifest phase plus preparation persistence rollback | Do not add |
| Venv service/resource/catalog | Not required | Existing public functions, locks, provenance, and process steps | Do not add |
| Automatic recreate on changed selector | Not required and destructive | Existing refresh semantics plus explicit recovery/replacement | Do not add |
| New dependency discovery or uv runner | Not required | Existing shared discovery and `internal/proc` steps | Do not add |
| New E2E framework | Not required | Focused script in the assigned test repository plus existing GitHub tests | Do not add |

## Risks / Trade-offs

- [A failed uv operation can leave a created or modified venv] → Document the durable boundary, never advance manifest/origin/lock to an unvalidated result, and retain both retry and standalone recovery paths.
- [A changed `--python` cannot change an already-created venv's runtime] → Report requested and concrete interpreter separately and document that a new/missing venv is required for runtime replacement.
- [Fresh-init planning cannot compare a future manifest file] → Bind preparation to the same captured effective config and project identity, then verify the manifest written by the preceding init action before publication.
- [Existing-manifest early returns could bypass preparation] → Gate those early returns on absence of `--venv` and cover external, Compose, no-op, repair, overwrite, and dry-run branches.
- [Bootstrap could capture the source selector] → Pass the already planned concrete venv interpreter into `_compose_followup_steps()` and assert plan/execution identity in tests.
- [Windows and POSIX interpreter layouts differ] → Reuse `_owned_python_executable()` and extend the existing Windows preparation job and platform-projection tests.
- [Combined output could leak selectors or paths containing secrets] → Keep one redacted public projection and existing sensitive-argv handling; machine envelopes expose only established sanitized fields.

## Migration Plan

The SDK parameter and CLI option are additive and default to `None`, so existing calls and manifests need no migration. Documentation changes present integrated init as the primary agent bootstrap while keeping `prepare-venv` as refresh/recovery. Rollback removes the optional composition but leaves ordinary venv, manifest interpreter, provenance, and lock artifacts consumable by the already-public standalone operation.

## Open Questions

None. The test-repository URL is supplied by the Multica project description at execution time and is not embedded in public Git artifacts.
