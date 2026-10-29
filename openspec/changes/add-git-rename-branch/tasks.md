## 1. Shared Ticket Allocation

- [ ] 1.1 Add frozen ticket-allocation/result types and move ticket validation, iteration parsing, catalogue projection, local/origin evidence capture, maximum allocation, provenance, and stale-absence checks into one SDK-owned module.
- [ ] 1.2 Switch `env create`/`env checkout` to the shared allocator while preserving existing command plans, provenance, locking, errors, and allocation behavior.
- [ ] 1.3 Relocate and extend focused allocation tests for free names, local/catalogue/origin collisions, gapped suffixes, unavailable evidence, and checkout stale-plan regression.

## 2. Public SDK Rename Operation

- [ ] 2.1 Implement `GitResource.rename_branch_command(ticket)` and delegating `rename_branch(ticket)` with one frozen inspectable command, attached-branch/idempotent planning, and typed JSON-safe result.
- [ ] 2.2 Add named repository/current-branch/HEAD/index/dirty/upstream and local/catalogue/origin revalidation before a non-force rename, with no dynamic process construction during execution.
- [ ] 2.3 Implement mismatched-upstream removal, final state verification, and bounded non-overwriting compensation with explicit incomplete-recovery errors.
- [ ] 2.4 Add SDK tests covering free and occupied names from every source, multiple iterations, already-matching no-op, detached HEAD, post-plan collisions, changed repository state, HEAD/index/dirty preservation, upstream cleanup, compensation, and absence of remote/worktree/catalogue mutation.

## 3. CLI, Inventory, and Documentation

- [ ] 3.1 Register `odcli git rename-branch TICKET` as a transport-only adapter using existing root selectors, ticket validation, `run_or_preview`, Rich/JSON/TOON output, dry-run, and typed failure handling.
- [ ] 3.2 Add exactly one `PUBLIC_LEAF_CASES` row and update CLI characterization, output-mode, SDK-first architecture, dry-run, help, and documentation-inventory fixtures/assertions.
- [ ] 3.3 Document project/environment invocations, local-only effects, idempotency, collision failure, and dry-run in the maintained README command inventory.

## 4. Verification Gates

- [ ] 4.1 Run focused shared-allocation, SDK Git rename, CLI output/inventory, and documentation tests; record commands and exit codes without committing logs or diagnostic evidence.
- [ ] 4.2 Run the repository formatting, lint, type, architecture, and applicable test gates within the local ten-minute automatic-suite limit; publish the exact implementation revision for CI when a required suite exceeds that limit.
- [ ] 4.3 Validate the completed OpenSpec change strictly and confirm no production registry, dependency, migration, Jira/Multica integration, remote mutation, or unrelated feature entered the implementation.
