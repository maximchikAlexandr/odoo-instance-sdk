"""Run the targeted mutation audit while retaining diagnostics on failure."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    from scripts.mutation_report import (
        MutationReportError,
        Target,
        parse_results,
        render_report,
        resolve_target,
    )
except ModuleNotFoundError:  # pragma: no cover - only direct script execution
    from mutation_report import (  # type: ignore[no-redef]
        MutationReportError,
        Target,
        parse_results,
        render_report,
        resolve_target,
    )

REPORT = ROOT / ".artifacts" / "mutation" / "results.txt"
Command = Sequence[str]
Execute = Callable[..., subprocess.CompletedProcess[str]]


def _commands(target: Target | None = None) -> tuple[tuple[str, Command], ...]:
    mutation_command: tuple[str, ...] = (
        sys.executable,
        "-m",
        "mutmut",
        "run",
        "--max-children",
        "32",
    )
    if target is not None:
        mutation_command += (target.filter,)
    results_command: tuple[str, ...] = (
        sys.executable,
        "-m",
        "mutmut",
        "results",
        "--all=true",
        *((target.filter,) if target is not None else ()),
    )
    return (
        (
            "bounded mutmut integration",
            (sys.executable, str(ROOT / "scripts" / "check_mutation_integration.py")),
        ),
        (
            "mutmut run",
            mutation_command,
        ),
        ("mutmut results", results_command),
    )


def _run_stage(
    label: str,
    command: Command,
    *,
    report: Path,
    execute: Execute,
    output_capture: list[str] | None = None,
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
    if output_capture is not None:
        output_capture.append(output)
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
    target: str | None = None,
    commands: Sequence[tuple[str, Command]] | None = None,
    execute: Execute = subprocess.run,
) -> int:
    """Execute mutation stages in order and return the first failing status."""
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("Mutation audit diagnostics\n", encoding="utf-8")
    selected: Target | None = None
    if target:
        try:
            selected = resolve_target(target)
        except MutationReportError as exc:
            report.write_text(
                "Mutation audit diagnostics\n\n=== target validation ===\n"
                f"target validation failed: {exc}\n",
                encoding="utf-8",
            )
            return 2
    result_output: list[str] = []
    stages = tuple(commands or _commands(selected))
    for index, (label, command) in enumerate(stages):
        status = _run_stage(
            label,
            command,
            report=report,
            execute=execute,
            output_capture=result_output if index == len(stages) - 1 else None,
        )
        if status:
            return status
    try:
        counts = parse_results(result_output[-1] if result_output else "", selected)
    except MutationReportError as exc:
        with report.open("a", encoding="utf-8") as stream:
            stream.write(f"\n[result completeness] failed: {exc}\n")
        return 2
    if counts.not_checked:
        with report.open("a", encoding="utf-8") as stream:
            stream.write(
                f"\n[result completeness] failed: not checked={counts.not_checked}; terminal classification is incomplete\n"
            )
        return 2
    report.write_text(
        report.read_text(encoding="utf-8")
        + "\n"
        + render_report(selected, result_output[-1], counts),
        encoding="utf-8",
    )
    return 0


def main() -> int:
    return run_audit(target=os.environ.get("MUTATION_TARGET") or None)


if __name__ == "__main__":
    raise SystemExit(main())
