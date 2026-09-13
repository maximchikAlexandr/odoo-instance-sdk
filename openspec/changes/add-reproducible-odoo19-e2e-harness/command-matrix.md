# Public CLI traceability matrix

This is a reviewed projection of `tests/unit/test_cli_output_modes.py::PUBLIC_LEAF_CASES` at canonical-inventory base `af9e1b3e8d127145b9488f11ec79519f9442db46`; the original full-change audit base remains `0ff164636617c03a51277055af45cef009277368`. It is not a source registry. Implementation adds the disposition and evidence fields to each existing `PublicLeafCase`; the generator SHALL emit this exact provenance, rewrite the complete 50-row table, and fail the check on any byte drift. `smoke` means covered in PR smoke and full; `critical` means the full critical path; `focused` means a full-tier case around the critical path; `not-applicable` requires the recorded reason.

| Public leaf | Existing class | Dry-run | E2E disposition | Evidence / rationale |
| --- | --- | ---: | --- | --- |
| `init` | mutating-or-spawning | yes | smoke | E2E-SM-01 / E2E-CP-01: manifest and target project |
| `doctor` | bounded-read-only | no | critical | E2E-CP-14: final project diagnosis |
| `stop` | mutating-or-spawning | yes | critical | E2E-CP-13: owned target process stop and repeat |
| `resource ls` | bounded-read-only | no | critical | E2E-CP-12: run-owned inventory |
| `resource doctor` | bounded-read-only | no | smoke | E2E-SM-05 / E2E-CP-12: ownership and health |
| `env create` | mutating-or-spawning | yes | critical | E2E-CP-02: pinned source, explicit owned venv, and audited hash-lock first install |
| `env ls` | bounded-read-only | no | critical | E2E-CP-03: registered environment identity |
| `env path` | bounded-read-only | no | critical | E2E-CP-03: worktree/config/venv paths |
| `env show` | bounded-read-only | no | not-applicable | upstream environment inspection is covered by focused command tests |
| `env rm` | mutating-or-spawning | yes | critical | E2E-CP-15: exact owned cleanup and repeat |
| `env sync` | mutating-or-spawning | yes | critical | E2E-CP-04: public `--hash-lock`/digest sync, `--require-hashes`, and idempotency |
| `backup ls` | bounded-read-only | no | critical | E2E-CP-09: downloaded catalog row |
| `backup inspect` | bounded-read-only | no | critical | E2E-CP-09: exact size/SHA/source identity |
| `backup validate` | bounded-read-only | no | smoke | E2E-SM-03 / E2E-CP-09: ZIP plus filestore validation |
| `backup rm` | mutating-or-spawning | yes | focused | E2E-FC-09: owned artifact deletion and repeat |
| `db refresh` | mutating-or-spawning | yes | smoke | E2E-SM-02 / E2E-CP-08: real remote download and restore |
| `db restore` | mutating-or-spawning | yes | focused | E2E-FC-05: exact catalog restore, occupied/repeat cases |
| `db ls` | bounded-read-only | no | smoke | E2E-SM-04 / E2E-CP-10: restored DB visible |
| `db reset-admin-password` | mutating-or-spawning | yes | focused | E2E-FC-06: target Odoo shell reset and redaction |
| `db rm` | mutating-or-spawning | yes | focused | E2E-FC-10: owned DB deletion; foreign DB refusal |
| `eval` | process-previewable-read-only | yes | critical | E2E-CP-11: restored model/attachment assertion |
| `exec` | mutating-or-spawning | yes | focused | E2E-FC-07: framed script result and non-zero failure |
| `test` | process-previewable-read-only | yes | critical | E2E-CP-07: probe module native runner report |
| `module ls` | process-previewable-read-only | yes | critical | E2E-CP-05: probe discovery/install state |
| `module update` | mutating-or-spawning | yes | critical | E2E-CP-06: deterministic update and repeat |
| `module test` | mutating-or-spawning | yes | focused | E2E-FC-08: compatibility alias equals top-level test |
| `module info` | bounded-read-only | no | not-applicable | upstream module inspection is covered by focused command tests |
| `module where` | bounded-read-only | no | not-applicable | upstream filesystem inspection is outside the lifecycle fixture |
| `module deps` | bounded-read-only | no | not-applicable | upstream dependency inspection is covered offline |
| `module install-order` | process-previewable-read-only | yes | not-applicable | upstream planning leaf is outside the lifecycle fixture |
| `translations export` | mutating-or-spawning | yes | not-applicable | Browser/localization/business behavior is outside the `base`-only fixture; existing command-plan tests remain authoritative |
| `deps verify` | process-previewable-read-only | yes | critical | E2E-CP-04: owned Python/Odoo dependency preflight |
| `vscode generate` | mutating-or-spawning | yes | not-applicable | Editor artifact generation is orthogonal to the server/database lifecycle and remains covered offline |
| `postgres approve-image` | mutating-or-spawning | yes | critical | E2E-CP-01: exact trusted image digest |
| `postgres ps` | bounded-read-only | no | critical | E2E-CP-01: health/ownership snapshot |
| `postgres up` | mutating-or-spawning | yes | critical | E2E-CP-01: public owned cluster start |
| `postgres stop` | mutating-or-spawning | yes | critical | E2E-CP-15: public stop plus harness volume cleanup |
| `db locks` | bounded-read-only | no | focused | E2E-FC-11: bounded real PostgreSQL diagnostics |
| `db stats` | bounded-read-only | no | focused | E2E-FC-11: restored DB statistics |
| `db bloat` | bounded-read-only | no | focused | E2E-FC-11: bounded estimate mode |
| `db init-monitoring` | mutating-or-spawning | yes | focused | E2E-FC-11: extension/setup idempotency |
| `psql` | native-passthrough | yes | focused | E2E-FC-12: inherited stream and dry-run plan |
| `run` | native-passthrough | yes | critical | E2E-CP-07 / E2E-CP-13: HTTP-ready lifecycle and SIGINT |
| `logs` | jsonl-stream | no | focused | E2E-FC-13: bounded subscription/cancellation and redaction |
| `shell` | native-passthrough | yes | focused | E2E-FC-06: exact target database and exit semantics |
| `monitor` | native-passthrough | no | not-applicable | Long-running dashboard service is a separate CI/dashboard concern and does not validate Odoo 19 lifecycle |
| `git commit` | mutating-or-spawning | yes | not-applicable | upstream Git workflow is outside the disposable Odoo fixture |
| `git check` | bounded-read-only | no | not-applicable | upstream Git policy inspection is covered by unit contracts |
| `git absorb` | mutating-or-spawning | yes | not-applicable | upstream Git mutation is outside the disposable Odoo fixture |
| `git sync` | mutating-or-spawning | yes | not-applicable | upstream remote Git publication is intentionally outside E2E |

## Scenario coverage

- E2E-SM-01..05: image-backed PR smoke; proves orchestration, backup/restore, validation, machine output, and cleanup only.
- E2E-CP-01..15: one stateful source-backed full critical path, executed in order within one serial test.
- E2E-FC-01..04: incorrect password, unreachable source, truncated/incompatible archive, occupied/repeated restore.
- E2E-FC-05..13: catalog restore, password reset, exec/module aliases, deletion, diagnostics, native streams, logs, and cleanup failures.
- E2E-REC-01..03: SIGINT, timeout, and partial-publication cleanup/leak audit.
- E2E-SEC-01..03: secret-canary scan, machine-output/non-zero semantics, and bounded artifact packaging.

The generator SHALL sort by the existing tuple order, not alphabetically, so changes remain reviewable against Click registration and the canonical unit test.
