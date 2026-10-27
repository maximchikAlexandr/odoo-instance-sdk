## 1. Catalog settings foundation

- [x] 1.1 Add reversible catalog migration `0015` and SQLAlchemy metadata for the revisioned `settings` table; update the catalog revision, schema fingerprints, historical-equivalence handling, and migration fixtures/tests without changing existing rows.
- [x] 1.2 Add typed catalog setting projections and create/read/list/update/delete APIs with bounded key/value input, canonical JSON serialization, secret-like key rejection, and transactional revision checks.
- [x] 1.3 Add the authenticated Starlette Admin settings view with field-local JSON/MR-assignee validation, revision-bound routes, stale edit/delete behavior, and dashboard component/HTTP tests.

## 2. Unified user dotenv

- [x] 2.1 Extract the existing strict dotenv grammar into a shared owner-only regular-file reader, retain project semantics, and add immutable `<ODCLI_USER_ROOT>/.env` loading with symlink/ownership/permission/error tests.
- [x] 2.2 Implement the process > selected-project > user resolver for `ODCLI_ADMIN_PASSWORD`, use it from reset/init/restore/refresh/replacement entry points, preserve interactive behavior and child redaction boundaries, and add focused precedence/flow tests.
- [x] 2.3 Move legacy GitLab token fallback to the unified user dotenv while preserving encrypted-store priority and explicit path overrides; add old-only deprecation, equal-value, differing-value conflict, `ODCLI_USER_ROOT`, permission, and no-leak tests.

## 3. Native checkout commit identity

- [x] 3.1 Extend project-aware member commit planning to accept the selected registered project/environment binding, capture catalog/worktree/Git-common-dir/origin evidence, and revalidate it before commit without requiring a checkout-local manifest.
- [x] 3.2 Add SDK and CLI regressions using temporary Git repositories for native manifest-free dry-run/execution, policy tag/ticket preservation, author and committer equality, foreign/stale binding and host/member rejection, unchanged `HEAD` on failure, safe preview, and no-member backward compatibility.

## 4. Configured MR assignment

- [x] 4.1 Derive and read the exact host/project catalog setting in `odcli-multica gitlab mr publish`, preserve absent behavior, and implement nonblank explicit `--assignee` override plus inspectable source/key intent in dry-run.
- [x] 4.2 Extend the existing GitLab publisher to resolve numeric ID or exact username, verify active project membership, send only `assignee_id` on create/update, validate provider authorization/errors, and verify the returned assignee in the frozen safe result.
- [x] 4.3 Add provider/component/CLI tests for absent, integer, username, explicit override, cross-host/project isolation, ambiguity/inactivity/non-membership/permission failures, create/update payloads, reviewer separation, response mismatch, and secret-free Rich/JSON/TOON output.

## 5. Atomic Admin secret editing

- [x] 5.1 Extend encrypted-store update methods to accept old and proposed normalized identities, perform one transactional primary-key update with uniqueness/revision checks, retain blank secrets and stable metadata/token keys, and roll back every validation/collision/stale failure.
- [x] 5.2 Make every business field editable in both existing Admin views, remove revision and technical fields from editable input, use the revision-bound route identity for compare-and-swap, map validation errors to fields, and return the new route after identity changes.
- [x] 5.3 Add SQLCipher and authenticated Admin HTTP/component regressions for each field and combined edits, blank/replaced secrets, key collisions, stale tabs/routes, timestamps/revisions, redirects, atomic rollback, authorization, and response/log/OpenAPI redaction.

## 6. Documentation and verification

- [x] 6.1 Update CLI help, README, package documentation, and examples for the single user dotenv, precedence and compatibility diagnostics, manifest-free native identity, catalog assignee key/value, explicit override, assignee-versus-reviewer semantics, and editable Admin fields using only public placeholders; every `--member-id` help surface SHALL name `multica workspace member list --output json`, require the person's `user_id`, and distinguish it from the membership-row `id`, agent ID, and squad ID.
- [x] 6.2 Run focused offline/dashboard tests for the changed Git, dotenv, catalog migration/settings, MR, and Admin contracts; run repository lint/type/OpenSpec checks and ensure the existing CI jobs cover every added test/fixture without private infrastructure.
