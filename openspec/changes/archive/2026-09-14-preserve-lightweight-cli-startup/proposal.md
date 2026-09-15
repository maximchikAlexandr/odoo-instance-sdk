## Why

Static CLI discovery currently imports the complete SDK operation graph: on current `main`, importing `odoo_instance_sdk.cli` loads both `httpx` and `odoo_instance_sdk.resources.monitor`, while `odcli --version` is unavailable. This prerequisite change establishes a lightweight metadata path before the later architecture work in GitHub #40, #45, #35, and #33 can alter the same boundaries.

## What Changes

- Add a root `odcli --version` option that reports the installed `odoo-instance-sdk` distribution version and works without project context.
- Preserve the complete root help command/option surface while preventing help and version startup from loading selected operation-only modules.
- Resolve the existing root-level SDK exports lazily without changing `__all__`, object identity, import syntax, or unknown-attribute behavior.
- Add deterministic import-boundary regression coverage and retain a reproducible local `importtime` before/after record as evidence, not as a CI timing threshold.
- Keep the current Click architecture and dependency set; no framework migration, plugin registry, generic command loader, or third-party lazy-import helper is introduced.

## Capabilities

### New Capabilities

- `sdk-package-imports`: Defines compatibility and lazy-resolution behavior for the package root's existing public exports.

### Modified Capabilities

- `cli-odcli`: Adds the version contract and lightweight metadata-only startup requirement while preserving the current help surface.
- `packaging`: Makes installed distribution metadata the single source of the version displayed by the CLI without adding dependencies.

## Impact

The change is limited to package-root export resolution, CLI import placement/registration, focused import and compatibility tests, and developer documentation for the measurement record. Public SDK names and CLI commands remain compatible; the project keeps Click and standard-library `importlib` mechanisms and adds no runtime dependency.
