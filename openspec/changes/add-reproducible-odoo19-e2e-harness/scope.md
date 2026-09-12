# Scope boundary and follow-up gaps

## In scope

- A pinned Odoo 19 Community reference server and PostgreSQL dependency topology.
- A pinned OdCLI-managed Odoo 19 source target for the full tier.
- A `base`-only technical addon, deterministic semantic fixture, and fresh real database-plus-filestore ZIP.
- Canonical `PUBLIC_LEAF_CASES` traceability, one critical path, focused failures, recovery, redaction, and cleanup audits.
- Required PR smoke, scheduled/manual full E2E, cold/warm measurements, content caches, and bounded evidence.
- An additive public hash-lock mode on the existing environment checkout/sync operations, implemented through the existing process boundary and one neutral dependency-sync argv builder; directly related product and CLI tests are included.
- Live pinned vulnerability scanning with exact scanner-result/non-expired-exception equality for the reviewed Odoo resolution.

## Out of scope

- Odoo Enterprise or any private repository, registry, package index, or secret.
- Sales, inventory, accounting, website, browser/UI acceptance, and other business addon stacks.
- Odoo versions other than pinned 19 Community.
- A custom production Odoo image or a mutable golden backup committed to Git or cached by CI.
- A new production process/container runner, public SDK type, public CLI leaf, output mode, or any product behavior beyond the approved paired hash-lock parameters and owned-environment synchronization semantics.
- Replacing existing unit, characterization, packaging, dashboard, PostgreSQL, or opt-in developer tests.
- Making the source-backed full tier a required per-PR gate before its scheduled/manual budgets are demonstrated.

## Follow-up gaps

These gaps are explicitly deferred and do not leave implementation choices in MYL-153 graph revision 2:

- Additional Odoo majors require a separate change after the Odoo 19 full tier is stable.
- Browser acceptance requires a separate product requirement; XML-RPC/HTTP verification is sufficient here.
- Backup caching is prohibited in this revision. It may be proposed separately only with p95 evidence that fresh backup generation alone exceeds 180 seconds.
- A custom test image is prohibited in this revision. It may be proposed separately only if pinned official images cannot satisfy a measured CI budget or required dependency pin.
- Promoting the source-backed full tier to every PR is a separate CI policy change after cold/warm evidence demonstrates the 25-minute full budget consistently.
- Changing the current product restore preflight so an empty target cluster is acceptable is a separate product-contract change; this harness uses a namespaced initialized sentinel database.
