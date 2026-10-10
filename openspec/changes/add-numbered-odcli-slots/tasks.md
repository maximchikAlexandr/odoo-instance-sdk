## 1. Root and slot identity foundation

- [ ] 1.1 Add validated slot-number, deterministic manager/tool/launcher/state path, and bounded manifest types without a registry database or new dependency.
- [ ] 1.2 Extend the central global path provider with canonical-default and trusted explicit-root selection while preserving the real `HOME` and repository-local `.odcli` paths.
- [ ] 1.3 Add focused provider inventory tests proving every SDK-owned global path descends from one selected root and canonical behavior is unchanged.

## 2. Exact-SHA slot lifecycle manager

- [ ] 2.1 Implement immutable install/replace commands that use slot-specific uv layouts, fixed repository origin, full-SHA validation, temporary staging, installed-provenance and selector-capability verification, lifecycle locking, and atomic publication.
- [ ] 2.2 Implement bounded list projection from valid manager manifests without executing slot code or scanning arbitrary home paths.
- [ ] 2.3 Implement fail-closed selective removal with exact path, symlink, manifest, launcher-content, running-lock, and neighboring-state protections.
- [ ] 2.4 Register canonical `slot install`, `slot list`, and `slot remove` leaves through the existing SDK-first command and Rich/JSON/TOON output boundaries, including public-leaf inventory and machine parity tests.

## 3. Runtime isolation and update policy

- [ ] 3.1 Gate legacy platformdirs migration so canonical startup retains existing adoption while an explicit noncanonical root performs no legacy/canonical discovery, copy, rewrite, lock, or cleanup.
- [ ] 3.2 Make the generated numbered launcher hold the shared lifecycle lock, preserve exact argv/exit status without a shell, and pass only root, slot, and executable identity selectors to the child.
- [ ] 3.3 Reject numbered-context slot management and every self-update mode before planning or effects, with the canonical exact-SHA replace remediation; retain ordinary `odcli update` behavior and isolation from slots.
- [ ] 3.4 Add unit and process-boundary regressions for fresh root behavior, migration bypass, canonical migration compatibility, full-lifetime locking, argument fidelity, direct-update rejection, and no cross-root access.

## 4. Integrated acceptance and documentation

- [ ] 4.1 Add packaging acceptance that installs two different exact revisions in independent uv layouts, exercises incompatible SQLite migrations concurrently, and proves writes remain confined to each `~/.odcli-N` while canonical and neighboring roots remain unchanged.
- [ ] 4.2 Add replacement and removal acceptance proving verified SHA reassignment preserves only the selected state, removal of one slot preserves canonical and other slots byte-for-byte, and corrupted identity fails closed.
- [ ] 4.3 Document numbered-slot setup/use/replace/remove, preserved canonical and `odcli-fix-<issue>` behavior, isolated SDK state classes, and the requirement for separate project copies or explicitly disjoint Odoo/PostgreSQL/database/filestore/Docker/port resources.
- [ ] 4.4 Run focused tests, packaging tests, public-leaf/output/architecture inventories, Ruff format/check, strict mypy, standard repository checks, `git diff --check`, and strict OpenSpec validation; record exact commands and evidence.
