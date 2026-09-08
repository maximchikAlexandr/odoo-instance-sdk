## Reproducible startup measurement

This document records before-and-after evidence for
`preserve-lightweight-cli-startup`. The values are machine-local evidence, not
portable timing targets.

### Baseline procedure and environment

Run from the repository root in a fresh shell. Keep the caller's working ref
unchanged by measuring the exact baseline in a disposable worktree:

```console
$ repository_root="$(pwd)"
$ git rev-parse origin/main
abf513f14325644b81208f8ec3ac47f607e2884f
$ baseline_worktree="$(mktemp -d)"
$ git worktree add --quiet --detach "$baseline_worktree" abf513f14325644b81208f8ec3ac47f607e2884f
$ cleanup() { git -C "$repository_root" worktree remove --force "$baseline_worktree"; }
$ trap cleanup EXIT
$ cd "$baseline_worktree"
$ uv sync --frozen --all-groups
```

`uv sync --frozen --all-groups` completed successfully with exit code `0`.
The disposable worktree is clean at creation, and the `EXIT` trap removes only
that worktree, leaving the caller's branch or ref unchanged.

Activate the environment before running the measurements:

```console
$ source .venv/bin/activate
$ python --version
Python 3.12.13
$ uv --version
uv 0.11.1 (Homebrew 2026-03-24 aarch64-apple-darwin)
$ uname -srm
Darwin 24.4.0 arm64
```

### Baseline importtime

Run this exact command three times in fresh Python processes. The displayed
`odoo_instance_sdk.cli` line is copied from the raw `-X importtime` output; the
second number is the cumulative value in microseconds.

```console
$ python -X importtime -c 'import odoo_instance_sdk.cli'
import time:     32654 |    2931979 | odoo_instance_sdk.cli
$ python -X importtime -c 'import odoo_instance_sdk.cli'
import time:      8144 |     759889 | odoo_instance_sdk.cli
$ python -X importtime -c 'import odoo_instance_sdk.cli'
import time:      4721 |     700504 | odoo_instance_sdk.cli
```

The pre-change values recorded in `design.md` for baseline commit
`abf513f14325644b81208f8ec3ac47f607e2884f` were `3,950,195 µs` (cold),
`714,440 µs`, and `611,752 µs`.

### Baseline forbidden-module presence

Run this exact fresh-process command after importing the CLI module:

```console
$ python -c 'import sys; import odoo_instance_sdk.cli; print({name: name in sys.modules for name in ("httpx", "odoo_instance_sdk.resources.monitor")})'
{'httpx': True, 'odoo_instance_sdk.resources.monitor': True}
```

### Baseline help and version outcomes

Run the commands once outside any project context:

```console
$ odcli --help
Usage: odcli [OPTIONS] COMMAND [ARGS]...

Options:
  --project PATH  Explicit project path.
  --env TEXT      Environment selector (UUID or name).
  --help          Show this message and exit.
exit code: 0
$ odcli --version
Error: No such option '--version'.
exit code: 2
```

The baseline had no version option. This record contains no duration assertion.

### Final implementation evidence

The final implementation was measured after the lazy package boundary, CLI
deferrals, fresh-interpreter tests, and packaging coverage were complete. The
measured implementation revision is the code-and-test commit immediately
preceding this evidence record: `d831ce1c778623108096f48de3edeeae1d49c816`.

Run the final comparison from a fresh shell started at the repository root,
after the baseline shell has exited. This symmetric disposable-worktree
procedure prepares the exact measured revision and leaves the caller's branch
or ref unchanged:

```console
$ repository_root="$(pwd)"
$ final_worktree="$(mktemp -d)"
$ git worktree add --quiet --detach "$final_worktree" d831ce1c778623108096f48de3edeeae1d49c816
$ cleanup() { git -C "$repository_root" worktree remove --force "$final_worktree"; }
$ trap cleanup EXIT
$ cd "$final_worktree"
$ uv sync --frozen --all-groups
```

The final worktree is clean at creation, and `uv sync --frozen --all-groups`
completed with exit code `0`. The `EXIT` trap is installed before the final
commands below and removes only the disposable final worktree when the shell
exits; the caller's ref is never checked out or detached.

The same environment context was used:

```console
$ source .venv/bin/activate
$ python --version
Python 3.12.13
$ uv --version
uv 0.11.1 (Homebrew 2026-03-24 aarch64-apple-darwin)
$ uname -srm
Darwin 24.4.0 arm64
```

Run this exact command three times in fresh Python processes:

```console
$ python -X importtime -c 'import odoo_instance_sdk.cli'
import time:     24800 |    1390422 | odoo_instance_sdk.cli
$ python -X importtime -c 'import odoo_instance_sdk.cli'
import time:      7100 |     292955 | odoo_instance_sdk.cli
$ python -X importtime -c 'import odoo_instance_sdk.cli'
import time:      8407 |     376551 | odoo_instance_sdk.cli
```

The final forbidden-module check was:

```console
$ python -c 'import sys; import odoo_instance_sdk.cli; print({name: name in sys.modules for name in ("httpx", "odoo_instance_sdk.resources.monitor")})'
{'httpx': False, 'odoo_instance_sdk.resources.monitor': False}
```

The final metadata commands were run outside any project context:

```console
$ odcli --help >/dev/null
exit code: 0
$ odcli --version
odcli, version 0.1.0
exit code: 0
```

Importtime values remain evidence only. Acceptance is based on successful
help/version behavior and deterministic module absence; no timing threshold is
defined here or in CI.

### Compatibility verification after PR #58

The baseline revision `abf513f14325644b81208f8ec3ac47f607e2884f` and the
measured implementation revision `d831ce1c778623108096f48de3edeeae1d49c816`
above are immutable historical measurement points. They do not name the
current `main` or the latest feature head and must not be interpreted as
moving branch references.

PR #58 was rechecked at feature revision
`6841c602bc2c56f71d1e412cb62f01e05fe2d0e2` against its `main` comparison
base `ed590b54fe4f96970de38075bb92b55d8d84c38d`. The PR changes operation-only
modules including `resources/environment.py`,
`internal/database_preparation.py`, `internal/port_allocation.py`, and
`storage/backup_catalog.py`; none became part of the metadata import path.

On that exact feature revision, `tests/unit/test_cli_startup.py` passed (`5
passed`). Independent fresh Python processes invoking each metadata option
also exited `0` and reported both `httpx` and
`odoo_instance_sdk.resources.monitor` absent from `sys.modules`:

```text
{"option": "--help", "exit_code": 0, "httpx": false, "monitor": false}
{"option": "--version", "exit_code": 0, "httpx": false, "monitor": false}
```

This is a compatibility verification, not a replacement timing baseline. The
lightweight metadata contract remains unchanged and no new duration threshold
is introduced.
