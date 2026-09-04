## Context

Project database preparation currently applies two independent trust checks to a repository-selected remote source: an exact operator-owned origin pin and a transport guard that unconditionally rejects non-loopback HTTP. Database HTTP operations already emit a once-per-process warning when a master password is sent over cleartext HTTP. See the proposal and delta spec for the requested behavior.

## Goals / Non-Goals

**Goals:**

- Make an exact external origin pin sufficient operator approval for HTTP and HTTPS test instances.
- Preserve a visible warning for cleartext password transport.
- Preserve URL canonicalization, exact port matching, redaction, and pre-mutation ordering.

**Non-Goals:**

- Silently accepting an unpinned remote origin.
- Disabling cleartext warnings or claiming HTTP is secure.
- Relaxing local-only restore/drop protections.
- Adding TLS configuration or certificate management.

## Decisions

### Remove the blocking transport guard from repository trust validation

`require_test_instance_origin_approval()` will canonicalize the origin and enforce the existing pin for every non-loopback source without first rejecting HTTP. The standalone password-transport assertion becomes unused and is removed rather than retained as dead policy.

Alternative rejected: introduce a second `ALLOW_INSECURE_HTTP` variable. The exact origin pin already provides explicit, narrowly scoped operator approval; a second switch adds configuration without identifying the destination any more precisely.

### Retain warning at the password-bearing HTTP boundary

`DatabaseResource._http()` already calls the cleartext-secret warning helper before database-manager requests. Keeping the warning there ensures direct and project-orchestrated operations share the same behavior and avoids duplicating warning policy in preflight.

### Keep exact-origin approval before mutation

Only the transport rejection changes. Missing pins, mismatched schemes or ports, malformed URLs, missing secrets, and all subsequent local preflight failures retain their current ordering and no-mutation guarantees.

## Risks / Trade-offs

- [Master passwords sent to approved HTTP origins are observable on the network] → Emit the existing cleartext warning and require an exact external origin pin.
- [Users may mistake approval for encryption] → Warning text continues to state that credentials are transmitted in cleartext and recommends HTTPS.
- [A repository could attempt to redirect trust] → Approval remains external to the repository and matches canonical scheme, host, and effective port exactly.

## Migration Plan

Remove the rejection helper and update trust tests, documentation, and the modified main requirement through the delta spec. Rollback restores the helper call and previous tests; no persisted data changes are required.
