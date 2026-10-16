## Why

Projects initialized by OdCLI can persist discovery metadata, but they cannot yet produce the complete project `odoo.conf` needed before database bootstrap when no source config exists. Python dependency preparation is also coupled to managed checkout environments, leaving the primary checkout without an equivalent reusable SDK and CLI workflow.

## What Changes

- Extend `odcli init` and its public SDK operation to accept ordered project-relative addon roots and server-wide modules, persist them in `.odcli/project.toml`, and generate `.odcli/odoo.conf` before bootstrap even without a source config.
- Preserve applicable source-config settings while resolving database, network, runtime-path, and secret values through the existing project/environment configuration mechanisms.
- Make repeated init reuse stored addon/module settings and prepared project resources; explicit values replace the corresponding stored values and participate in the existing confirmed-overwrite and dry-run contracts.
- Add a public SDK operation and `odcli prepare-venv` command that create or refresh the primary checkout virtual environment, jointly resolve discovered and explicit requirement files through the existing uv execution boundary, and persist the selected interpreter.
- Reuse one dependency-input discovery algorithm for primary-checkout preparation, `env checkout --create-venv`, and `env sync`, including recursive addon-module discovery, Odoo-core requirements, canonical-path deduplication, deterministic planning output, and typed errors for missing explicit inputs.
- Extend focused unit, CLI characterization, integration, and CI coverage for the new manifest, config-generation, dependency-discovery, dry-run, overwrite, and environment-reuse contracts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-init`: Persist ordered addon roots and server-wide modules and generate a complete project config as part of initial and repeated initialization.
- `development-environment`: Prepare the primary checkout virtual environment and share dependency-input discovery and uv synchronization with managed environments.
- `cli-odcli`: Expose SDK-first `init` options and the new `prepare-venv` command with the established planning, dry-run, output, confirmation, and process contracts.

## Impact

The change affects the public project-init and environment/dependency SDK surface, Click command registration and characterization, `ProjectConfig` TOML serialization, generated Odoo configuration, uv command planning/execution, managed-environment checkout and sync, and their unit/integration tests. It adds no third-party dependency or parallel runner and keeps secrets out of the repository manifest.
