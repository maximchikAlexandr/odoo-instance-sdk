## 1. Cache-read integration over the existing helper

- [ ] 1.1 Reuse the owner portion of `verify_gitlab_identity()` and add bounded token self-information validation for cache-read purpose, requiring the same runtime provider ID, active state, and the exact standard scope set `read_repository` without changing credential records or persisting provider identity.
- [ ] 1.2 Keep the existing helper/resolver path, add an install-stable `git-credential-odcli` entrypoint only where missing, enforce exact host/member/purpose and finite `get` parsing, and document process-local daemon stop → validate → start/rollback configuration.
- [ ] 1.3 Add focused core and installed-artifact tests for owner/scope agreement, broader/granular/inactive/incompatible tokens, malformed or wrong-host requests, no fallback, no secret persistence, and wheel/sdist entrypoint presence.

## 2. Minimal bridge adoption

- [ ] 2.1 Extend the existing `RootCreatorContext` and execution-revalidation path with optional explicit current-human workspace member selection and safe source metadata; preserve automatic root-creator behavior and add no selector/result subsystem.
- [ ] 2.2 Thread optional `member_id` and `--member-id` through the existing raw Git, sync, and merge-request command paths while preserving their single credential snapshot, checkout/repository/branch/origin checks, transports, assignee semantics, and redaction.
- [ ] 2.3 Remove implicit project `.odcli` mapping/dotenv defaults from `_resolve_credentials()` and rely on the existing encrypted-first resolver, effective user root/`ODCLI_USER_ROOT`, process environment, explicit paths, and legacy conflict rules.
- [ ] 2.4 Add focused bridge tests for explicit success and invalid/non-human/cross-workspace/drift failures, unchanged automatic selection, user-root-only resolution without project copies, two-account isolation, and only the changed MR/output observations.

## 3. Delivery verification and documentation

- [ ] 3.1 Extend existing CI jobs only as needed for the focused core, bridge, and packaging tests; do not add a framework or duplicate unchanged MR/redaction/context matrices.
- [ ] 3.2 Extend `scripts/e2e_gitlab_member_workflow.py` with one integrated isolated path covering cache identity/scope, automatic and explicit members, user-root-only credentials, two-account isolation, failure non-fallback, daemon-generation rollback, and sanitized evidence/cleanup.
- [ ] 3.3 Update operator and SDK/CLI documentation for helper installation, exact cache token constraints, process-local stop → validate → start/rollback, optional `--member-id`, user-root precedence, and unchanged security boundaries.
