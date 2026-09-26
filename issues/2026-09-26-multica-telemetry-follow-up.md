## Summary

Defer the observability half of #70 into a separate `later` slice: Multica daemon resource metrics in `odcli ps` and issue/run/usage annotations in `odcli env list`. These are useful operational views, not prerequisites for preparing an isolated Odoo analysis environment in the current technical-specification workflow.

## Context and dependencies

- Parent scope split: #70.
- First deliver the focused checkout/adoption/binding primitive; consume its explicit environment/run identity instead of inventing another registry.
- Reuse #67's existing process/inventory boundaries and coordinate PID attribution with #68.
- Keep `odcli-multica` independently installed, using public core SDK and `multica-py` only.

## Scope

1. Contribute one `multica-daemon` shared process group to `odcli ps`: verified root PID, process identity/create time, observation time, lifecycle state and CPU/RSS or a typed unavailable reason. Root process only initially; never double-count Codex children.
2. Add compact Multica issue/status/run-count context to `odcli env list`, separate from process metrics. Do not put token/task columns in `ps` or PID/CPU/RSS columns in `env list`.
3. Resolve attribution from explicit environment/run binding first, then canonical exact path **and runtime host**; branch name alone is insufficient. Missing and ambiguous facts remain explicit.
4. Show typed token/input/output/cache-read/cache-write counters with an explicit scope. Reverify the selected upstream release: current `multica-py` TaskRun already contains `usage`, so do not assume only issue totals exist or assert precise per-run attribution without supported populated data.
5. When only issue totals are available, distinguish `issue_total` from `shared_issue`; deduplicate across environments and never divide totals proportionally or fabricate per-checkout costs.
6. Use compact human units in Rich, exact integers and scope in JSON/TOON. Expose equivalent finite Python SDK telemetry/process results.
7. Integrate with existing inventory/watch loops, bounded timeouts and deterministic ordering. One unavailable source must not hide core environments or other projects. No new polling daemon, history database or metric store.
8. Open a separate upstream Multica/multica-py issue only if a concrete missing per-run contract is established during implementation; it does not block honestly scoped issue totals.

## Acceptance criteria

- [ ] A running, stopped, missing or stale-PID daemon is represented honestly once; PID identity is checked before sampling.
- [ ] Explicit binding and same-host exact-path attribution work; ambiguous and cross-host paths never silently match.
- [ ] One/multiple environments sharing an issue do not duplicate aggregate usage; missing/per-run/issue-total scope fixtures are covered.
- [ ] Rich/JSON/TOON agree semantically and machine counters retain precision.
- [ ] `env list --watch` and `ps --watch` reuse core loops; unavailable Multica affects only its annotation/group.
- [ ] SDK commands use frozen typed plans/results and existing public boundaries, with no core dependency on Multica.
- [ ] Fake client/executable tests cover timeouts, cancellation, deterministic ordering, stale PID, no double-counting and pagination without live user configuration.
- [ ] Clean-wheel package/core isolation and supported-version contract tests pass.

## Out of scope

Checkout/adoption implementation itself; task dispatch and status automation; billing, quotas or budgets; machine scheduling; dashboard/UI rendering; persistent metric history, notifications; business-analysis/reporting skills; Temporal workflows; generic tracker or plugin frameworks.

## Impact

Operational convenience after the deterministic environment workflow is available. This issue must not gate the first checkout/binding delivery.
