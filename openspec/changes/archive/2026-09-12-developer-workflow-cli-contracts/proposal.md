## Why

OdCLI's next developer-workflow increment is currently split across six overlapping issue contracts, while the implementation already has shared execution, output, context-resolution, catalogue, and lifecycle boundaries. Planning them independently would duplicate infrastructure and make dry-run, machine output, safety, and ticket semantics diverge, so this change defines one reviewable contract against exact repository baseline `0ff164636617c03a51277055af45cef009277368`.

## What Changes

- **BREAKING** Replace the `--json` compatibility alias with the single `--format rich|json|toon` selector and replace Jira-specific environment-allocation names and machine provenance with tracker-neutral Ticket Allocation terminology.
- Add typed `--fields` projection for eligible bounded machine-readable results, structured doctor remediations, focused `env show`, effective-runtime diagnostics, and width-aware/contentful Rich presentations.
- Consolidate global user storage below `~/.odcli/` through an idempotent, locked, conflict-safe migration while preserving repository-local `.odcli/` data and absolute machine paths.
- Make backup ownership explicit, repair `backup validate` presentation, restore generated Compose config during idempotent init, and make stopped-project database restore self-sufficient or fail before mutation.
- Add manifest-backed Odoo module discovery, dependency ordering, and changed-module update selection while reusing the existing resolver, test change selector, execution plan, process boundary, and exclusive update path.
- Add optional planned `msgfmt` validation to translation export without a package dependency or a second statistics model.
- Add staged-only Odoo commit generation, history checks, a thin optional `git-absorb` adapter, and safe same-branch Git synchronization/publication through immutable command/action plans.
- Classify concurrent Odoo module operations, preserve process-step visibility in Rich dry-runs, and make short resource command names canonical while retaining long-name compatibility.
- Keep every resulting requirement and implementation task traceable to GitHub issues #34, #43, #54, #62, #64, or #65; do not edit or close those sources as part of this change.

## Capabilities

### New Capabilities

- `odoo-module-workflow`: Manifest-safe module discovery, dependency analysis, changed selection, updates, and the public module resource. [Sources: GH#34; GH#64 §6, §8]
- `translation-export`: Optional plan-captured GNU `msgfmt` validation and typed export results. [Source: GH#54]
- `odoo-git-workflow`: Odoo commit generation and validation, optional absorb execution, and safe feature-branch synchronization. [Source: GH#65]

### Modified Capabilities

- `command-execution`: Shared plans retain nested process visibility, optional executable preconditions, Git operations, stdin, and publication safety without alternate runners. [Sources: GH#54; GH#64 §2, §6; GH#65]
- `cli-odcli`: One format selector, field projection, structured remediations, focused environment output, canonical aliases, adaptive Rich presentation, typed errors, and new module/Git command surfaces. [Sources: GH#34; GH#43; GH#54; GH#62; GH#64 §4-§6, §8-§11; GH#65]
- `environment-monitor`: A single snapshot supplies focused environment details while stopped/unavailable runtime facts remain visible. [Source: GH#43]
- `instance-runtime-binding`: Doctor explains the configured and currently available effective runtime for environment and project owners without starting processes. [Source: GH#43]
- `development-environment`: Ticket Allocation becomes tracker-neutral and global environment paths migrate under the unified user root. [Source: GH#64 §7, §11]
- `backup-catalog`: Backups record canonical project ownership and catalogue records migrate safely with the unified user root. [Source: GH#64 §1, §7]
- `database-backup`: Project-originated downloads carry direct project ownership independent of source type or restore linkage. [Source: GH#64 §1]
- `database-restore`: Restore from a stopped project provides its own bounded Database Manager runtime or rejects the operation before mutation. [Source: GH#64 §2]
- `project-init`: Re-init independently validates and atomically repairs generated Compose config, and new Git commit settings are explicit. [Sources: GH#64 §3; GH#65]
- `local-resource-lifecycle`: All owned global resource paths participate in the single-root migration and conflict/rollback contract. [Source: GH#64 §7]
- `local-pgadmin`: pgAdmin user data moves under the unified OdCLI root without creating a parallel path provider. [Source: GH#64 §7]
- `packaging`: `git-absorb` and `msgfmt` remain optional system capabilities rather than Python dependencies, with architecture gates for the shared boundaries. [Sources: GH#54; GH#65]

## Impact

The change affects the public CLI and Python SDK, typed result models, project configuration, SQLite migrations, path providers, backup/restore lifecycle, monitor selection, Git and module resources, documentation, shell completion, and focused/unit/integration/packaging tests. It deliberately adds no renderer hierarchy, process runner, persistent module index, tracker client, forge API, binary installer, or runtime dependency. The six GitHub issues remain external requirement records and are not modified by this planning change.
