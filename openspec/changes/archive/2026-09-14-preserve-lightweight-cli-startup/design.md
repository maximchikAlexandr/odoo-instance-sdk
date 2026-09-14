## Context

The console script points at `odoo_instance_sdk.cli:cli`, so Python initializes `odoo_instance_sdk/__init__.py` before loading the CLI module. The package root currently imports every public SDK object eagerly; `cli.py` and its command modules then import clients, resources, monitoring, output, and operation helpers at module load time. Consequently static Click metadata traverses the same graph as an operation.

The baseline was reproduced on `origin/main` commit `abf513f14325644b81208f8ec3ac47f607e2884f` with Python 3.12.13 on macOS Darwin arm64 after `uv sync --frozen --all-groups`. Three fresh `python -X importtime -c 'import odoo_instance_sdk.cli'` runs reported cumulative CLI import times of 3,950,195 µs (cold), 714,440 µs, and 611,752 µs. Three `odcli --help` runs took 4.19 s (cold), 0.88 s, and 0.78 s. Both `httpx` and `odoo_instance_sdk.resources.monitor` were present in `sys.modules`; `odcli --version` exited 2 as an unknown option. These numbers are machine-local evidence, not portable targets.

The change must precede GitHub #40, #45, #35, and #33 without implementing their command-plan, service-layer, runtime-argument, or PostgreSQL-diagnostic scopes. Existing CLI characterization and public SDK compatibility remain authoritative.

## Goals / Non-Goals

**Goals:**

- Make package and CLI metadata imports defer operation-only modules.
- Add the native Click version option backed by installed distribution metadata.
- Preserve every root SDK export and the full CLI help/command surface.
- Add deterministic regression checks and a reproducible before/after measurement record.

**Non-Goals:**

- Splitting the CLI into a plugin system, registry, generic lazy command loader, or new application/service architecture.
- Migrating Click, adding `lazy-loader`, changing runtime dependencies, or introducing a timing assertion in CI.
- Implementing any behavior from GitHub #40, #45, #35, or #33.
- Optimizing operation execution after a command has been selected.

## Decisions

### 1. Resolve package-root exports with PEP 562 and an explicit map

Keep the existing ordered `__all__` value and add one private mapping from each public name to its canonical module and attribute. Runtime `__getattr__` imports only the requested module with `importlib`, retrieves the object, stores it in module globals, and returns it. Unknown names raise the standard `AttributeError`. Static-only imports live under `TYPE_CHECKING` so strict mypy continues to see the public surface.

This is the smallest standard-library mechanism that preserves `from odoo_instance_sdk import Name`, star imports, object identity, and discoverable `__all__`. A third-party lazy-import dependency is unnecessary. Module-wide proxies and a generated registry were rejected because they add semantics and moving parts without improving this bounded export list.

### 2. Use Click's native eager version option

Decorate the existing root group with `click.version_option(package_name="odoo-instance-sdk")` and do not pass a hard-coded version. Click resolves the installed distribution metadata and handles formatting and eager exit before project context. The existing console-script target and group callback remain unchanged.

A custom callback and a duplicated `0.1.0` constant were rejected because Click already implements the required installed-package behavior.

### 3. Move only operation-only imports behind existing callbacks

Trace the import graph rooted at `cli.py` and the existing `commands` modules, then relocate imports that pull in `client`, resources, monitor, or their HTTP graph into the callback/helper that first executes the operation. Imports used only for annotations move behind `TYPE_CHECKING`; lightweight Click command definitions and their current registration order remain eager so root help stays complete.

No generic lazy group is introduced. This keeps command discovery boring and explicit while allowing a selected operation to pay its normal import cost. Shared helpers are changed only where their top-level imports violate the two forbidden-module assertions.

### 4. Test behavior in fresh subprocesses

Add focused tests that start `sys.executable` subprocesses for package import, `--help`, and `--version`, then assert exit/output plus `sys.modules` absence for `httpx` and `odoo_instance_sdk.resources.monitor`. A separate compatibility test walks the unchanged `__all__` contract, compares each resolved export by identity with its canonical object, verifies caching, and checks an unknown attribute.

Fresh interpreters avoid false results from pytest collection or sibling tests that have already imported heavy modules. Tests assert module boundaries rather than duration, eliminating host-load flakiness.

### 5. Keep performance evidence in a focused developer document

Add a small CLI startup measurement document containing the exact environment preparation, importtime and module-presence commands, baseline commit/results above, final commit/results, and interpretation. The implementer records final values in the same worktree after all tests pass and includes the same summary in the PR.

The document separates deterministic acceptance (`sys.modules`) from illustrative timings and prevents later maintainers from mistaking the local numbers for an SLA.

## Risks / Trade-offs

- [A public export is mapped to the wrong canonical module] → Cover every unchanged `__all__` entry with an explicit identity assertion and star/direct-import compatibility checks.
- [A type-only import becomes a runtime NameError inside a callback] → Keep callback-local imports for runtime values and use `TYPE_CHECKING` only where postponed annotations are the sole consumer; run strict mypy and the existing CLI suite.
- [Root help loses or reorders commands while imports move] → Retain eager Click definitions and registration, then update the characterization snapshot only for the additive `--version` line.
- [A transitive import reintroduces HTTP or monitor modules] → Assert forbidden module absence in fresh subprocesses for package import, help, and version.
- [Lazy exports make the first SDK object access slightly slower] → Accept the one-time import at first use and cache the exact object in module globals; operation paths already require that module.
- [Local importtime values vary substantially] → Record cold and warm runs with environment details, but never encode a duration threshold in tests or CI.

## Migration Plan

1. Establish the baseline from exact `origin/main` using the documented commands.
2. Introduce lazy package exports and compatibility tests before changing CLI imports.
3. Add the Click version option and defer the minimal operation-only import edges while preserving command registration.
4. Run focused regression tests, the existing characterization suite, Ruff, strict mypy, offline/compatibility tests, and packaging tests.
5. Record final measurements and commit SHA in the developer document and PR. Rollback is a normal revert because there is no data, configuration, or persisted-state migration.

## Open Questions

None. The forbidden module set, compatibility surface, measurement method, and excluded follow-up issues are fixed by GitHub #32.
