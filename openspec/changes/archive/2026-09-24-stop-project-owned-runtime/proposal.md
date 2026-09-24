## Why

`odcli run --detach` can persist a proven-owned runtime for an initialized project's main checkout, but the matching `odcli stop` command rejects project context before reaching the existing identity checks. This leaves the supported project-owned detached lifecycle incomplete and forces operators to stop the process manually.

## What Changes

- Allow the existing top-level `odcli stop` command to operate on either the resolved initialized project or a registered development environment.
- Generalize the existing safe stop primitive to re-read, validate, terminate, and conditionally clear the persisted runtime for its exact `project | environment` owner.
- Preserve fail-closed PID/create-time, executable, argv, cwd, config, and process-group checks, including idempotent handling of absent or already-vanished owned processes.
- Report owner-neutral project/environment identity in stop results and update help text accordingly.
- Add one parameterized regression matrix covering both owner kinds without adding a command, registry, migration, or supervisor.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-odcli`: Expand the existing top-level stop contract and output from environment-only selection to the shared initialized-project or environment context.
- `instance-runtime-binding`: Apply the existing safe out-of-process stop and conditional runtime-row cleanup to both persisted runtime owner kinds.

## Impact

- CLI callback/help and bounded Rich/JSON/TOON result projection in `src/odoo_instance_sdk/commands/cli_parts/callbacks.py`.
- Runtime identity re-read and conditional cleanup in `src/odoo_instance_sdk/resources/instance/` plus the existing catalogue owner-neutral runtime APIs.
- Parameterized unit coverage in `tests/unit/test_runtime_stop.py` and CLI output/help contract coverage.
- No storage migration, dependency, public command, or parallel lifecycle model is introduced.
