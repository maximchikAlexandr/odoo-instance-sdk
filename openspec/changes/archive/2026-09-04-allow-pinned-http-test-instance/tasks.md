## 1. Update Trust Policy

- [x] 1.1 Remove the non-loopback HTTP transport rejection from repository-selected origin approval while retaining canonical exact-origin pin enforcement; verify focused trust tests accept pinned HTTP and reject unpinned or mismatched origins.
- [x] 1.2 Remove the obsolete transport-assertion helper and update its direct unit coverage; verify URL normalization, loopback checks, and cleartext-warning tests remain green.

## 2. Preserve Cleartext Risk Signaling

- [x] 2.1 Add preparation/database-resource regression coverage proving pinned HTTP reaches the password-bearing request and emits the existing cleartext warning without exposing the password.
- [x] 2.2 Update README security wording to distinguish permitted pinned HTTP from recommended HTTPS and verify documentation contract tests pass.

## 3. Verification and Delivery

- [x] 3.1 Run formatting, lint, strict type checking, focused trust/preparation tests, and the full repository test and coverage gate.
- [x] 3.2 Run `openspec validate allow-pinned-http-test-instance --strict` and verify all implementation tasks and artifacts are coherent.
