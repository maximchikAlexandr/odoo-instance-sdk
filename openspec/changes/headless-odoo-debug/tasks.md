# Tasks

## 1. Public models and breakpoint rules

- [x] 1.1 Add frozen snapshot, value, breakpoint, and trigger models plus one `DebugError` with the stable codes from the headless-debug spec, and verify a parametrized unit test round-trips them as JSON without DAP ids
- [x] 1.2 Parse `FILE:LINE` and `FILE:LINE:CONDITION`, reject missing files and non-positive lines, and verify a parametrized test keeps a condition that itself contains colons
- [x] 1.3 Apply snapshot limits and secret redaction, and verify tests cap locals, stack, source, strings, output, and eval depth and redact credential-shaped values

## 2. Private DAP client and local RPC

- [x] 2.1 Implement the private debugpy client for the request and event subset in the design, and verify tests cover split frames, interleaved responses, correlation, timeout, and EOF
- [x] 2.2 Implement versioned newline JSON over a `0700` directory and `0600` socket, and verify tests reject a version mismatch and a second live session without spawning Odoo
- [x] 2.3 Reconcile a stale socket and a reused PID, and verify the unit test removes only the dead session metadata

## 3. Session command plans

- [x] 3.1 Add `DebugResource` command siblings for every verb, capturing debugpy argv as `python -m debugpy --listen 127.0.0.1:0` plus the existing Odoo arguments, and verify dry-run executes no process and convenience methods delegate once
- [x] 3.2 Enforce workers, reload, missing debugpy, and occupied HTTP port before spawn, and verify each failure is stable and happens before the Odoo process step
- [x] 3.3 Implement start, continue, step, pause, eval, restart, and stop transitions, including one reproduction process and atomic breakpoints, and verify the state-machine tests with a fake executor
- [x] 3.4 Clean owned process groups, registry, secret config, and the artifact lock on stop, restart, target exit, and protocol failure, and verify an unrelated PID is not signaled

## 4. CLI leaves

- [x] 4.1 Register `odcli debug` with the shared format options and environment resolver, and verify project-only context fails before a session file exists
- [x] 4.2 Map each verb to the public command sibling and render Rich, JSON, and TOON from one envelope, and verify `--json` exits 2 and a wait interrupt exits 130 without a stop RPC
- [x] 4.3 Add the eight debug leaves to `PUBLIC_LEAF_CASES` only, and verify the existing leaf contract test passes

## 5. VS Code profile maintenance

- [x] 5.1 Replace the refuse-existing writer with a text splice that inserts or updates only the identity-marked profile, and verify comments, compounds, and a same-name foreign profile survive
- [x] 5.2 Report create, update, no-op, and field-level conflict from one captured change, and verify dry-run writes nothing while apply refuses a file that changed after planning

## 6. Documentation and integration

- [x] 6.1 Document the agent workflow, `workers=0`, recorded-environment debugpy, persistence, Ctrl+C, and the eval warning in the README and debug help, and verify the README inventory contract still matches the Click tree
- [x] 6.2 Add one serial integration test that drives real debugpy and a fake `odoo-bin` through separate CLI processes for start, wait, eval, step, atomic breakpoints, `--to`, a blocked trigger, restart, interrupt, and stop, and verify the session files are gone at the end
