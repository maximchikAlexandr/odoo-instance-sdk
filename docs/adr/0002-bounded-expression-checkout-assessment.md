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
| bounded checkout pipeline after the preliminary slice | 5 | 6 | 7 branches removed versus 6 boundary operations; retain Expression provisionally |

The after-slice count is reproducible from the four concrete stage functions:
the stage bodies contain five conditional nodes, while the bounded Result has
one initial input conversion, one conversion per stage (four total), and one
terminal `default_with` extraction.  Thus the stop-condition comparison is
`6 <= (12 - 5)`, rather than a subjective comparison of line counts.  This is
still only a preliminary checkout result.

After #35 implements its vertical planning slice, contributors must append the
same before/after measurement and outcome here. A positive checkout result
cannot waive the mandatory post-#35 recheck; Expression cannot expand beyond
this bounded use until that reassessment is recorded.

## Post-#35 measurement: native run arguments

The `pass-native-odoo-run-args` vertical slice was measured against the exact
pre-slice revision `ea24ac9e79ea497fee985e72a6854cf61f08d614` and the reviewed
post-slice revision `ddbdca3b8d0945464d433a80a4033a7520380591`.  The counted
native-argument planning functions are
`OdooInstance.run_foreground_command` and `OdooInstance.shell_command`.  The
shared `_validate_runtime_args` helper is also reported separately because it
is a validation boundary, not a replacement for an Expression planning stage.

| checkpoint | planning branches | Expression adapters/unwraps | outcome |
| --- | ---: | ---: | --- |
| native run-argument planning before #35 (`ea24ac9e79ea497fee985e72a6854cf61f08d614`) | 15 | 0 | exact pre-slice baseline |
| native run-argument planning after #35 (`ddbdca3b8d0945464d433a80a4033a7520380591`) | 15 | 0 | 0 branches removed; retain Expression provisionally |

The following command is the measurement procedure (run from the repository):

```bash
uv run python - <<'PY'
import ast
import subprocess

functions = {"run_foreground_command", "shell_command"}
for revision in (
    "ea24ac9e79ea497fee985e72a6854cf61f08d614",
    "ddbdca3b8d0945464d433a80a4033a7520380591",
):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:src/odoo_instance_sdk/resources/instance.py"],
        text=True,
    )
    tree = ast.parse(source)
    counts = {
        node.name: sum(
            isinstance(child, (ast.If, ast.IfExp, ast.Match))
            for child in ast.walk(node)
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in functions
    }
    print(revision, counts, sum(counts.values()))
PY
```

It prints `15` for both revisions: `run_foreground_command` is `10 -> 10`
and `shell_command` is `5 -> 5` (`ast.Match` is included even though neither
revision uses it).  The new `_validate_runtime_args` helper is `0 -> 4`; those
four nodes implement the explicit protected-option boundary and are not
Expression adapters.  The slice introduced **0 Expression adapters/unwraps**
and removed **0 planning branches**, so the unchanged stop condition is
`0 <= 0`: retain Expression provisionally under its existing bounded lock and
startup exclusions.  This is a completed post-#35 assessment; any future slice
must repeat this exact revisioned measurement.

## Consequences

The lock records a reproducible dependency without importing Expression during
package, help, or version startup. The branch/adapter ratio remains an explicit
review gate, and typed stage signatures can be retained if the dependency is
removed.
