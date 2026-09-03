## Why

Some approved remote Odoo test instances are intentionally available only over HTTP. The current project database preparation preflight rejects them before backup work, even when the operator has explicitly pinned the exact origin.

## What Changes

- Allow a non-loopback HTTP test-instance origin when it has an exact external origin approval.
- Continue warning that the master password is transmitted in cleartext.
- Preserve exact-origin pinning, secret redaction, URL validation, and all local destructive-operation guards.
- Remove the obsolete password-transport rejection helper and update documentation and tests.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-database-preparation`: permit externally pinned HTTP test-instance origins while retaining explicit trust approval and cleartext warnings.

## Impact

- Affects test-instance origin trust validation and project database refresh preflight.
- Changes the security posture for explicitly approved HTTP origins: credentials can cross the network unencrypted, with a runtime warning.
- Does not relax origin pinning, local-target restrictions, redaction, or direct database-operation safeguards.
