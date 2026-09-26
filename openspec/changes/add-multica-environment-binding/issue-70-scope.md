# Complete disposition of GitHub #70

Source: [issue #70](https://github.com/maximchikAlexandr/odoo-instance-sdk/issues/70), read 2026-09-26. The source issue has three top-level deliverables plus package/API/acceptance constraints. The following table accounts for all substantive sections; shared safeguards are retained in both relevant slices instead of being discarded when telemetry moves out.

`Now` means proposed scope of #70 / this change, not already implemented. `Later` means [telemetry follow-up #105](https://github.com/maximchikAlexandr/odoo-instance-sdk/issues/105), created with labels `enhancement` and `later`. `Boundary` means no implementation item to move. Issue #70's original body has not been rewritten; this table records the proposed replacement scope for review.

| Original #70 point | Disposition | Concrete treatment |
|---|---|---|
| Separate distribution in this repository | Now | `odcli-multica`, independent import/executable/version; no new Git repository. |
| Dependency on core and external multica-py | Now | Public APIs only; add native checkout and preserve existing daemon JSON identity fields upstream in multica-py as prerequisites. |
| Root uv workspace and no core relocation | Now, reuse #69 | Wait for/reuse that issue's one-time scaffold; do not implement other members. |
| Standalone/combined uv tool installation | Now | Both clean-wheel installation contracts remain. No extension package manager. |
| Visible optional integrated flags/lazy import | Simplify now | No `--multica-issue` core flag in this slice; native checkout + extension prepare/bind preserve core CLI semantics and core-only import isolation. Not a deferred feature without a demonstrated need. |
| Independent tags/version ranges/releases | Now | Keep package-qualified tags and compatible published dependencies. |
| Core-only, package, compatibility, all-member CI | Now | Keep isolation gates; all-member job covers members actually delivered, not a dependency on all future features. |
| Future repository split thresholds | Boundary | Keep in packaging guidance, not a feature to implement. |
| Compare two checkout ownership models | Now, completed research | Source-backed comparison in `research.md`. |
| Force OdCLI-owned worktree + Multica registration MVP | Replace now | Native Multica-owned checkout + generic core adoption; per-project local_directory does not implement issue-scoped routing. |
| Ensure repository/project resource and register local_directory | Replace now | Validate existing configured project/repository; native task checkout handles its own ownership. No automatic project-resource creation/update. |
| One worktree, no copy/move to second root | Now | Native path stays in place; only Odoo-owned data/config are provisioned. |
| `env bind`/`env unbind` reconciliation | Now | Explicit verified IDs, local association, no data deletion or task changes. |
| Partial failures and idempotent retries | Now | Explicit checkout/prepare/bind boundaries; retry incomplete phase, no false rollback. |
| Exact dry-run and immutable command composition | Now | Capture each phase once; real phase boundary for output-dependent paths. No hidden dynamic steps. |
| Daemon shared `odcli ps` group | Later | CPU/RSS, stopped/unavailable process group, PID timestamp validation. Minimal public daemon/runtime identity for same-host binding stays now, without metrics. |
| Root-only metrics and no Codex double counting | Later | Keep this constraint with the daemon telemetry feature. |
| No second poller/watch loop | Later + boundary now | No watcher here; future telemetry reuses #67. |
| Task annotation in `env list` | Later | Keep issue/run identity in explicit `env status` now; inventory enrichment, task counts and active/total runs wait. |
| Explicit-ID/exact-path attribution, no branch guessing | Now + later | Binding uses verified identity now; telemetry later consumes it and must qualify path by runtime host. |
| Issue/run token totals and cache counts | Later | Exact typed counters and usage scope; reverify current `TaskRun.usage`, not the old assumption that only issue totals exist. |
| issue_total/shared_issue, no guessed allocation | Later | Preserve deduplication and honest unknown scope; supported per-run facts need evidence. |
| Per-run API-gap follow-up | Later, conditional | File only if release research proves a gap; current typed run model already has a usage field. |
| Compact Rich units, exact JSON/TOON counters | Later | Counters/units move; common bounded output contract stays now. |
| Public client checkout/telemetry/process examples | Split | Core adoption + context/prepare/bind/status/unbind now; telemetry and process resource later; native checkout owned by multica-py. |
| Frozen types, no Any/private imports/generic tracker | Now + later | Applies to both slices. |
| Unavailable/incompatible Multica does not break core | Now + later | Explicit context/bind may fail; local cleanup/unbind and core inventory remain usable. |
| Canonical paths, stale binding, bounded timeouts/order | Now + later | Keep locally useful checks now; telemetry sampling variants later. |
| Offline fake-client/temp-Git tests | Now + later | Checkout/binding/ownership cases now; usage/PID/watch cases later. |
| Acceptance: distribution/build/install/version | Now | Packaging spec and tasks group 3/5. |
| Acceptance: one-checkout/preview/partial/rebind | Now, revised model | Adoption and binding specs, tasks groups 2/4/5. |
| Acceptance: daemon/token/watch/usage tests | Later | Entire telemetry acceptance set is preserved in the follow-up. |
| No task UI/lifecycle replacement, auto-install/start | Boundary | Still excluded; no new later issue for prohibited scope. |
| No billing/budgets/scheduling/machine selection/dashboard | Boundary | Remain out of scope of both issues. |

## Additional useful primitives from this analysis

1. Generic existing-checkout adoption with explicit code ownership, necessary for safe native Multica reuse.
2. A minimal project link and exact issue/run/host context check, so routing does not depend on agent interpretation of task prose.
3. Source/base/HEAD validation before initial adoption: native checkout may preserve an old/dirty checkout or cached refs.
4. Separate SDK artifact root and project identity: Multica checkout paths/common dirs differ, and core cleanup currently assumes its own layout.
5. Same-input retry returning the same environment UUID, with phase-specific recovery for scripts/activities.
6. Finite binding status and non-destructive unbind, including code-GC and wrong-host diagnostics.

These are technical integration primitives. No new analysis skills, Temporal worker, task launcher, role system or reporting pipeline is part of this change.
