## Context

At base `f0918da2d36b59c35cf0a949efc58744d1efb7f9`, project/environment resolution already produces one configured `OdooInstance`; `_shell_script_command()` captures an immutable headless Odoo-shell process and an honest rollback action; `run_odoo_tests_command()` owns the current test command; and `commands/output.py` projects bounded values through one Rich/JSON/TOON pipeline. The test runner source calls Odoo 19 `run_tests()` once and maps native counts, but discards its `stats` mapping. Odoo 19 stores each native stat as `Stat(time: float, queries: int)` keyed by test ID.

The implementation must preserve the repository's SDK-first and `internal/proc/` boundaries, the test-owned `PUBLIC_LEAF_CASES` authority, and ordinary test result compatibility. Inspection handles metadata from the live registry, so it needs a configured database and Odoo process but no HTTP server. Planning and implementation must not expose arbitrary record values, credentials, callable field attributes, or a general-purpose ORM/SQL surface.

## Goals / Non-Goals

**Goals:**

- Expose three explicit, frozen, bounded inspection results from a public `OdooInspector` facade and matching immutable command siblings.
- Use one existing rollback-only Odoo-shell primitive for inspection and the existing test runner for profiling.
- Preserve one typed result graph across Rich, JSON, and TOON while keeping ordinary `odcli test` output byte-shape compatible at the decoded-field level.
- Make missing-object, validation, stable ordering, read-only, redaction, and native-stat behavior fully testable.

**Non-Goals:**

- General `search`, `read`, `write`, raw SQL, arbitrary field-value export, relation traversal, or a dynamic `inspect(kind, **kwargs)` API.
- A second test runner, custom profiler, log parser, history store, dashboard, flamegraph, threshold gate, or alternative output/config/registry layer.
- Command-local `--env`, inspect `--dry-run`, an HTTP server, or changes to test selection and exit rules.

## Decisions

### D1: One focused facade backed by the existing shell command

Add a small `OdooInspector` dataclass resource initialized with the other `OdooInstance` resources and exposed as `instance.inspector`. It has only `model(name)`, `field(model, name)`, and `ref(xmlid)` convenience methods plus `model_command(...)`, `field_command(...)`, and `ref_command(...)`. Each convenience method delegates once to its sibling command. The command embeds JSON-escaped validated identifiers in a fixed script, calls `instance._shell_script_command(..., commit=False, exclusive=False)`, and converts the framed payload into one exact frozen result type.

The existing alternative is direct `run_shell_script_command()`. It remains available for trusted SDK scripts, but making CLI callbacks construct those scripts would violate the SDK-first boundary and duplicate validation/result conversion. A general query/inspector framework is rejected because there are only three fixed operations.

### D2: Three exact results and deterministic absence

Add frozen, unknown-field-forbidden `OdooModelInfo`, `OdooFieldInfo`, and `OdooRefInfo` values. Model output contains `name`, `exists`, nullable bounded `description` and `table`, nullable `abstract`, `transient`, and `auto`, and nullable non-negative `field_count`. Field output contains `model`, `name`, `exists`, nullable bounded `label`, nullable `type`, `required`, `readonly`, `store`, and `index`, nullable bounded `relation`, and nullable booleans `related` and `computed`. Reference output contains `xmlid`, `exists`, nullable bounded `model`, nullable positive `record_id`, and nullable bounded `display_name`.

Valid but missing models, fields, and XML IDs return `exists=false` with every discovered-metadata field null; they are successful bounded reads, not transport failures. Invalid identifiers fail before command construction. Strings returned by Odoo are normalized to strings and capped at 256 Unicode code points; no callable or arbitrary record field is serialized. `ref` uses `env.ref(xmlid, raise_if_not_found=False)` and reads only `_name`, `id`, and `display_name` from the resolved record.

The alternative is to raise for missing objects. It is rejected because a uniform typed absence result is deterministic across SDK and all CLI formats and lets automation distinguish absence from an Odoo/process/configuration failure.

### D3: Profile is an opt-in extension of the existing test spec/result

Add `profile: bool = False` to `OdooTestSpec`, `OdooTestProfileEntry(test_id, duration, query_count)`, `OdooTestProfile(total_duration, total_query_count, entries)`, and nullable `profile` on `OdooTestResult`. `_test_runner_source()` receives the flag and, only when true, projects `_r.stats` into primitive `{test_id, duration, query_count}` entries. The converter validates finite non-negative durations and non-negative integer query counts, sorts entries by descending duration then ascending test ID, and derives totals from the validated entries. It does not trust a separate textual total.

When profiling is false, the runner payload and CLI projection retain their existing count fields and the CLI omits `profile` entirely. When profiling is true, the same `run_tests()` result supplies both counts and profile; a missing/invalid native stats mapping is an actionable execution error rather than fabricated zero profile data. Existing failure, zero-test, `allow_empty`, process, and diagnostic rules remain authoritative.

The alternative is parsing Odoo's stats log. It is rejected because human log detail depends on logger configuration and loses the typed `Stat` source already present on the native result.

### D4: CLI remains a thin adapter over public SDK values

Register one lazy `inspect` group with `model`, `field`, and `ref` leaves. They use the existing `ready_instance()` order, call only the matching public inspector method/command, and pass the frozen result through the shared output document. Add `--profile` to the existing `test` callback and compatibility `module test` path so both construct the same profiled `OdooTestSpec`; do not add a callback-local runner or profile mapper.

All three inspect leaves are bounded read-only entries in `PUBLIC_LEAF_CASES`, each with its public SDK primitive, no dry-run, and one focused real-Odoo disposition. `test` remains its single canonical row and dry-run command plan; profile mode changes the captured fixed runner source in that existing plan. Rich tables render the same fields as decoded JSON/TOON and machine stdout stays one clean document.

The alternative is a new CLI renderer or command inventory for diagnostics. It is rejected because the existing bounded output and leaf matrix already enforce serialization, redaction, exit, and E2E ownership.

### D5: Verification extends existing seams

Focused tests cover identifier validation, script/transaction capture, exact missing results, bounded strings, no arbitrary/callable values, strict payload conversion, delegation, exports, CLI context resolution, leaf inventory, format parity, and clean stdout. Existing test tests extend their matrices for profile flag propagation, native stats mapping, invalid stats, ordering/totals, unprofiled shape, selection variants, failure/zero semantics, alias parity, and dry-run plan reuse. A real-Odoo focused integration uses the existing probe addon and native test runner rather than a new harness.

The existing CI jobs and real-Odoo workflow are sufficient. The test repository's existing Python renderer/font are reused for required text renders. Its missing database immutability check is one bounded fingerprint helper with fake-process/config tests, owned by final integration; a new framework is rejected.

### D6: Ponytail full subsystem gate

- `OdooInspector` facade: necessary because the CLI must delegate to public typed SDK primitives; the existing raw shell-script command is too low-level for this public contract. Keep the facade, limited to three methods.
- Inspection result models: necessary to prevent dynamic registry objects from crossing the process/output boundary; dictionaries alone would weaken validation and exports. Keep exactly three small models and no generic base/result hierarchy.
- Test profile models: necessary because native stats are repeated entries with totals and must remain immutable/typed. Keep one entry and one aggregate model; do not add a profiler service.
- E2E evidence utilities: reuse the tracked Python renderer and font. Add only the missing test-repository database fingerprint helper because no current wrapper/CLI operation can prove whole-database immutability without exposing data; keep it outside the SDK API and give it focused tests.
- New runner, planner, renderer, registry, collector, configuration layer, persistence, or dependency: existing alternatives already satisfy the change. Add none.

## Risks / Trade-offs

- [Odoo registry attributes vary by field/model implementation] → Read only the named stable attributes, coerce only primitive values, and fail conversion rather than serialize dynamic objects.
- [A display name or description can be unexpectedly large or sensitive] → Limit model/field text to metadata and reference output to `display_name`, cap strings, and test that arbitrary fields and credentials never enter the payload or plan.
- [Native `stats` can contain unusual IDs or numeric values] → Validate ID/string, finite non-negative duration, and integer query count at the SDK boundary; fail invalid data without log fallback.
- [Adding a nullable field to the SDK result could leak into ordinary CLI output] → Project `profile` conditionally only when requested and lock the unprofiled decoded field set with regression tests.
- [Inspection starts an Odoo shell process] → Reuse the captured process boundary, dependency readiness, configured database, rollback action, redaction, and lock; no HTTP process is started.

## Migration Plan

1. Add frozen models/exports and the focused inspector resource, then prove immutable command capture, rollback, validation, and SDK delegation.
2. Register the three CLI leaves through the existing context/output seams and update the canonical test-owned inventory.
3. Extend the existing test spec/result, runner source, converter, and both CLI spellings with opt-in profile data while locking the unprofiled shape.
4. Add documentation, focused unit/contract tests, existing CI wiring, and real-Odoo fixture assertions; validate the OpenSpec change and repository gates on one exact candidate.

Rollback is additive: remove the inspect group/facade/models and profile option/models/mapping while leaving the existing shell and unprofiled test runner behavior intact. No catalog, database, or file-format migration is required.

## Open Questions

None. Missing-object behavior, result fields, bounds, native source, ordering, totals, CLI compatibility, and verification ownership are fixed by this design.
