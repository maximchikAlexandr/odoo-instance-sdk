"""Render mutmut results for one module filter."""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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

    for line in output.splitlines():
        stripped = line.strip()
        name, separator, _status = stripped.partition(":")
        if separator and fnmatch.fnmatchcase(name, sys.argv[1]):
            print(stripped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
