# Proposal

## Why

Creating an Odoo 19 addon by hand repeats the same manifest, package, and security boilerplate, and a second template engine would drift from Odoo's own scaffold renderer. Issue #38 needs one configurable `odcli scaffold` path that composes a known addon and lets native Odoo 19 render it.

## What Changes

- Add `odcli scaffold MODULE [DEST]` and `OdooClient.scaffolds.create_command()` / `create()`.
- Add a user config file at the canonical OdCLI config root and `odcli config path`. A missing file means built-in defaults.
- Accept the same `[scaffold]` schema in that user file and in `.odcli/project.toml`.
- Merge built-in defaults, user config, project config, and CLI overrides with shallow manifest replacement and per-layer include/exclude/omit.
- Materialize one temporary custom template and invoke the configured Odoo 19 executable as `scaffold --template <path>`.
- Preview and execution share one immutable command plan. Dry-run creates no temporary or destination files. Failure removes a partial addon and the temporary template.
- Register both leaves in `PUBLIC_LEAF_CASES` and document the CLI and SDK.

## Capabilities

### New Capabilities

- `addon-scaffold`: Configurable Odoo 19 addon creation through the native scaffold renderer, shared user/project config, and the inspectable command contract.

### Modified Capabilities

- `cli-odcli`: The public command surface gains `scaffold` and `config path`.

## Impact

- Public SDK: `ScaffoldResource`, `UserConfig`, `ScaffoldConfig`, `ScaffoldResult`, and `ProjectConfig.scaffold`.
- CLI leaves, Rich/JSON/TOON output, README, and the test-owned command matrix.
- User config lives at `~/.odcli/config/config.toml` (or `ODCLI_USER_ROOT`), not a new platformdirs location. The repository already forbids new artifacts under the legacy platformdirs roots.
- No second renderer and no plugin or remote-template mechanism.
