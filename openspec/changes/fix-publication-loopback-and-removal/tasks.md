# Tasks

## 1. Trusted loopback peers

- [x] 1.1 Accept reserved loopback peers while retaining unspecified/multicast/non-loopback-reserved rejection; verify the public settings address matrix.
- [x] 1.2 Clarify the existing instance-publication settings requirement with accepted loopback and rejected unsafe-peer scenarios; validate the change strictly.

## 2. Guarded environment route cleanup

- [x] 2.1 Preserve the publication action when capturing SDK-owned worktree steps; verify cleanup for existing/missing worktrees and no-settings operation.
- [x] 2.2 Reconcile publication only after existing preflight/evidence validation; verify dirty worktrees and occupied ports leave publication and artifacts untouched.
- [x] 2.3 Preserve owned artifacts and cleanup_failed on publication failure; verify both existing and absent worktrees and retain caller-owned coverage.
- [x] 2.4 Extend the existing publication lifecycle requirement with these state and failure scenarios; validate the change strictly.

## 3. Integration verification

- [x] 3.1 Run publication/removal, monitor-publication, checkout and alternate-root acceptance tests together; record their result and lint/type checks in delivery notes.
- [x] 3.2 Run disposable CLI/Caddy removal for present and missing worktrees, check persisted and live routes, and record the local-only evidence boundary.
