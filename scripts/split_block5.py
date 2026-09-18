#!/usr/bin/env python3
"""Behaviour-preserving splits for OpenSpec block 5.4 oversized modules."""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/odoo_instance_sdk"
MAX_LINES = 700


def read_lines(path: Path) -> list[str]:
    return path.read_text().splitlines(keepends=True)


def slice_lines(lines: list[str], start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


def is_header_node(node: ast.stmt) -> bool:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return isinstance(node.value.value, str)
    if isinstance(node, ast.If):
        return isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
    return False


def module_header(lines: list[str]) -> str:
    tree = ast.parse("".join(lines))
    parts: list[str] = []
    for node in tree.body:
        if is_header_node(node):
            parts.append(slice_lines(lines, node.lineno, node.end_lineno or node.lineno))
        else:
            break
    return "".join(parts)


def top_level_spans(lines: list[str]) -> list[tuple[int, int, str]]:
    tree = ast.parse("".join(lines))
    spans: list[tuple[int, int, str]] = []
    for node in tree.body:
        spans.append(
            (
                node.lineno,
                node.end_lineno or node.lineno,
                getattr(node, "name", type(node).__name__),
            )
        )
    return spans


def group_spans(
    spans: Sequence[tuple[int, int, str]], max_lines: int = MAX_LINES
) -> list[list[tuple[int, int, str]]]:
    groups: list[list[tuple[int, int, str]]] = []
    current: list[tuple[int, int, str]] = []
    total = 0
    for span in spans:
        size = span[1] - span[0] + 1
        if current and total + size > max_lines:
            groups.append(current)
            current = []
            total = 0
        current.append(span)
        total += size
    if current:
        groups.append(current)
    return groups


def write_grouped_package(
    old_path: Path,
    package_dir: Path,
    module_names: Sequence[str],
) -> None:
    lines = read_lines(old_path)
    header = module_header(lines)
    spans = [span for span in top_level_spans(lines) if span[0] > len(header.splitlines())]
    groups = group_spans(spans)
    names = list(module_names[: len(groups)])
    if len(names) < len(groups):
        names.extend(f"part{index}" for index in range(len(names) + 1, len(groups) + 1))
    package_dir.mkdir(parents=True, exist_ok=True)
    import_base = "odoo_instance_sdk." + ".".join(package_dir.relative_to(SRC).parts)
    for name, group in zip(names, groups, strict=True):
        start = group[0][0]
        end = group[-1][1]
        (package_dir / f"{name}.py").write_text(header + slice_lines(lines, start, end))
    init = '"""Split package; public imports preserved via re-exports."""\n\nfrom __future__ import annotations\n\n'
    init += "".join(f"from {import_base}.{name} import *  # noqa: F403\n" for name in names)
    init += "\n"
    (package_dir / "__init__.py").write_text(init)
    old_path.write_text(
        f'"""Compatibility re-export shim."""\n\nfrom __future__ import annotations\n\n'
        f"from {import_base} import *  # noqa: F403\n"
    )


def split_executor() -> None:
    old = SRC / "internal/proc/executor.py"
    lines = read_lines(old)
    header = module_header(lines)
    pkg = SRC / "internal/proc"
    (pkg / "run.py").write_text(header + slice_lines(lines, 39, 925))
    (pkg / "spawn.py").write_text(
        header
        + "from odoo_instance_sdk.internal.proc.run import PreparedStep, SubprocessExecutor, prepared_step\n\n"
        + slice_lines(lines, 927, 946)
    )
    (pkg / "terminate.py").write_text(header + slice_lines(lines, 948, 1148))
    old.write_text(
        textwrap.dedent(
            '''\
            """Compatibility re-export shim for the split proc executor."""

            from __future__ import annotations

            from odoo_instance_sdk.internal.proc.run import *
            from odoo_instance_sdk.internal.proc.spawn import spawn
            from odoo_instance_sdk.internal.proc.terminate import *

            __all__ = [
                "ProcessExecutionError",
                "ProcessHandle",
                "ProcessResult",
                "ProcessSpawnError",
                "ProcessTimeoutError",
                "SubprocessExecutor",
                "is_process_alive",
                "owned_handle",
                "prepared_step",
                "run_captured",
                "run_captured_limited",
                "spawn",
                "terminate",
                "terminate_pid",
                "wait_foreground",
            ]
            '''
        )
    )


def split_helpers_file(path: Path, import_base: str) -> None:
    lines = read_lines(path)
    if len(lines) <= MAX_LINES:
        return
    tree = ast.parse("".join(lines))
    header = module_header(lines)
    spans = [
        (node.lineno, node.end_lineno or node.lineno, getattr(node, "name", "?"))
        for node in tree.body
        if not is_header_node(node)
    ]
    groups = group_spans(spans, max_lines=800)
    if len(groups) < 2:
        return
    path.unlink()
    stem = path.stem
    parent = path.parent
    names: list[str] = []
    for index, group in enumerate(groups, start=1):
        name = f"{stem}_{index}"
        names.append(name)
        start = group[0][0]
        end = group[-1][1]
        (parent / f"{name}.py").write_text(header + slice_lines(lines, start, end))
    shim = '"""Split helper shim."""\n\nfrom __future__ import annotations\n\n' + "".join(
        f"from {import_base}.{name} import *  # noqa: F403\n" for name in names
    )
    path.write_text(shim)


def main() -> None:
    jobs = (
        (
            "internal/database_replacement.py",
            "internal/dbreplace",
            ["planning", "validation", "execution"],
        ),
        (
            "internal/doctor.py",
            "internal/doctor",
            ["manifest", "runtime", "catalog", "postgres", "environment"],
        ),
        (
            "internal/database_preparation.py",
            "internal/dbprep",
            ["source", "materialize", "preflight", "orchestrate"],
        ),
        ("resources/monitor.py", "resources/monitor", ["planning", "collection", "projection"]),
        (
            "resources/database.py",
            "resources/database",
            ["lifecycle", "backup_restore", "diagnostics"],
        ),
        (
            "resources/postgres.py",
            "resources/postgres",
            ["lifecycle", "backup_restore", "diagnostics"],
        ),
        ("commands/env.py", "commands/env", ["checkout", "list", "show", "remove", "sync"]),
    )
    for rel, package, names in jobs:
        write_grouped_package(SRC / rel, SRC / package, names)
    split_executor()
    split_helpers_file(
        SRC / "resources/environment/helpers_2.py",
        "odoo_instance_sdk.resources.environment",
    )
    print("Block 5.4 splits complete")


if __name__ == "__main__":
    main()
