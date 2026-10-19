## Context

The shared alternate-tool engine already stages an exact VCS revision, verifies installed provenance and selector confinement, publishes launcher/tool/manifest paths under an exclusive per-identity lifecycle lock, and restores the previous numbered tool when publication fails. Hot-fix policy lives in `scripts/odcli_agent_tools/fix_tool.py`, while independent compatibility evidence is produced by `reviewer.py`. Today `_check_install_target()` deliberately rejects `replace=True` for hot fixes, and removing one also deletes its selected state root.

The update must preserve a populated `~/.odcli-fix-ISSUE` without letting candidate code touch it before compatibility approval. It must keep the developer-only management boundary, exact-SHA GitHub provenance, execution-architecture rules, bounded output, and canonical/neighbor isolation.

## Goals / Non-Goals

**Goals:**

- Advance exactly one existing hot-fix identity to its current reviewed PR head.
- Bind approval to both the installed and candidate revisions before mutation.
- Reuse staged publication, provenance checks, selector probing, rollback, and lifecycle locking.
- Preserve the launcher identity, selected root, and all selected-root contents.
- Make success and all fail-closed paths independently testable through the developer helper.

**Non-Goals:**

- No public `odcli` product command, general downgrade mechanism, arbitrary repository replacement, or automatic polling.
- No state migration executed during planning, review, or staging.
- No hot-fix issue/PR reassignment, canonical update, merged-fix retirement, or external Odoo/PostgreSQL/resource lifecycle.
- No new manifest version or persistent transition journal unless implementation demonstrates that the existing atomic backup transaction cannot satisfy a specified rollback scenario.

## Decisions

### 1. Add `advance` beside the existing canonical `update`

`fix_tool.py advance ISSUE SHA --pr PR --review FILE` is a distinct developer-helper operation. It first inspects the installed package-owned manifest, validates issue/PR continuity, then applies the same live GitHub head/link checks used by install. The name avoids changing the established `update` command, which updates canonical OdCLI and reconciles merged fixes.

Alternative: overload `update` with optional issue arguments. Rejected because it creates two unrelated mutation modes under one parser branch and makes automation mistakes more likely.

### 2. Bind compatibility evidence to the transition

`reviewer.py` gains a read-only `transition INSTALLED_SHA CANDIDATE_SHA` operation. The verdict contains `previous_sha`, `sha`, `compatible`, and findings under the existing session envelope. Its prompt compares catalogue/migration behavior for preserved state as well as selector and shared-resource safety. `advance` requires exact equality with the installed manifest and current PR head and rejects reuse of the installed manifest's review session.

Alternative: reuse candidate-only compatibility evidence. Rejected because it proves coexistence with isolated roots, not that the candidate may safely consume the existing root on its next invocation.

### 3. Extend the shared install transaction instead of adding an updater

The internal coordinator continues to call the single `install_alternate_tool(..., replace=True)` transaction. Hot-fix replacement is allowed only with an explicit existing-state preservation mode, matching current manifest issue/PR identity, and candidate review provenance. Staging and the disposable selector probe remain outside the exclusive lock; the current installation is re-read and structurally verified under the lock immediately before its published paths move.

The transaction backs up and swaps only `tool_root`, `tool_bin`, launcher, and manifest. It never moves or deletes `state_root`. Existing rollback restores all four published artifacts if any activation step fails.

Alternative: implement a second hot-fix updater in the helper. Rejected because it would duplicate uv installation, provenance, locking, launcher rendering, rollback, and cleanup mechanics already owned by the engine.

### 4. Fail fast on self-replacement and serialize other use

Before staging, the engine compares `selected_identity()` with the target and rejects replacement of the currently active hot-fix. Other launcher processes retain the existing shared lock; the updater waits for them to exit before the exclusive publication section, then revalidates the manifest. This preserves the current lock protocol without a second activity detector.

Alternative: inspect process tables or terminate the active launcher. Rejected because process discovery is race-prone and stopping Odoo or external resources is outside this lifecycle.

### 5. Return bounded transition evidence

The internal engine returns the new manifest projection already used by install/inspect. The helper emits one JSON object containing identity, launcher, previous SHA, candidate SHA, PR, and preserved state-root path; errors remain redacted and non-zero. It does not print review findings, environment values, state contents, or subprocess output.

Alternative: print only the launcher as install currently does. Rejected because update verification needs an unambiguous old/new revision and retained-root assertion.

### Ponytail full: subsystem gate

No new subsystem is needed. The requested behavior remains in the existing reviewer helper, hot-fix policy helper, and package-owned alternate-tool lifecycle. A transition-specific reviewer command and helper parser branch are necessary because candidate-only review and canonical update are not equivalent; a new installer, runner, lock manager, state migrator, journal, dependency, or public SDK layer is removed from the design.

## Risks / Trade-offs

- **[Compatibility review is static]** → Bind it to both SHAs and require uncertainty to fail closed; automated tests cover the mechanical preservation guarantee, while the reviewer owns semantic migration safety.
- **[Candidate becomes stale after review]** → Re-read the live PR head immediately before invoking replacement and bind engine provenance to the same full SHA.
- **[Concurrent update changes the installed revision]** → Re-read and verify the expected installed manifest under the exclusive lifecycle lock; mismatch aborts without publishing the staged candidate.
- **[Failure occurs mid-publication]** → Reuse the existing same-filesystem backup and `os.replace` rollback sequence, cover each publication failure point, and never include the state root in the swap.
- **[A process starts while staging]** → Launcher use takes the shared lock; exclusive publication waits, then swaps only after all current users exit.
- **[State may require a later application migration]** → The transition review explicitly evaluates that path; update itself does not execute candidate code against preserved state.

## Migration Plan

1. Add transition-review schema/prompt and helper parsing without changing existing install compatibility review.
2. Add fail-closed `advance` policy validation and bounded output.
3. Narrowly permit verified hot-fix replacement in the shared lifecycle while retaining state.
4. Extend focused unit, packaging/integration, skill-doc, and architecture inventory coverage.
5. Rollback is code rollback: existing installations remain manifest-version compatible; if activation fails at runtime, the transaction restores the old published installation automatically.

## Open Questions

None. The interface, evidence schema, ownership boundaries, rollback behavior, and verification surface are fixed by this design.
