# Checkout integration research

## Evidence baseline — 2026-09-26

This is source and contract inspection, not a live Multica/Odoo acceptance run. No application task, runtime, project resource, checkout, database, or daemon lifecycle was mutated.

| Source | Inspected revision/state | Finding |
|---|---|---|
| Odoo Instance SDK planning base | `f7c3f7c9093529d6744c30745e220efb9aea8f80` | COPY checkout, external-file restore, command parity, and guarded cleanup exist; caller-owned checkout adoption does not. |
| Current planning input | `cb638fa4d575b0dbb8ca5d4452897568102484ca` | Existing OpenSpec used two raw `multica-py` command calls and local decoders; this revision replaces that target design. |
| MYL-271 / MYL-272 | planning `in_review`; implementation `in_progress`; no linked PR reported by Multica | Source/COPY predecessor is not yet a verified integrated implementation. WP-01 and WP-04 evidence exists on MYL-272, but partial WP evidence does not open the gate. |
| `multica-py` issue #93 | OPEN; baseline `d1b5f0e154c5587eca4cebd8bd2a6d39ae3d4d06`; upstream target v0.5.3 / `ff8b285497809e084915016c40c2bc5e5991ffbc` | Requires full public CLI parity. Finding B1 requires typed native checkout; finding A1/D1 requires correct complete daemon status. Generic raw command invocation explicitly does not satisfy typed parity. |
| Multica native checkout | upstream v0.5.3 source | Task-bound checkout owns repository cache/branch/path association; project/daemon-wide `local_directory` is not an issue-level selector. |

The dependency states above are observations, not completion claims. Before implementation, refresh every row against the integrated code and record the exact compatible revisions/versions.

## Native checkout traced end to end

1. Native `repo checkout` uses the active task credential, daemon endpoint, current task directory, URL, and requested ref. It creates or reuses a daemon-managed checkout and returns its path.
2. The daemon authenticates workspace/task ownership and controls repository cache, ref fetching, branch naming, collisions, and checkout retention. The extension must not reproduce or bypass those rules.
3. A zero exit/result does not by itself prove requested HEAD or a clean checkout: cached refs and retained dirty/unpushed work are valid native outcomes. Core adoption therefore verifies repository, HEAD/base, and clean state on first use without resetting code.
4. Native task/runtime lifecycle owns checkout availability. Odoo preparation is a separate phase and cannot promise permanent checkout retention.

## Why project `local_directory` is not selected

The local-directory resource is scoped to a project and daemon and rejects ambiguous multiple matches. It deliberately routes relevant tasks to one shared directory; it does not select a distinct checkout per issue. Replacing it per task or creating a project per environment would introduce routing and lifecycle machinery outside this change.

| Model | Decision | Reason |
|---|---|---|
| OdCLI worktree plus rewritten project resource | Reject | Mutates a project-wide route and can create a second checkout. |
| Native Multica checkout plus core adoption | Select | Preserves native task registration and introduces one reusable core primitive. |
| Private Multica storage/HTTP emulation | Reject | Bypasses public ownership and compatibility contracts. |
| Extension-side checkout registry | Reject | Duplicates native checkout identity and requires reconciliation/GC. |

## Current core seams

- `resources/environment/checkout.py`, `checkout_planning.py`, and `checkout_artifacts.py` capture worktree creation and COPY provisioning in one path; adoption must reuse the latter without Git acquisition.
- `resources/environment/cleanup.py` derives removal steps from `worktree_path` and assumes an SDK-created worktree. Caller-owned code requires an explicit ownership branch before adoption is safe.
- Project filtering uses Git common-directory identity. Independent native clones need explicit configured-project identity plus separate actual-checkout identity.
- `models/backup.py` and the catalog currently lack the complete ownership/artifact evidence required for safe adopted cleanup.
- Existing environment runtime, status, diagnostics, stop, remove, and backup resources are sufficient post-provisioning surfaces; no extension runtime manager is needed.

## `multica-py` parity dependency

The input design treated `CliResource.command_command()` as sufficient. Issue #93 supersedes that assumption:

- B1 requires a public typed native-checkout operation rather than raw argv.
- A1 and D1 require daemon status to decode real lifecycle values and retain identity, OS, server, workspace/runtime, and related fields.
- The completion boundary requires all approved public CLI families, inputs, response variants, and transports; closing only checkout/status is insufficient.
- A raw command escape hatch alone does not count as typed resource/model coverage.

Therefore this change specifies the semantics it consumes but does not invent method names or wire shapes. After #93 is fully implemented, research SHALL identify the exact public command siblings, result types, cancellation/unknown-outcome behavior, redaction guarantees, and supported revision/version. The OpenSpec SHALL then be updated and republished at a new exact SHA before implementation.

## Core predecessor dependency

MYL-271 is the planning issue; MYL-272 is the implementation issue. They are one functional gate, not two separate dependencies. Required evidence is:

- the planning issue is closed;
- the MYL-272 implementation is complete, independently verified, and integrated into the selected implementation base;
- the actual named-source and/or exact-retained-backup COPY operations consumed here exist as public contracts with tests;
- adoption does not duplicate source selection, restore, retention, readiness, or cleanup behavior already provided by the predecessor.

MYL-272 being `in_progress`, having accepted individual WP SHAs, or lacking a linked PR is not evidence of integrated completion. At revalidation, inspect the final merge/integration SHA rather than relying on the issue description.

## Scope allocation

- Deployment/operator policy chooses approved code/base/source and passes exact selections into these primitives.
- Existing core project configuration remains the source of Odoo repository/config/secret-root identity.
- Skills/scripts/workers compose checkout, context, preparation, and lifecycle commands and persist their results.
- Infrastructure owns topology, domains/proxy, anonymization, and restrictive credentials.
- GitHub #105 owns later telemetry and inventory enrichment.

## Mandatory re-research checklist

Implementation remains prohibited until a new planning revision records all of the following:

1. Exact integrated MYL-272 base SHA and the public source/COPY types and operation signatures actually used.
2. Exact `multica-py` revision/version completing all of #93 and the typed checkout/daemon-status APIs actually used.
3. Updated compatibility constraints, packaging metadata, test fixtures, and any changed failure/cancellation semantics.
4. Reconciled proposal, design, every delta spec, tasks, issue-70 disposition, and delivery plan.
5. Recomputed estimate properties when evidence or scope changes, strict OpenSpec validation, repository checks, independent Plan Verifier approval, normal push, and remote-SHA equality for the new exact SHA.

No raw-command fallback, local output decoder, private HTTP call, or speculative signature may be introduced to bypass this checklist.
