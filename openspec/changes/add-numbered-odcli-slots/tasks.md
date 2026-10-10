## 1. Alternate-root identity foundation

- [ ] 1.1 Add only validated numbered-slot and hot-fix identities plus deterministic state, manager, tool, launcher, manifest, and lock paths without provenance, lock-acquisition, shim-rendering, registry, or service helpers.
- [ ] 1.2 Extend the central global path provider with canonical-default and trusted explicit-root selection while preserving real `HOME` and repository-local `.odcli` paths.
- [ ] 1.3 Add provider inventory tests proving every SDK-owned global path descends from exactly one selected root and canonical behavior remains unchanged.

## 2. Numbered exact-SHA lifecycle manager

- [ ] 2.1 Implement immutable install/replace commands using slot-specific uv layouts and numbered-manager-owned provenance/capability checks, lock acquisition, shim rendering, temporary staging, and atomic publication.
- [ ] 2.2 Implement bounded numbered-slot list projection from valid manager manifests without executing slot code or scanning arbitrary home paths.
- [ ] 2.3 Implement fail-closed numbered-slot removal with exact path, symlink, manifest, launcher-content, running-lock, and neighboring-root protections.
- [ ] 2.4 Register canonical `slot install`, `slot list`, and `slot remove` through the SDK-first command and Rich/JSON/TOON boundaries, including public-leaf inventory and machine parity tests.
- [ ] 2.5 Make numbered launchers hold their lifecycle lock, pass only root/slot/executable selectors, preserve argv and exit status without a shell, and reject numbered-context slot management before manager discovery or effects.

## 3. Shared runtime isolation and update policy

- [ ] 3.1 Gate legacy platformdirs adoption so only canonical startup performs discovery/copy/rewrite/cleanup and every explicit alternate root starts independently.
- [ ] 3.2 Propagate selected-root identity through all remaining global consumers, locks, catalogues, reports, storage/update journals, and migration paths.
- [ ] 3.3 Reject every numbered and hot-fix self-update mode before planning or effects with launcher-specific canonical remediation; preserve ordinary `odcli update` and keep it isolated from alternate roots.
- [ ] 3.4 Add focused path, migration, update, and process-boundary regressions for canonical compatibility, empty alternate roots, direct-update rejection, and zero cross-root access.

## 4. Existing hot-fix workflow isolation

- [ ] 4.1 Move hot-fix manager metadata and locks out of canonical `~/.odcli`, select deterministic `~/.odcli-fix-ISSUE`, and update the skill contract without copying any existing user state.
- [ ] 4.2 Update hot-fix install/reviewer gates with skill-owned provenance/capability checks and shim rendering to verify exact SHA, repository, isolated-root capability, launcher identity, and compatibility of explicitly shared external resources before publication.
- [ ] 4.3 Update hot-fix shim and skill-managed reconcile/retire flow with skill-owned lock acquisition to hold lifecycle locks and remove only the eligible hot-fix launcher, uv layout, metadata, branch when safe, and `~/.odcli-fix-ISSUE`, preserving canonical and neighboring roots.
- [ ] 4.4 Add autonomous-skill tests for two concurrent hot fixes, fresh isolated migration, direct-update rejection, selective retirement, active-lock/corrupt-identity failure, and canonical/numbered/neighbor byte preservation.

## 5. Integrated acceptance and documentation

- [ ] 5.1 Add acceptance with canonical `odcli`, two numbered revisions, and two hot-fix revisions using incompatible SQLite migrations concurrently; prove each process accesses only its selected root.
- [ ] 5.2 Add migration/update/removal acceptance proving no alternate launcher adopts legacy or canonical state, canonical update preserves alternate roots, and one numbered removal or hot-fix retirement preserves all neighbors.
- [ ] 5.3 Document numbered and hot-fix setup/use/revision/retirement, isolated state classes, no-copy policy, and the requirement for separate project copies or disjoint Odoo/PostgreSQL/database/filestore/Docker/port resources.
- [ ] 5.4 Run focused, autonomous-skill, packaging, public-leaf/output/architecture, Ruff, strict mypy, repository, diff, and strict OpenSpec gates; record exact evidence.
