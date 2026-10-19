# Delivery plan

## Planning baseline

- Task key: `MYL-515`.
- OpenSpec change: `fix-named-remote-and-init-contracts`.
- Approved implementation base: `origin/main` at `7ff2b01c129d0e20eb2521f2b2d860796b0502ef`.
- Planning branch/worktree: `feat/MYL-515-named-remote-and-init-contracts` in the dedicated managed checkout; production code is unchanged.
- Implementation evidence: open PR #155 head `da3c64f3fa6cb092c828da740b0301dd24359686` was inspected read-only as a close analogue and scope cross-check. It is not the approved base, accepted implementation, or test evidence for this plan.
- Test repository: resolve the URL from the current Multica project description at execution time. Use it only as a parameter; do not place its private hostname, credentials, personal data, or URL in public Git artifacts.
- Existing foundations retained: typed project manifest and immutable configure command, remote database resolver, preparation/materialization pipeline, port allocator, generated-config writer, Git worktree probes, PostgreSQL digest approval store, process boundary, output envelope, and real-Odoo harness.

## Estimate contract

- Authoritative totals exist only in root planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; they are intentionally not duplicated here.
- Basis: remaining work from the approved base to all change acceptance criteria for one experienced developer familiar with Python, Click, Odoo/PostgreSQL lifecycle, and this repository, without AI acceleration. Active developer-hours include investigation, implementation, focused tests, documentation, attended verification, and likely review repair. Unattended CI/service waits, approval queues, and external blocking are excluded.
- Confidence: medium-high. The full base paths, tests, CI, close analogues, and an unmerged implementation diff were inspectable; independent acceptance and live-environment repeatability remain unverified.
- Calibration: uncalibrated because no comparable human timing history was supplied. The estimate uses scoped implementation units and observed repository structure, not elapsed commit timestamps or line counts.
- Tests were not run for the estimate or planning phase.
- Material assumptions: the existing resolver, approval store, generated-config writer, preparation pipeline, and private test-repository access are reusable; no new manifest/catalog schema, dependency, or server API is required. A change to those assumptions requires planning revision and re-estimation.

## Delivery topology

Delivery mode: `dag`.

The authoritative `Estimate, hours` property selects multi-package delivery, and the implementation has a genuine first execution frontier of independent write zones. Stages are consecutive topological layers. Manager SHALL materialize each WP child with the listed integer `stage` and direct `depends_on` only.

Stage map: `WP-01 -> 1`, `WP-02 -> 1`, `WP-03 -> 1`, `WP-04 -> 1`, `WP-05 -> 2`, `WP-06 -> 3`.

### WP-01 — Optional named remote foundation

- Stage: `1`.
- Direct `depends_on`: none.
- Task coverage: `1.1`–`1.5`, each covered exactly once.
- Independent deliverable: optional named-source database round-trip, offline remote CLI, refresh resolver integration, actionable COPY planning failure, and `ps` compatibility.
- Owned responsibility scope: `RemoteSourceConfig` and manifest adapters; named remote command adapters; database source selection/resolution; the COPY source-selection seam; named-remote/process-inventory focused tests, fixtures, snapshots, and directly related docs. This is a responsibility scope, not an exhaustive file allowlist.
- Critical shared files: owns named-source changes in `project.py`, `commands/remote.py`, `internal/dbprep/source.py`, and the COPY source-selection adapter for this stage. It SHALL NOT edit init registration/generated config, allocator implementation, restore Git steps, or the developer fix tool.
- Contract surface: nullable database with strict explicit validation; absent TOML line; immutable offline add/update; explicit priority/single selection/distinct failures; retained credential/provenance; typed COPY guidance; read-only inventory.
- Definition of done / evidence: every named-source delta scenario has focused red/green coverage; public CLI dry-run and `ps` preserve manifest bytes; backup is not called for resolver errors; no new discovery or runner path exists.
- Parallel-safety rationale: its production writes are confined to the remote model/CLI/source-selection seam and do not overlap allocator, restore publication, or isolated hotfix verification.

### WP-02 — Bounded HTTP allocation

- Stage: `1`.
- Direct `depends_on`: none.
- Task coverage: `3.1`–`3.2`, each covered exactly once.
- Independent deliverable: one deterministic automatic HTTP candidate stream across the complete inclusive range with exact explicit-port behavior.
- Owned responsibility scope: shared port allocation and its focused unit/public-checkout coverage, including related fixtures/snapshots and documentation. It SHALL keep PostgreSQL allocation unchanged.
- Critical shared files: owns `internal/port_allocation.py` and allocator-focused tests. Any required public checkout assertion SHALL be added without modifying WP-01's named-source source-selection code.
- Contract surface: preferred/default first candidate; ascending full-range fallback; catalogue/manifest/config/live exclusions; stable `no_free_port` diagnostic; exact explicit request; pre-run recheck.
- Definition of done / evidence: preferred, lower fallback, out-of-range, absent, both boundaries, exhaustion, exact explicit conflict/success, and PostgreSQL compatibility matrices pass.
- Parallel-safety rationale: it changes the allocator only; no stage-one sibling writes that module.

### WP-03 — Captured restore publication checks

- Stage: `1`.
- Direct `depends_on`: none.
- Task coverage: `3.3`–`3.4`, each covered exactly once.
- Independent deliverable: main-checkout restore captures and consumes generated-config Git safety checks before default publication.
- Owned responsibility scope: restore preparation step construction, generated-config Git validation consumption, Git worktree adapter support, and focused preparation/restore tests/fixtures/snapshots.
- Critical shared files: owns `internal/dbprep/materialize_steps.py`, the restore publication call path, and the narrow `internal/git_worktree.py` captured-step support. It SHALL not change named-source resolution or init orchestration.
- Contract surface: immutable read-only Git steps; same-root and untracked proof; tracked/Git-error/root-drift refusal; prior binding preservation; compensation and retained evidence; no secret projection.
- Definition of done / evidence: strict preparation tests prove successful publication plus all refusal cases, no `unplanned_step`, correct coalesced accounting, and unchanged external-source/filestore behavior.
- Parallel-safety rationale: restore planning and Git validation are disjoint from stage-one remote, allocator, and developer-tool files.

### WP-04 — Pinned hotfix provenance compatibility

- Stage: `1`.
- Direct `depends_on`: none.
- Task coverage: `4.1` exactly once.
- Independent deliverable: the isolated developer verifier accepts only the exact expected uv VCS provenance and still rejects every foreign or mismatched source.
- Owned responsibility scope: `scripts/odcli_agent_tools/fix_tool.py` and its dedicated compatibility tests/fixtures; no production package or canonical installer changes.
- Contract surface: expected repository identity; exact trusted SHA; matching `vcs_info.commit_id`; isolated root; fail-before-import.
- Definition of done / evidence: exact uv form passes; foreign, mutable, missing, and mismatched provenance fail; canonical installation remains untouched.
- Parallel-safety rationale: the script and its focused tests are isolated from all other stage-one write zones.

### WP-05 — Integrated init registration, secret, and consent

- Stage: `2`.
- Direct `depends_on`: `WP-01`.
- Task coverage: `2.1`–`2.5`, each covered exactly once.
- Independent deliverable: one immutable init command supports both named-source arities, merges re-init sources, materializes a safe local master password, and captures exact PostgreSQL image consent before writes.
- Owned responsibility scope: typed/public init request and Click parsing; init registration/merge; generated-config password selection/repair; PostgreSQL digest-resolution/approval composition; init/PostgreSQL/output/architecture tests and directly related fixtures/snapshots.
- Critical shared files: owns `commands/cli_parts/init_registration.py`, `project_init.py`, `internal/project_init.py`, and `internal/generated_config.py` after consuming WP-01's nullable model. It SHALL not reopen allocator, restore Git, or hotfix verifier ownership.
- Contract surface: repeatable mixed remote arities and equals syntax; option-boundary fidelity; one init snapshot; preservation/merge; execution-only random secret; `0600` and complete redaction; approval ordering; yes/interactive/noninteractive/dry-run/external behavior; existing startup recheck.
- Definition of done / evidence: focused tests cover parser edge cases, retries, preservation/repair, failure atomicity, output secrecy, approval reuse/refusal/drift, and bootstrap inclusion; no nested remote mutation or second trust/secret subsystem exists.
- Parallel-safety rationale: dependency on WP-01 prevents model/parser drift. Its write zone is otherwise separate from completed stage-one allocator, restore, and hotfix work.

### WP-06 — Integrated documentation, E2E, and delivery verification

- Stage: `3`.
- Direct `depends_on`: `WP-02`, `WP-03`, `WP-04`, `WP-05`. `WP-01` remains a transitive prerequisite through `WP-05` and SHALL NOT be materialized as a direct edge.
- Task coverage: `4.2`–`4.3` and `5.1`–`5.3`, each covered exactly once.
- Independent deliverable: coherent user/main-spec documentation, private test-repository E2E assets, complete repository verification, and publication-ready evidence for the integrated change.
- Owned responsibility scope: README/help reconciliation, active main OpenSpec updates, canonical CLI/architecture inventories, directly related cross-cutting tests/fixtures/snapshots/CI files, private test-repository script/fixtures, and evidence publication contract. Tester does not edit Implementer-owned product tests.
- Critical shared files: owns cross-cutting documentation/spec/inventory reconciliation only after all implementation predecessors land. It SHALL not redesign predecessor production contracts.
- Contract surface: final public syntax/messages; main-spec reconciliation; test-to-scenario mapping; exact revisions and commands; Multica attachments/read-back; failure-safe dedicated-environment cleanup.
- Definition of done / evidence: strict OpenSpec validation and repository checks pass; E2E cases complete against the exact candidate; product/test MR, commit, file and CI links plus commands/exit codes/assertions and required images are published in Multica and read back.
- Parallel-safety rationale: this is the integration successor and starts only after every production contract is stable.

## E2E decision and source mapping

`e2e_required=true`.

Focused tests are sufficient for model validation, TOML omission, parser boundaries, candidate ordering, Git-step consumption, approval failure branches, redaction, and dry-run mutation boundaries. A local E2E remains necessary because no cheaper test proves the real interaction among a named remote with an omitted database, remote database listing, credentialed backup, local Compose restore using the generated `admin_passwd`, default publication, and the selected OCI image approval. The issue's prior live report and open PR claims are historical inputs, not evidence for the implementation revision delivered from this plan.

Planning SHALL NOT execute tests. Tester SHALL use a dedicated `odcli-N` slot with its own product checkout, private test-repository checkout, database, Compose project, HTTP/PostgreSQL ports, XDG/catalog roots, credential file, and generated config. The reference checkout, canonical installation, existing projects, and pre-existing databases SHALL remain untouched.

## Verification matrix

| Case ID | OpenSpec requirement/scenario | Verification gap / local E2E decision | Affected API/schema | Local test script | Independent Tester actions | CI disposition / assertions | Artifacts and images |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E2E-01 | CLI optional remote registration; init three/four-field forms; re-init preservation; `ps`; dry-run/offline behavior | Local E2E required for installed public CLI parsing, actual manifest bytes, and visible before/after behavior across separate invocations | No OpenAPI operation; Click CLI and project TOML are authoritative | `tests/e2e/myl515_named_remote_init_e2e.py` in the dedicated private test-repository branch/MR | Against baseline and candidate with the E2E-01 equivalence/reset contract below: capture `remote add --help` and `init --help`; initialize a disposable project; run dry-run registration and prove no byte/network change; execute three-field add/init; mix a four-field source; branch-only update; re-init with and without remote inputs; run `ps --format json`; inspect TOML and source set independently | Implementer-owned unit/CLI characterization tests cover blank/non-string rejection, equals/literal-option parsing, immutable plan identity, output modes, and loader compatibility. Assertions: absence round-trips as no key; explicit value persists; unrelated sources survive; no registration HTTP/backup; baseline `ps` fails on the omitted database while candidate `ps` succeeds and both manifest hashes remain unchanged | Product/test revision links, exact sanitized commands/exit codes, before/after manifest text, actual baseline/candidate help/error text, existing help/registration PNGs, and the mandatory `ps` pair `myl515-ps-baseline-failure.txt` + `.png` / `myl515-ps-candidate-success.txt` + `.png`, attached in Multica |
| E2E-02 | Single database resolution; named credential/provenance; generated local password; restore/default publication; image consent | Local E2E required for the real single-database HTTP backup + Compose/PostgreSQL/Odoo + filesystem/Git publication boundary; mocks cannot prove the combined success path. Zero/multiple/unavailable database-list branches are explicitly excluded from local E2E and assigned to CI-01 | Odoo database list/backup/restore HTTP endpoints; no changed OpenAPI document in this repository | Same script, separate lifecycle phase with explicit setup/reset/cleanup | Resolve the private test URL only at runtime; run the paired baseline/candidate lifecycle under the E2E-02 equivalence/reset contract below; initialize candidate Compose project with `--local-config --yes` and a named source without database; prove approval record matches resolved digest without exposing it in output; verify generated config mode and presence (not value) of a nonblank local `admin_passwd`; run refresh/restore; inspect retained backup provenance, local database/filestore, generated config/default binding, login/readiness; repeat init and verify password/config identity are retained | Existing real-Odoo smoke/tier plus focused preparation/init/PostgreSQL suites. Assertions: one remote name selected; backup uses named credentials; restore succeeds with generated local password; prior default changes only after captured Git checks; stored approval remains fail-closed on drift. Tester inspects CI and does not edit product fixtures | Multica-only sanitized endpoint labels, commands/exit codes, assertions, generated-config mode/hash (no secret), backup UUID, database/filestore/default observations, approval reference/digest provenance, product/test MR/commit/file/CI links, and the mandatory lifecycle pair `myl515-lifecycle-baseline-failure.txt` + `.png` / `myl515-lifecycle-candidate-completion.txt` + `.png` |
| CI-01 | HTTP range, explicit port, COPY guidance, tracked/Git-error/root-drift refusals, hotfix provenance, zero/multiple/unavailable database-list results, and all deterministic negative/redaction cases | Lower-level tests are sufficient: the database-list branches end at the captured resolver/backup boundary, so controllable provider/HTTP-adapter fakes prove the exact typed failure and that the backup callable remains untouched without adding a second live service matrix | No OpenAPI change | No extra local script | Independently inspect scenario-to-test mapping and run documented focused commands against the candidate; verify `tests/unit/test_named_remote_preparation.py` and `tests/unit/internal/test_remote_database_resolution.py` inject zero, multiple, and unavailable list results and assert typed errors plus zero backup calls; verify no sibling changed another WP's owned contract | Existing offline tests, CLI characterization, strict typing, Ruff/format, production-line/catalogue/E2E contract, compatibility, package, and applicable real-Odoo jobs. Assertions include inclusive bounds, exact messages/codes, no unplanned Git, compensation, foreign-pin rejection, zero secret projection, and deterministic zero/many/unavailable list handling before backup. Implementer owns these product tests; Tester only runs and inspects them | Product commit/PR/file and CI links plus sanitized command/exit summaries in Multica; raw logs/diagnostic JSON/hashes remain outside Git |

## Local script, fixtures, and cleanup

- The script SHALL accept all environment-specific inputs as arguments or documented environment variables: product checkout/revision, test-repository checkout/revision, dedicated `odcli-N`, private remote URL, credential variable name/value, source Git ref, Compose image, HTTP/PostgreSQL ports, XDG/catalog roots, fixed locale/terminal width, and evidence directory. It SHALL contain no private default URL, hostname, credentials, personal identity, or hardcoded database owned by another checkout.
- Setup SHALL validate clean dedicated checkouts, allocate a unique project/Compose name and database prefix, and record baseline/candidate/test-code revisions before mutation. For Odoo testing it SHALL use its own checkout, database and ports, never the reference checkout.
- The registration phase SHALL instrument or otherwise prove that dry-run/add/init do not contact the remote. The lifecycle phase SHALL use the actual private test service and existing CLI; generated evidence must come from commands and files, not model-authored substitutes.
- E2E-01 SHALL create two fresh copies of one canonical fixture whose manifest bytes contain the same named remote with its database omitted, the same local/default database, and the same non-secret source metadata. Baseline and candidate SHALL use separate empty XDG/catalog roots but identical locale, terminal width, output mode, and command arguments. Before each `ps` capture, the script SHALL restore the canonical manifest bytes, clear only that run's generated state, record the manifest hash, and verify the dedicated service state is unchanged. This reset makes `myl515-ps-baseline-failure.txt`/`.png` and `myl515-ps-candidate-success.txt`/`.png` a reproducible visible pair.
- E2E-02 SHALL snapshot one single-database remote fixture and create independent baseline/candidate local projects with the same source Git ref, image reference, sanitized remote identity, credentials, locale, terminal width, and invocation arguments. Before each run, the script SHALL restore the remote snapshot and allocate a fresh dedicated database, filestore, Compose project, ports, and empty XDG/catalog roots; after the run it SHALL remove those resources before the next half of the pair. The baseline SHALL capture failure of the same omitted-database init/refresh lifecycle before successful restore; the candidate SHALL capture completion. The reproducible pair is `myl515-lifecycle-baseline-failure.txt`/`.png` and `myl515-lifecycle-candidate-completion.txt`/`.png`.
- Zero, multiple, and unavailable database-list results SHALL NOT be exercised by local E2E. They SHALL be deterministic Implementer-owned lower-level cases using controllable database-list provider/HTTP-adapter fakes, with assertions for the typed error and zero backup calls. Those fakes require no service fixture and therefore no runtime cleanup; normal test isolation removes their temporary files/state.
- The script SHALL assert public results first, then inspect manifest/config/catalog/database/filestore state independently. It SHALL distinguish the product revision under test from test-code and evidence revisions.
- Cleanup SHALL run on success or failure, stop/remove only the dedicated Compose/Odoo resources, remove its slot database, port bindings, temporary XDG/catalog roots, credential file, generated secrets, and working directories, while preserving the test MR, branch, commits, and Multica attachments. It SHALL not close/merge the MR or delete its branch without separate authority.

## Independent walkthrough and image contract

- Baseline is the approved base; candidate is the exact implementation SHA. The E2E-01 and E2E-02 equivalence/reset contracts above are mandatory; a capture made without their recorded fixture hash/snapshot identity and reset assertions is invalid.
- Changed CLI help and registration/error output require actual baseline/candidate text and PNGs. On macOS, render captured text with bundled `scripts/render_terminal.swift` from `openspec-e2e-testing`. Label these as text renders, not terminal-window screenshots. Preserve the tested output language; this plan and agent-authored explanation remain English.
- Capture both the baseline lifecycle failure and candidate successful init/refresh completion using the mandatory lifecycle filenames above, but only when the output contains no secret. Do not render secret-bearing config, credentials, raw environment, or approval-store content.
- Redirection can change ANSI, TTY, prompt, and progress behavior. Record those limitations honestly; a text render does not prove interactive terminal styling.
- Missing required baseline/candidate text, PNG, or attachment read-back prevents complete verification. Decorative or invented images are prohibited.

## Evidence publication

- Test script and necessary fixtures SHALL be ordinary commits in a dedicated private test-repository branch/MR. Keep test-code commits separate from a functional commit genuinely created by the verified utility. If the utility creates no functional commit, do not manufacture a second commit.
- Tester SHALL publish one final Multica comment containing exact product SHA, test-code SHA, baseline SHA, sanitized commands and exit codes, scenario assertions, provenance, and product/test MR/commit/file/CI links. Attach required text/PNG evidence and read back the comment and attachments.
- Images, actual output/logs, diagnostic JSON, hashes, runtime assertions, and provenance evidence SHALL be published only in Multica, not committed to GitLab. JSON may enter Git only when its generation is itself the tested functional result.
