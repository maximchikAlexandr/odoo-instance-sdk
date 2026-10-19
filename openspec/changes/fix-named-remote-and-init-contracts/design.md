## Context

`RemoteSourceConfig.database` is currently mandatory at the model, TOML loader/serializer, `remote add`, and init boundaries, although `resolve_remote_database_name()` already implements explicit-name priority and single-name discovery for refresh. The current base also has separate, reusable primitives for bounded port allocation, owner-only generated config, PostgreSQL image digest approval, immutable commands, captured process steps, and default publication. The defect is therefore a cross-cutting integration gap rather than a missing subsystem.

The approved implementation base is `origin/main` at `7ff2b01c129d0e20eb2521f2b2d860796b0502ef`. The open implementation PR #155 at `da3c64f3fa6cb092c828da740b0301dd24359686` is read-only evidence: it confirms the touched seams and focused regressions, but it is not the planning base or an acceptance result.

Repository constraints require SDK-first CLI adapters, one immutable preview/execution snapshot, `internal/proc` for subprocesses, typed JSON-safe plans, bounded output, no secret projection, and no parallel runner or source-discovery implementation.

### Ponytail full gate

No new subsystem is necessary. For each apparent concern, the existing alternative is sufficient and is retained:

- Remote discovery: reuse `resolve_remote_database_name`; do not add a registry, provider, or second HTTP path.
- Init registration: extend the existing typed init request and snapshot; do not invoke nested `remote add` mutations.
- Secret creation: extend the existing generated-config write action; do not add a secret store or CLI option.
- HTTP selection: correct `find_free_port`; do not add a reservation daemon or second allocator.
- Restore Git safety: capture the existing `git_worktree` checks as prepared read-only steps; do not execute Git from an action callback.
- Image consent: compose the existing digest resolution and approval store into init; do not add a trust database or flag.
- Verification: extend existing pytest suites and public CLI characterization; do not add a runner or E2E framework.

## Goals / Non-Goals

**Goals:**

- Allow a named remote to persist without a database while preserving strict validation for explicit values.
- Make refresh the only place that performs remote database discovery and preserve failure-before-backup guarantees.
- Make init/re-init, generated config, HTTP allocation, restore publication, process inventory, and image consent consistent with that model.
- Preserve immutable planning, dry-run honesty, secret redaction, compatibility, and existing ownership boundaries.

**Non-Goals:**

- Removing or migrating legacy `[test_instance]` configuration.
- Adding server discovery during remote registration or init dry-run.
- Persisting an auto-selected remote database back into the named-source manifest.
- Changing PostgreSQL automatic port allocation, backup formats, authentication storage, or canonical installation.
- Adding catalog migrations, dependencies, public facades, or a new execution abstraction.

## Decisions

### 1. Optionality belongs in the named-source value model

`RemoteSourceConfig.database` becomes `str | None`. The loader accepts absence only, rejects non-string and blank/whitespace explicit values, and the serializer omits the TOML key for `None`. CLI `remote add` makes `--database` optional; update keeps the existing value—including `None`—when only another field changes.

Alternative: encode absence as an empty string. Rejected because it weakens the trust boundary and cannot distinguish omission from invalid input.

### 2. Resolution remains a refresh concern

Registration and init remain offline. During refresh/materialization, the current resolver returns an explicit configured database without listing or otherwise lists once and accepts exactly one name. Zero, multiple, and unavailable results retain distinct typed errors and occur before backup. Named-source password lookup, source name, branch provenance, and backup identity continue through the existing preparation pipeline.

COPY checkout cannot safely discover and retain a database inside its current source-selection phase. If a named source still has `database=None`, planning returns `ConfigError` directing the caller to refresh and select the retained backup UUID or configure a database.

Alternative: make COPY perform its own list call. Rejected because it duplicates discovery and changes the immutable checkout contract.

### 3. Init parses both remote arities into one typed request

The Click boundary accepts repeatable `NAME URL GIT_REF` and legacy `NAME URL DATABASE GIT_REF`, including `--remote=NAME`. Parsing must stop only at recognized option boundaries so values such as a literal `--remote` passed to `--run-arg` retain their meaning. All entries are normalized before constructing one init snapshot. Re-init merges supplied names over existing sources; no remote flags preserve all sources.

Alternative: call `configure_remote_source_command()` after init. Rejected because it creates a second mutation, drift window, and dry-run projection.

### 4. Generated local master password is materialized only in execution

The generated-config writer chooses the first nonblank `admin_passwd` from the imported source config, then the existing generated config, otherwise `secrets.token_urlsafe(32)`. Generation happens inside the captured write action, never during planning/dry-run. The destination stays owner-only and imported source files remain unchanged. Repair detection treats missing/blank local values as stale.

Alternative: accept a CLI value or store it in the manifest. Rejected because both create durable secret projection surfaces.

### 5. Automatic HTTP allocation is one bounded candidate stream

Automatic candidates are 8000 through 9000 inclusive. A valid in-range preferred port is tested first; absent preference uses 8069 first; out-of-range preference is ignored. The ascending range is then scanned once without repeating the first candidate, checking catalog/manifests/configs and live bind availability. Explicit `--http-port` tests only that exact value, including out-of-range values.

Alternative: continue incrementing from the preferred value. Rejected because it skips lower candidates and breaks on out-of-range preferences.

### 6. Restore consumes captured Git observations

When main-checkout Compose restore may update an existing generated config in a Git checkout, planning adds read-only prepared steps for repository root and tracked-path status. Execution consumes those exact steps in `validate_generated_config_target`; a tracked file, Git failure, or repository-root drift blocks manifest/config publication. Existing compensation and retained-artifact behavior remains authoritative.

Alternative: execute Git lazily from default-switch action code. Rejected because it creates an unplanned subprocess after database/filestore mutation.

### 7. Init composes the current PostgreSQL trust primitive

For Compose init execution, digest resolution and one approval action precede project, venv, password, cluster, and bootstrap writes. A matching stored approval skips the action. `--yes` confirms the exact selected digest; interactive Rich asks with reference and digest; noninteractive mode without `--yes` fails closed. Refusal/resolution failure changes neither project files nor approval. Dry-run and external PostgreSQL perform no resolution or approval.

Alternative: make `--yes` bypass the trust store or approve a mutable tag. Rejected because startup already enforces exact digest consent.

### 8. Pinned hotfix compatibility remains an isolated verifier change

The existing developer fix tool may recognize uv's exact `git+https://...git@SHA` direct URL only when repository identity and `vcs_info.commit_id` match the trusted pin. Its isolated root and independent compatibility checks remain unchanged.

Alternative: loosen generic source validation. Rejected because it would authorize foreign or mismatched installs.

## Risks / Trade-offs

- [CLI variable-arity parsing can consume neighboring option values] → Cover mixed/repeated forms, equals syntax, and literal `--remote` run arguments through public CliRunner tests.
- [Optional model field may break unrelated readers such as `ps`] → Load both absent and explicit values through process-inventory CLI tests and assert an unchanged manifest.
- [Generated secret could leak through a plan or failure] → Generate only in the write callback and assert Rich/JSON/TOON, repr, errors, logs, argv, and plan fingerprints omit it.
- [Large HTTP candidate range increases probes] → Keep deterministic early candidates and stop at the first free port; exhaustive failure is bounded.
- [Restore may have already created retained artifacts before publication rejection] → Preserve existing typed retained-artifact result and previous default; never claim a successful switch.
- [Image approval adds an attended branch] → Reuse `--yes` and stored approval while keeping completeness validation and other checks independent.
- [Open PR evidence may diverge from final implementation] → OpenSpec and tests remain authoritative; implementers must satisfy the base revision and strict validation, not copy a branch blindly.

## Migration Plan

No persisted-data migration is required. Existing named sources with explicit databases load and serialize unchanged. New omitted values are represented by an absent TOML key. Re-init repairs legacy generated configs with missing/blank `admin_passwd` in place while preserving source config and existing valid generated values. Rollback restores the prior code; manifests with omitted named-source databases then require an explicit database before use, so operators should add the key before downgrading.

## Open Questions

None. The issue defines externally observable behavior and failure messages completely enough for implementation.
