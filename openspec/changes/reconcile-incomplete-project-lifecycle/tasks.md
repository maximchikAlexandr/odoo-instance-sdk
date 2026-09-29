## 1. Resume incomplete Compose initialization

- [ ] 1.1 Replace the identical-manifest boolean shortcut with a typed decision that preserves non-Compose no-op/config-repair behavior and routes identical Compose projects into captured completion verification.
- [ ] 1.2 Adapt `init_project_command` to skip the already-satisfied manifest mutation after lock-bound equality revalidation while reusing the existing ensure-running and SQL bootstrap steps.
- [ ] 1.3 Add CLI/SDK regressions for fail-after-manifest then successful retry, complete retry without `tmp` recreation, repeated failure, generated-config repair, and truthful Rich/JSON/TOON `dry_run` metadata.

## 2. Persist and reconcile incomplete restore evidence

- [ ] 2.1 Add one linear catalogue migration and matching declarative schema/model support for `complete` versus `incomplete` restore bindings, including historical-row, fresh-schema, downgrade, and metadata-equivalence tests.
- [ ] 2.2 On restore failure, consume the captured `exists-after` probe and record an incomplete binding only for the exact selected target, source provenance, and active owned-cluster claim; preserve the primary error when proof or recording fails.
- [ ] 2.3 Expose incomplete state through the existing failure/inventory projections without allowing it to satisfy default switching, successful restore coalescing, or readiness.
- [ ] 2.4 Extend guarded database drop/retry reconciliation to exact incomplete bindings while retaining execution-time cluster, volume, active-use, provenance, and filestore checks and retiring evidence only after successful cleanup.
- [ ] 2.5 Add focused failure, unavailable-probe, unrelated-database, evidence-drift, exact drop, and exact retry tests through public SDK/CLI boundaries.

## 3. Keep default and generated config coherent

- [ ] 3.1 Add a preparation-lock-bound default-switch helper that preflights ownership, updates manifest plus project-owned generated `db_name`/`dbfilter`, preserves unrelated settings, and compensates ordinary write failures from validated snapshots.
- [ ] 3.2 Use the helper after restore postconditions and before `default_switched=true`, while leaving external user-managed source configs byte-for-byte unchanged.
- [ ] 3.3 Add success, next-run effective selection, user-config preservation, manifest/config drift, and injected write/compensation failure tests.

## 4. Integrated delivery verification

- [ ] 4.1 Update existing user/SDK documentation and canonical output/architecture inventories for resumable init and explicit incomplete-restore recovery without creating parallel registries.
- [ ] 4.2 Run focused init/preparation/catalog/drop/output tests, catalogue migration gates, Ruff format/check, strict mypy, repository architecture/schema checks, full non-real-Odoo test suite, `git diff --check`, and strict OpenSpec validation.
