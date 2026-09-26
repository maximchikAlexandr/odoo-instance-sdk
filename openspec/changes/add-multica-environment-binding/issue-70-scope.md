# Complete disposition of GitHub #70

The table preserves the substantive points of original [#70](https://github.com/maximchikAlexandr/odoo-instance-sdk/issues/70). `Now` is the reviewed implementation scope, not shipped functionality. `Later` is [#105](https://github.com/maximchikAlexandr/odoo-instance-sdk/issues/105). `Remove` is unnecessary implementation, not an automatic new backlog feature.

| Original point | Decision | Reviewed scope |
|---|---|---|
| Separate distribution in this repository | Now | Independent `odcli-multica` import/executable/version, no new repository. |
| Core and external multica-py dependencies | Gate | Consume the verified integrated MYL-272 core contracts and the supported `multica-py` revision/version that implements all of #93. |
| Root uv workspace, no core relocation | Now | Reuse only #69's one-time scaffold, not its progress feature. |
| Standalone/combined uv tool installation | Now | Both clean-wheel contracts; no extension package manager. |
| Visible optional core integrated flags/lazy import | Remove flag | Native checkout and standalone prepare; core CLI remains unchanged and isolated. |
| Independent tags/version ranges/releases | Now | Package-qualified tags, compatible published dependencies. |
| Core/package/compatibility/all-member CI | Now | Test delivered members, not future features. |
| Future repository split thresholds | Boundary | Packaging guidance, not a feature. |
| Compare checkout ownership models | Done research | Native Multica ownership selected from actual source. |
| Force OdCLI-owned worktree + registration MVP | Replace | Native checkout then generic core adoption. |
| Ensure repository/resource and register local_directory | Remove mutation | Validate explicit existing context; project-wide local_directory is not an issue route. |
| One worktree, no copy/move | Now | Keep native path; allocate only Odoo-owned data/config. |
| bind/unbind reconciliation | Remove now | No redundant persistent association; caller saves context + environment UUID. |
| Partial failures/idempotent retry | Now | Two phases, core UUID/recovery, no false rollback or binding-repair state. |
| Exact preview/immutable composition | Now | Separate captured native and core commands, no dynamic compositor. |
| Daemon group CPU/RSS/PID in ps | Later | Minimal same-host identity read stays now; metrics and sampling wait. |
| Root-only metrics/no Codex double-counting | Later | Preserve telemetry constraint. |
| No second watch loop | Boundary/later | No watcher now; #105 reuses #67. |
| Task/run-count annotations in env list | Later | Context output now; inventory enrichment later. |
| Explicit identity/exact path/no branch guessing | Now + later | Validate now; #105 defines minimal attribution without requiring a binding store first. |
| Token/input/output/cache counts | Later | Verify supported usage scope and current TaskRun.usage. |
| issue_total/shared_issue/dedup | Later | No proportional allocation or duplicate totals. |
| Per-run upstream gap issue | Later, conditional | File only after evidence; existing run usage field is not automatically a gap. |
| Compact Rich/exact machine counters | Later | Common bounded output contract stays now. |
| Public checkout/telemetry/process client example | Split | Typed native checkout plus context/prepare now; telemetry/process enrichment later. |
| Frozen types/no Any/private imports/generic tracker | Now + later | Shared safety/maintainability contracts. |
| Multica unavailable must not break core | Now + later | Preparation may fail; core lifecycle and cleanup remain usable. |
| Canonical paths/stale state/timeouts/order | Now + later | Core missing-code diagnostics now; no extra binding state machine. |
| Fake-client/temp-Git tests | Now + later | Adoption/context tests now; metrics/usage tests later. |
| Acceptance: distribution/build/install/version | Now | Retained in packaging and tasks. |
| Acceptance: one checkout/preview/partial/rebind | Revise now | Adoption/retry checks retained; rebind removed with registry. |
| Acceptance: daemon/token/watch/usage | Later | Preserved in #105. |
| No task UI/lifecycle replacement/auto-install | Boundary | Excluded in both. |
| No billing/scheduling/machine selection/dashboard | Boundary | Excluded in both. |

## Review of the eight user-facing proposals

1. **Keep** the separate optional package.
2. **Keep, simplify** native checkout: use the final public typed checkout operation from the complete #93 implementation; do not preserve a raw-command adapter.
3. **Keep** generic core adoption and necessary ownership/lifecycle changes.
4. **Simplify** project/context: explicit inputs plus read-only facts; remove project-link TOML/CRUD.
5. **Simplify** CLI/SDK: context and prepare only; remove bind/unbind/status and persistent binding/history/locks.
6. **Keep, simplify** retries: core UUID idempotency and safe cleanup remain; binding failure/reconciliation machinery disappears.
7. **Require** complete multica-py #93 before implementation: consume its typed checkout and daemon-status contracts; local output decoders are forbidden.
8. **Keep, simplify** contracts: support the used existing output module directly, no new facade or execution framework.

No additional business workflow, Temporal implementation or automatic task routing is introduced. This split is reflected in the revised #70 body and #105's caller-owned attribution prerequisite.
