## Why

Several common `odcli` workflows still require shell workarounds, hide useful execution context, or reject a valid initialized-project context. GitHub #53 now groups the remaining behavior gaps, and addressing them together lets the CLI keep one context, execution-plan, output, and monitoring contract instead of accumulating command-specific exceptions.

## What Changes

- Add restore progress with opt-in per-step command output while preserving machine-output determinism, redaction, and subprocess contracts.
- Add a guarded CLI-private `odcli db drop <database>` PostgreSQL workflow with cluster scoping, confirmation, dry-run, active-connection handling, default/system-database protection, and catalogue reconciliation while preserving public Odoo HTTP SDK drop semantics.
- Load `.odcli/.env` only from the resolved initialized project, with process-environment precedence, fail-closed parsing/permissions behavior, and secret-safe child propagation.
- Extend VS Code generation, module update, and Odoo test commands to initialized project checkouts without creating synthetic development environments; define changed-test base selection in that context.
- Replace the custom help formatter with `rich-click` for Click help and validation errors only, keeping command results and startup boundaries unchanged.
- Introduce one concise, decision-oriented Rich projection for dry-run plans while retaining the complete immutable snapshot in JSON and TOON.
- Make `odcli run --dry-run` report an occupied port as a failed precondition instead of aborting before the plan is emitted.
- Persist and monitor runtimes owned directly by a project, exposing them through snapshot schema version 4 (`ProjectSummary.runtime`), the existing HTTP API, and dashboard alongside environment-owned runtimes without registration writes during resolution, monitoring, or dry-run.
- Preserve `odcli eval` user stdout and actionable user-code diagnostics separately from Odoo startup logs in Rich, JSON, and TOON output.
- Add focused unit, integration, CLI contract, API, dashboard, redaction, and disposable-environment regression coverage for every affected workflow.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-odcli`: Extend command context rules, help/error rendering, database drop, restore feedback, dry-run presentation, and eval output contracts.
- `command-execution`: Represent plan preconditions and human projections without weakening immutable machine snapshots or secret redaction.
- `database-management`: Define safe project-cluster database deletion, active-session policy, and post-drop catalogue reconciliation.
- `database-restore`: Expose step progress and associated opt-in command streams for restore plans.
- `project-init`: Define bounded project-local `.odcli/.env` discovery, precedence, validation, permissions, and secret handling.
- `local-odoo-testing`: Support project-owned test execution and deterministic changed-test base selection.
- `instance-runtime-binding`: Model persisted runtime ownership as an explicit environment-or-project union.
- `server-lifecycle`: Preserve project-owned runtime records and separate eval user output, startup diagnostics, and user-code failures.
- `environment-monitor`: Discover initialized projects without environments and collect project-owned Odoo runtime metrics.
- `dashboard-http-api`: Serialize and render project-owned runtimes through the existing snapshot contract.
- `packaging`: Add `rich-click` without regressing lightweight CLI startup or shell completion.

## Impact

The change touches the Click entry point and output adapter, context resolution, database and instance resources, runtime/catalogue persistence, monitor models and collectors, FastAPI serialization, the React dashboard, packaging metadata, and their contract tests. Database-drop and live restore checks must run only against disposable test clusters; the user's working instance and database remain out of scope without explicit approval. Public machine envelopes receive additive typed owner/context fields and snapshot schema advances additively from 3 to 4 for the single nullable project-runtime field, while existing command names, public SDK methods, selector precedence, exit codes, and native subprocess behavior remain compatible.
