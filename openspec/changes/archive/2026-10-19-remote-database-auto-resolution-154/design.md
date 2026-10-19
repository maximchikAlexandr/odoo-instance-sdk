## Context

PR #155 already contains the behavioral implementation for optional remote databases, Compose init consent, generated local credentials, captured Git guards, and pinned hot-fix provenance. The review found that image consent is still embedded in the 1,007-line `project_init.py` coordinator, the primary requirements lag behind the implementation, and eight focused tests combine unrelated branches. The fix must preserve the reviewed command-plan and failure semantics while making the boundaries and evidence easier to audit.

## Goals / Non-Goals

**Goals:**

- Keep `init_project_command()` as the phase coordinator and move only image-consent capture/execution into a focused helper.
- Preserve immutable step IDs/order, dry-run behavior, approval-store semantics, and fail-closed image resolution.
- Align primary specs and an issue-scoped change with the implemented optional remote, init, COPY, credential, Git-validation, image-consent, and hot-fix provenance contracts.
- Split the eight D/F tests into focused public-behavior scenarios without dropping acceptance coverage.
- Make `make lint`, `make types`, strict OpenSpec validation, and the focused suites pass.

**Non-Goals:**

- No new executable, dependency, public API, remote transport, or real-Odoo E2E behavior.
- No changes to the unrelated active OpenSpec change, review process, PR identity, or reviewer scope.
- No weakening of coverage or deletion of a scenario merely to improve test grades.

## Decisions

### Keep phase ownership in a small internal helper

Add a focused helper beside the existing init implementation that captures an optional `PreparedCommand[str]` plus approval `PreparedAction`, returns the prepared steps, and executes the consent pair against the existing `RunContext`. The helper receives the already-created `ComposeFollowup` and callback; it does not create a second plan or invoke the CLI. This is the smallest boundary that removes feature-specific control flow from the coordinator while keeping all existing values and order.

Alternative: move all Compose init phases into a new orchestration class. Rejected because it would widen the diff and duplicate the existing phase contracts.

### Keep tests scenario-focused

Use separate test functions for success/error or acceptance/refusal outcomes. Shared fixtures and small factories may remove setup repetition, but each test retains explicit action, plan, mutation, rollback, provenance, and secrecy assertions. Parameterization remains only where the behavior contract is the same (for example, a matrix of failure steps).

### Treat the primary specs as the product contract

Update the existing named-remote, init, restore, and numbered-tool requirements in place, then mirror the affected full requirement blocks as issue-scoped delta specs. The delta documents include the implementation-only safety contracts for generated credentials, captured Git checks, and image consent so this review has a traceable proposal/design/tasks/deltas record.

## Risks / Trade-offs

- [Risk] Moving the consent code can alter plan ordering or dry-run behavior. → Keep the same step IDs and splice the helper's prepared steps at the original location; retain image approval tests before and after the move.
- [Risk] Splitting tests can accidentally lose a branch. → Map every original assertion to a named scenario and rerun the same curated coverage command with branch coverage enabled.
- [Risk] Broad spec edits can drift from implementation. → Validate strict OpenSpec and run the existing documentation-contract suite before publishing the fix.

## Migration Plan

1. Create the issue-scoped artifacts and update the primary requirements.
2. Extract image consent, split tests, and fix the narrow typing issue.
3. Run focused suites, strict OpenSpec validation, lint/types, and before/after comparable coverage.
4. Commit the bounded Conventional Commit sequence on the existing feature branch and push normally; rollback is the prior reviewed SHA if acceptance rejects the batch.

## Open Questions

None for this bounded fix; real-Odoo E2E remains an external prerequisite and is not part of this implementer handoff.
