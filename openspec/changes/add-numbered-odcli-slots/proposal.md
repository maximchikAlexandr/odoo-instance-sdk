## Why

PR #140 already proves isolated exact-SHA `odcli-N` and `odcli-fix-ISSUE` launchers, but its public `odcli slot` surface is broader than the operator workflow requires and does not bind a numbered tool to the independent project checkout and external resources needed for safe concurrent Odoo work. The revision must keep the proven one-engine isolation while moving numbered management into bounded service tooling and reusing the safe parts of GitLab `binta-test-repo/scripts/agent_checkout.py`.

## What Changes

- **BREAKING** Remove the public `odcli slot install/list/remove` group and all public help, output-mode, inventory, and real-Odoo matrix entries for it.
- Keep one package-owned alternate-tool lifecycle for numbered and hot-fix launchers: exact-SHA provenance, root selection, manifest, shim, locking, inspection, and selective cleanup remain shared.
- Add one private SDK service-tool path that atomically selects the smallest free positive number under a global allocation lock, treats any existing launcher, state root, manager metadata, or linked checkout as occupied, and owns only reservation plus external-checkout linkage around existing SDK lifecycles.
- Bind each numbered installation to one independently cloned project checkout and one SDK-owned Python environment created from an audited hash lock; record deterministic, verified linkage without changing real `HOME`.
- Extract only independent clone/origin/marker and safe external-checkout deletion from GitLab `agent_checkout.py`. Send the caller-owned clone through existing environment `adopt_command()`/`adopt()` for config, COPY database/backup, port, venv and catalogue creation, and through existing environment `remove()` for their cleanup. GitLab retains concrete project/environment inputs and Binta-specific scenarios.
- Keep `seed_odoo.py`, database diagnostics, administrator-password setup, persistent-stand policy, and secrets in GitLab; do not create a generic checkout framework or public SDK API.
- Preserve canonical-only legacy migration, fail-closed provenance, alternate self-update guards, cross-root isolation, and selective removal.
- Replace PR #140's public-command tests/docs with private-tool, allocation, linked-checkout, cross-resource, cleanup, packaging, and architecture acceptance.
- Deliver SDK changes in PR #140 and GitLab-owned wrapper/configuration changes in a separate GitLab MR; neither repository silently owns the other's configuration.

## Capabilities

### New Capabilities

- `numbered-odcli-slots`: Private exact-SHA numbered-tool allocation, one shared numbered/hot-fix lifecycle, linked independent checkout plus isolated Python-environment management, cross-root/resource safety, and selective cleanup.

### Modified Capabilities

- `development-environment`: Selected global roots remain isolated while numbered tooling binds each root to a separately configured checkout and SDK-owned Python environment.
- `self-update`: Alternate launchers remain unable to self-update, and numbered remediation points to service tooling rather than a public CLI group.
- `cli-odcli`: The public CLI explicitly has no numbered-installation management group; alternate launchers expose only the ordinary supported surface.
- `bug-report`: Existing hot-fix management continues through the same shared lifecycle without inheriting numbered checkout policy.

## Impact

In the SDK repository, the change retains and narrows `internal/alternate_tool.py`, adds one private allocation/external-checkout coordinator that delegates all environment mutation to existing `adopt`/`remove`, updates the autonomous-work skill wrapper, removes `commands/slot.py` and its registration/output inventory, adjusts self-update guidance, and replaces affected tests/docs. In GitLab `binta-test-repo`, `scripts/agent_checkout.py` becomes a thin input wrapper while Binta-specific seed, diagnostics, password, project values and scenarios stay local. No new dependency, environment framework, config rewriter, restore/drop path, daemon, registry database, public console command, or secret-in-Git flow is introduced.
