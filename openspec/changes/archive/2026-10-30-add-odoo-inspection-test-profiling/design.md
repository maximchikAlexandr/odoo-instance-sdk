## Context

At base `f0918da2d36b59c35cf0a949efc58744d1efb7f9`, the hard infrastructure already exists. `OdooInstance._shell_script_command()` captures a configured headless Odoo-shell process with an explicit rollback action; `run_odoo_tests_command()` calls Odoo 19 `run_tests()` once and converts its result; `commands/output.py` owns Rich/JSON/TOON documents; `commands/test.py` owns target and changed selection; and `PUBLIC_LEAF_CASES` is the test-owned CLI inventory. The missing work is a few bounded values and adapters over those seams.

GitHub #37 explicitly requires one `OdooInspector` facade, three frozen inspection result models, matching command methods, one native profile entry type, and opt-in `test --profile`. Those are product requirements, not planning inventions. The implementation must not turn them into a generic ORM client, second runner, new planner, or new output framework.

## Goals / Non-Goals

**Goals:**

- Add the three exact registry/XML-ID reads required by #37 through the existing rollback-only shell command.
- Add native Odoo test stats to the existing test result only when requested.
- Keep CLI callbacks as thin argument/result adapters and preserve ordinary test output.
- Prove the new paths with focused unit tests, one existing real-Odoo fixture, and the minimum independent visible-output walkthrough.

**Non-Goals:**

- Generic ORM/search/read/write, raw SQL, record-value dumps, recursive traversal, or dynamic inspection dispatch.
- A second runner, profiler service, history, thresholds, dashboard, or persistence.
- Profiling the legacy `module test` alias, exhaustive retesting of unchanged selection branches, a database fingerprint utility, or a new evidence renderer.

## Decisions

### D1: Keep the facade and three inspection models because #37 requires them

Add one small `OdooInspector` resource at `instance.inspector`. It exposes only `model`, `field`, and `ref` plus their matching command siblings. Each convenience method calls its command sibling once. `OdooModelInfo`, `OdooFieldInfo`, and `OdooRefInfo` remain separate frozen values because their fields are different and #37 explicitly asks for three typed result models; a generic result/dictionary hierarchy would add indirection while weakening validation.

Each command validates its identifier, embeds only the validated string in one fixed script, calls `_shell_script_command(commit=False, exclusive=False)`, and converts primitive JSON into the result. Valid missing objects use `exists=false` with null discovered metadata. There is no new executor, base class, registry abstraction, or process boundary.

### D2: Keep only the smallest profile value graph

Extend `OdooTestSpec` with `profile: bool = False`. Add `OdooTestProfileEntry(test_id, duration, query_count)` and one aggregate `OdooTestProfile(total_duration, total_query_count, entries)`, then add nullable `profile` to `OdooTestResult`. The entry is explicit in #37; the aggregate is the smallest way to keep totals and entries behind one optional field without adding several nullable fields to the existing result.

When profiling is enabled, `_test_runner_source()` projects `_r.stats` from the same native `run_tests()` result. The existing converter validates the primitive values, sorts duration descending then test ID, and derives totals. When disabled, the runner payload and result remain ordinary. No collector, callback, log parser, or second invocation is added.

### D3: Add only the requested CLI surface

Register one lazy `inspect` group with model, field, and ref leaves. They reuse `ready_instance()`, the shared output document, and one small Rich table per result shape. Add `--profile` only to top-level `odcli test`; the legacy `module test` alias remains unchanged because #37 does not request a second public spelling. Target and changed selection both construct the same `OdooTestSpec`, and profile-aware dry-run shows the existing captured command.

The three inspect leaves get one row each in `PUBLIC_LEAF_CASES`. No second inventory, callback-local script, inspect `--dry-run`, command-local `--env`, or separate renderer is introduced.

### D4: Tests follow changed branches, not the entire neighboring system

Focused tests cover: exact model fields/validation; one-delegation facade behavior; fixed script/result conversion for existing and missing objects; `commit=false`; native profile mapping, ordering and totals; ordinary profile omission; target and changed profile propagation; dry-run reuse; output parity; and the three inventory rows. Existing selection, failure, zero-test, port, locking, and alias tests remain the regression net and are not duplicated into a new combinatorial matrix.

The existing real-Odoo probe fixture gains focused assertions for one model, field, XML ID, missing result, rollback behavior, and one non-empty native profile entry. Independent E2E reuses the existing numbered-slot wrapper and tracked Python text renderer/font. It adds no test-repository helper and no whole-database dump; read-only behavior is proved at the actual command transaction boundary and by the focused real-Odoo regression.

### D5: Ponytail full audit

| Candidate addition | Existing alternative | Decision |
| --- | --- | --- |
| `OdooInspector` facade | raw trusted shell API | Keep: explicitly required by #37 so CLI can call a bounded public SDK primitive. |
| Three inspection models | untyped dictionaries or a generic union | Keep: explicitly required and simpler than a dynamic schema. |
| Profile entry model | raw `_r.stats` tuples | Keep: explicitly required typed SDK output. |
| Profile aggregate model | several nullable fields on `OdooTestResult` | Keep: one cohesive optional `profile` object is smaller and preserves ordinary output projection. |
| Command siblings | direct CLI execution | Keep: explicitly required reuse of #40 command plans. |
| Profile on `module test` | top-level `test --profile` | Remove: not requested; default-false SDK behavior preserves the alias. |
| Exhaustive selection/exit matrices | existing mature tests | Remove: add only target, changed, dry-run, mapping, and unprofiled regressions touched by the flag. |
| Database fingerprint helper | `commit=false` command plan plus real-Odoo rollback regression | Remove: it tests the framework around the feature, not #37 implementation. |
| New renderer or dependency | tracked test-repository Python renderer/font | Remove: reuse existing assets for required images. |
| Multiple implementation WPs | one cohesive change across adjacent seams | Collapse if the final estimate is within the mandatory single-WP threshold. |

## Risks / Trade-offs

- Odoo field/model attributes vary → read only the named primitive attributes from #37 and reject malformed payloads.
- Display text can be large or sensitive → cap returned strings and never read arbitrary record fields.
- Native stats can be malformed → validate only the required ID/duration/query primitives and fail without log fallback.
- Optional SDK fields could leak into ordinary CLI output → project `profile` only when the flag is set and keep one exact unprofiled-shape regression.

## Migration Plan

1. Add the required result values, inspector facade, fixed scripts, and focused SDK tests.
2. Extend the existing runner/result with optional native stats and focused mapping tests.
3. Register the requested CLI leaves/flag, inventory rows, output tests, and help/README examples.
4. Extend the existing real-Odoo focused case, run repository gates, publish one exact candidate, and execute the accepted minimal E2E walkthrough.

Rollback removes only the additive facade/models/CLI leaves/profile flag and restores the unchanged runner projection. No database, catalog, configuration, or file-format migration exists.

## Open Questions

None. The component count above follows explicit #37 SDK requirements; everything else uses existing project seams.
