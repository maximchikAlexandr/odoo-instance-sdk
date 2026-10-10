## 1. Shared alternate-tool foundation

- [ ] 1.1 Add validated numbered/hot-fix identities, deterministic state/tool/launcher/manifest/lock paths, and canonical-default trusted user-root selection without changing real `HOME` or project-local `.odcli`.
- [ ] 1.2 Extract the existing fix-tool installation path into one package-owned pipeline for temporary uv staging, exact installed provenance, selector-capability probing, atomic publication, and numbered-state preservation on explicit replace.
- [ ] 1.3 Extract one discriminated manifest, shim renderer, full-lifetime lifecycle lock, bounded inspect/list, and fail-closed removal path that deletes only a structurally verified idle identity plus its state.
- [ ] 1.4 Reduce `fix_tool.py` to canonical-interpreter bootstrap and PR/reviewer/issue/merge/ancestry/branch policy that delegates install/inspect/remove to the shared module; update compatibility review to shared external resources only.
- [ ] 1.5 Add shared-engine, packaging-bootstrap, and thin hot-fix-policy tests proving both launcher kinds use the same probe, renderer, lock, manifest, and cleanup implementation.

## 2. Numbered command adapter

- [ ] 2.1 Add canonical `slot install NUMBER SHA [--replace]`, `slot list`, and `slot remove NUMBER` as a thin adapter over shared lifecycle operations with no uv, manifest, shim, lock, or cleanup implementation in the command layer.
- [ ] 2.2 Register the slot group through existing command/output boundaries, public-leaf inventory, and Rich/JSON/TOON parity.
- [ ] 2.3 Reject numbered-context slot management before shared-engine discovery or effects and preserve exact argv/exit behavior for ordinary numbered commands.
- [ ] 2.4 Add numbered policy/output tests for invalid identity, existing-vs-replace intent, bounded list, running/corrupt removal, and neighbor preservation using shared-engine fixtures.

## 3. Runtime confinement and update policy

- [ ] 3.1 Gate legacy platformdirs adoption so only canonical startup performs discovery/copy/rewrite/cleanup and every explicit alternate root starts independently.
- [ ] 3.2 Propagate selected-root identity through remaining SDK-owned global consumers, catalogues, locks, reports, storage/update journals, and migration paths.
- [ ] 3.3 Reject numbered and hot-fix self-update before planning/effects with launcher-specific canonical remediation while preserving isolated ordinary `odcli update`.
- [ ] 3.4 Add path, migration, update, and process-boundary regressions proving canonical compatibility, empty alternate roots, and zero cross-root access.

## 4. Integrated acceptance and documentation

- [ ] 4.1 Add acceptance with canonical `odcli`, two numbered revisions, and two hot-fix revisions using incompatible SQLite migrations concurrently; prove each process accesses only its selected root.
- [ ] 4.2 Add migration/update/removal acceptance proving no alternate adopts legacy/canonical state, canonical update preserves alternates, and shared removal of one numbered or policy-eligible hot fix preserves every neighbor.
- [ ] 4.3 Document the single shared lifecycle, thin numbered/hot-fix policy split, setup/use/revision/retirement, no-copy rule, old-style remediation, and non-isolated external resources.
- [ ] 4.4 Add an architecture guard against duplicate uv/provenance/probe/shim/lock/remove mechanics and run focused, skill, packaging, output/inventory, Ruff, strict mypy, repository, diff, and strict OpenSpec gates.
