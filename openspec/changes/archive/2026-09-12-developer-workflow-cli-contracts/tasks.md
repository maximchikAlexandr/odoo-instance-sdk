## 1. Shared CLI, Plan, and Configuration Foundations

- [x] 1.1 Add contract-first tests for removal of `--json`, eligible typed `--fields`, preserved envelopes/collections/metadata, and pre-execution usage failures; implement one pure recursive projection derived only from each leaf's concrete typed result schema, with no command/field allowlist, at the existing output boundary and update `PUBLIC_LEAF_CASES`. [Source: GH#62]
- [x] 1.2 Extend the shared Rich plan projection so nested process steps are always shown in immutable order, and add width/content contract helpers without a renderer registry or reconstructed argv. [Source: GH#64 §4-§6, §9]
- [x] 1.3 Rename Jira-specific allocation implementation, metavariable, provenance, errors, help, docs, and the complete existing checkout/allocation/lifecycle regression suite to tracker-neutral Ticket Allocation while preserving every allocation and late-collision case and eliminating the old machine key. [Source: GH#64 §11]
- [x] 1.4 Add typed secret-free doctor remediation models/rendering and one on-demand optional-executable resolver usable by doctor and immutable plans; prove findings remain read-only and optional absence non-fatal. [Sources: GH#62; GH#54; GH#65]
- [x] 1.5 Extend project config models/init serialization with tracker-neutral `ticket_link_enabled` and `ticket_base_url` settings and precedence, migrate or actionably reject the historical vendor-specific alpha setting when incomplete, and add no standalone global-config subsystem. [Sources: GH#65; GH#64 §11]
- [x] 1.6 Move the existing module and translation callbacks into their owned command modules and leave only stable registration hooks at the shared CLI composition boundary so later packages can mutate disjoint files; update the architecture inventory and lifecycle import expectations while preserving callback behavior and semantic command IDs. [Sources: GH#34; GH#54; GH#64 §10]

## 2. Unified Storage and Backup Ownership

- [x] 2.1 Inventory every current platformdirs-backed config/data/cache/state path and add a single `~/.odcli/` path provider plus durable migration journal and global lock, explicitly excluding repository-local `.odcli/`. [Source: GH#64 §7]
- [x] 2.2 Implement staged fresh/legacy/conflict/interruption migration for catalogue, environments, projects, backups, locks, and pgAdmin, preserving modes/ownership and deleting only verified sources; add retry tests including HOME with spaces and paths outside HOME. [Source: GH#64 §7]
- [x] 2.3 Add and test the sequential v15-to-v16 catalogue migration for nullable indexed backup `project_id`, deterministic-provenance-only backfill/relink, preserved backup UUID and event/restore/environment relations, foreign-key integrity, and path rewrites coordinated with the storage migration. [Source: GH#64 §1, §7]
- [x] 2.4 Capture canonical project ownership at download start for local and remote sources and replace project list joins with direct ownership; prove cross-project isolation and global listing, and prove zero-owner or ambiguous multi-project legacy provenance remains null, excluded from every project scope, and globally visible until explicit safe relink. [Source: GH#64 §1]

## 3. Module Developer Workflow

- [x] 3.1 Add frozen module models and `ModuleResource` with one safe on-demand manifest catalogue, Odoo root precedence, nearest-addon resolution, shadow warnings, and containment protections. [Source: GH#34]
- [x] 3.2 Implement direct dependency results and stable transitive install ordering with explicit missing-edge and cycle diagnostics; test without executing manifests. [Source: GH#34]
- [x] 3.3 Reuse the test changed-file snapshot for `module update --changed`, capture selection provenance in the shared plan, enforce exclusivity/stale failure/no-op/not-installed/confirmation rules, and use the existing exclusive update path. [Source: GH#34]
- [x] 3.4 Add `module info|where|deps|install-order|update` CLI/SDK parity across environment/project/root-selector contexts and Rich/JSON/TOON, including exact nested dry-run process visibility. [Sources: GH#34; GH#64 §6]
- [x] 3.5 Map only the known concurrent Odoo module `UserError` to `module_operation_in_progress` and add matched/unmatched/process-failure CLI regressions proving no retry or wait. [Source: GH#64 §8]

## 4. Environment Inspection and CLI Presentation

- [x] 4.1 Add a pure single-snapshot environment/project/cluster selector and `env show [ENVIRONMENT]` with cwd/explicit ambiguity rules, stopped/null metric visibility, typed format parity, and no process starts. [Source: GH#43]
- [x] 4.2 Extend doctor through the existing owner resolver/runtime view for project and environment ownership, configured-versus-available runtime facts, invalid paths/stopped services/ambiguous database, secret redaction, and structured remediations. [Sources: GH#43; GH#62]
- [x] 4.3 Make environment-list Rich layout adaptive at 80/120/180 columns with required primary fields and path-aware HOME shortening while preserving absolute JSON/TOON and `env path`. [Source: GH#64 §4, §7]
- [x] 4.4 Add a real-leaf Rich presentation audit and repair bounded command renderers, including a typed `backup validate` view for valid/invalid/unavailable results, without changing machine payloads. [Source: GH#64 §5, §9]
- [x] 4.5 Make all eleven short resource spellings canonical on the same Click objects, retain long aliases, and verify group/leaf help, completion, machine IDs, safety, and format parity. [Source: GH#64 §10]
- [x] 4.6 Separate manifest and generated-config idempotence in init; atomically repair missing/stale Compose config with target protections, `0600`, secret-free dry-run, and byte-preserved manifest/source tests. [Source: GH#64 §3]

## 5. Translation Validation

- [x] 5.1 Add available/unavailable `msgfmt` plan tests and capture one absolute `msgfmt --check --statistics -o /dev/null -` step with `LC_ALL=C` and generated PO stdin before publication. [Source: GH#54]
- [x] 5.2 Execute the captured optional validation step, bound/preserve warnings and statistics, keep destination atomic on failure, and emit equivalent Rich/JSON/TOON validation results without a second statistics model. [Source: GH#54]
- [x] 5.3 Verify help, dry-run inertness, architecture inventory, and wheel/sdist metadata contain no gettext dependency, wrapper, configuration, or extra flag. [Source: GH#54]

## 6. Stopped-Project Restore Lifecycle

- [x] 6.1 Add restore regressions for stopped Odoo success, foreign listener, auxiliary startup failure, pre-mutation unsupported config, filestore/default postconditions, cleanup, compensation, and retained-artifact retry. [Source: GH#64 §2]
- [x] 6.2 Build an immutable bounded auxiliary Database Manager process from resolved project runtime/config, with free-endpoint and ownership preconditions, readiness, exact recovery guidance, and guaranteed owned cleanup through existing process primitives. [Source: GH#64 §2]
- [x] 6.3 Route the existing restore pipeline through the auxiliary runtime without weakening backup/target/filestore/ownership/postcondition/compensation/default-switch guarantees or duplicating restore logic. [Source: GH#64 §2]

## 7. Odoo Git Workflow

- [x] 7.1 Add frozen Git models/resource and staged-context probes; reuse WP-03's module mapping, implement safe scope resolution, deterministic tag precedence, exact tracker-neutral configured ticket-link message, immutable commit command, hooks, confirmation, and dry-run parity with no vendor-specific supported identifier or output. [Sources: GH#65; GH#64 §11]
- [x] 7.2 Implement base resolution and `git check` for exact message/module/protected-branch rules and pending fixups with bounded Rich/JSON/TOON diagnostics. [Source: GH#65]
- [x] 7.3 Add always-visible `git absorb` as a thin captured external-tool adapter with staged-only behavior, base/dry-run/explicit-rebase options, unmapped-hunk reporting, missing-tool error, doctor warning, and install hints. [Source: GH#65]
- [x] 7.4 Implement `git sync` preconditions, fetch/integration/rebase/conflict guidance, pre-push check, same-name SSH-only publication, normal fast-forward, and exact fetched-SHA lease with stale-lease failure and no retry. [Source: GH#65]
- [x] 7.5 Wire `odcli git commit|check|absorb|sync` and public SDK siblings through shared context, output, execution, confirmation, and redaction boundaries; cover temporary-repository new/fast-forward/rebase/conflict/push safety paths without network services. [Source: GH#65]

## 8. Documentation and Verification

- [x] 8.1 Update README, CLI help, shell completion, changelog, and public SDK examples for all six source issues, including breaking format/ticket names, storage migration recovery, module/translation/Git workflows, and absolute-path caveats. [Sources: GH#34; GH#43; GH#54; GH#62; GH#64; GH#65]
- [x] 8.2 Run strict OpenSpec validation, Ruff format/check, strict mypy, focused/full pytest, architecture/security/redaction/output/catalogue/docs checks, frontend/codegen checks, and package build/install gates; record command and exit-code evidence. [Sources: GH#34; GH#43; GH#54; GH#62; GH#64; GH#65]
- [x] 8.3 Audit the final diff for one renderer/runner/resolver/catalogue/module mapping/Git resource, no prohibited dependencies or integrations, complete requirement-to-test traceability, exact base ancestry, and clean worktree. [Sources: GH#34; GH#43; GH#54; GH#62; GH#64; GH#65]
