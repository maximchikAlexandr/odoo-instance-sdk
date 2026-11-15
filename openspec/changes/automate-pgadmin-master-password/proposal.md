# Proposal

## Why

Opening the shared local pgAdmin still stops on pgAdmin's desktop "Set Master Password" dialog, so the imported Odoo server is not usable. GitHub #147 requires OdCLI to generate that master password, keep it in a private user-level file, and unlock the pinned desktop-mode container through a supported pgAdmin interface without discarding an existing profile.

## What Changes

- Generate a pgAdmin master password on first use and store it in an owner-only `~/.odcli/pgadmin/master.env` file that is never mounted, logged, or placed in Docker metadata.
- Reuse that file across opens and container recreates. Reject missing, corrupt, or insecure files without resetting pgAdmin or deleting saved server passwords.
- Keep `MASTER_PASSWORD_REQUIRED` enabled. Disable only OS secret storage, which the container cannot provide, and unlock desktop mode with pgAdmin's existing `POST /browser/master_password` on loopback.
- After unlock, prove the selected database accepts a read-only `SELECT 1` through the existing passfile. Do not treat HTTP 200 or an imported server label as authentication.
- Restore the dashboard Open pgAdmin action so an eligible environment opens the returned loopback URL. Leave query-tool deep links unimplemented: pgAdmin does not document a stable browser deep link for this image.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `local-pgadmin`: Replace the deferred master-password requirement with automatic provisioning, reuse, supported unlock, `SELECT 1` proof, and fail-closed credential handling.
- `web-sdk-codegen`: Show the typed Open pgAdmin action from `pgadmin.state` now that the credential flow exists.

## Impact

- pgAdmin file preparation, container environment matching, reconciliation command snapshot, and the opt-in pinned-image smoke test.
- Dashboard environment cards and their frontend tests. The public open result stays `state` plus a credential-free loopback URL.
- No new dependency. `MASTER_PASSWORD_HOOK` stays unused because the pinned pgAdmin documents it for server-mode external authentication only.
