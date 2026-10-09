## 1. Error and output boundaries

- [ ] 1.1 Extend the existing Click/Rich boundary so validation failures for a resolved canonical bounded leaf with valid explicit JSON/TOON format emit one sanitized v1 `usage_error` document and exit `2`, while unknown command/format and unsupported native/live paths retain native Click stderr behavior.
- [ ] 1.2 Add parameterized no-effect contract tests for invalid number, range, typed field, and missing argument across JSON/TOON plus unchanged human usage output, using `PUBLIC_LEAF_CASES` as the only leaf inventory.
- [ ] 1.3 Classify invalid UTF-8 source input and expected update-state filesystem access failures at their owning handlers, with stable codes, sanitized actionable Rich/machine output, non-zero exits, and isolated traceback/secret/effect regression tests.

## 2. Focused inspection and startup

- [ ] 2.1 Resolve `env show` UUID/name/cwd identity and owning project from existing catalogue data before collection, fail missing/ambiguous selectors before probes, and take one canonical project-scoped snapshot for the unchanged typed result.
- [ ] 2.2 Add focused call-count/scope tests covering UUID, name, cwd, ambiguity, absence, stopped state, unavailable metrics, and guards that reject unrelated project probes or a second collector.
- [ ] 2.3 Move database-preparation failure context and every demonstrated operation-only dependency off CLI metadata import paths while preserving failure projection and public export identity.
- [ ] 2.4 Add deterministic fresh-process import-boundary tests and record first/repeated installed `--help`/`--version` measurements before and after from outside the checkout without a shared-CI timing threshold; retain catalogue-command regression coverage.

## 3. Human guidance and help

- [ ] 3.1 Refine the existing grouped doctor tables to render sanitized reason, target, state, and shell-safe remediation text without internal repr fields while preserving complete JSON/TOON facts and mutation behavior.
- [ ] 3.2 Add normal-width, narrow-width, redirected-output, unsafe-text, unavailable/partial/no-work, WARN/ERROR preservation, and machine-schema tests for doctor rendering.
- [ ] 3.3 Configure inherited `-h`/`--help` policy on the common Click tree and verify root, group, and leaf metadata paths plus native `-- -h` passthrough with no resolution, snapshot, process, or mutation.
- [ ] 3.4 Add one to three accurate examples and constraints to `init`, `env create`, `db restore`, `run`, `exec`, and `test`, then exercise them through help, parse, and safe dry-run fixtures without renaming commands/options or changing `--create-venv` defaults.

## 4. Completion and field discovery

- [ ] 4.1 Add direct Click completion callbacks for root/positional environment selectors and named remote selectors using only existing local catalogue/project readers, with prefix, spaces, project scope, unreadable-source, and side-effect guard tests.
- [ ] 4.2 Reuse the typed result-schema path source for bounded close-match `--fields` suggestions and dotted/comma-current-item completion, with per-leaf isolation, value-redaction, invalid exit `2`, and valid projection parity tests.
- [ ] 4.3 Run existing command/option/path completion characterization to prove the new value callbacks do not replace Click's standard completion.

## 5. Compatibility evidence and documentation

- [ ] 5.1 Update `CHANGELOG.md` and the maintained CLI documentation for the intentional supported machine usage-error boundary, expected failure hygiene, focused collection, help aliases/examples, and local completion behavior.
- [ ] 5.2 Run the focused CLI/output/env/monitor/startup/doctor/completion suites, formatting, lint, type checks, full repository test gate, and installed wheel/executable checks; record platform or real-service limitations separately from passed evidence and confirm no unrelated production change or new dependency/infrastructure.
