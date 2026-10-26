## Context

The four reported failures cross existing boundaries rather than requiring new product layers. `GitResource` already receives the selected project/environment instance, but `member_commit_environment()` reclassifies the worktree solely from a checkout-local manifest. The safe project dotenv parser and `ODCLI_USER_ROOT` already exist, while legacy GitLab fallback alone reads `credentials/.env`. The catalog already has locked Alembic migrations and the authenticated Admin already composes catalog and encrypted-secret views. Finally, `odcli-multica gitlab mr publish` already resolves an explicit username and writes `assignee_id` on both create and update.

Security constraints are fail-closed identity selection, owner-only secret files, no secret-bearing public projection, transactional compare-and-swap updates, and no fallback to ambient Git identity or a different GitLab user. The requester prefers to perform broad E2E verification personally; the delivery contract therefore limits live verification to the one browser-generated Admin form behavior that lower-level tests cannot completely demonstrate.

## Goals / Non-Goals

**Goals:**

- Prove a native checkout from the selected registered project/environment binding without requiring a second manifest, and freeze one verified member identity into the commit step.
- Make `<ODCLI_USER_ROOT>/.env` the sole recommended user dotenv while retaining a bounded, conflict-safe transition for the old GitLab token file.
- Store typed non-secret settings in the existing catalog and expose small authenticated Admin CRUD, including an exact host/project MR assignee default.
- Apply and verify that assignee through the existing MR publication flow, with explicit CLI input taking precedence.
- Make encrypted-secret identity changes atomic, secret-preserving, and stale-write safe while removing editable technical metadata from forms.

**Non-Goals:**

- Rewriting MR !705 history, changing GitLab reviewer semantics, adding multiple assignees, or modifying core `odcli git mr create` defaults.
- Moving encrypted credentials into the catalog, duplicating encrypted-store records into dotenv, or adding a new configuration database.
- Adding a custom Admin frontend, a second MR transport, automatic file migration, a new dotenv dependency, or real production-project MR mutation in verification.

## Decisions

### 1. Registered binding, not checkout-local configuration, proves project-aware commit mode

For project-aware `--member-id` commits, the selected instance SHALL provide a registered project or environment binding. The option value is the person's Multica account `user_id` returned by `multica workspace member list --output json`, not that roster row's membership `id`, an agent ID, or a squad ID. CLI help will state that distinction and the lookup command wherever `--member-id` is accepted. Planning will capture the bound project ID, environment ID when present, canonical project root, canonical worktree root, Git common directory, and normalized origin/host. The catalog record and current repository snapshot will be compared to that binding; the checkout may omit `.odcli/project.toml`. The same evidence will be re-read immediately before the commit step along with the existing staged-tree checks. Only after this proof will the existing exact member/host credential and GitLab identity verification populate both `GIT_AUTHOR_*` and `GIT_COMMITTER_*`.

Alternative: copy or synthesize `.odcli/project.toml` in the task checkout. Rejected because the catalog binding is already authoritative and a copied manifest creates drift and secret/configuration ownership problems. Alternative: treat the checkout as generic. Rejected because it discards Odoo message policy and disallows `--tag`.

### 2. Reuse one strict dotenv parser and preserve purpose-limited delivery

The existing deterministic parser will be factored into a location-neutral owner-only regular-file reader. Thin project and user loaders retain path-specific sanitized errors and immutable mappings. Effective secret lookup uses the first nonblank value in this order: explicit process environment, selected project `.odcli/.env`, then `<ODCLI_USER_ROOT>/.env`. User values are consumed only by named resolvers (`ODCLI_ADMIN_PASSWORD` and mapped legacy GitLab token keys); they are not merged into general child environments. Existing project ordinary-variable delivery to Odoo children stays unchanged.

For GitLab fallback, the encrypted store still wins. If fallback reaches the selected token key, the new user file is authoritative. The old `<ODCLI_USER_ROOT>/credentials/.env` is read only for compatibility: an old-only value is accepted with a non-secret deprecation diagnostic; equal values select the new file with the same diagnostic; differing nonblank values fail before transport. Higher process/project values short-circuit lower-file reads. Explicit `environment_path` test/API overrides retain their current authority and do not invoke the default compatibility pair.

Alternative: continue two independent parsers and files. Rejected because their grammars and permission behavior can drift. Alternative: automatically move or delete the old file. Rejected because a library command must not rewrite user secrets implicitly.

### 3. Add generic canonical-JSON settings to the existing catalog

Alembic revision `0015` will add `settings(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, revision INTEGER, created_at TEXT, updated_at TEXT)` with positive revision and JSON-validity constraints. Catalog methods will parse JSON, reject non-finite numbers and duplicate/object ambiguity, and serialize compact UTF-8 canonical JSON with sorted object keys. Admin will expose key/value CRUD under the existing administrator authentication and use revision-bound opaque route identity for compare-and-swap edits/deletes. Keys matching the existing secret-key classifier are rejected; the view and docs state that values are public-to-admin configuration, not secret storage.

The MR key is exactly `gitlab.mr.default_assignee:https://<normalized-host>/<normalized-project-path>`. Its JSON value is either a positive integer GitLab user ID or a nonblank exact username string; `null`, arrays, objects, booleans, display names with whitespace, and non-positive numbers are invalid for that key. Deleting the row disables the default. Other rows may hold any canonical JSON scalar, array, or object.

Alternative: add assignee columns to projects or secret records. Rejected because the setting is neither project-registration state nor a credential, and a typed key/value table is the requested reusable shape. Alternative: a new database or TOML file. Rejected because it duplicates migration, locking, permissions, and Admin access already provided by the catalog.

### 4. Resolve one assignee before mutation and verify the provider response

`publish_merge_request_command()` will derive the exact settings key from the already verified repository host/project. A nonblank explicit `--assignee` wins; otherwise the catalog value is used; absence preserves current behavior. A username is resolved by exact `GET /users?username=...` response matching, while a numeric value uses exact user lookup. The selected user is then checked through the project membership endpoint before MR mutation. Invalid, ambiguous, inactive, inaccessible, or non-member values fail without fallback. The existing payload continues to use `assignee_id`, never `reviewer_ids`, for both POST and PUT.

The response must contain the selected ID and username in `assignee`/`assignees`; mismatch is a typed provider-consistency failure and safe output contains only ID/username. GitLab remains authoritative for the publishing token's assignment permission: authorization/validation rejection is surfaced and never retried or redirected to another user. Dry-run reports the setting key, override/default source, and unresolved intent without making catalog-external or GitLab requests.

Alternative: implement a second MR creation path from Admin settings. Rejected because the extension already owns exact create/update idempotency. Alternative: use `reviewer_ids`. Rejected because the requested workflow and GitLab API distinguish assignment from review.

### 5. Use the route identity as the hidden concurrency token for secret edits

Secret edit forms will make `base_url`, `member_id`, `host`, and `login` editable, keep secret controls blank/write-only, and make `revision`, `admin_id`, and timestamps absent from editable form input. The revision already embedded in the opaque route identity becomes the submitted compare-and-swap token; no visible or arbitrary revision field is accepted.

Each update starts `BEGIN IMMEDIATE`, reads the old row and expected revision, normalizes all proposed business keys, checks uniqueness, and performs one primary-key-changing `UPDATE`. Blank password/token retains the stored secret, the internal GitLab token key and `created_at` remain unchanged, and `revision`/`updated_at` advance once. Constraint, validation, or stale failures roll back the transaction. The returned projection carries the new route identity so Starlette Admin redirects to the new detail/edit URL; the old route no longer resolves.

Alternative: delete then insert. Rejected because a crash or uniqueness failure could lose the record or leave two identities. Alternative: expose revision as an editable form field. Rejected because the URL already carries a bounded signed-by-state concurrency identity and technical metadata is not business input.

### 6. Ponytail full gate

- **Catalog settings subsystem:** necessary because persistent non-secret Admin configuration does not exist; existing alternative is the catalog migration/connection/Admin composition; decision is one table, one catalog mixin, and one generated Admin view, with no new database or service.
- **Unified user dotenv loader:** necessary because user secrets currently have two paths and parsers; existing alternative is the strict project parser plus `get_user_root()`; decision is to extract/reuse parsing and add only a narrow user loader/resolver, not a general environment framework.
- **Native binding verifier:** necessary because manifest-only classification rejects registered task checkouts; existing alternative is the runtime/catalog binding already attached to the instance; decision is a focused binding proof in the existing member-commit path, not a new checkout registry.
- **MR assignee resolver:** necessary for the stored default; existing alternative is the current explicit-assignee lookup and transport; decision is to extend it with catalog selection, numeric lookup, membership preflight, and response verification, not create another publisher.
- **Secret identity update path:** necessary because current update methods cannot change keys; existing alternative is the encrypted store's transaction/CAS code; decision is to broaden those methods atomically, not add migration tables or replace SQLCipher.

## Risks / Trade-offs

- **Catalog binding or repository evidence drifts between planning and execution** → re-read both and fail before the immutable commit step.
- **Legacy and new dotenv files disagree** → compare only the selected fallback key, emit no values, and fail rather than choose silently.
- **Generic settings become an accidental secret sink** → reject secret-like keys, document/publicly project values as non-secret, and keep real credentials in SQLCipher/dotenv.
- **GitLab username lookup is case-insensitive** → require exactly one response and exact normalized username equality; never accept display-name search.
- **GitLab accepts mutation but returns an unexpected assignee projection** → return a typed consistency failure with the MR URL/ID and no automatic compensating mutation.
- **Primary-key update collides with another secret record** → perform normalized uniqueness checks and the update in one transaction, preserving both original rows on failure.
- **The browser view can diverge from Python view metadata** → retain dashboard component tests and add one isolated live Admin walkthrough with before/after images.

## Migration Plan

1. Ship catalog metadata, Alembic `0015`, schema-fingerprint updates, and migration fixtures/tests before consumers. Upgrade is additive and preserves every existing row.
2. Ship the settings model/Admin view, then the MR consumer; absence of the key keeps existing behavior.
3. Ship the unified user loader and documentation. Keep old GitLab dotenv reads under the explicit compatibility rule; do not move or delete user files.
4. Ship atomic secret identity updates and Admin field changes together so form and store contracts cannot diverge.
5. Rollback may downgrade `0015` only after exporting/removing settings; downgrade drops only the new table. Older binaries continue to use the old GitLab dotenv path and ignore the new user file, so operational rollback requires retaining the old file until the compatibility period ends.

## Open Questions

None. The key format, JSON value types, precedence, compatibility behavior, assignee semantics, concurrency token, and verification boundary are fixed by this design.
