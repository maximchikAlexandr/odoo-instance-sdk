## 1. Bootstrap Lifecycle Event

- [ ] 1.1 Extend `database_events` metadata with `bootstrapped` and add the next single-head Alembic table-rebuild migration that preserves rows, sequences, indexes, foreign keys, restore/drop constraints, and fresh-schema equivalence without fabricating legacy evidence; make downgrade refuse when bootstrap events exist.
- [ ] 1.2 Add narrow catalog operations to validate and append exact `bootstrapped` evidence for active-cluster `tmp` and to read the latest exact lifecycle event by sequence; cover valid append, invalid payload/claim refusal, event ordering, migration preservation, and metadata equivalence.

## 2. Bootstrap Publication

- [ ] 2.1 Change the shared bootstrap executor to return a typed internal `created` versus `already_ready` outcome while preserving captured spawn/probe/readiness behavior and `init_bootstrap_failed` mapping.
- [ ] 2.2 Add one conditional catalog action to immutable init and first-run plans that appends `bootstrapped` only after newly created `tmp` passes readiness and the active claim is validated; cover plan visibility/consumption, success, no adoption, dry-run/external/failure inertness, and write-failure refusal.

## 3. Guarded Deletion and Reconciliation

- [ ] 3.1 Extend the existing drop ownership gate to dispatch on the latest exact event: preserve current restore-binding validation for `restored`, accept only exact active-cluster `bootstrapped` for `tmp`, and refuse `dropped`, missing, malformed, foreign, stale, active-bound, or changed evidence through the existing locked revalidation and safety checks.
- [ ] 3.2 Call the existing idempotent `record_database_dropped` in each authorized already-absent return, retain the successful-drop reconciliation and contained-filestore behavior, and cover bootstrap success, restore/drop revocation, identity/default/confirmation/session matrices, execution-time change, interrupted retry, typed output, redaction, and unchanged public SDK inventory.

## 4. Public-Boundary Evidence and Documentation

- [ ] 4.1 Extend the opt-in disposable Compose regression through the public CLI/SDK boundary: create normal bootstrap `tmp` with event evidence, stop Odoo, remove it successfully, then vary one identity component and prove no session termination, `DROP DATABASE`, volume deletion, unrelated filestore deletion, or evidence adoption occurs.
- [ ] 4.2 Document `odcli db rm tmp --force-default --yes`, conditional `--force-connections`, stopped/exact-owned prerequisites, legacy/mismatch refusal, latest-event revocation, and unchanged restore requirements; run focused catalog/bootstrap/drop/CLI tests, repository formatting/lint/type gates, strict OpenSpec validation, and the opt-in regression when its documented prerequisites are available.
