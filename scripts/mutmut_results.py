"""Render mutmut results for one module filter."""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project_results(output: str, module_filter: str) -> str:
    lines: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        name, separator, _status = stripped.partition(":")
        if separator and fnmatch.fnmatchcase(name, module_filter):
            lines.append(stripped)
    return "\n".join(lines) + ("\n" if lines else "")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: mutmut_results.py MODULE_FILTER", file=sys.stderr)
        return 2

    result = subprocess.run(
        [sys.executable, "-m", "mutmut", "results", "--all=true"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = result.stdout or ""
    if result.returncode:
        print(output, end="")
        return result.returncode

    sys.stdout.write(_project_results(output, sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
