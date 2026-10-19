## 1. Transition Review Contract

- [ ] 1.1 Extend `reviewer.py` with a structured transition schema and `transition INSTALLED_SHA CANDIDATE_SHA` command that validates both immutable revisions, records the independent session, and fails closed on uncertain preserved-state compatibility.
- [ ] 1.2 Add focused reviewer tests for exact old/new SHA binding, incompatible verdicts, missing revisions, and read-only prompt coverage of selected-root migration plus shared-resource safety.

## 2. Shared Hot-Fix Replacement Lifecycle

- [ ] 2.1 Narrow `alternate_tool.py` target validation so only a healthy identity-matching hot-fix manifest with the same issue/PR provenance and an existing non-symlink state root may enter replacement while ordinary install behavior remains unchanged.
- [ ] 2.2 Revalidate the expected installed manifest under the existing exclusive lifecycle lock, reject self-replacement, preserve the state root, and retain atomic restoration of tool, bin, launcher, and manifest on every publication failure.
- [ ] 2.3 Return bounded JSON-safe transition evidence without exposing review findings, environment values, subprocess output, or selected-state contents.

## 3. Developer Helper Policy

- [ ] 3.1 Add `fix_tool.py advance ISSUE SHA --pr PR --review FILE` with installed-manifest inspection, exact transition-review validation, current PR-head/link verification, identity continuity checks, and a single delegation to the shared replacement lifecycle.
- [ ] 3.2 Keep canonical `fix_tool.py update` semantics unchanged and add helper tests for success, stale/malformed/reused-session review, moved PR, issue/PR mismatch, same-SHA input, unhealthy installation, self-replacement, and bounded failures before mutation.

## 4. Preservation and Rollback Verification

- [ ] 4.1 Extend alternate-tool unit tests with populated state sentinels, concurrent shared/exclusive lock behavior, pre-lock manifest change, candidate preparation failure, each activation rollback boundary, symlink/foreign ownership rejection, and canonical/neighbor noninterference.
- [ ] 4.2 Extend packaging/integration acceptance through the developer-helper boundary to install an old reviewed revision, populate catalogue/backups/environment/project metadata, advance to a new reviewed revision, invoke the retained launcher, and assert byte-for-byte state preservation plus old/new manifest provenance.
- [ ] 4.3 Add failure-path integration coverage through the same boundary for stale review, mismatched identity, preparation failure, activation rollback, and lifecycle conflict, asserting the old launcher remains usable and external Odoo/PostgreSQL/filestore/Docker/port resources are not stopped or mutated.
- [ ] 4.4 Update architecture inventories and tests so the reviewer/helper additions remain policy-only and no duplicate installer, process runner, provenance validator, launcher renderer, lifecycle lock, or cleanup algorithm is introduced.

## 5. Guidance and Repository Gates

- [ ] 5.1 Update the autonomous-work skill to document transition review plus `advance` as the supported active-hot-fix path and clearly distinguish it from canonical update/reconciliation and remove/reinstall.
- [ ] 5.2 Run focused reviewer, helper, alternate-tool, packaging/integration, architecture, formatting, lint, type, and strict OpenSpec checks; record commands and outcomes without committing generated evidence or private environment data.
