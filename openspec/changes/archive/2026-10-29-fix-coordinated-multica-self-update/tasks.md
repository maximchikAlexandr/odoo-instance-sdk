## 1. Correct companion command construction

- [x] 1.1 Generate the forward, rollback, and recovery `odcli-multica` VCS requirement with `#subdirectory=packages/odcli-multica` while leaving the embedded `odoo-instance-sdk` requirement at the repository root and pinning both to their selected immutable revisions.
- [x] 1.2 Replace the unsupported companion `--version` verification argv with the designed supported read-only launcher probe while retaining exact protected metadata checks for both installed distributions.

## 2. Focused regression coverage

- [x] 2.1 Extend self-update unit tests to assert exact companion and SDK requirements, supported launcher argv, success, non-zero launcher failure, malformed metadata, package mismatch, SDK mismatch, retained recovery evidence, and resumed coordinated completion.
- [x] 2.2 Extend the isolated packaging E2E to install supported core and companion uv tools at a baseline containing this fix, update both to a descendant revision, and prove the launcher, both `direct_url.json` revisions, journal cleanup, and no mutation outside isolated HOME/XDG/UV directories.

## 3. Delivery verification

- [x] 3.1 Update directly affected self-update documentation if command or recovery examples encode the old requirement/probe, then run focused unit/packaging checks, repository lint/type/offline/package gates, and strict OpenSpec validation on the exact candidate revision within the workspace test-time policy.
