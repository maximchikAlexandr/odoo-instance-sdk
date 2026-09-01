## Commit Messages

Use Conventional Commits for all repository commits:

```text
<type>[optional scope]: <description>
```

Allowed types: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`, `ci`,
`chore`, `style`, `revert`.

The repository enforces this with `.githooks/commit-msg`. Enable it locally with:

```sh
git config core.hooksPath .githooks
```

## Execution architecture (GitHub #45 / MYL-68)

Changes that add or alter a process-spawning or mutating operation must follow
these repository-local rules:

- Use one immutable inspectable command snapshot for preview and execution.
  Public SDK operations that can spawn a child expose an additive
  `<operation>_command()` sibling; the existing convenience method delegates
  to it and does not rebuild argv, cwd, environment, stdin, or actions.
- Route every production `subprocess.run` and `subprocess.Popen` launch through
  `src/odoo_instance_sdk/internal/proc/`, with argv and `shell=False`. Keep
  process handles, inherited stdio, foreground cleanup, termination, and
  recording/fake execution at that boundary. Do not retain a parallel runner or
  module-local subprocess seam after migration.
- Keep public plans frozen and JSON-safe. Redact argv, environment, stdin,
  scripts, observations, warnings, errors, repr output, and fingerprints with
  one projection; preserve argv argument boundaries and never put secrets in a
  digest. In-process filesystem, HTTP, database, lock, cleanup, signal, and
  precondition effects are honest `ActionStep` values, not invented shell
  commands.
- Use the typed bounded output pipeline for bounded CLI results. Callbacks do
  not write output, select serializers, branch on output mode, or construct
  subprocess argv. `PublicLeafCase`/`PUBLIC_LEAF_CASES` in
  `tests/unit/test_cli_output_modes.py` is the single CLI leaf inventory;
  do not add a second table. Native passthrough, Rich-live, and JSONL cases
  require a concrete documented exception or transport reason, and an
  eligible spawning leaf still needs command-local dry-run coverage.
- Use Expression only for pure, sequential, expected-error planning stages
  (resolve, validate, select, normalize, and capture). Expression must not
  appear in public SDK types, Click registration, serializers, process effects,
  locks, cleanup, rollback, compensation, or foreground lifecycle. Narrow
  untyped third-party values once at an adapter and return a concrete type.
- Do not add explicit `Any` or bare `object` annotations in production. Prefer
  recursive JSON-safe unions, concrete models, protocols, and exhaustive
  callbacks. Do not introduce a vague abstraction, a generic renderer/runner
  hierarchy, or a single-use interface.

### Required #45 and #35 process gates

Before broad adoption, record the checkout planning branch count and the
Expression adapter/unwrap count in a focused decision record. If adapters and
unwraps exceed the planning branches removed, remove Expression while keeping
the typed stage signatures. A positive checkout result is preliminary only and
cannot waive the mandatory post-#35 vertical-slice recheck: after GitHub #35's
planning slice, repeat the same branch-versus-adapter/unwrap measurement under
this rule and remove Expression before broader adoption when that stop
condition is negative. The #35 gate is mandatory even when checkout's result is
positive.

The architecture inventory and contract tests are the checked source for
current direct launches, output writes, imprecise annotations, module-local
subprocess patches, and public methods that transitively spawn. Shrink those
line-specific entries as each migration lands; do not silence a new finding by
moving it to an undocumented allowlist.
