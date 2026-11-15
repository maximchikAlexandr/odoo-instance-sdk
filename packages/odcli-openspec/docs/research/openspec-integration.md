# OpenSpec integration research

Verified on 2026-11-15 against the installed OpenSpec CLI `1.13.2` (`/opt/homebrew/bin/openspec`) and the upstream documents [CLI reference](https://github.com/Fission-AI/OpenSpec/blob/main/docs/cli.md) and [agent commands](https://github.com/Fission-AI/OpenSpec/blob/main/docs/commands.md). The adapter accepts the same JSON fields on 1.12 and 1.13. It does not scrape terminal text.

## Commands the adapter uses

| Command | Role |
| --- | --- |
| `openspec --version` | Exact `1.12.x` or `1.13.x` line. Any other stdout is `openspec_incompatible`. |
| `openspec validate CHANGE --type change --json --no-interactive` | Binding validation. Non-zero exit is `invalid_change`. |
| `openspec status --change CHANGE --json` | Planning artifacts. `isPlanningComplete` is not implementation progress. |
| `openspec list --json` | `completedTasks`, `totalTasks`, and task `status` for the target worktree. |

`list` and `status` do not accept `--no-interactive` in 1.13.2. `validate` does. `openspec view` is a human dashboard and is not used. Stores and worksets stay out of this slice.

## Sanitized JSON

`openspec list --json`:

```json
{
  "changes": [
    {
      "name": "refactor-cli-output-boundary",
      "completedTasks": 11,
      "totalTasks": 41,
      "status": "in-progress"
    }
  ],
  "root": {"path": "/example/worktree", "source": "nearest"}
}
```

`openspec status --change refactor-cli-output-boundary --json` reports `isPlanningComplete`, `artifacts[].status` (`done` or `skipped` count as complete), and `root.path`. A missing change returns exit code 1 and a `status` array containing `code: change_error`. That JSON is not implementation-task completion.

`openspec validate <change> --type change --strict --json` returns an object with `items` and `summary`. Exit code 0 is success.

## Progress rules

Planning count is the number of status artifacts. Implementation count comes only from `list`. Percentage is `(completedTasks * 100 + totalTasks // 2) // totalTasks` when `totalTasks > 0`. That is integer half-up rounding. `totalTasks == 0` yields `implementation_percent: null` and task state `no_tasks`, never 0% or 100%.

Probes run in the bound environment worktree. A missing executable, timeout, malformed JSON, or unsupported shape sets a typed reason and does not invent a percentage. The default inventory probe timeout is 0.4 seconds and the provider budget is 1.2 seconds so it returns before the core 2 second provider deadline.

## Checkout composition

`odcli env checkout TICKET --openspec-change CHANGE` imports `odcli-openspec` before checkout planning. The install hint is `uv tool install --with-executables-from odcli-openspec odoo-instance-sdk`. The extension appends OpenSpec steps to the captured checkout command through the public continuation seam. Dry-run prints that snapshot and does not launch OpenSpec or write a binding. If the worktree is created and validation then fails, the result keeps `environment_created` and `binding_failed` and names the worktree path.

Binding files live at `$ODCLI_USER_ROOT/openspec/<environment-id>.json` (default `~/.odcli`). They store root, change id, executable, version, bind time, and an observation digest. They do not copy proposal, spec, design, or task bodies. `unbind` deletes that file only.

## Later or reject

- Generate proposal, spec, design, or tasks with an LLM: `reject`. OpenSpec's own agent commands stay the authoring surface.
- Mark task checkboxes from commits, tests, or agent claims: `reject`. OpenSpec remains the source of truth.
- Install or upgrade Node or the OpenSpec CLI automatically: `reject`. The host executable is an explicit dependency.
- A second dashboard, collector, or watch loop: `reject`. `odcli env list` already renders provider columns.
- Persist progress history or send notifications: `later`. One current observation is enough for this slice.
- Beta stores and worksets: `later`. No concrete OdCLI workflow needs them yet.
