"""Run the targeted mutation audit while retaining diagnostics on failure."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / ".artifacts" / "mutation" / "results.txt"
Command = Sequence[str]
Execute = Callable[..., subprocess.CompletedProcess[str]]


def _commands() -> tuple[tuple[str, Command], ...]:
    return (
        (
            "bounded mutmut integration",
            (sys.executable, str(ROOT / "scripts" / "check_mutation_integration.py")),
        ),
        ("mutmut run", (sys.executable, "-m", "mutmut", "run", "--max-children", "2")),
        ("mutmut results", (sys.executable, "-m", "mutmut", "results", "--all=true")),
    )


def _run_stage(
    label: str,
    command: Command,
    *,
    report: Path,
    execute: Execute,
) -> int:
    with report.open("a", encoding="utf-8") as stream:
        stream.write(f"\n=== {label} ===\n")
        stream.write("$ " + " ".join(command) + "\n")
    environment = os.environ.copy()
    environment["NO_COLOR"] = "1"
    completed = execute(
        list(command),
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = completed.stdout or "(no child output)\n"
    with report.open("a", encoding="utf-8") as stream:
        stream.write(output)
        if not output.endswith("\n"):
            stream.write("\n")
        if completed.returncode:
            stream.write(f"[{label}] failed with exit code {completed.returncode}\n")
    return completed.returncode


def run_audit(
    *,
    report: Path = REPORT,
    commands: Sequence[tuple[str, Command]] | None = None,
    execute: Execute = subprocess.run,
) -> int:
    """Execute mutation stages in order and return the first failing status."""
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("Mutation audit diagnostics\n", encoding="utf-8")
    for label, command in commands or _commands():
        status = _run_stage(label, command, report=report, execute=execute)
        if status:
            return status
    return 0


def main() -> int:
    return run_audit()


if __name__ == "__main__":
    raise SystemExit(main())
