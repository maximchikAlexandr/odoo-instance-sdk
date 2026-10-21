## Why
COPY/adoption can reconstruct a source runtime whose strict identity differs from the running primary Odoo. The fallback helper currently binds the primary HTTP port and fails, forcing users to stop a healthy instance.

## What Changes
- Capture a separate free loopback port for a spawned auxiliary Database Manager with the existing allocator.
- Route session HTTP and readiness to that helper; preserve strict runtime identity and owned-process cleanup.
- Preserve reuse of an exactly proven existing runtime and the original project configuration.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `database-restore`: independent auxiliary endpoint while the primary remains running.
- `development-environment`: COPY/adoption works alongside an active primary.

## Impact
Internal auxiliary-session and Database Manager transport routing only; no CLI flags, catalogue migrations or process-supervisor changes.
