# Shared filestore research gate

Decision: **fail**.

Direction B from GitHub issue 4 asked for a backup-scoped shared filestore. This change does not ship that mode. `isolated` remains the only production filestore behavior, and a template clone never joins a backup-scoped pool.

## Experiments

| Experiment | Result |
| --- | --- |
| Two Odoo processes restored from one verified backup share one append-only filestore on Linux without missing or corrupted attachments | not proven |
| The same pair shares that filestore on macOS without missing or corrupted attachments | not proven |
| Only `_gc_file_store` is suppressed, and every other autovacuum or cleanup path still runs | not proven |
| Concurrent writers from both databases do not lose or corrupt attachment files | not proven |
| Pool membership survives process restart and records every database that can still read each file | not proven |
| Rollback of one database removes only that database's membership and leaves the other database readable | not proven |

## Why the gate fails

The issue allows a shared mode only when those invariants are proven without patching Odoo core, disabling all autovacuum, or trusting incomplete membership. This workspace has no automated Linux and macOS lab that can run two live Odoo servers against one backup and observe filestore garbage collection. Recording a best-effort shared mode without that proof would violate the issue.

## Consequence

No pool table, filestore status command, filestore garbage-collection command, daemon, or scheduler is added. `reject_shared_filestore_mode("shared")` fails before mutation. Checkout continues to copy a private filestore for template clones and keeps the existing isolated restore for backup copies.
