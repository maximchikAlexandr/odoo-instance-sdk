## Why

OdCLI already creates and independently repairs a secure project-owned `.odcli/odoo.conf` for Compose initialization, and managed environments already compile and install dependencies through uv. The remaining gaps are narrower: init cannot store or render ordered addon roots and server-wide modules, dependency discovery is incomplete and duplicated between managed-environment paths, and the primary checkout has no equivalent venv-preparation operation.

## What Changes

- Add ordered project-relative addon roots and server-wide modules to `ProjectConfig` and `odcli init`, preserving stored values when options are omitted and replacing a list when new values are supplied.
- Extend the existing generated-config renderer and independent repair check so the stored addon/module values appear in `.odcli/odoo.conf`; retain current source preservation, secret handling, atomic write, confirmation, and bootstrap ordering.
- Extend the existing dependency-input helpers into one deterministic recursive discovery path shared by primary-checkout preparation, `env checkout --create-venv`, and `env sync`.
- Add one public SDK operation and `odcli prepare-venv` command that reuse the repository's existing uv planning, process, locking, and manifest-update patterns for the primary checkout.
- Add focused tests, canonical CLI inventory updates, documentation, and one local E2E workflow for the remaining real-process gap.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-init`: Store ordered addon roots and server-wide modules and include them in the already-generated project runtime config.
- `development-environment`: Share complete dependency discovery with a new primary-checkout venv preparation operation.
- `cli-odcli`: Expose the init inputs and SDK-first `prepare-venv` command through existing output and dry-run contracts.

## Impact

The change affects optional `ProjectConfig` fields, init option transport, existing generated-config rendering/repair, existing environment dependency planning, one narrow primary-venv SDK surface, CLI registration/inventories, and their tests and documentation. It adds no dependency, runner, resolver, settings subsystem, catalog model, or CI framework. The broad changes already present on current `main`—Git workflows, build packaging, and dynamic process environments—remain outside this change.
