## Why

Automatically prepared checkouts can reach adoption or their first commit while still carrying a generated branch name, forcing agents to rename branches manually without OdCLI's ticket-allocation collision checks. A public, inspectable rename operation is needed now so callers can select the next Jira-style branch name safely while preserving the existing checkout allocation contract.

## What Changes

- Add `odcli git rename-branch TICKET` using existing project/environment selection, bounded Rich/JSON/TOON output, and dry-run behavior.
- Add a public SDK command builder and convenience operation that return the previous and selected branch names through one immutable inspectable command snapshot.
- Move ticket suffix parsing and allocation from the environment CLI adapter into a reusable SDK-owned component, preserving current `env create`/`env checkout` behavior.
- Allocate `TICKET`, `TICKET_1`, `TICKET_2`, and later suffixes from local heads, OdCLI catalogue history, and current `origin` heads; return the current branch unchanged when it already matches the requested ticket family.
- Revalidate the selected name immediately before mutation and fail safely for detached HEAD, unavailable evidence, stale collisions, or any rename that would overwrite/switch to an existing branch.
- Preserve HEAD, checkout/index/dirty state, avoid worktree/database/environment creation, avoid push/remote rename, and remove an upstream that would still target a differently named remote branch.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `odoo-git-workflow`: Add the SDK-owned safe current-branch rename and shared ticket-allocation behavior.
- `cli-odcli`: Add the `git rename-branch` bounded CLI leaf, selector, output, dry-run, and inventory contracts.

## Impact

The change affects the Git CLI adapter, public `GitResource`/models, ticket-allocation ownership currently in `commands/env/checkout.py`, catalogue and Git-ref inspection helpers, CLI characterization/inventory tests, focused Git tests, README command inventory, and the existing Git/environment OpenSpec contracts. It adds no dependency, Jira API integration, new top-level command group, push behavior, database behavior, or migration.
