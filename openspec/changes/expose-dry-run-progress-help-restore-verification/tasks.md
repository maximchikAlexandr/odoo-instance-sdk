## 1. Shared Filesystem-Effect Contract

- [ ] 1.1 Add one validated JSON-safe `file_effects` builder for existing `ActionStep.details`, covering action, normalized path, move destination, subject, condition, and uncertainty without a second public plan model.
- [ ] 1.2 Render structured effects from the existing plan in Rich while preserving equal JSON/TOON values, action ordering, compatibility fields, redaction, and canonical fingerprints.
- [ ] 1.3 Add parametrized schema, path-normalization, redaction, Rich projection, stale-plan, and no-mutation contract tests for create/modify/replace/delete/move/conditional/unknown effects.

## 2. Project, Translation, VS Code, and Bug-Report Effects

- [ ] 2.1 Extend translation export planning/execution for `i18n`, PO/POT create/replace, ISO-name uncertainty, optional msgfmt ordering, atomic publication, and temporary-file exclusion.
- [ ] 2.2 Extend `init` and `vscode generate` plans for applicable generated files, create/modify/no-op classification, `--write` gating, directory creation, and existing-file refusal.
- [ ] 2.3 Extend bug-report init/submit plans for draft directory, `report.md`, `metadata.json`, `reviews/`, and exact metadata modification while retaining publication descriptions.
- [ ] 2.4 Add parametrized unit/integration cases for every applicable mode, non-write mode, existing/new target, uncertainty, plan/execution parity, and zero target mutations under dry-run.

## 3. Environment, Database, Backup, and Restore Effects

- [ ] 3.1 Extend environment create/checkout/sync/removal plans for worktree, generated config, dependency lock, owned/reused venv, cleanup rollback, backup, and filestore preservation/deletion conditions.
- [ ] 3.2 Extend database restore/refresh/drop plans for backup/config/filestore paths, prior-filestore move destination, publication/cleanup/compensation order, and provenance-bound filestore retention.
- [ ] 3.3 Integrate standardized delete/retain effects into backup deletion without losing existing path, presence, size, or relationship fields.
- [ ] 3.4 Add parametrized ownership, conditional-effect, drift, retry, dry-run-inertness, and plan/execution parity tests across environment/database/backup flows.

## 4. Git, Runtime, Module, and Self-Update Effects

- [ ] 4.1 Describe known Git commit/absorb/sync index, history, worktree, rebase, and push consequences with explicit conflict-dependent uncertainty and no fabricated final file list.
- [ ] 4.2 Describe only confirmed managed config/log/data roots for run, shell, exec, eval, test, module test, and module update, including the arbitrary-code prediction limit.
- [ ] 4.3 Describe self-update metadata snapshot, installation replacement, OdCLI data migration, verification, and rollback scope without labelling internal package files or OdCLI state as Odoo backup data.
- [ ] 4.4 Add parametrized raw-stream compatibility, uncertainty, redaction, and dry-run-inertness tests for these command families.

## 5. Machine Progress Stream

- [ ] 5.1 Extend existing `StepEvent`/`RunContext.progress` with additive byte unit/rate data and monotonic validation while preserving current Rich lifecycle semantics.
- [ ] 5.2 Wire trustworthy stage/byte events into Git clone and backup download, and expose one stable restore-consumer adapter over the existing progress lifecycle without editing restore domain implementation.
- [ ] 5.3 Emit compact JSON-lines or Record-Separator-framed TOON progress on stderr for normal machine execution while retaining exactly one final stdout document and no dry-run progress.
- [ ] 5.4 Add parametrized known/unknown total, rate, framing, failure, interruption, stream separation, and secret-canary tests.

## 6. Machine-Readable Help

- [ ] 6.1 Project a versioned command/argument/option tree directly from registered Click command objects, including types, required/default state, choices/ranges, repeatability, descriptions, and safe default availability.
- [ ] 6.2 Support root and descendant `--help --format json|toon` ordering without resolving project/domain context, changing normal command format scope, or maintaining a second registry.
- [ ] 6.3 Add JSON/TOON semantic-parity, Rich compatibility, unsafe-default, hidden/deprecated, alias, root/subtree, and command-inventory drift tests.

## 7. Restore Verification Result

- [ ] 7.1 Add typed independent neutralization and filestore verification outcomes with `passed|failed|not_checked`, sanitized reasons, and unique expected/found/missing counts.
- [ ] 7.2 Query distinct safe attachment store identities through the bound database, verify them under the captured filestore root, and preserve restore compensation/publication rules on mismatch or query failure.
- [ ] 7.3 Expose outcomes consistently in Rich, JSON, and TOON without allowing database existence or password reset to imply verification success.
- [ ] 7.4 Add parametrized complete/incomplete/duplicate/unsafe/not-applicable/query-failure tests and focused real-Odoo restore coverage.
- [ ] 7.5 Integrate the stable progress adapter into restore acquisition, import, filestore, verification, and cleanup stages while retaining restore implementation and restore-domain test ownership here.

## 8. Cross-Cutting Verification and Documentation

- [ ] 8.1 Update `PUBLIC_LEAF_CASES`-based characterization and existing generated command matrix without introducing a second inventory; cover every affected leaf and directly affected regression.
- [ ] 8.2 Add or extend repository CI fixtures for selected real-Odoo restore/file-effect/progress paths, keeping automated suites within the workspace time policy and using exact published revisions for CI.
- [ ] 8.3 Document file-effect vocabulary, conditional/unknown semantics, machine-help schema, stderr progress framing, stdout compatibility, restore verification states, and explicit prediction exclusions.
- [ ] 8.4 Complete independent exact-revision E2E evidence according to `delivery-plan.md`, including Mac text/PNG before-after captures, GitLab test-script MR evidence, Multica attachments/read-back, and failure-safe cleanup that preserves evidence branches/commits.
