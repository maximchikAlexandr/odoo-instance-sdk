# Design

## Context

See proposal.md for motivation. Publication validation and environment removal already have typed public operations and captured process/action plans. The existing instance-publication and development-environment specs require retryable cleanup and guarded removal.

## Goals / Non-Goals

**Goals:** repair these existing operations without new APIs or storage, preserving preflight and failure semantics.

**Non-Goals:** alter upstream bind-address validation, introduce broad trusted subnets, change shared-state locks, manage alternate installations or provision PostgreSQL.

## Decisions

- Exempt loopback peers only from the reserved-address check. Removing the reserved check entirely would weaken validation for unrelated addresses.
- Append worktree steps to the captured publication action instead of replacing the plan. Keep all existing steps and executor boundaries.
- Reconcile publication inside the guarded removal path, after preflight and retained-evidence validation but before artifact deletion. Keeping reconciliation before preflight would remove routes even when a dirty checkout or occupied endpoint blocks deletion. Rechecking preflight twice would consume the same captured process step twice.
- Keep existing no-settings behavior and idempotent removal. Existing publication failure handling records cleanup_failed and preserves the remaining owned artifacts.

## Risks / Trade-offs

- Reserved-address classification varies across Python versions → test both explicit loopback families and representative rejected categories.
- Route cleanup is an external mutation → test success, failure and preflight refusal with present/absent worktrees, and use disposable Caddy for a local CLI smoke.
- Local Caddy acceptance does not prove production HTTPS or Odoo behavior → report that evidence boundary explicitly.

## Migration Plan

No schema/config migration. Deploy the normal OdCLI package after review; existing explicit ::1 peers become valid. Rollback restores previous package behavior. This change does not make isolated roots safe for concurrent shared-resource mutations.
