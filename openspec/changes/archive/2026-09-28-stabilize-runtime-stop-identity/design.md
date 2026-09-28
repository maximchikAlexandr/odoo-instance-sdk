## Context

The current runtime table persists owner, PID/create time, revision, endpoint, and database, but no immutable process identity. `OdooInstance._read_runtime_identity()` therefore calls `_project_runtime_expectations()` for project owners and rebuilds argv from the current `StartConfig`, executable prefix, effective logfile, and default run args. Normal checkout evolution can change those inputs without changing the already-running child, causing false fail-closed rejection. `_validate_runtime_identity()` then collapses every argv difference to `argv`.

Foreground and detached commands already share immutable `PreparedStep` capture, the process boundary already exposes one redacted argv projection that preserves argument boundaries, and both launch modes already converge on `_persist_runtime_identity()`. The catalogue is Alembic-managed at revision `0002`, so retaining evidence after the launching CLI exits requires an explicit schema revision rather than another in-memory registry.

## Goals / Non-Goals

**Goals:**

- Bind stop authorization to immutable launch-time evidence for both project and environment owners.
- Keep all existing fail-closed PID/create-time/executable/cwd/config/process-group checks and conditional cleanup.
- Diagnose the safe component that differs without persisting or returning secret values.
- Reuse the captured process step, owner-neutral runtime row, common redaction projection, and existing termination boundary.

**Non-Goals:**

- No supervisor, daemon, second process runner, second registry, port-based ownership inference, or automatic mismatch override.
- No public success-envelope change and no attempt to reconstruct a trustworthy snapshot for already-running legacy rows.
- No comparison or digest of raw secret values; the existing rule that secrets never enter plan fingerprints remains authoritative.
- No general redesign of environment runtime metadata, process inventory, or command planning.

## Decisions

### D1. Persist one versioned launch-identity JSON document

Add nullable `runtime.launch_identity_json` through Alembic revision `0004` (after the existing catalogue `0003` revision) and mirror it in `catalog_schema.py`. New `_upsert_runtime()` calls require a document with:

- `schema_version = 1`;
- canonical executable;
- exact common redacted argv projection with boundaries intact;
- captured sensitive argv indices and executable-prefix length;
- canonical cwd and config path.

The value is encoded deterministically as JSON and validated on read. A single JSON column keeps one atomic evidence object and avoids speculative columns for each future option while remaining JSON-safe and inspectable. Alternative: add separate executable/argv/cwd/config columns. Rejected because their consistency would be spread across multiple optional fields and every schema evolution would require another column set. Alternative: store only a plan fingerprint. Rejected because a hash cannot produce field-level diagnostics and repository rules forbid putting secrets in a digest.

### D2. Capture from the exact prepared launch step

Pass the foreground/detached `PreparedStep` (or a small identity value derived from it at command construction) into `_persist_runtime_identity()`. Use `project_process_step()`/`redacted_argv()` with the step's already-captured sensitive indices; do not call `_build_cli_args()` again. Persist only after spawn succeeds and before foreground wait or detached return, preserving the existing artifact-lock and runtime-row ordering.

Alternative: snapshot `self.config` inside `_persist_runtime_identity()`. Rejected because it repeats the defect: it is a second construction path that can diverge from the immutable command executed.

### D3. Compare live evidence through the same secret-free projection

At stop, decode the persisted document and project live `psutil.Process.cmdline()` using the persisted sensitive positions and the common redaction helper. Compare canonical executable and cwd/config directly, then compare the captured executable-prefix slice and normalized protected option map from the safe argv projections. Preserve the current protected-option scope rather than turning every optional Odoo argument into a new ownership contract.

Sensitive positions compare structurally: the option and argument boundary must remain in place, but the raw secret value is deliberately not persisted, hashed, or echoed. Non-secret protected values remain exact. This is the necessary trade-off between secret non-retention and diagnosable ownership checks; PID/create time, executable, cwd, config, and process group continue to provide independent evidence.

Alternative: persist raw argv. Rejected because protected options can carry passwords. Alternative: reconstruct expected argv then redact both sides. Rejected because reconstruction remains mutable and cannot prove what was launched.

### D4. Return stable component labels, never values

Replace boolean-only argv matching with a helper that returns a bounded tuple of safe labels. Prefix differences produce `argv: executable-prefix`; protected option differences produce `argv: <option>` in deterministic option order. Top-level checks retain `create_time`, `executable`, `cwd`, `config`, and `process_group`. Unknown or malformed documents produce `captured launch identity unreadable`; missing legacy evidence produces `captured launch identity unavailable`.

The CLI continues using the shared exception/output sanitizer. Tests inject credentials and control characters to prove that the catalogue, exception, Rich, JSON, and TOON projections contain only labels/redaction markers.

### D5. Legacy live rows remain fail-closed

Migration `0004` adds the nullable column without inventing evidence for current rows. A missing snapshot cannot authorize a signal, so stop retains the row and returns the unavailable-identity diagnostic. Once a runtime exits, existing stale-row behavior can clear it only when absence is established without PID reuse; the next OdCLI launch writes complete evidence.

Alternative: fall back to current reconstruction for legacy rows. Rejected because that would preserve the reported false rejection and could treat mutable present-day configuration as historical launch authority.

### D6. Extend existing test matrices and inventories

Add cases to existing parameterized runtime-stop tests for configuration drift, prefix/protected-binding mismatch labels, malformed/missing snapshots, owner/snapshot TOCTOU, and secret redaction. Extend current detached/foreground persistence and catalogue migration tests, plus the existing `PUBLIC_LEAF_CASES` stop coverage. Do not add a parallel process fake, stop implementation, or CLI inventory.

## Risks / Trade-offs

- **[Persisted safe argv becomes stale if projection rules change]** → Version the document; decode v1 with its explicit structure and require a deliberate new schema version for incompatible projection semantics.
- **[Sensitive values cannot be compared exactly without retaining secret-derived evidence]** → Compare their option presence and boundary only; retain all independent PID/create-time/executable/cwd/config/process-group checks and exact comparison for every non-secret protected binding.
- **[Corrupt catalogue JSON could weaken checks]** → Strictly validate type, version, paths, indices, prefix bounds, and argv structure; any failure stops before a signal.
- **[Existing live rows become manually recoverable only]** → Report the precise unavailable-snapshot reason, retain evidence, and populate complete identity on the next launch; never silently weaken authorization.
- **[Migration rollback loses snapshots]** → Downgrade may drop only the new nullable column after stopping managed runtimes; post-downgrade stop reverts to the older code contract and must not be performed while new-code runtimes remain active.

## Migration Plan

1. Add Alembic revision `0004` after the existing catalogue `0003` revision and update schema/revision equivalence checks.
2. Deploy code that writes v1 launch identity on every new owner-neutral runtime upsert and reads it for stop.
3. Existing rows remain nullable and fail closed if their process is still live; operators use the already-documented verified manual recovery only when necessary.
4. A subsequent normal launch replaces the row with complete captured evidence.
5. Roll back only after managed runtimes are stopped; downgrade removes the nullable column and restores catalogue revision `0002`.

## Open Questions

None. The security trade-off for secret-bearing positions is fixed by the repository rule that secrets are neither persisted nor digested.
