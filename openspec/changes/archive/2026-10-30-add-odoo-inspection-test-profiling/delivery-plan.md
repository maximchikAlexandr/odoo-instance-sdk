## Delivery basis

- Planning issue: `MYL-151`
- OpenSpec change: `add-odoo-inspection-test-profiling`
- Approved implementation base: `f0918da2d36b59c35cf0a949efc58744d1efb7f9`
- Estimate authority: numeric optimistic, weighted, and pessimistic totals live only in the root planning issue properties. This document records the executor, evidence, confidence, scope, and topology without duplicating them.
- Executor/basis: remaining active hours for one experienced developer familiar with this Python/Odoo repository, without AI acceleration. The estimate includes implementation, focused tests, integration/documentation, and likely review repair; unattended CI, approval queues, meetings, and external waiting are excluded.
- Confidence: medium-high and uncalibrated. The exact public contract is fixed by GitHub #37 and all implementation seams have close local analogues. Remaining uncertainty is limited to Odoo 19 registry attribute normalization, native `_r.stats` conversion, and the existing real-Odoo fixture runtime. No tests or project scripts were run for estimation.
- Evidence: `models/command.py::OdooTestSpec/OdooTestResult`, `internal/automation.py::_test_runner_source` and `run_odoo_tests_command`, `resources/instance/identity.py::_shell_script_command`, `commands/test.py`, shared output helpers, lazy CLI registration, `PUBLIC_LEAF_CASES`, and the existing real-Odoo focused fixture/handler. Shared types/exports and review repair are counted once; unchanged selection, exit, locking, and process behavior are covered by existing tests rather than reimplemented.

## Ponytail result

The implementation retains only components explicitly required by #37 or needed to preserve one typed optional profile object: one facade, three inspection result models, one profile entry and one aggregate. It reuses the existing shell command, test runner, output document, selection logic, inventory, real-Odoo harness, numbered-slot wrapper, and Python renderer/font.

Removed from the previous plan: profile support on the legacy `module test` alias; exhaustive combinations for unchanged flags and exit branches; a test-repository database fingerprint helper/MR; whole-database dump normalization; Python SDK documentation beyond the requested README/help; multiple WPs, stages, and DAG management. Read-only behavior is verified at `_shell_script_command(commit=False)` and by the focused real-Odoo rollback regression, not by a second database-audit subsystem.

## E2E decision

`e2e_required=true`.

Focused unit and existing real-Odoo CI prove conversion, rollback, result shape, and native stats. A small independent walkthrough remains necessary because #37 changes visible Rich output and because the final acceptance must observe the new CLI against an actual configured Odoo 19 database without an HTTP server. Two focused cases share one baseline/candidate lifecycle; they do not create an additional test harness or product fixture.

The project description supplies `TEST_REPOSITORY_URL`. Tester SHALL use a clean reviewed `TEST_REPOSITORY_SHA` and the existing `binta_test_greeting` addon/test. Private URLs, hostnames, configuration, slot/database identifiers, and raw outputs remain outside public Git.

| Case | Required behavior / remaining live gap | Public actions | Automated disposition | Independent assertions | Multica-only evidence |
| --- | --- | --- | --- | --- | --- |
| `E2E-01` | Live registry/XML-ID values, missing-object behavior, no HTTP prerequisite, and the new Rich tables | Candidate: `inspect model binta.test.greeting`, `inspect field binta.test.greeting greeting`, `inspect ref base.user_admin`, one missing model/ref, plus JSON/TOON parity. Baseline captures root help because inspect does not yet exist. | Tasks `1.1`–`1.3` cover models, fixed source/converters, rollback plan, output parity, and inventory; task `3.1` extends the existing real-Odoo focused case and asserts its fixture sentinel remains unchanged. | Exact bounded keys/values; missing results use `exists=false`; decoded JSON/TOON agree; Rich contains the same facts; no arbitrary record value/credential/callable; no Odoo HTTP runtime starts. | Baseline help text/render, candidate model/field/ref text/renders, sanitized parity assertions, commands/exits, exact SHAs, no-runtime observation. |
| `E2E-02` | A real non-empty Odoo 19 `_r.stats` entry and visible profile output | Baseline and candidate ordinary `odcli test binta_test_greeting`; candidate `--profile` in Rich/JSON/TOON and `--changed --base HEAD --profile --dry-run --format json`. | Tasks `2.1`–`2.2` cover one native invocation, mapping/order/totals, target/changed propagation, dry-run reuse, invalid stats, and ordinary omission; task `3.1` adds the existing real-Odoo profile assertion. | Candidate ordinary decoded shape equals baseline and omits `profile`; profiled formats agree; entry is non-empty with finite duration and integer query count; totals equal entries; dry-run shows one existing command and starts no process. | Ordinary/profile text and before/after renders, sanitized JSON/TOON assertions, dry-run assertion, exact SHAs/commands/exits. |

No OpenAPI operation or dashboard route changes. No new test-repository script or MR is required: `scripts/agent_checkout.py`, `tests/e2e/render_terminal.py`, its tracked font, and the existing addon are sufficient.

## Isolated numbered-slot walkthrough

Tester uses the project-approved resources without touching the legacy/reference runtime:

- wrapper/reference checkout: `~/e2e/odcli/binta-e2e-reference` at clean `TEST_REPOSITORY_SHA`;
- profile: `~/e2e/odcli/numbered-project-e2e`;
- reference checkout argument: `~/e2e/odcli/binta-e2e-reference`;
- one fresh port, unique COPY database, and wrapper-allocated `odcli-N` per baseline/candidate product SHA.

```bash
python3 scripts/agent_checkout.py create \
  --project ~/e2e/odcli/numbered-project-e2e \
  --reference-checkout ~/e2e/odcli/binta-e2e-reference \
  --reference-sha "$TEST_REPOSITORY_SHA" \
  --sdk-sha "$PRODUCT_SHA" \
  --http-port "$HTTP_PORT" \
  --target-database "$TARGET_DATABASE"
```

Readiness records the returned slot, launcher, interpreter, checkout/environment, exact SDK/reference SHAs, owned database/port, and confirms the cluster is healthy, the addon is installed, and no Odoo HTTP runtime is active. It also verifies the reviewed test-repository SHA tracks the renderer/font, `"$INTERPRETER" -c 'import PIL'` succeeds, and the renderer `--help` exits zero. Dirty/wrong revisions, resource collisions, failed COPY, missing launcher/config, or unavailable renderer fail readiness without consuming a functional PASS.

Rich output is captured as actual UTF-8 text at 120 columns and stable English locale. JSON/TOON are captured separately. ANSI, carriage-return progress, and interactive states are not claimed. Existing tracked assets render each sanitized capture:

```bash
"$INTERPRETER" "$SLOT_CHECKOUT/tests/e2e/render_terminal.py" \
  --input "$CAPTURE_TXT" \
  --font "$SLOT_CHECKOUT/tests/e2e/fixtures/terminal-font.ttf" \
  --columns 120 \
  --label "$PRODUCT_SHA" \
  --output "$CAPTURE_PNG"
```

The renderer must exit zero and produce a non-empty PNG; its built-in checks reject ANSI/carriage returns and over-width text. Renderer/font hashes, command exits, and attachment read-back are evidence. No database dump or fingerprint is produced: the product regression proves the immutable rollback command and the real-Odoo focused case proves the fixture sentinel remains unchanged.

## Evidence and cleanup

Tester posts one final Multica comment with case-level result, exact product/test-repository SHAs, product PR/commit links, commands/exits, concise sanitized assertions, required PNG attachments with read-back, and cleanup result. Images, captured text, JSON/TOON, logs, hashes, environment/database/port identifiers, and diagnostics are evidence only and are not committed.

Each allocated slot is removed in an `EXIT INT TERM` cleanup path with the same wrapper and project. Cleanup verifies its checkout/database are gone and port is free, while preserving product/test branches and commits. It never stops Colima, the persistent PostgreSQL cluster, reference checkout, Multica daemon, or unrelated processes, and it does not merge/close/delete branches or MRs.

```bash
python3 scripts/agent_checkout.py remove "$SLOT_ID" \
  --project ~/e2e/odcli/numbered-project-e2e
```

## Automated verification ownership

Implementer owns the focused product tests and existing real-Odoo fixture/handler changes in tasks `1.1`–`3.1`; Tester does not edit them. Candidate checks include the focused affected tests, `make lint`, `make types`, ordinary offline tests, the existing real-Odoo disposition, packaging/help checks, `git diff --check`, and scoped strict OpenSpec validation. An automatic suite expected to exceed ten minutes runs in GitHub Actions on the exact candidate; a local suite reaching the limit is stopped without affecting manual walkthrough time.

## Delivery topology

`delivery_mode=single_wp_no_dag`.

The authoritative weighted estimate property is within the mandatory single-WP threshold. The work is also naturally cohesive: the facade/models, runner extension, CLI projection, and focused verification touch adjacent shared contracts and converge in one candidate. Creating separate coordination fronts would add handoffs without isolating independently shippable behavior.

### `WP-MYL-151-01` — Minimal Odoo diagnostics

- **Direct `depends_on`:** none
- **Task coverage:** `1.1`, `1.2`, `1.3`, `2.1`, `2.2`, `3.1`, `3.2`, `3.3`
- **Independent deliverable:** the complete #37 inspector/profile capability, focused regressions, existing-harness real-Odoo proof, documentation, and one exact candidate ready for independent E2E.
- **Owned responsibility scope:** the smallest model placement and lazy exports; one inspector resource and `OdooInstance` initialization; existing shell/test runner conversion; lazy inspect CLI registration; top-level test flag/result projection; `PUBLIC_LEAF_CASES` rows and directly related tests; existing real-Odoo probe/handler; README/help and generated command matrix. Directly related tests, fixtures, snapshots, docs, and CI selector files are included without becoming an exhaustive allowlist.
- **Critical shared files:** `models/command.py` or one focused inspection model module, `resources/instance/__init__.py`, the new inspector resource, `internal/automation.py`, `commands/test.py`, inspect command registration, public exports, `tests/unit/test_cli_output_modes.py`, and existing real-Odoo focused files.
- **Contract surface:** exact #37 fields; `exists=false` missing rule; `commit=false`; no arbitrary values/callables; one native runner; optional stable profile; unchanged ordinary result; top-level flag only; identical Rich/JSON/TOON facts.
- **Definition of done / evidence:** focused tests and repository gates pass; real-Odoo existing fixture proves inspection and a non-empty profile; README/help match the contract; exact candidate is published; Tester completes both cases with required images/read-back and safe cleanup.
- **Parallel-safety rationale:** not applicable. This mode intentionally has one WP and no stages, frontier, or DAG.

## Coverage audit

The single WP covers every task exactly once. There are no stages or dependency edges. A public-field change, generic ORM scope, second runner, new persistence/dependency, or new independently owned test harness requires a planning revision and re-estimation; ordinary implementation/test files directly related to the eight tasks do not.
