## 1. Encrypted-first bridge credentials

- [x] 1.1 Replace the project-only bridge credential lookup with delegation to the existing core encrypted-first resolver for the verified root creator and exact host, passing the current project mapping path, project dotenv path, and process-environment overrides while translating failures to sanitized bridge errors.
- [x] 1.2 Add focused `odcli-multica` tests for encrypted-only resolution, encrypted precedence over conflicting legacy values, exact-absence legacy fallback, explicit overrides, broken-store no-fallback behavior, exact member/host rejection, and secret-free projections.
- [x] 1.3 Extend bridge Git sync/push/passthrough and merge-request publication tests to prove encrypted-only credentials reach the existing private execution/HTTP boundaries without TOML mapping, token environment, URL, argv, or log exposure.

## 2. Project disk accounting

- [x] 2.1 Add the `backup_bytes` field and expanded aggregate semantics to `ProjectStorageFootprint`, monitor schema assertions, HTTP/OpenAPI fixtures, and all typed construction sites.
- [x] 2.2 Refactor project storage collection to reuse existing bounded filesystem, catalogue, PostgreSQL-size, and filestore-containment helpers; aggregate main checkout/artifacts, non-removed working environments, distinct related databases/filestores, and available project-owned backups with canonical identity de-duplication and partial completeness.
- [x] 2.3 Add focused Python tests for arithmetic, contained/duplicate filesystem paths, repeated database identities, duplicate backup file identities, zero/no backups, missing files, failed database measurement, stopped runtimes, cache reuse, and isolation from unrelated projects.

## 3. Dashboard and generated contracts

- [x] 3.1 Regenerate the checked OpenAPI document and TypeScript client with the repository's existing codegen workflow and verify the additive `backup_bytes` contract without hand-editing generated files.
- [x] 3.2 Move Disk into the shared instance-card metric row, keep it visible for stopped runtimes, add the project backup subtotal and `project total` scope, preserve incomplete/unavailable rendering, and remove the `Resources` detail row.
- [x] 3.3 Extend dashboard component and generated-contract tests for running and stopped project/worktree cards, formatted backup subtotal, explicit project scope, incomplete/unavailable values, and absence of the former `Resources` row.

## 4. Verification and documentation consistency

- [x] 4.1 Run the focused credential and monitor/storage Python test files, then the repository lint and type checks; if an automated suite is expected to exceed or reaches the workspace's 10-minute local limit, stop only that suite and run it in GitHub Actions on the published exact revision.
- [x] 4.2 Run the existing dashboard codegen check, component tests, and build/package checks applicable to the changed generated client and UI under the same 10-minute automated-suite rule.
- [x] 4.3 Verify the final diff contains no credential value, generated evidence/log/image artifact, new dependency, second resolver, second storage collector, or local E2E script, and record exact commands, exit codes, revision, and file/commit links for independent verification in Multica.
