# Delivery plan

## Planning baseline

- Task key: `MYL-511`.
- OpenSpec change: `support-safe-hotfix-update`.
- Approved implementation base: `origin/main` at `d6a4e093377cd6c29fcf564406626ea5f17718aa`.
- Planning branch/worktree: `feat/MYL-511-safe-hotfix-update` in the dedicated managed checkout; production code is unchanged.
- Test repository: resolve the private URL from the current Multica project description at execution time. Private URL/hostname, credentials, personal data, and runtime evidence SHALL NOT enter public Git artifacts.
- Existing foundation retained: one alternate-tool installer/publisher, exact VCS provenance, disposable selector probe, per-identity lifecycle lock, atomic backup/rollback publication, hot-fix issue/PR/review policy, selected-root isolation, canonical update/reconciliation, bounded inspection, and current CI/test infrastructure.

## Estimate contract

- Authoritative totals SHALL exist only in root planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; this file intentionally does not duplicate them.
- Basis: remaining work from the approved base to all acceptance criteria for one experienced developer familiar with Python, uv tools, POSIX locking, GitHub CLI, and pytest, without AI acceleration. Active hours include investigation, implementation, required tests/docs, attended verification, likely review fixes, and local E2E script/fixture work; unattended CI, approvals, meetings, and external waiting are excluded.
- Confidence: medium. Acceptance, affected paths, reusable transaction seams, and close local tests are directly inspectable; uncertainty is concentrated in adversarial rollback/locking coverage, transition-review semantics across historical state formats, real uv installation, and review repair.
- Calibration: evidence-based and uncalibrated because no comparable actual executor timings were supplied.
- Evidence: the shared alternate transaction and rollback in `internal/alternate_tool.py`; review and GitHub policy helpers under `scripts/odcli_agent_tools/`; focused alternate/helper/packaging acceptance tests; architecture inventories; current CI workflows; and this change's complete requirement-to-verification map.
- Material assumptions: existing manifest version, lock protocol, atomic same-filesystem replacement, process boundary, and canonical trusted-interpreter bootstrap remain reusable; no public product command, persistent journal, state format migration, new dependency, Windows support, or live external-resource orchestration is added. Scope expansion requires re-estimation.
- Tests SHALL NOT be run to produce the estimate.

## Delivery topology

Delivery mode: `dag`.

The authoritative `Estimate, hours` property is above the multi-package threshold. There is a real parallel frontier: transition-review work and shared lifecycle work have disjoint production and focused-test write zones. They converge into helper policy, followed by one serial integrated verification/publication package.

Stage mapping: `WP-MYL-511-01 -> 1`, `WP-MYL-511-02 -> 1`, `WP-MYL-511-03 -> 2`, `WP-MYL-511-04 -> 3`. Stages are consecutive topological layers, not WIP policy.

### WP-MYL-511-01 — Bind independent review to the state transition

- **Stage:** `1`.
- **Tasks:** `1.1`, `1.2`.
- **Depends on:** none.
- **Standalone deliverable:** the repository reviewer can produce a read-only, session-backed compatibility verdict bound to one exact installed SHA and one exact candidate SHA, and fails closed on unproven selected-root migration or shared-resource safety.
- **Owned responsibility scope:** `scripts/odcli_agent_tools/reviewer.py`, transition-review prompt/schema material, reviewer-focused portions of `tests/unit/test_odcli_autonomous_skill.py`, and directly related fixtures/docs. It does not modify alternate publication, hot-fix mutation policy, or shared lifecycle tests.
- **Contract surface:** `transition INSTALLED_SHA CANDIDATE_SHA`; structured `previous_sha`/`sha`/compatibility/findings under a non-empty session envelope; read-only code inspection; no state mutation or merge authorization.
- **DoD / evidence:** exact old/new binding, invalid SHA, unavailable commit, incompatible/uncertain transition, prompt coverage, session provenance, redaction, and existing candidate-only compatibility behavior pass focused tests; no product CLI or process architecture changes.
- **Parallel-safety rationale:** production and focused-test writes are confined to reviewer evidence. WP-02 owns the package lifecycle and separate alternate-tool tests; neither package edits the other's critical files.

### WP-MYL-511-02 — Preserve state in the shared replacement transaction

- **Stage:** `1`.
- **Tasks:** `2.1`, `2.2`, `2.3`, `4.1`.
- **Depends on:** none.
- **Standalone deliverable:** the package-owned alternate lifecycle can replace one verified hot-fix installation by exact SHA while preserving its selected root, serializing launcher use, rejecting self-replacement/foreign ownership, and restoring the old usable installation at every activation failure boundary.
- **Owned responsibility scope:** `src/odoo_instance_sdk/internal/alternate_tool.py`, its private CLI projection, `tests/unit/test_alternate_tool.py`, and directly related package/process fixtures. It owns expected-manifest revalidation, preservation mode, bounded result fields, rollback injection, and lock behavior; it does not edit reviewer/helper policy files or their shared test module.
- **Contract surface:** the existing `install_alternate_tool(..., replace=True)` transaction with narrowly gated hot-fix state preservation; unchanged manifest version, launcher identity, state-root path, process seam, and numbered replacement behavior.
- **DoD / evidence:** healthy matching replacement succeeds; populated state remains byte-identical; preparation failures publish nothing; every tool/bin/launcher/manifest activation failure restores the old usable installation; pre-lock drift, symlink/foreign identity, self-target, and concurrent shared-lock cases fail safely; canonical and neighbors remain unchanged.
- **Parallel-safety rationale:** this package exclusively owns the lifecycle implementation and alternate-tool tests. WP-01 touches only reviewer evidence, so stage-1 siblings share no mutable production or test file.

### WP-MYL-511-03 — Expose fail-closed hot-fix advance policy

- **Stage:** `2`.
- **Tasks:** `3.1`, `3.2`.
- **Depends on:** `WP-MYL-511-01`, `WP-MYL-511-02`.
- **Standalone deliverable:** `fix_tool.py advance` validates the installed identity, transition review, current PR head and issue link before delegating exactly once to the shared replacement transaction, while canonical `update` remains unchanged.
- **Owned responsibility scope:** `scripts/odcli_agent_tools/fix_tool.py`, helper-policy portions of `tests/unit/test_odcli_autonomous_skill.py`, and direct parser/output fixtures. Reviewer semantics and lifecycle internals are fixed predecessor contracts and SHALL NOT be reimplemented here.
- **Contract surface:** `advance ISSUE SHA --pr PR --review FILE`; healthy installed-manifest inspection; issue/PR/SHA continuity; GitHub head/link validation; one bounded JSON result; unchanged install/reconcile/canonical-update commands.
- **DoD / evidence:** positive delegation plus stale/malformed review, moved PR, same SHA, issue/PR/repository mismatch, unhealthy install, self-target, and zero-mutation failures pass focused tests; no duplicate uv, provenance, lock, launcher, rollback, or cleanup logic exists.
- **Parallel-safety rationale:** both direct predecessors are complete before this consumer writes the shared helper test module or composes their contracts, preventing contract drift and same-file conflict with WP-01.

### WP-MYL-511-04 — Integrated preservation evidence and publication gates

- **Stage:** `3`.
- **Tasks:** `4.2`, `4.3`, `4.4`, `5.1`, `5.2`.
- **Depends on:** `WP-MYL-511-03`.
- **Standalone deliverable:** repository and local-E2E evidence prove populated-state preservation, real launcher/lock behavior, fail-closed rollback, canonical/neighbor/external-resource isolation, one-owner architecture, and clear supported guidance at exact revisions.
- **Owned responsibility scope:** packaging/integration acceptance, architecture inventories, autonomous-work skill guidance, directly related fixtures/snapshots/docs, repository gates, and the private test-repository E2E script/MR contract. Minimal repairs stay within predecessor contracts; production redesign requires planning revision.
- **Contract surface:** full developer-helper → canonical private engine → staged candidate → atomic publication flow; private test repository resolved from project context; required macOS before/after/result captures; Multica-only runtime evidence; no new public product or OpenAPI surface.
- **DoD / evidence:** all delta scenarios map to passing focused/CI checks or E2E cases; actual predecessor→candidate uv transition preserves state and canonical/neighbor digests; process lock case proves no mixed publication; mandatory text/PNG evidence, exact SHAs, PR/MR/file links, commands/exits and attachment read-back are published in Multica; strict OpenSpec and repository gates pass.
- **Parallel-safety rationale:** serial fan-in after the complete helper flow. This package owns cross-surface acceptance and publication evidence, avoiding concurrent edits to implementation contracts while integrated failures are diagnosed.

## Topology and task coverage audit

Direct edges only:

- `WP-MYL-511-01 -> WP-MYL-511-03`
- `WP-MYL-511-02 -> WP-MYL-511-03`
- `WP-MYL-511-03 -> WP-MYL-511-04`

Execution frontiers: stage 1 runs WP-01 and WP-02 independently; stage 2 joins their fixed contracts in WP-03; stage 3 verifies the integrated behavior and evidence. No transitive edge is repeated.

Task coverage is complete and one-time:

| WP | OpenSpec tasks |
| --- | --- |
| `WP-MYL-511-01` | `1.1`, `1.2` |
| `WP-MYL-511-02` | `2.1`, `2.2`, `2.3`, `4.1` |
| `WP-MYL-511-03` | `3.1`, `3.2` |
| `WP-MYL-511-04` | `4.2`, `4.3`, `4.4`, `5.1`, `5.2` |

## E2E decision

`e2e_required=true`.

Focused unit/integration tests are sufficient for every validation branch, injected publication failure, review schema error, live-PR response variant, and architecture-inventory rule. One local E2E remains necessary because no cheaper check proves that a real immutable uv installation can replace another under the actual filesystem lock and launcher boundary while retaining a non-empty selected root and leaving an isolated canonical tool unchanged. The visible developer-helper command/output also changes, so macOS before/after terminal-text renders are mandatory.

Planning SHALL NOT execute tests. Tester SHALL use an isolated OS `HOME`, a dedicated canonical uv-tool fixture, a unique hot-fix identity, private XDG/cache paths, and its own product/test-repository checkouts. If an Odoo project is included for observation, it SHALL use a dedicated `odcli-N`, database, filestore, Docker/Compose project, and loopback port rather than a reference checkout or existing database.

## Verification matrix

| Case ID | OpenSpec requirement/scenario | Verification gap / local E2E decision | Affected API schema | Local test script | Independent Tester steps | CI disposition / assertions | Artifacts and images |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E2E-01 | Reviewed successor; compatible transition; populated state survives; neighbor/external preservation; supported guidance | Local E2E required for real uv staging, provenance, launcher, manifest swap, and retained filesystem state | None; developer helper and private JSON engine only | `tests/e2e/myl511_hotfix_advance.py` in the private test-repository MR | Build an isolated candidate canonical uv tool; use the package engine only to seed a healthy predecessor manifest at a remote-reachable predecessor SHA; populate catalogue/backup/environment/project sentinel files; obtain a real transition review for predecessor→candidate; invoke public `fix_tool.py advance` against the actual candidate PR head; invoke the retained launcher; inspect manifest/root/canonical/neighbor hashes; cleanup only dedicated roots | Implementer adds focused engine/helper/packaging tests. Assertions: old/new provenance exact; launcher/root stable; every state sentinel retained byte-for-byte; candidate launcher usable; canonical/neighbor hashes unchanged; no cleanup/resource operation occurs | Multica-only sanitized command/exit output, product/test SHAs, PR/MR/file links, hashes/assertions, review provenance, and `myl511-advance-result.png` plus captured text |
| E2E-02 | Concurrent launcher use delays publication safely; selected tool cannot replace itself | Same-process mocks cannot prove real `flock` lifetime across launcher/updater processes, so a focused local process case is required; self-replacement is a lower-level deterministic check | None | Same script, `lock` case | Hold the predecessor launcher on a bounded command under its shared lock; start advance externally; prove published SHA remains old until holder exits, then candidate publication completes; separately invoke the helper with selected-identity environment and assert rejection before uv/engine mutation | Existing/new process integration tests assert exclusive revalidation and no mixed paths; helper unit test asserts self-target fails before staging | Multica-only process timestamps, sanitized output/exit codes, old/new manifest observations, and no image because output semantics are unchanged |
| CI-01 | Stale/malformed review; moved PR; issue/PR mismatch; unproven transition | Lower-level checks are sufficient because failures occur before any mutating call and GitHub/reviewer responses are explicit inputs | None | No additional local case | Independently inspect scenario-to-test mapping and run focused reviewer/helper nodes without editing Implementer-owned tests | Parameterized tests cover both SHA fields, session/compatibility, malformed JSON, same SHA, PR head/link/repository and installed identity/PR/health; assert zero engine mutation calls | Product commit/PR/file and CI links plus sanitized command/exit summary in Multica |
| CI-02 | Preparation failure; activation rollback; pre-lock manifest change; symlink/foreign ownership | Existing package/unit seams can deterministically inject each filesystem/provenance boundary more completely than a live failure | None | No additional local case | Inspect injection coverage, run focused alternate/packaging tests, and compare rollback assertions to every publication step | Parameterized tests assert old tool/bin/launcher/manifest usability, state bytes, staging cleanup, expected-manifest revalidation, and no canonical/neighbor mutation at every failure point | Product test/file links and assertion summary in Multica; raw logs stay out of Git |
| CI-03 | Guidance and architecture ownership | Static docs/inventory tests are sufficient; no runtime behavior remains | None | No additional local case | Verify skill text distinguishes transition `advance` from canonical `update`; inspect one-owner inventory | Architecture tests reject duplicate installer/runner/provenance/renderer/lock/cleanup; CLI/product inventories remain unchanged | Product docs/test links and commands/exits in Multica |

## Local E2E script and fixture contract

- The private test-repository script SHALL accept product checkout/revision, baseline revision, predecessor SHA, candidate SHA, issue/PR, transition-review path, isolated `HOME`, cache/XDG roots, hot-fix identity, fixed locale, terminal width, timeouts, and artifact directory as explicit arguments or documented inputs. It SHALL contain no private default URL/hostname, credential, personal identity, or hardcoded reusable issue/PR.
- Setup SHALL verify clean dedicated checkouts, absence of the selected hot-fix paths, a trusted candidate canonical uv-tool installation inside isolated `HOME`, both remote-reachable immutable SHAs, and an actual candidate PR head/link. The package engine may seed only the predecessor installation; the behavior under test SHALL be invoked through `fix_tool.py advance`.
- Fixtures SHALL place non-empty deterministic bytes representing catalogue data, retained backups, environment metadata, and project configuration under the selected root; add a neighboring alternate-root sentinel and canonical executable/manifest digest. The script SHALL record initial digests before the public action.
- The transition review SHALL be produced by the repository reviewer helper against exact predecessor and candidate SHAs. A fabricated approval is forbidden. Review and GitHub waiting time is external to developer-hours, but unavailable authenticated tools or an unreviewable transition blocks the local case.
- The success case SHALL assert the helper's bounded JSON projection before internal inspection, then check exact candidate provenance, unchanged launcher/root, preserved sentinel digests, usable candidate launcher, and unchanged canonical/neighbor evidence.
- The lock case SHALL use bounded child processes and deterministic readiness markers, never sleeps as the assertion. It SHALL prove the candidate is not published while the old launcher holds the shared lock and is published only after normal holder exit.
- Failure-safe cleanup SHALL remove only dedicated hot-fix/canonical fixtures, isolated roots/caches, and optional dedicated Odoo/Compose/database/port resources. It SHALL preserve the test MR, branch, commits, and Multica attachments and SHALL NOT close/merge the MR or delete its branch without separate authority.

## Independent walkthrough and visual evidence

- Baseline is approved `origin/main`; candidate is the exact implementation SHA. Use equivalent checkout state, fixed locale, fixed terminal width, and matching commands.
- Capture actual `fix_tool.py --help` text before and after: baseline lacks `advance`, while candidate documents its required issue, SHA, PR, and review inputs. Capture the successful candidate advance JSON with private paths sanitized but identity, prior/candidate revision labels, and retained-root outcome visible.
- On macOS, render actual captured text to PNG with bundled `scripts/render_terminal.swift` from `openspec-e2e-testing`. These are renders of captured text, not terminal-window screenshots. Preserve the tested output language; agent-authored explanations remain English. Record ANSI, TTY, wrapping, and progress limitations honestly.
- Required files: `myl511-fix-help-before.txt/.png`, `myl511-fix-help-after.txt/.png`, and `myl511-advance-result.txt/.png`. Missing baseline/candidate text, any PNG, or attachment read-back prevents complete verification.

## Evidence publication

- The E2E script and necessary fixtures SHALL be ordinary commits in the dedicated private test-repository MR. If a verified utility genuinely creates a functional commit, retain it unchanged and link it; do not create a second commit when no functional result exists.
- Implementer owns product tests/fixtures/CI changes. Tester SHALL NOT edit those files; Tester independently runs and inspects them and reports findings.
- Tester SHALL publish one final Multica comment with exact baseline/product/test-code SHAs, sanitized commands and exit codes, assertions, provenance, product PR and test MR/commit/file links, plus required image attachments, then read back the comment and attachments.
- Images, actual outputs/logs, diagnostic JSON, hashes, commands/exits, provenance, and assertion results SHALL remain in Multica and SHALL NOT be committed to Git. JSON is allowed in Git only when generation of that JSON is itself the tested product behavior.
