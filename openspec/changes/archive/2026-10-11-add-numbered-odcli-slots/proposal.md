## Why

PR #140 already proves isolated exact-SHA `odcli-N` and `odcli-fix-ISSUE` launchers and has removed the public numbered-management surface. Its remaining architecture problem is that developer-only numbered management lives inside `.agents/skills/odcli-autonomous-work`, which is the instruction surface for agents using OdCLI in ordinary project work. The revision must keep the proven one-engine isolation while separating user and developer instructions, moving executable helpers outside every skill directory, and deleting the obsolete `~/.odcli/fix-tools` narrative instead of preserving a legacy registry concept.

## What Changes

- **BREAKING** Remove the public numbered-management group and all public help, output-mode, inventory, and real-Odoo matrix entries for it.
- Keep one package-owned alternate-tool lifecycle for numbered and hot-fix launchers: exact-SHA provenance, root selection, manifest, shim, locking, inspection, and selective cleanup remain shared.
- Keep one private SDK service-tool path that atomically selects the smallest free positive number under a global allocation lock, treats any existing launcher, state root, manager metadata, or linked checkout as occupied, and owns only reservation plus external-checkout linkage around existing SDK lifecycles.
- Bind each numbered installation to one independently cloned project checkout and one SDK-owned Python environment created from an audited hash lock; record deterministic, verified linkage without changing real `HOME`.
- Extract only independent clone/origin/marker and safe external-checkout deletion from GitLab `agent_checkout.py`. Send the caller-owned clone through existing environment `adopt_command()`/`adopt()` for config, COPY database/backup, port, venv and catalogue creation, and through existing environment `remove()` for their cleanup. GitLab retains concrete project/environment inputs and Binta-specific scenarios.
- Keep `seed_odoo.py`, database diagnostics, administrator-password setup, persistent-stand policy, and secrets in GitLab; do not create a generic checkout framework or public SDK API.
- Preserve canonical-only legacy migration, fail-closed provenance, alternate self-update guards, cross-root isolation, and selective removal.
- Keep `.agents/skills/odcli-autonomous-work` limited to agents using OdCLI and hot-fix unblocking; add a separate `.agents/skills/odcli-developer-work` instruction surface for agents developing OdCLI and managing numbered installations.
- Move every Python executable helper used by either skill into one repository-owned top-level tooling area outside `.agents/skills/**`; skill directories contain instructions and non-executable prompt material only.
- Remove every `~/.odcli/fix-tools` reference and any registry discovery, adoption, migration, compatibility, or cleanup support; isolated hot-fix roots remain supported without a legacy-registry story.
- Refocus private-tool, skill-boundary, allocation, linked-checkout, cross-resource, cleanup, packaging, and architecture acceptance on the separated instruction and executable-code ownership.
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

In the SDK repository, the change retains `internal/alternate_tool.py` and the private allocation/external-checkout coordinator, keeps `commands/slot.py` absent, adds one developer-only skill, narrows the user skill, relocates Python helpers to a repository-owned top-level tooling area, removes the obsolete fix-registry narrative, updates self-update guidance, and refocuses affected tests/docs. In GitLab `binta-test-repo`, the existing thin wrapper and Binta-specific ownership stay unchanged unless path references to SDK tooling require an exact revision update. No new dependency, environment framework, config rewriter, restore/drop path, daemon, registry database, public console command, or secret-in-Git flow is introduced.
