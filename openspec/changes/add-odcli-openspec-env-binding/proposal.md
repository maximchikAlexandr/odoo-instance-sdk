# Proposal

## Why

Agents and humans cannot see which OpenSpec change each OdCLI environment is implementing. Checkout rows already share one inventory, but OpenSpec progress stays trapped in whichever worktree happens to be current, so a complete planning folder can be mistaken for finished implementation.

## What Changes

- Add an independently versioned `odcli-openspec` distribution in the existing uv workspace. It depends on a public `odoo-instance-sdk` range and does not add Node or OpenSpec to the core package.
- Bind one OpenSpec change to an environment during `odcli env checkout --openspec-change CHANGE`, or later with `odcli-openspec env bind` / `env unbind`.
- Contribute one compact OpenSpec summary per checkout row through the existing environment-facts entry point. Planning completion and implementation-task completion stay separate.
- Record a versioned research note for the verified OpenSpec 1.13.2 JSON CLI before the production slice. Optional integrations are listed only as `later` or `reject`.
- Allow environment-fact detail values to keep JSON numbers, booleans, and null so task counts survive Rich, JSON, and TOON.

## Capabilities

### New Capabilities

- `odcli-openspec`: standalone distribution, change binding, target-worktree progress, and the typed SDK client.

### Modified Capabilities

- `cli-odcli`: `env checkout` accepts `--openspec-change` and fails before mutation when that extension package is missing. Environment-fact details preserve typed scalar values.

## Impact

- New workspace member `packages/odcli-openspec`, plus one root member-list change. `packages/odcli-multica` and `src/odoo_instance_sdk` stay in place.
- Core checkout gains one optional flag and a public command-continuation seam. The extension does not import `odoo_instance_sdk.internal`.
- Binding state lives in an extension-owned file under the OdCLI user root, keyed by environment id. Catalog rows and OpenSpec artifacts are not copied or deleted by unbind.
- CI gains an isolated wheel job for this distribution. Core-only installation still has no extension code.
