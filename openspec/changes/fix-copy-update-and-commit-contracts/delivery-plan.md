# Delivery Plan

## Contract baseline

- Task key: `MYL-546`
- OpenSpec change: `fix-copy-update-and-commit-contracts`
- Approved base: `origin/main` at the exact SHA recorded in the planning publication comment.
- Requirements source: GitHub #169 plus this change's proposal, design, four delta specifications, and tasks.
- Estimate source of truth: the planning issue custom properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; numerical totals are intentionally not duplicated here.

## Ponytail review

`ponytail full` found no justified new subsystem. COPY reuses checkout/restore contracts, Git identity reuses the verified GitLab projection, Multica only composes existing context/root-creator APIs, and coordinated update extends the existing self-update plan, snapshot, and journal. Separate credential, orchestration, evidence, or update frameworks are excluded.

## E2E decision

`e2e_required=false`. The reported defects are deterministic contract and composition errors with focused seams already present in checkout/restore, Git CLI, Multica client, and self-update packaging tests. GitHub #169 explicitly excludes E2E from this change and requests live verification separately. A numbered Odoo slot, private test-repository mutation, screenshots, and evidence-only scripts would add operational scope without resolving a remaining acceptance gap. Planning does not execute tests.

| Verification case | Required implementation evidence | Independent Tester action | Images / generated artifacts |
| --- | --- | --- | --- |
| `V-MYL546-COPY` | Focused ordinary/adoption fixtures assert target filestore bytes, missing-file refusal, source preservation, no-file-backed success, and backup ownership. | Run and inspect the focused checkout/restore tests at the candidate SHA. | None. |
| `V-MYL546-GIT` | Exact message tests plus SDK/CLI/Multica tests assert bare URL, retained history compatibility, verified root-human identity, immutable preview/execute parity, and pre-commit refusal. | Run focused core and `odcli-multica` suites; inspect assertions and one disposable local commit fixture. | Preserve normal test output in Multica only; no screenshot required. |
| `V-MYL546-UPDATE` | Unit and isolated packaging tests assert unchanged core-only behavior, aligned core/companion/embedded-SDK revisions, preflight ambiguity refusal, warning, partial failure, and recovery state. | Run focused self-update and packaging selections in their existing isolated uv-tool fixtures. | None. |
| `V-MYL546-REPO` | Standard lint, formatting, typing, architecture/redaction, coverage, and strict OpenSpec checks pass. | Re-run published commands without editing implementation fixtures. | Commands, exit codes, and candidate SHA in Multica only. |

Tester publishes commands, exit codes, candidate SHA, concise assertions, and MR/commit links in Multica with read-back. Diagnostic JSON, logs, hashes, screenshots, private URLs, identities, and evidence-only reports are not committed. Existing test fixtures and any required generated product artifacts belong to the Implementer; Tester only verifies them. No dedicated E2E branch, live service, numbered Odoo checkout, image capture, or cleanup workflow is planned.

## Delivery topology

`delivery_mode=single_wp_no_dag`. The authoritative `Estimate, hours` property is at or below the threshold. Exactly one work package covers every OpenSpec task; it has no `depends_on`, stage, frontier, or DAG metadata.

### WP-MYL-546-01-all-fixes

- **Task coverage:** `1.1`, `1.2`, `2.1`, `2.2`, `2.3`, `3.1`, `3.2`, `4.1`, each exactly once.
- **Deliverable:** all four reported consistency defects are fixed together with focused regressions, documentation, and repository validation.
- **Owned responsibility scope:** checkout/auxiliary-restore filestore handling; core Git message and identity composition; `odcli-multica` root-creator wiring; existing self-update plan/snapshot/journal extension; directly related tests, fixtures, help, generated docs, and validation metadata.
- **Critical shared files:** `resources/environment/checkout*.py`, `resources/instance/auxiliary_restore*.py`, `resources/git.py`, `commands/git.py`, `internal/git_explicit.py`, `packages/odcli-multica`, `internal/self_update*.py`, focused tests, and affected documentation. This is responsibility guidance, not an exhaustive allowlist.
- **Contract surface:** `development-environment`, `odoo-git-workflow`, `cli-odcli`, and `self-update` deltas; existing immutable-command, redaction, verified GitLab identity, backup ownership, optional-extras, and recovery contracts.
- **Definition of done / evidence:** every scenario in the four delta specs has focused automated coverage; no ambient or machine identity fallback exists; COPY never reaches `READY` with missing target attachment files; core-only update behavior is unchanged; companion mismatch or partial failure is recoverable and not reported as success; documentation and standard repository checks pass; exact commands and exit codes are handed off.
- **Parallel-safety rationale:** one WP intentionally owns all interacting changes, so there are no sibling write zones or cross-WP contract races.
