## Implementation

- [x] Reproduce captured response timeout before changing production code.
- [x] Replace repeated assignment rescanning without bypassing redaction.
- [x] Add assignment boundary and pinned installer provenance regressions.
- [x] Complete focused tests, lint, types and independent review.
- [x] Install the exact compatibility-reviewed isolated hotfix and run one live six-file export.
- [x] Publish PR and retain the unmerged branch; CI must pass before handoff.

## Validation results

The tested executable is `odcli-fix-158`, pinned to `5171c0488b95f2a8893740bc4727a14f8485d1e0`. An independently reviewed, clone-local external configuration confines addon/data/log writes; the shared database is accessed only through the rollback export shell. A single invocation with three installed modules and two languages completed in 22.2 seconds and produced six non-empty files, all passing msgfmt validation. POT/PO source identities matched for all three modules; protected original translation files remained byte-identical. Canonical OdCLI stayed at its original revision.

The 197 focused cases, 24 installer/bootstrap cases, lint, types, strict OpenSpec validation, and independent code review passed. The 200 KB regression first failed on old code by exceeding its five-second timeout. The complete local compatibility run passed 4140 non-serial cases (one skip); the serial group passed 174 cases (seven skips) but failed Docker PostgreSQL setup because host network address pools were exhausted. All ten CI checks passed for the executable commit; the final documentation commit must also have green CI before handoff.
