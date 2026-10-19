## 1. Optional named remote contract

- [ ] 1.1 Make `RemoteSourceConfig.database` optional across construction, TOML loading, serialization, listing, and update preservation while rejecting blank/whitespace/non-string explicit values and retaining URL/name/ref validation.
- [ ] 1.2 Make `remote add --database` optional and keep add/update/dry-run on the existing immutable SDK filesystem command; extend Rich/JSON/TOON and manifest round-trip tests for absent and explicit values.
- [ ] 1.3 Remove the named-source non-null assertion from refresh, reuse `resolve_remote_database_name` for explicit/single/zero/multiple/unavailable cases, and verify failure before backup with password and provenance retention.
- [ ] 1.4 Replace COPY's assertion with the specified actionable `ConfigError`, and add checkout/planning regressions showing no artifact, request, backup, or database mutation when the database is absent.
- [ ] 1.5 Add public `odcli ps --format json` regressions for absent and explicit remote databases and prove successful output with byte-for-byte unchanged manifests.

## 2. Init registration, generated secret, and image consent

- [ ] 2.1 Extend the init CLI parser and typed request to accept repeatable/mixed three- and four-field `--remote` forms, equals syntax, and neighboring/literal option values without adding a second mutation or network effect.
- [ ] 2.2 Merge supplied named sources over existing sources by normalized name, preserve all unsupplied and legacy sources, and test re-init, duplicates, dry-run, SDK-first plan identity, and bootstrap inclusion.
- [ ] 2.3 Extend generated-config rendering/repair to preserve a nonblank source or generated `admin_passwd`, otherwise generate a distinct random password only during execution, retaining `0600`, source immutability, idempotence, dry-run inertness, and complete output/plan/error/fingerprint redaction.
- [ ] 2.4 Capture exact Compose image resolution and approval before all init writes, wire `--yes`, interactive Rich consent, existing-approval skip, noninteractive refusal, and external/dry-run exclusions through the current approval store and immutable init command.
- [ ] 2.5 Add focused init/PostgreSQL tests for approval success/refusal/resolution failure/digest reuse and drift, completeness independence, generated-password creation/repair/preservation, and absence of partial project or approval mutations.

## 3. Allocation and restore publication safety

- [ ] 3.1 Correct the shared HTTP allocator to try a valid preferred candidate or 8069 first, then scan 8000–9000 inclusive once; keep explicit requests exact and PostgreSQL allocation unchanged.
- [ ] 3.2 Add allocator and public checkout matrices for preferred/occupied/reserved/out-of-range/absent candidates, both boundaries, exhaustion text/code, explicit out-of-range success/conflict, catalogue/config/live-probe exclusions, and pre-run recheck compatibility.
- [ ] 3.3 Capture generated-config repository-root and tracked-path Git checks as read-only restore preparation steps and consume them during default publication without launching Git from a mutation action.
- [ ] 3.4 Add strict preparation/restore regressions for successful publication, tracked-file refusal, Git failure, repository-root drift, previous-default preservation, compensation/retained evidence, coalesced accounting, and secret redaction.

## 4. Compatibility and documentation

- [ ] 4.1 Restrict the isolated developer hotfix verifier to the expected uv `git+https://...git@SHA` repository and matching `vcs_info.commit_id`; test exact acceptance and foreign/mutable/mismatched rejection without changing canonical installation.
- [ ] 4.2 Update README/help and canonical CLI characterization/inventory for optional remote registration/init forms, automatic HTTP range, generated local master password, image consent, and supported COPY alternatives.
- [ ] 4.3 Reconcile affected main OpenSpec requirements for CLI, project init, environment allocation, database preparation/restore, and PostgreSQL trust after implementation without weakening unrelated contracts.

## 5. Verification and evidence

- [ ] 5.1 Add the planned private test-repository E2E script and fixtures for offline optional registration, single-database refresh/restore, generated-password restore use, and image consent in a dedicated `odcli-N` environment; keep raw evidence outside Git.
- [ ] 5.2 Run focused named-remote, init, allocator, preparation/restore, PostgreSQL, process-inventory, CLI-output, developer-tool, architecture/inventory, formatting, Ruff, strict typing, line-limit/catalogue, and applicable real-Odoo checks.
- [ ] 5.3 Run strict OpenSpec validation and publish exact product/test revisions, commands, exit codes, scenario assertions, MR/commit/file/CI links, required before/after text and PNG attachments, with attachment read-back and failure-safe cleanup.
