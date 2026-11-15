# Design

## Context

See proposal.md for why this change exists. The uv workspace already contains `packages/odcli-multica`; this change adds one member instead of a second root layout. `CheckoutInventory` already discovers read-only providers through the `odoo_instance_sdk.environment_facts` entry point and renders one Rich column per provider. OpenSpec 1.13.2, verified locally, is the JSON authority: `list --json` carries `completedTasks` and `totalTasks`, while `status --change --json` carries planning artifacts and does not mean implementation is finished.

## Goals / Non-Goals

**Goals:**

- One captured checkout-plus-binding plan, executed as that same snapshot.
- Progress probed only in the bound worktree, with separate planning and task numbers.
- A small public continuation seam so the extension can append steps without importing `internal.proc`.

**Non-Goals:**

- Generating OpenSpec artifacts, editing task checkboxes, or installing Node.
- Beta OpenSpec stores and worksets.
- A second environment collector, watch loop, or progress history.

## Decisions

### D1. Workspace member, not a new repository

Add `packages/odcli-openspec` to the existing `[tool.uv.workspace] members` list. Published dependencies use `odoo-instance-sdk>=0.1.0,<0.2`. `[tool.uv.sources]` points at the workspace only for development; `uv build --package odcli-openspec --no-sources` keeps the version range.

Alternative: a git pin, as `odcli-multica` uses today. Rejected for this package because the issue requires a public version range that stays valid outside the checkout.

### D2. One command continuation seam

Core `execution.continue_command` appends public process and action steps to an already captured command. The original callback runs on a nested context bound as the active context and sharing the executor. Nested step ids are acknowledged on the outer ledger after that callback returns, so checkout is not described as skipped and is not launched twice. The extension calls only this public helper.

Alternative: a second OpenSpec plan rendered beside checkout. Rejected because dry-run and execution would be two snapshots.

### D3. Binding file outside the catalog and the git worktree

Store `~/.odcli/openspec/<environment-id>.json`, honoring `ODCLI_USER_ROOT`. The file holds root path, change id, executable, version, bind timestamp, and an observation digest. Unbind deletes that file only.

Alternative: a file inside the worktree. Rejected because `.odcli` is not fully gitignored and a bind would dirty the branch.

### D4. Adapter over documented JSON only

Accept OpenSpec 1.12 and 1.13 when `list` contains `name`, `completedTasks`, `totalTasks`, and `status`, and `status` contains `artifacts[].status` plus `isPlanningComplete`. Any other shape or version is `openspec_incompatible`. Planning complete count is the number of artifacts whose status is `done` or `skipped`. Implementation percentage is `(completed * 100 + total // 2) // total` when `total > 0`.

`openspec validate CHANGE --type change --json --no-interactive` is the binding validation command. `list` and `status` do not accept `--no-interactive` in 1.13.2, so those argv values omit it.

### D5. Partial checkout result

The composed command returns a typed result after checkout. Binding failure is a value with `environment_created` and `binding_failed`, not an exception that the CLI would present as a rolled-back checkout. The CLI then exits non-zero and includes the worktree path in the error details.

### D6. Facts stay inside the existing 2 second provider deadline

The provider probes bound rows sequentially, default 0.4 seconds each and 1.2 seconds overall. Rows that would exceed the budget are `unavailable` with `probe_budget_exceeded` and are not probed. Unbound rows do not launch OpenSpec.

### D7. Core flag imports the extension lazily

`--openspec-change` is always visible. The import happens only when the option is set, and it happens before `checkout_command` runs.

## Risks / Trade-offs

- [OpenSpec 1.14 changes JSON] → the adapter rejects unknown shapes instead of guessing; the research note records 1.13.2.
- [Provider budget hides later rows] → those rows stay visible with an explicit reason.
- [Nested command ledger is subtle] → continuation lives in one core helper with a focused test, not in the extension.
- [Checkout planning still reads the catalog before bind] → the missing-package failure happens before that planning call.

## Migration Plan

Ship the package at `0.1.0` with the documented tag `odcli-openspec-v0.1.0`. No catalog migration. Removing the package leaves existing binding files unused and checkout without the flag unchanged.

## Open Questions

None. The verified CLI shapes and the already-landed workspace scaffold determine the approach.
