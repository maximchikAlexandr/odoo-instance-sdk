## 1. Canonical Git repository identity

- [x] 1.1 Move the existing strict repository-identity parser into the shared internal Git boundary, preserve its unsafe-input rejection contract, and make numbered coordination consume that one implementation.
- [x] 1.2 Normalize both configured-project and caller-owned checkout origins in environment adoption, map parser failures to the existing sanitized repository-identity conflict, and retain exact rejection for different transport, host, port, or path identities.
- [x] 1.3 Add parameterized unit tests for scp-like/`ssh://` equivalence, reversed forms, host case, non-equivalent repositories, secret-bearing or unsupported syntax, and mutation-free adoption rejection.

## 2. Local COPY restore lifecycle

- [x] 2.1 Capture the existing local auxiliary Database Manager session for named-remote and retained-backup COPY checkout/adoption without changing backup-source selection or adding a second execution context.
- [x] 2.2 Preserve one immutable auxiliary action sequence, remote wait/transfer only for named sources, one local PostgreSQL before/after probe pair, COPY journal transitions, secret redaction, and cleanup on success, failure, and cancellation.
- [x] 2.3 Extend checkout/adoption unit and integration contract tests for named-remote and retained-backup plan shape, exact step consumption, local target postcondition, isolated filestore binding, and failure-safe cleanup while preserving source and neighboring resources.

## 3. Numbered workflow regression and delivery checks

- [x] 3.1 Extend numbered coordinator tests to prove shared normalizer use, publication only after ready local COPY adoption, bounded list/inspect linkage, and no ready identity after restore failure.
- [x] 3.2 Add or extend the repository's GitHub CI-compatible tests and fixtures for the affected adoption and COPY paths; keep Mac/private-host lifecycle evidence out of Git and do not add a parallel test framework.
- [x] 3.3 Run focused tests, full repository lint/type/offline checks, strict OpenSpec validation, and verify that no secret, private hostname, concrete local port/database, diagnostic JSON, log, screenshot, or generated visual report is tracked.
