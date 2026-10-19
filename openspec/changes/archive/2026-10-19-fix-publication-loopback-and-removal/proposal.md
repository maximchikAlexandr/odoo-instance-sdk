# Proposal

## Why

Publication settings reject the valid IPv6 loopback peer `::1` because Python classifies it as reserved. Environment removal also loses its publication cleanup action when an SDK-owned worktree exists, leaving stale routes and bypassing cleanup failures.

## What Changes

- Accept explicit IPv4/IPv6 loopback trusted peers while retaining unsafe-address rejection.
- Retain route cleanup in the captured removal plan for existing and missing SDK-owned worktrees.
- Run removal ownership, port and dirty-worktree preflight before route cleanup; preserve owned artifacts when route cleanup fails.
- Add regression matrices for address validation, route cleanup, absent publication configuration and blocked removal.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `instance-publication`: explicitly cover loopback peers and publication reconciliation across environment worktree states and preflight failures.

## Impact

Two existing SDK implementation files and their unit tests; no new dependencies, CLI flags, storage schema or public API changes. Shared-root concurrency, alternate-tool installation/provenance and PostgreSQL ownership are outside this change.
