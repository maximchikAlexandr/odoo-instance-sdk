## 1. Catalog Bootstrap-Origin Contract

- [ ] 1.1 Add the `bootstrap_databases` current-origin relation to catalog metadata with exact project, active cluster, Compose project, volume, normalized endpoint, fixed `tmp` database, contained data-directory, and creation-time fields; add the next single-head Alembic migration and metadata-equivalence coverage without synthesizing legacy rows.
- [ ] 1.2 Add validated catalog operations to publish, read, conditionally retire, and atomically finalize deletion of an exact bootstrap binding; require an active matching cluster claim and make completed restore transactions supersede the same cluster/database bootstrap binding.
- [ ] 1.3 Cover fresh catalogs, prior-head upgrade/downgrade policy, malformed/pending/foreign claim refusal, idempotent exact publication, identity-bound retirement, restore supersession, and dropped-event plus origin-retirement atomicity in catalog tests.

## 2. Bootstrap Publication

- [ ] 2.1 Change the shared bootstrap executor to return a typed internal `created` versus `already_ready` outcome while preserving its captured spawn/probe/readiness behavior and existing `init_bootstrap_failed` mapping.
- [ ] 2.2 Add one conditional catalog mutation action to the immutable init and first-run plans; publish exact bootstrap origin only after a newly created `tmp` passes readiness and a current active project-cluster claim is revalidated, and skip publication for preview, external PostgreSQL, already-ready, failed, or mismatched cases.
- [ ] 2.3 Extend bootstrap/init/foreground tests to prove action-plan visibility and consumption, successful exact publication, no adoption of an existing `tmp`, no write on dry-run or failure, and fail-closed behavior when publication fails after creation.

## 3. Guarded Database Deletion

- [ ] 3.1 Refactor the internal drop ownership projection into explicit restore and bootstrap variants; for the bootstrap variant require the fixed `tmp` name and equality across canonical project, active claim, Compose project, expected and inspected volume/container labels, endpoint, and contained project data directory while leaving restore validation unchanged.
- [ ] 3.2 Reuse the existing cluster lock and planning-versus-execution equality check for the bootstrap variant, preserving denylist/template/default override/confirmation/environment/runtime/session/forced-connection rules and refusing every missing, legacy, foreign, malformed, changed, or unreadable identity before mutation.
- [ ] 3.3 After verified absence, call the identity-bound atomic catalog finalizer for bootstrap evidence, including the already-absent retry path; retain existing restore reconciliation and contained-filestore semantics and prevent a retired origin from authorizing a replacement database.
- [ ] 3.4 Extend unit ownership matrices and CLI boundary tests for matching bootstrap success, unchanged restore behavior, legacy/no-origin refusal, every identity mismatch, active bindings/sessions, configured-default and confirmation gates, pre-mutation change, post-drop finalization retry, typed output, redaction, and unchanged public SDK method inventory.

## 4. End-to-End Evidence and Documentation

- [ ] 4.1 Extend the opt-in disposable Compose regression through the public CLI/SDK boundary: create normal bootstrap `tmp` with recorded claim/origin evidence, stop Odoo, remove it successfully, then vary one ownership component and prove no session termination, `DROP DATABASE`, volume deletion, unrelated filestore deletion, or catalog adoption occurs.
- [ ] 4.2 Document the supported `odcli db rm tmp --force-default --yes` lifecycle, conditional `--force-connections`, required stopped/exact-owned state, legacy and mismatch refusals, and the fact that ordinary databases still require completed restore provenance.
- [ ] 4.3 Run focused catalog/bootstrap/drop/CLI tests, the repository formatting/lint/type gates for touched code, strict OpenSpec validation, and the disposable Compose test when its documented Docker/PostgreSQL prerequisites are available; record any unavailable opt-in prerequisite without weakening the required test.
