## 1. Catalog setting contract

- [ ] 1.1 Extend the existing MR-assignee key classifier so the exact global key and canonical scoped keys share the positive-integer-or-exact-username value validation, without a schema migration or wildcard scope.
- [ ] 1.2 Add focused catalog and Admin regressions for valid global scalar values, invalid global values, scoped-key compatibility, and independent deletion of global and scoped rows.

## 2. Merge-request selection

- [ ] 2.1 Extend `publish_merge_request_command()` to short-circuit on a nonblank explicit override, otherwise read the exact-project key and then the global key only when the project row is absent, while failing closed on a present invalid selected value.
- [ ] 2.2 Make the dry-run projection expose both candidate keys and their fixed precedence without reading runtime settings, while preserving the existing frozen publication result and `default` source value.
- [ ] 2.3 Add focused client and output-contract regressions for explicit, project-specific, global, absent, blank-override, unrelated-project, and invalid-higher-priority cases; prove exact-username defaults on both create and update, catalog lookup order, and that no lower-priority value or provider mutation is used after failure.

## 3. Documentation and verification

- [ ] 3.1 Update CLI help and `packages/odcli-multica/README.md` to document the global key, project override, explicit override, no-assignee fallback, and assignee-versus-reviewer distinction.
- [ ] 3.2 Run the focused catalog/Admin and `odcli-multica` MR/output tests, then the repository lint, type, and applicable offline test gates on the exact candidate revision; record commands, exit codes, and revision without committing generated evidence.
