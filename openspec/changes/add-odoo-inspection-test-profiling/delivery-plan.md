## Delivery basis

- Planning issue: `MYL-151`
- OpenSpec change: `add-odoo-inspection-test-profiling`
- Approved implementation base: `f0918da2d36b59c35cf0a949efc58744d1efb7f9` (`origin/main`, refreshed before planning)
- Estimate authority: planning-issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` are the only persisted numeric totals. This document records evidence, confidence, assumptions, verification, and topology without duplicating those totals.
- Estimate basis: remaining effort for one experienced developer familiar with the Python/Odoo stack, without AI acceleration; active developer-hours include investigation, implementation, required tests, and review fixes, while unattended CI, approval queues, meetings, and external blocking are excluded.
- Estimate confidence: medium. The existing command/output/test seams, tracked Python renderer/font, PostgreSQL tooling, and close local analogues are strong, while live Odoo registry attribute variation, native stats validation, whole-database dump normalization, and the broad canonical CLI matrices create bounded uncertainty. No product tests or project scripts were run for estimation.
- Estimate evidence: `resources/instance/identity.py::_shell_script_command`, `internal/automation.py::_test_runner_source` and `run_odoo_tests_command`, `commands/test.py`, frozen command models, `PUBLIC_LEAF_CASES`, focused real-Odoo probe fixtures, the test repository's renderer/font and database-diagnostics pattern, current CI workflows, all OpenSpec scenarios, and tasks `1.1`–`5.4`. Shared model/export work is counted once in the foundation; the fingerprint helper and its focused tests are counted once in final integration; review/repair and integration verification are included without a blanket percentage.

## E2E decision

`e2e_required=true`.

Focused unit/contract tests can prove identifier validation, payload conversion, immutable command capture, rollback action, serializer parity, unprofiled compatibility, and native-stat mapping with fakes. They cannot prove that Odoo 19 exposes the expected live registry attributes and XML-ID values through the configured headless shell, that its actual `OdooTestResult.stats` carries a non-empty `Stat(time, queries)` entry for the selected addon test, or that the new Rich output is usable on macOS without an HTTP server. Those are material cross-component gaps, so one readiness-gated baseline/candidate walkthrough is required.

The project description identifies `TEST_REPOSITORY_URL` as the private Odoo scenario repository. Its URL/hostname and private configuration SHALL remain outside public Git artifacts. Tester SHALL use a clean reviewed `TEST_REPOSITORY_SHA` from that repository and the persistent numbered-slot profile described below. The baseline and candidate use the same test-repository revision and equivalent copied database fixture in separate fresh slots.

The inspected test repository already contains `tests/e2e/render_terminal.py`, `tests/e2e/fixtures/terminal-font.ttf`, and its Pillow dependency lock; those tracked files are the renderer authority. One missing reusable input is explicit: Implementer SHALL add `tests/e2e/fingerprint_database.py` plus focused fake-process/config tests to that test repository under task `5.1`, publish the functional commit/MR, and make its reviewed SHA the common `TEST_REPOSITORY_SHA` before Tester readiness. The helper is not product behavior and adds no SDK command. Implementer also owns the product repository's automated tests, fixture changes, command-matrix regeneration, and required CI wiring in tasks `2.2`–`5.1`; Tester only executes the reviewed helper and does not edit either repository during verification.

## Verification matrix

| Case | OpenSpec requirement/scenario | Remaining gap / local E2E decision | Public action and local script | Independent Tester steps | Existing/planned CI disposition | Assertions | Multica-only artifacts/images |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `E2E-01` | `odoo-registry-inspection`: public inspector, existing/missing model/field/ref, bounded XML-ID identity, rollback-only headless execution; `cli-odcli`: SDK-first group, format parity/safety, leaf inventory | Only a real configured Odoo 19 registry/database proves the selected attributes, missing behavior, rollback/no-HTTP execution, and absence of record-value leakage. Local E2E required. | Existing `scripts/agent_checkout.py`; reviewed `tests/e2e/fingerprint_database.py`; baseline `odcli --help`; candidate `odcli inspect model binta.test.greeting`, `inspect field binta.test.greeting greeting`, `inspect ref base.user_admin`, and missing equivalents in Rich/JSON/TOON. | Prove no Odoo HTTP listener/runtime before inspection; run the reviewed whole-database fingerprint helper before and after candidate reads; compare decoded JSON/TOON; inspect Rich text and bounded key sets; verify no runtime/listener was started; render actual captured text with the reviewed Python renderer and font. | Implementer adds the fingerprint helper/focused tests in the functional test-repository MR, focused model/resource/CLI/output/redaction tests to existing offline CI, and focused real-Odoo coverage to the existing workflow. `PUBLIC_LEAF_CASES` remains the sole command-matrix authority. | Existing objects have exact bounded fields; missing objects return `exists=false`; JSON/TOON results match; Rich shows the same facts; no arbitrary greeting/user/credential/config fields; normalized whole-database fingerprints are exactly equal; no HTTP listener/process; output/diagnostics are separated and sanitized. | `e2e-01-baseline-help.txt/.png`, `e2e-01-candidate-model.txt/.png`, `e2e-01-candidate-field.txt/.png`, `e2e-01-candidate-ref.txt/.png`, decoded key/parity assertions, bounded before/after fingerprint JSON, exact SHAs/commands/exits, process/port observation. |
| `E2E-02` | `local-odoo-testing`: opt-in native profile, frozen ordering/totals, preserved behavior; `cli-odcli`: additive profile, ordinary shape compatibility, existing selection, one dry-run plan | Only Odoo 19's real `run_tests()` result proves a non-empty native stats mapping with duration/query count. The visible Rich profile also needs a genuine before/after capture. Local E2E required. | Existing wrapper and `binta_test_greeting` test; baseline/candidate ordinary `odcli test binta_test_greeting`; candidate `--profile` in Rich/JSON/TOON and `--changed --base HEAD --profile --dry-run` for plan-only verification. | Run ordinary baseline and candidate against fresh equivalent slots; compare decoded result key sets and exit semantics; run candidate profile and assert at least one `binta_test_greeting` test ID, finite non-negative duration and integer query count, stable order, and exact totals; inspect dry-run plan without execution; render actual Rich text before/after. | Implementer extends existing runner/unit/CLI matrices and existing real-Odoo test case. Automated coverage owns `--changed` execution, tags/reload/allow-empty, invalid stats, failures, zero tests, alias parity, and no-addon-change because those branches are cheaper and more deterministic below the live stand. | Ordinary candidate field set equals baseline and omits `profile`; profiled JSON/TOON match; totals equal entries; stable ordering holds; native test ID is present; exit/count behavior remains ordinary; dry-run contains the existing command plan and no profile result; no human-log parsing evidence or second test invocation. | `e2e-02-baseline-test.txt/.png`, `e2e-02-candidate-profile.txt/.png`, sanitized ordinary/profile JSON and TOON parity assertions, dry-run plan assertion, exact SHAs/commands/exits, test-repository revision. |

No OpenAPI operation or schema is affected: these are local CLI/SDK operations and SHALL NOT add dashboard HTTP routes or regenerate `openapi.json` for product behavior. OpenAPI is therefore marked not applicable for both cases, while existing generated-contract non-regression remains part of normal repository checks.

## Isolated numbered-slot procedure

Use the project-approved persistent PostgreSQL/profile resources without stopping Colima or touching the reference/legacy checkout:

- wrapper checkout: `~/e2e/odcli/binta-e2e-reference` at reviewed clean `TEST_REPOSITORY_SHA`;
- profile: `~/e2e/odcli/numbered-project-e2e`;
- reference checkout: `~/e2e/odcli/binta-e2e-reference`;
- PostgreSQL endpoint: the profile-owned persistent cluster on loopback port `15434` (credentials remain only in its owner-only env file and never enter evidence);
- one fresh free `HTTP_PORT`, unique `TARGET_DATABASE`, and wrapper-allocated `odcli-N` identity per baseline/candidate slot.

For `PRODUCT_SHA=<approved baseline>` and then `PRODUCT_SHA=<exact candidate>`, Tester runs the existing wrapper from the clean reviewed reference:

```bash
python3 scripts/agent_checkout.py create \
  --project ~/e2e/odcli/numbered-project-e2e \
  --reference-checkout ~/e2e/odcli/binta-e2e-reference \
  --reference-sha "$TEST_REPOSITORY_SHA" \
  --sdk-sha "$PRODUCT_SHA" \
  --http-port "$HTTP_PORT" \
  --target-database "$TARGET_DATABASE"
```

Tester records the returned slot identifier, environment ID, launcher, interpreter, checkout path, copied database, exact SDK SHA, and reference SHA; verifies the checkout is isolated and the PostgreSQL cluster is healthy; confirms the selected addon is installed; and reserves an owner-only evidence directory outside both repositories. Before any functional action, readiness SHALL verify that the common reviewed `TEST_REPOSITORY_SHA` tracks the renderer, font, fingerprint helper, and helper tests; that `"$INTERPRETER" -c 'import PIL'` and both helpers' `--help` invocations exit zero; and that `/opt/homebrew/bin/pg_dump` is executable. Readiness fails closed on a dirty/wrong reference, SHA mismatch, occupied port, missing launcher/interpreter/config, database-copy failure, missing reviewed helper/input, or failed helper smoke check. A readiness failure consumes no functional PASS and must be diagnosed before another live attempt.

The baseline slot captures root help and ordinary test output. The candidate slot runs the exact inspect/profile commands from the matrix. `--format rich` output is captured as actual text at a fixed 120-column width and stable English locale; JSON and TOON are captured separately and decoded for assertions. ANSI/TTY-only progress is not claimed: redirected bounded output is a real renderable text projection, and any unsupported interactive/progress state is reported explicitly.

Each sanitized text capture is rendered by the existing tracked test-repository helper, using the same reviewed helper/font bytes for baseline and candidate:

```bash
"$INTERPRETER" "$SLOT_CHECKOUT/tests/e2e/render_terminal.py" \
  --input "$CAPTURE_TXT" \
  --font "$SLOT_CHECKOUT/tests/e2e/fixtures/terminal-font.ttf" \
  --columns 120 \
  --label "$PRODUCT_SHA" \
  --output "$CAPTURE_PNG"
```

The command SHALL exit zero, the PNG SHALL be non-empty, and the renderer SHALL reject ANSI, carriage returns, invalid UTF-8, or lines wider than 120 columns. Images preserve the tested output language and are labelled as renders of captured text, not terminal-window screenshots. Renderer script/font SHA-256 values and command exit codes are recorded in Multica.

Implementer's reviewed `tests/e2e/fingerprint_database.py` SHALL resolve `ENVIRONMENT_ID` through the slot-local SDK state selected by `ODCLI_USER_ROOT=$HOME/.odcli-$SLOT_ID`, require the environment's `target_db_name` to equal `TARGET_DATABASE`, and read its owner-only `generated_config_path`. It SHALL invoke exactly `/opt/homebrew/bin/pg_dump` with `--data-only --format=plain --no-owner --no-privileges --quote-all-identifiers --encoding=UTF8`; host, port, user, and database come from the generated config, while the password is passed only in the child environment and never in argv/stdout/stderr/evidence. The helper SHALL normalize only PostgreSQL 18's random `\\restrict`/`\\unrestrict` guard lines, retain every other dump byte, and emit a mode-0600 JSON document containing only schema version, `database_bound=true`, SHA-256, normalized byte count, COPY-section count, and sequence-statement count.

Tester invokes it immediately before the first candidate inspect action and immediately after the last one:

```bash
ODCLI_USER_ROOT="$HOME/.odcli-$SLOT_ID" \
"$INTERPRETER" "$SLOT_CHECKOUT/tests/e2e/fingerprint_database.py" \
  --environment-id "$ENVIRONMENT_ID" \
  --expected-database "$TARGET_DATABASE" \
  --pg-dump /opt/homebrew/bin/pg_dump \
  --output "$EVIDENCE_DIR/e2e-01-before-db.json"
```

The after invocation differs only in `--output .../e2e-01-after-db.json`. Both SHALL exit zero and the two complete bounded JSON documents SHALL be byte-equal. The helper SHALL create its raw dump as an owner-only temporary file, delete it in `finally` on success, timeout, signal, or `pg_dump` failure, and report only a sanitized failure class/exit code. Tester wraps slot execution in an `EXIT INT TERM` cleanup trap that calls the same wrapper `remove` command for every successfully allocated slot; fingerprint failure prevents E2E PASS but does not skip slot/database cleanup. No raw dump, connection field, database name, row value, or credential is attached or committed. A differing fingerprint, newly started HTTP listener/runtime, extra output key, credential/value leak, missing native profile entry, or profile mismatch fails the applicable case.

## Evidence, attempts, and cleanup

Before the paired walkthrough, Tester records baseline/candidate product SHAs, test-repository SHA, slot/database/port ownership, product focused-test/CI disposition, locale/width, renderer path/version, and readiness checks. One consolidated baseline/candidate pair is the planned final evidence run. A product-code change invalidates candidate evidence; rerun only impacted cases after focused deterministic checks and explicit impact assessment. Do not spend live attempts debugging scripts or setup.

Tester posts one final Multica comment containing product PR/commit links, case-level PASS/FAIL, concise assertions, exact commands and exit codes, sanitized hashes/provenance, required PNG attachments plus attachment read-back, the functional test-repository MR/commit/helper/test links, and cleanup result. Actual images, text/log output, JSON/TOON, diagnostics, hashes, environment identifiers, hostnames, and database names remain Multica-only; no evidence-only file is committed to GitHub or GitLab. Product tests/fixtures/generated command documentation and the reusable fingerprint helper/tests remain normal implementation commits and are linked, not copied as evidence.

Cleanup preserves product and any functional test-repository branches/MRs/commits. It removes each disposable slot using the same wrapper and project, verifies the unique database/checkouts are gone and ports are free, and deletes staged evidence only after successful attachment/read-back. If a product runtime was never started, none is stopped. If any auxiliary process was started, Tester stops only its recorded PID after comparing it with `multica daemon status --output json`; the Multica daemon, Colima, persistent PostgreSQL cluster, reference checkout, PR/MR, and branches are never stopped, merged, closed, or deleted.

```bash
python3 scripts/agent_checkout.py remove "$SLOT_ID" \
  --project ~/e2e/odcli/numbered-project-e2e
```

## Automated verification ownership

Implementer owns the tests and fixtures required by tasks `1.1`–`5.1`: focused resource/model/runner tests; CLI/help/output/redaction and `PUBLIC_LEAF_CASES` matrices; exact unprofiled shape regression; existing real-Odoo probe addon/tests; generated command matrix; documentation contracts; and any minimal existing CI selector update. Tester does not modify them and reports findings against the exact candidate.

Candidate verification SHALL include focused affected tests, `make lint`, `make types`, the existing offline test job, existing real-Odoo smoke/full disposition as applicable, packaging/documentation contracts, `git diff --check`, and strict scoped OpenSpec validation. An automated local suite expected to exceed ten minutes runs in GitHub Actions on the exact published candidate; if a local suite reaches ten minutes, only its owned test process is stopped and the result is moved to CI. Manual agent-driven CLI actions, evidence inspection, and image preparation are not automatic suites and retain the time needed for honest verification.

## Delivery topology

`delivery_mode=dag`.

The authoritative `Estimate, hours` property is above the multi-package threshold. Stage 2 contains two naturally independent packages: registry inspection owns a new resource/fixed scripts and instance initialization, while test profiling owns the existing test runner/converter. Their shared public models and exports are completed by the stage-1 predecessor, so the siblings have no conflicting write zones. Every `tasks.md` item is assigned exactly once.

### `WP-MYL-151-01` — Typed diagnostic contracts

- **Stage:** `1`
- **Direct `depends_on`:** none
- **Task coverage:** `1.1`, `1.2`
- **Independent deliverable:** the complete frozen public data contract for three inspection results and opt-in test profile input/output, including validation, serialization, lazy exports, and backward-compatible construction.
- **Owned responsibility scope:** inspection/profile model modules or the smallest existing model placement, `OdooTestSpec`/`OdooTestResult` field extensions, root/model lazy exports, and directly related model/serialization tests. Critical shared files include `src/odoo_instance_sdk/models/command.py`, any new focused model module, `src/odoo_instance_sdk/models/__init__.py`, `src/odoo_instance_sdk/__init__.py`, and their focused tests.
- **Contract surface:** exact nullable fields and defaults; frozen/unknown-field-forbidden values; bounded strings; finite/non-negative profile data; no secrets/callables; ordinary construction remains valid.
- **Definition of done / evidence:** model and export tests prove exact fields/defaults, valid round trips, invalid-value rejection, compatibility without profile, and no sensitive/dynamic values. The completed public types are stable inputs for both stage-2 packages.
- **Parallel-safety rationale:** foundation runs alone and owns every shared public model/export file before sibling work begins.

### `WP-MYL-151-02` — Read-only registry inspector

- **Stage:** `2`
- **Direct `depends_on`:** `WP-MYL-151-01`
- **Task coverage:** `1.3`, `2.1`, `2.2`, `2.3`
- **Independent deliverable:** `instance.inspector` with three typed convenience/command pairs, fixed read-only shell scripts, strict converters, deterministic absence, and focused plan/safety tests.
- **Owned responsibility scope:** new inspector resource/module, `OdooInstance` resource initialization/typing, fixed script generation and payload conversion, and inspector-focused tests. Critical shared files include `src/odoo_instance_sdk/resources/instance/__init__.py`, `resources/instance/identity.py` only for initialization/type wiring if required, the new inspector module, and directly related tests/fixtures. It consumes stage-1 types/exports without revising them.
- **Contract surface:** public method names and delegation; model/field/ref result shapes; identifier validation; 256-code-point bounds; missing-object nulls; `env.ref(..., raise_if_not_found=False)`; one existing immutable shell command with `commit=false`; no HTTP, raw SQL, record values, writes, or dynamic query API.
- **Definition of done / evidence:** focused unit/recording-executor tests prove existing/missing conversions, early invalid-input failure, exact captured process/rollback parity, configured context reuse, one process, no commit/write/HTTP, sanitized errors, and no sensitive/callable payload values.
- **Parallel-safety rationale:** this package owns new inspection code and narrow instance resource wiring; its stage-2 sibling owns test runner/converter files and test-profile tests, so neither writes the other's critical paths.

### `WP-MYL-151-03` — Native test profile runner

- **Stage:** `2`
- **Direct `depends_on`:** `WP-MYL-151-01`
- **Task coverage:** `3.1`, `3.2`, `3.3`
- **Independent deliverable:** the existing test runner optionally maps Odoo 19 `_r.stats` into validated, ordered typed profile data while ordinary result and exit behavior remain unchanged.
- **Owned responsibility scope:** `internal/automation.py` test source/converter path, resource testing adapter only if required, and focused runner/test-model compatibility tests. Critical shared files include `src/odoo_instance_sdk/internal/automation.py`, `src/odoo_instance_sdk/resources/testing.py`, `tests/unit/resources/test_cli_automation.py`, and directly related runner tests. It consumes stage-1 types without revising model/export files.
- **Contract surface:** one `run_tests()` invocation; profile flag; native `Stat.time`/`queries`; validation; stable order/totals; actionable invalid-stats failure; unchanged counts/failures/zero/diagnostics/exit rules; no logs, custom counters, second runner, or persistence.
- **Definition of done / evidence:** focused tests prove flag/source capture, single invocation, mapping/order/totals, malformed/non-finite/negative rejection, failure/process/zero cases, and exact unprofiled payload/result compatibility.
- **Parallel-safety rationale:** this package writes only the existing test runner/converter and its focused tests; it does not touch inspector resource/instance initialization owned by its stage-2 sibling.

### `WP-MYL-151-04` — CLI convergence and canonical output

- **Stage:** `3`
- **Direct `depends_on`:** `WP-MYL-151-02`, `WP-MYL-151-03`
- **Task coverage:** `4.1`, `4.2`, `4.3`, `4.4`, `4.5`
- **Independent deliverable:** one integrated CLI surface for inspect and test profile with SDK-first delegation, Rich/JSON/TOON parity, unprofiled compatibility, exact leaf inventory, and immutable dry-run reuse.
- **Owned responsibility scope:** new inspect command module/lazy registration; existing test and compatibility-module adapters; command-local Rich projections; canonical `PUBLIC_LEAF_CASES`, generated command matrix, help/output/security/characterization tests, and directly related fixtures/snapshots. Critical shared files include `commands/cli_parts/registration.py`, the new inspect command, `commands/test.py`, the narrow `commands/module.py` compatibility seam, `tests/unit/test_cli_output_modes.py`, `tests/unit/commands/test_test.py`, CLI security/help tests, and `tests/integration/real_odoo/command-matrix.md`.
- **Contract surface:** exact command names/options; root context precedence; no inspect dry-run/command-local environment/query options; matching SDK primitives; conditional profile field; clean machine stdout; stderr diagnostics; stable formats/order; single canonical inventory; unchanged test selection/exit semantics.
- **Definition of done / evidence:** parameterized CLI matrices pass for existing/missing inspect values and profiled/unprofiled target/changed/alias paths; decoded JSON/TOON are equal; Rich shows the same graph; unprofiled key sets are unchanged; dry-run uses the existing test command; no leaks/ANSI/progress/duplicate rows or output authorities remain.
- **Parallel-safety rationale:** this is the sole convergence owner after both complete SDK contracts exist; no sibling writes CLI registries, shared output tests, or canonical inventory files.

### `WP-MYL-151-05` — Real-Odoo integration, guidance, and exact-candidate delivery

- **Stage:** `4`
- **Direct `depends_on`:** `WP-MYL-151-04`
- **Task coverage:** `5.1`, `5.2`, `5.3`, `5.4`
- **Independent deliverable:** one documented, fully checked exact implementation candidate with existing real-Odoo automated coverage and an independently executable numbered-slot evidence contract.
- **Owned responsibility scope:** existing real-Odoo probe addon/tests and disposition wiring; README/CLI/SDK documentation and documentation contracts; required existing CI selector/fixture changes; repository/OpenSpec checks; publication evidence and Tester handoff. In the test repository, this WP also owns the functional `tests/e2e/fingerprint_database.py` helper and its focused fake-process/config tests; the existing renderer/font are reused unchanged. Critical product files include `tests/fixtures/addons/odcli_e2e_probe/`, `tests/integration/real_odoo/`, applicable real-Odoo workflow/action files, `README.md`, `docs/python-sdk.md`, and directly related verification metadata. Directly related tests, generated command documentation, fixtures, and service files are included without becoming an exhaustive allowlist.
- **Contract surface:** live Odoo 19 registry/XML-ID and native stats behavior; no mutation/no HTTP/no leak assertions; documentation boundaries; existing CI workflow reuse; exact product SHA; private test-repository parameterization; Multica-only images/logs/hashes; safe numbered-slot cleanup.
- **Definition of done / evidence:** focused/repository/strict OpenSpec checks pass under the ten-minute policy or run in CI on the exact candidate; real-Odoo coverage proves non-empty native profile and bounded inspection; the test-repository helper tests prove exact environment/database binding, normalized deterministic hashing, secret-free bounded output, failure sanitization, and temporary-file cleanup; documentation matches the public contract; both reviewed SHAs are published; independent Tester executes `E2E-01`/`E2E-02`, attaches required rendered PNGs and sanitized assertions with read-back, and proves cleanup while preserving branches/commits.
- **Parallel-safety rationale:** final integration/verification begins only after the single CLI convergence package; it has no sibling and owns shared documentation, real-Odoo fixtures, and delivery evidence.

## Stage and dependency contract

| `wp_id` | `stage` | direct `depends_on` |
| --- | ---: | --- |
| `WP-MYL-151-01` | 1 | none |
| `WP-MYL-151-02` | 2 | `WP-MYL-151-01` |
| `WP-MYL-151-03` | 2 | `WP-MYL-151-01` |
| `WP-MYL-151-04` | 3 | `WP-MYL-151-02`, `WP-MYL-151-03` |
| `WP-MYL-151-05` | 4 | `WP-MYL-151-04` |

Stages are consecutive topological levels. Stage 2 is the executable parallel frontier; its siblings share only the completed stage-1 contract and have disjoint critical write zones. Dependencies are direct only and do not encode WIP limits.

## Coverage audit

Tasks `1.1`–`1.2` belong only to `WP-MYL-151-01`; `1.3` and `2.1`–`2.3` only to `WP-MYL-151-02`; `3.1`–`3.3` only to `WP-MYL-151-03`; `4.1`–`4.5` only to `WP-MYL-151-04`; and `5.1`–`5.4` only to `WP-MYL-151-05`. Automated product checks remain Implementer-owned and the live numbered-slot walkthrough remains the independent Tester gate. Any product-scope expansion, changed public field/absence rule, new shared abstraction/dependency, or changed contract dependency requires a planning revision and re-estimation; operational stage advancement does not.
