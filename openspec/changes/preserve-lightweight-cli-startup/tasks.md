## 1. Preserve the baseline

- [x] 1.1 Add a focused CLI-startup measurement document with the exact `uv sync --frozen --all-groups`, `python -X importtime`, module-presence, `odcli --help`, and `odcli --version` commands plus the recorded `abf513f14325644b81208f8ec3ac47f607e2884f` baseline environment and results from `design.md`.
- [x] 1.2 Re-run the documented baseline commands on exact `origin/main`, copy their raw cumulative values and module-presence results into the startup document, and verify the record is sufficient to reproduce without introducing a duration assertion.

## 2. Make package exports lazy and compatible

- [x] 2.1 Replace eager runtime imports in `src/odoo_instance_sdk/__init__.py` with an explicit private name-to-module mapping and PEP 562 `__getattr__` that imports, caches, and returns only requested exports while keeping static imports under `TYPE_CHECKING`.
- [x] 2.2 Preserve the exact pre-change ordered `__all__` value and add focused tests that compare every lazy export with its canonical object, verify direct/star-import compatibility and cached identity, and require an informative `AttributeError` for an unknown name.
- [x] 2.3 Add a fresh-interpreter package-import test asserting that bare `import odoo_instance_sdk` leaves `odoo_instance_sdk.client`, `odoo_instance_sdk.resources.monitor`, and `httpx` absent from `sys.modules`.

## 3. Keep CLI metadata paths lightweight

- [x] 3.1 Add Click's native eager `--version` option to the existing root group with `package_name="odoo-instance-sdk"`; test exit `0`, installed version output, and operation outside project context without a command-local version literal.
- [x] 3.2 Trace `odoo_instance_sdk.cli` and existing `commands` imports, moving only operation-only client/resource/monitor/HTTP imports into the callbacks or helpers that use them and moving annotation-only imports under `TYPE_CHECKING`; keep the current command definitions, registration order, entry point, and operation behavior.
- [x] 3.3 Finalize fresh-subprocess tests for both `--help` and `--version` that assert successful output and absence of `httpx` and `odoo_instance_sdk.resources.monitor` in `sys.modules`, with no timing threshold.
- [x] 3.4 Update the root help characterization snapshot only for the additive `--version` option, then run the full existing CLI characterization and boundary tests to prove command names, order, selectors, options, passthrough behavior, and exit semantics remain stable.

## 4. Verify installed-package behavior and evidence

- [x] 4.1 Extend isolated wheel packaging coverage to invoke `odcli --version` outside a project, compare its output with the wheel's installed distribution metadata, and confirm the runtime dependency set is unchanged.
- [x] 4.2 After the implementation commit and focused tests pass, repeat the documented importtime/help measurements three times in the same environment, record that measured commit/environment/results and forbidden-module absence in the startup document, and include the baseline/final evidence and gate results in the required issue completion handoff without treating timing as a gate.

## 5. Run project verification and hand off

- [x] 5.1 Run focused package-import, version, CLI import-boundary, characterization, and packaging tests; fix all failures within GitHub #32 scope.
- [x] 5.2 Run `make lint` and `make types`, including Ruff formatting/checks and strict mypy for source plus repository test/script typing.
- [x] 5.3 Run the repository's offline/compatibility and packaging gates (`make test`, `make compat`, and `make package`), then record every gate result and any environment-only exclusion in the required issue completion handoff.
- [x] 5.4 Confirm the diff contains no implementation from GitHub #40, #45, #35, or #33, then commit and push `feat/MYL-67-lightweight-cli-startup` through the SSH remote. PR creation is not required under the updated acceptance; report the final SHA, branch, baseline/final measurements, and verification results in the handoff.
