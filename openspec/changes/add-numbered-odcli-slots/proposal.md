## Why

PR #140 already proves isolated exact-SHA `odcli-N` and `odcli-fix-ISSUE` launchers, but its public `odcli slot` surface is broader than the operator workflow requires and does not bind a numbered tool to the independent project checkout and external resources needed for safe concurrent Odoo work. The revision must keep the proven one-engine isolation while moving numbered management into bounded service tooling and reusing the safe parts of GitLab `binta-test-repo/scripts/agent_checkout.py`.

## What Changes

- **BREAKING** Remove the public `odcli slot install/list/remove` group and all public help, output-mode, inventory, and real-Odoo matrix entries for it.
- Keep one package-owned alternate-tool lifecycle for numbered and hot-fix launchers: exact-SHA provenance, root selection, manifest, shim, locking, inspection, and selective cleanup remain shared.
- Add one private SDK service-tool path that atomically selects the smallest free positive number under a global allocation lock, treating any existing launcher, state root, manager metadata, or linked checkout as occupied and never overwriting orphaned data.
- Bind each numbered installation to one independently cloned project checkout and record deterministic, verified linkage without changing real `HOME`.
- Extract only reusable checkout create/inspect/remove mechanics from GitLab `agent_checkout.py` into that private SDK path. GitLab retains environment configuration for the reference checkout, backup, port range, database naming, private `.env`, Odoo binaries, and Binta-specific scenarios.
- Keep `seed_odoo.py`, database diagnostics, administrator-password setup, persistent-stand policy, and secrets in GitLab; do not create a generic checkout framework or public SDK API.
- Preserve canonical-only legacy migration, fail-closed provenance, alternate self-update guards, cross-root isolation, and selective removal.
- Replace PR #140's public-command tests/docs with private-tool, allocation, linked-checkout, cross-resource, cleanup, packaging, and architecture acceptance.
- Deliver SDK changes in PR #140 and GitLab-owned wrapper/configuration changes in a separate GitLab MR; neither repository silently owns the other's configuration.

## Capabilities

### New Capabilities

- `numbered-odcli-slots`: Private exact-SHA numbered-tool allocation, one shared numbered/hot-fix lifecycle, linked independent checkout management, cross-root/resource safety, and selective cleanup.

### Modified Capabilities

- `development-environment`: Selected global roots remain isolated while numbered tooling binds each root to a separately configured checkout.
- `self-update`: Alternate launchers remain unable to self-update, and numbered remediation points to service tooling rather than a public CLI group.
- `cli-odcli`: The public CLI explicitly has no numbered-installation management group; alternate launchers expose only the ordinary supported surface.
- `bug-report`: Existing hot-fix management continues through the same shared lifecycle without inheriting numbered checkout policy.

## Impact

In the SDK repository, the change retains and narrows `internal/alternate_tool.py`, adds one private package-owned checkout coordinator, updates the autonomous-work skill wrapper, removes `commands/slot.py` and its registration/output inventory, adjusts self-update guidance, and replaces affected tests/docs. In GitLab `binta-test-repo`, `scripts/agent_checkout.py` becomes a thin environment/configuration wrapper over the private SDK coordinator while Binta-specific seed, diagnostics, password, backup, database, filestore, and port policy stay local. No new dependency, daemon, registry database, public console command, or secret-in-Git flow is introduced.
