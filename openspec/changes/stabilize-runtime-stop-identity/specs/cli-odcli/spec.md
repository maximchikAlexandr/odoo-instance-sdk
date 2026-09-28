## MODIFIED Requirements

### Requirement: Stop a selected environment runtime

Top-level `odcli stop` SHALL resolve exactly one initialized project or registered environment through the existing shared context rules. Its help SHALL describe the selected runtime rather than requiring an environment. It SHALL re-read the exact persisted `owner_kind`, `owner_id`, PID, create time, and versioned secret-free launch identity captured from the immutable process step used to start that runtime. At execution it SHALL terminate only when the owner and every live PID create-time, executable, captured executable-prefix/protected-argv, cwd, and config check match and, on POSIX, the live process satisfies `pgid == pid`; Windows SHALL require the same available identity checks before existing process-tree termination. It SHALL NOT reconstruct authoritative project launch identity from mutable stop-time checkout configuration and SHALL never infer ownership from a listening port.

No matching runtime row, or an absent PID still associated with the selected owner, SHALL return idempotent success and conditionally clear only that owner's stale row. Reused, partial, inaccessible, owner-changed, malformed-snapshot, missing-snapshot, or mismatched identity SHALL fail closed and SHALL NOT signal any process. Rejections SHALL provide bounded sanitized component evidence: argv differences SHALL name `executable-prefix` or the protected option name, while no raw argument value, password, secret, full argv, environment value, or unsafe path SHALL be emitted. Rich, JSON, and TOON success output SHALL continue to identify `owner_kind`, canonical `project_id`, nullable `environment_id` and `environment_name`, and status consistently.

#### Scenario: Stop cwd-owned environment runtime
- **WHEN** cwd resolves a running environment and the re-read owner/runtime/captured-launch evidence plus every required live-process identity check match
- **THEN** `odcli stop` terminates that exact owned process group through the existing process boundary, verifies exit, clears only its environment-owned runtime row, and reports environment ownership

#### Scenario: Stop explicitly selected environment runtime
- **WHEN** `odcli --env ENVIRONMENT stop` is invoked outside the worktree with matching captured and live identity
- **THEN** it has the same plan, safety, output, and exit behavior as cwd environment resolution

#### Scenario: Stop project runtime after unrelated checkout evolution
- **WHEN** cwd or `--project` resolves an initialized main checkout whose project-owned process still matches its captured launch identity after a branch switch, revision change, or mutable configuration-source change
- **THEN** `odcli stop` terminates that exact owned process group, verifies exit, clears only its project-owned runtime row, reports project ownership with null environment identity, and leaves project registration intact

#### Scenario: Already stopped is idempotent for either owner
- **WHEN** the selected project or environment has no matching runtime row, or its previously owned process is confirmed absent without PID reuse
- **THEN** `stop` succeeds without signaling a process and conditionally clears only the matching stale row when present

#### Scenario: Port occupancy is not ownership
- **WHEN** an unrelated process listens on the selected owner's recorded port or a PID has been reused
- **THEN** `stop` does not signal it and reports an ownership validation failure when stale or conflicting identity remains

#### Scenario: Protected binding mismatch is actionable and sanitized
- **WHEN** the live process changes one protected binding from the captured launch identity
- **THEN** `stop` fails without signaling or clearing the row and its Rich, JSON, and TOON error message names only the safe differing component, such as `argv: --database`

#### Scenario: Secret-bearing mismatch evidence is bounded
- **WHEN** identity validation encounters a secret-bearing argv position or attacker-controlled process text
- **THEN** every output mode emits the common redaction marker where needed, preserves the safe option name, and emits neither raw values nor the full live or captured argv

#### Scenario: Persisted owner or snapshot changes during stop
- **WHEN** the persisted owner kind, owner id, PID, create time, or captured launch identity differs between planning and execution revalidation
- **THEN** `stop` fails closed without signaling or clearing either owner's runtime row

#### Scenario: Pre-migration live row has no snapshot
- **WHEN** `odcli stop` selects a live legacy runtime row without captured launch identity
- **THEN** it reports a sanitized unavailable-identity failure, sends no signal, retains the row, and does not authorize termination from reconstructed current configuration

