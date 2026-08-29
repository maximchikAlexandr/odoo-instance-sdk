## Status

Accepted as a preliminary assessment for MYL-68. Reassess after the GitHub #35
vertical slice.

## Context

GitHub #45 calls for a bounded Expression experiment around pure checkout
planning. The experiment must earn its dependency: adapter and unwrap ceremony
must not exceed the planning branches it removes. Checkout's positive result is
therefore not a waiver for the later #35 measurement.

## Decision

Keep Expression as a bounded runtime dependency (`expression>=5,<6`) for the
future pure resolve/validation/normalization/capture pipeline only. It must not
cross public SDK models, Click registration, serializers, process effects,
locks, cleanup, rollback, compensation, or lifecycle code.

Use the same metric at both checkpoints:

1. Count `ast.If`, `ast.IfExp`, and `ast.Match` nodes in
   `EnvironmentResource._prepare_checkout`,
   `EnvironmentResource._audit_checkout_plan`, and
   `EnvironmentResource.plan_checkout`.
2. Count explicit Expression adapter/unwrap operations introduced by the slice
   (the Expression `Result` boundary conversions, including success/error
   extraction).
3. Remove Expression immediately if adapters/unwraps exceed branches removed.

The preliminary pre-slice snapshot is:

| checkpoint | planning branches | Expression adapters/unwraps | outcome |
| --- | ---: | ---: | --- |
| checkout before the Expression slice | 12 | 0 | baseline only; no payoff claimed |

After #35 implements its vertical planning slice, contributors must append the
same before/after measurement and outcome here. A positive checkout result
cannot waive the mandatory post-#35 recheck; Expression cannot expand beyond
this bounded use until that reassessment is recorded.

## Consequences

The lock records a reproducible dependency without importing Expression during
package, help, or version startup. The branch/adapter ratio remains an explicit
review gate, and typed stage signatures can be retained if the dependency is
removed.
