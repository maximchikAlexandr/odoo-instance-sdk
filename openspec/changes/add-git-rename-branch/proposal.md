## Why

Automatically prepared checkouts can reach adoption or their first commit while still carrying a generated branch name, forcing agents to rename branches manually without OdCLI's ticket-allocation collision checks. A public, inspectable rename operation is needed now so callers can select the next Jira-style branch name safely while preserving the existing checkout allocation contract.

## What Changes

- Add `odcli git rename-branch TICKET` using existing project/environment selection, bounded Rich/JSON/TOON output, and dry-run behavior.
- Add the required public SDK command builder and convenience operation, returning the previous and selected branch names through the repository's existing immutable command pattern.
- Move only the existing ticket-allocation data and helpers from the environment CLI adapter to a narrow SDK-owned module so checkout and rename call the same code without introducing another policy or framework.
- Allocate `TICKET`, `TICKET_1`, `TICKET_2`, and later suffixes from local heads, OdCLI catalogue history, and current `origin` heads; return the current branch unchanged when it already matches the requested ticket family.
- Revalidate the current branch, HEAD, and selected-name absence immediately before one non-force local rename; fail safely for detached HEAD, unavailable evidence, stale collisions, or any overwrite attempt.
- Preserve HEAD, checkout/index/dirty state and Git's normal renamed-branch configuration/upstream behavior; avoid worktree/database/environment creation and any fetch, push, or remote rename.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `odoo-git-workflow`: Add the SDK-owned safe current-branch rename and shared ticket-allocation behavior.
- `cli-odcli`: Add the `git rename-branch` bounded CLI leaf, selector, output, dry-run, and inventory contracts.

## Impact

The change affects the Git CLI adapter, public `GitResource`/models, ticket-allocation ownership currently in `commands/env/checkout.py`, catalogue and Git-ref inspection helpers, CLI characterization/inventory tests, focused Git tests, README command inventory, and the existing Git/environment OpenSpec contracts. It adds no dependency, Jira API integration, new top-level command group, push behavior, database behavior, or migration.
