## Delivery contract

- Change: `fix-numbered-slot-create-lifecycle`.
- Approved base: `origin/main` at `9db311ef97787df0ae0e60c81958f339ce27ae30`.
- Authoritative estimate: the `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` properties on planning issue `MYL-460`; numeric totals SHALL NOT be copied into this file.
- Estimate confidence: medium. The two failing seams, reusable implementation paths, and close tests are inspectable; uncertainty is concentrated in auxiliary-runtime integration and real Mac/Compose cleanup behavior. The estimate assumes one experienced developer familiar with Python, pytest, Git, Odoo Database Manager, and this repository, without AI acceleration. Tests were not run during estimation.
- Implementation repository: `https://github.com/maximchikAlexandr/odoo-instance-sdk`.
- Local E2E repository: use the GitLab test repository URL from the active Multica project description. Private hostnames, resolved paths, ports, database names, credentials, and personal data SHALL remain runtime parameters and SHALL NOT be committed to the public SDK repository.
- Scope source: `proposal.md`, `design.md`, `tasks.md`, the three delta specs in this change, and the affected main specs under `openspec/specs/`.

## Verification strategy

The change modifies behavior at two public-enough boundaries: `EnvironmentResource.adopt_command()` and the repository-owned numbered service wrapper. Focused pytest coverage is required in the SDK repository; a separate real Mac lifecycle is required because unit doubles cannot prove Odoo restore, Compose database visibility, filestore ownership, launcher publication, or selective cleanup. No new test framework is introduced.

The Tester SHALL use a dedicated `odcli-N`, checkout, target database, filestore, HTTP port, evidence branch, and test-repository MR. The reference checkout, persistent source instance/database/backup, canonical OdCLI installation, and any existing numbered slots are read-only neighbors. Secrets SHALL be read from the existing private configuration/environment, never placed in argv, stdout/stderr, Git, screenshots, diagnostic JSON, or Multica comment text.

### Coverage matrix

| Case ID | OpenSpec requirement/scenario | OpenAPI | Local test script | Independent agent steps | CI disposition | Assertions | Artifacts / images |
|---|---|---|---|---|---|---|---|
| E01 | Adoption canonical repository identity: equivalent SSH forms; numbered complete lifecycle | Not applicable | `scripts/e2e_myl460_numbered_lifecycle.py equivalent-lifecycle` in the GitLab test repository | From clean dedicated baseline and candidate checkouts, confirm the project and cloned checkout expose transport-equivalent origins without editing the reference; run candidate `create`, then `list`, `inspect`, `remove`; independently inspect Git HEAD/remote, launcher resolution, catalogue environment, PostgreSQL target, filestore, and absence after removal | Implementer adds parameterized SDK unit/contract tests; existing `offline-tests` and `compatibility` jobs run them | Candidate publishes one ready slot; list/inspect agree on identifier and linkage; target DB, filestore, checkout, venv, launcher and manifest appear, then only those owned resources disappear; source/reference/canonical/neighbor digests remain stable | Sanitized command transcript, assertion summary, before failure text from approved base, after success text from candidate, `myl460-e01-before.png`, `myl460-e01-after.png`, tested SHAs and artifact hashes; Multica only except the reusable script/fixtures committed to the GitLab MR |
| E02 | Adoption canonical repository identity: different/unsafe origins rejected | Not applicable | Focused SDK pytest; optional `scripts/e2e_myl460_numbered_lifecycle.py mismatched-remote` using only disposable clones | Inspect the disposable clone's redacted origin classification, invoke create with a deliberately non-equivalent non-secret repository identity, then verify reference and all pre-existing slot resources are unchanged | Implementer-owned parameterized tests in `tests/unit/resources/test_environment_adoption.py`, `tests/unit/test_agent_checkout.py`, and architecture inventory if ownership moves | Typed/sanitized conflict occurs before Odoo/catalog mutation; no launcher, manifest, environment, DB, filestore, checkout, or tool remains for the attempted slot | Sanitized pytest result and, if local case runs, assertion summary plus hashes; no decorative image because no user-visible output contract changes |
| E03 | Explicit COPY sources and auxiliary lifecycle: named remote and retained backup restore locally | Not applicable | E01 covers named remote; SDK integration fixture covers retained backup | For E01, inspect process/catalog evidence that remote access is backup acquisition only and target DB belongs to the dedicated local Compose cluster; compare target absence/presence through native PostgreSQL inspection and verify isolated filestore content | Implementer extends existing checkout/adoption tests for named remote and retained backup plan steps, probe consumption, journal and cleanup; no new CI job | Exactly one auxiliary lifecycle and one before/after probe pair; named source has wait/transfer actions, retained backup does not; target is local and ready only after DB/filestore postconditions | Sanitized local assertions, bounded relevant logs, tested SHA and hashes in Multica; reusable fixtures in SDK GitHub MR |
| E04 | Restore/startup failure leaves no untracked partial numbered slot | Not applicable | `scripts/e2e_myl460_numbered_lifecycle.py failure-cleanup` using a dedicated occupied auxiliary port or another deterministic disposable precondition | Snapshot free-slot identity and neighbors; create a disposable foreign listener on the assigned test port; invoke create; stop only that listener by recorded PID; inspect list/catalog/PostgreSQL/filestore/checkout/launcher afterward | Implementer covers primary failure plus auxiliary cleanup and numbered recovery linkage with existing recording/fake executors | Create fails closed; no ready list/inspect entry; all safely proven partial owned artifacts are absent; unresolved destructive ownership, if any, remains explicitly linked as recovery evidence; neighbors and source remain unchanged | Sanitized command/result text and assertion report in Multica; image only if the candidate exposes a changed terminal result relative to the approved base |

### Local script contract

The GitLab test-repository script SHALL be a small Python orchestration script around the repository's existing `scripts/agent_checkout.py`; it SHALL not duplicate SDK logic or become a second validator framework. It accepts runtime parameters or environment variables for candidate executable/revision, dedicated port, target database, reference checkout and evidence root. Secret values are accepted only through the existing private environment contract and are removed from child output projections.

Each case SHALL record: SDK product SHA under test, test-script commit SHA, test-repository commit SHA, sanitized command with parameter names replacing private values, exit status, start/end timestamps, selected numbered identity, assertion results, and SHA-256 for published files. Raw environment dumps, package caches, backup bytes, binaries, full application logs, and credentials are forbidden.

Setup SHALL first verify clean/pushed reference state, canonical installation provenance, free dedicated resources, source reachability, exact candidate SHA, and an empty target. Cleanup SHALL run on success and failure, remove only the test slot and the separately recorded disposable listener, and then re-check the source/reference/canonical/neighbor invariants. Cleanup SHALL preserve the evidence MR, branch, commits, and attachments; it SHALL NOT merge, close, or delete them without a separate instruction.

### Before/after terminal evidence on Mac

E01 changes the observed result from an approved-base failure to a candidate lifecycle success. Tester SHALL capture the actual sanitized UTF-8 terminal text for equivalent fixtures and render it to PNG with the bundled `openspec-e2e-testing/scripts/render_terminal.swift`. The text retains the tested output language; labels and authored explanation are English. Record terminal width and locale. ANSI colors, TTY-only progress, spinners, and interactive prompts are not claimed when redirection changes them. The rendered PNG is evidence derived from captured text, not a screenshot of a terminal window.

The baseline and candidate runs SHALL use separate dedicated resource names and a reset between them. If the approved-base run cannot be reproduced safely, verification remains incomplete; Tester SHALL report the evidence gap rather than synthesize baseline output.

### Evidence publication

Tester SHALL post one final Multica comment on the implementation/verification issue and verify read-back. It SHALL include links to the SDK GitHub MR, the GitLab test MR/branch/commits, and relevant committed test files; exact product/test SHAs; sanitized commands and exit codes; assertion summary; cleanup result; and attachments for required text, PNGs, bounded logs, and diagnostic hashes. Real logs, screenshots, diagnostic JSON, command provenance, assertion outputs, and hashes are Multica-only. JSON enters Git only when JSON generation itself is the product behavior under test.

## Repository checks

Implementer owns the focused tests and all applicable existing repository gates: formatting/lint, type checks, offline tests, compatibility tests, architecture inventory, packaging checks when affected, strict OpenSpec validation, and secret/private-data scan. Tester does not edit SDK CI tests or fixtures; Tester verifies their coverage and reports findings.

Planning intentionally did not execute application code, tests, migrations, restore operations, or the local E2E.

## Work package topology

- `delivery_mode`: `single_wp_no_dag`
- Estimate threshold source: authoritative `Estimate, hours` property on `MYL-460`.
- Work packages: exactly one. No `depends_on`, stage, frontier, or DAG metadata applies.

### WP-MYL-460-01 — Repair and verify numbered local COPY creation

- Task coverage (complete and one-time): `1.1`, `1.2`, `1.3`, `2.1`, `2.2`, `2.3`, `3.1`, `3.2`, `3.3`.
- Deliverable: one reviewed SDK change that accepts only canonical transport-equivalent remotes, restores every explicit COPY source through the bounded local auxiliary lifecycle, preserves recovery/isolation contracts, and supplies the required automated coverage and tester handoff.
- Owned responsibility scope: shared Git repository identity parsing and callers; adoption planning; COPY auxiliary selection and execution-plan projection; restore probes/journal/cleanup interactions; numbered coordinator delegation; affected unit/integration/architecture tests, fixtures, documentation, and repository checks. Critical shared files include `src/odoo_instance_sdk/internal/git_worktree.py`, `src/odoo_instance_sdk/internal/agent_checkout.py`, `src/odoo_instance_sdk/resources/environment/checkout_api.py`, `src/odoo_instance_sdk/resources/environment/checkout.py`, and their directly related tests. This is a responsibility boundary, not an exhaustive file allowlist.
- Contract surface: no new public API or CLI leaf; existing `EnvironmentResource.adopt_command()` typed errors and result remain; repository identity becomes canonical and shared; named-remote/retained-backup plans gain the already-specified local auxiliary actions; remote transfer, local probe, COPY journal, secret, recovery, and selective cleanup schemas remain compatible.
- Definition of done / evidence: every OpenSpec task is complete; focused tests prove equivalence, mismatch rejection, plan step parity, local target restoration, failure cleanup, and numbered publication/removal; applicable repository gates and strict OpenSpec validation pass; no private or generated evidence artifact is tracked; implementation handoff names exact commit/MR; Tester completes or reports gaps against E01–E04 with Multica read-back.
- Parallel-safety rationale: Git identity extraction and COPY auxiliary selection converge in the same adoption command and tests; splitting them would create overlapping writes in `checkout_api.py`, checkout plan assertions, and numbered acceptance coverage. One WP avoids contract drift and matches the authoritative estimate threshold.
