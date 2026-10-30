## 1. Shared Ticket Allocation

- [x] 1.1 Move the existing `_TicketAllocation` data plus ticket validation/parsing, catalogue projection, local/origin evidence capture, maximum allocation, provenance, and stale-absence helpers from the Click adapter into one narrow SDK-owned module without changing their behavior or shape.
- [x] 1.2 Switch `env create`/`env checkout` imports to the moved helpers while preserving existing command plans, provenance, locking, errors, and allocation behavior.
- [x] 1.3 Move the existing focused allocation tests with their owner and add only the rename-sharing regression cases; do not retain duplicate allocation matrices.

## 2. Public SDK Rename Operation

- [x] 2.1 Implement `GitResource.rename_branch_command(ticket)` and delegating `rename_branch(ticket)` with one frozen inspectable command, attached-branch/idempotent planning, one non-force `git branch -m` step, and a typed JSON-safe result.
- [x] 2.2 Revalidate only current branch, HEAD, and selected-name absence across local/catalogue/origin evidence before mutation; fail stale rather than reallocating, and preserve standard Git branch configuration/upstream behavior.
- [x] 2.3 Add focused SDK tests for free and occupied names from every source, multiple iterations, already-matching no-op, detached HEAD, post-plan branch/HEAD/collision changes, dirty/index/untracked preservation, standard branch-config behavior, and absence of remote/worktree/catalogue mutation.

## 3. CLI, Inventory, and Documentation

- [x] 3.1 Register `odcli git rename-branch TICKET` as a transport-only adapter using existing root selectors, ticket validation, `run_or_preview`, Rich/JSON/TOON output, dry-run, and typed failure handling.
- [x] 3.2 Add exactly one `PUBLIC_LEAF_CASES` row and update CLI characterization, output-mode, SDK-first architecture, dry-run, help, and documentation-inventory fixtures/assertions.
- [x] 3.3 Document project/environment invocations, local-only effects, idempotency, collision failure, and dry-run in the maintained README command inventory.

## 4. Verification Gates

- [x] 4.1 Run focused shared-allocation, SDK Git rename, CLI output/inventory, and documentation tests; record commands and exit codes without committing logs or diagnostic evidence.
- [x] 4.2 Run the repository formatting, lint, type, architecture, and applicable test gates within the local ten-minute automatic-suite limit; publish the exact implementation revision for CI when a required suite exceeds that limit.
- [x] 4.3 Validate the completed OpenSpec change strictly and confirm no production registry, dependency, migration, Jira/Multica integration, remote mutation, or unrelated feature entered the implementation.
