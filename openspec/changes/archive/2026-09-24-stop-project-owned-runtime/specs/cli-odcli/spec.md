## MODIFIED Requirements

### Requirement: Stop a selected environment runtime

Top-level `odcli stop` SHALL resolve exactly one initialized project or registered environment through the existing shared context rules. Its help SHALL describe the selected runtime rather than requiring an environment. Without adding a runtime migration, it SHALL re-read the exact persisted `owner_kind` and `owner_id` plus PID/create time and reconstruct expected executable, argv, cwd, and config from the resolved owner's canonical runtime inputs. At execution it SHALL terminate only when the owner and every live PID create-time, executable, argv, cwd, and config check match and, on POSIX, the live process satisfies `pgid == pid`; Windows SHALL require the same available identity checks before existing process-tree termination. It SHALL never infer ownership from a listening port. No matching runtime row, or an absent PID still associated with the selected owner, SHALL return idempotent success and conditionally clear only that owner's stale row; reused, partial, inaccessible, owner-changed, or mismatched identity SHALL fail with actionable sanitized evidence and SHALL NOT signal any process. Rich, JSON, and TOON success output SHALL identify `owner_kind`, canonical `project_id`, nullable `environment_id` and `environment_name`, and status consistently.

#### Scenario: Stop cwd-owned environment runtime

- **WHEN** cwd resolves a running environment and the re-read owner/runtime/config evidence plus every required live-process identity check match
- **THEN** `odcli stop` terminates that exact owned process group through the existing process boundary, verifies exit, clears only its environment-owned runtime row, and reports environment ownership

#### Scenario: Stop explicitly selected environment runtime

- **WHEN** `odcli --env ENVIRONMENT stop` is invoked outside the worktree with matching live identity
- **THEN** it has the same plan, safety, output, and exit behavior as cwd environment resolution

#### Scenario: Stop project-owned main-checkout runtime

- **WHEN** cwd or `--project` resolves an initialized main checkout whose project-owned detached runtime and every required live-process identity check match
- **THEN** `odcli stop` terminates that exact owned process group, verifies exit, clears only its project-owned runtime row, reports project ownership with null environment identity, and leaves project registration intact

#### Scenario: Already stopped is idempotent for either owner

- **WHEN** the selected project or environment has no matching runtime row, or its previously owned process is confirmed absent without PID reuse
- **THEN** `stop` succeeds without signaling a process and conditionally clears only the matching stale row when present

#### Scenario: Port occupancy is not ownership

- **WHEN** an unrelated process listens on the selected owner's recorded port or a PID has been reused
- **THEN** `stop` does not signal it and reports an ownership validation failure when stale or conflicting identity remains

#### Scenario: Persisted owner changes during stop

- **WHEN** the persisted owner kind, owner id, PID, create time, or canonical runtime expectations differ between planning and execution revalidation
- **THEN** `stop` fails closed without signaling or clearing either owner's runtime row
