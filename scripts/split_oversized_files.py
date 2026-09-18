#!/usr/bin/env python3
"""Split any production module still above the line limit."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/odoo_instance_sdk"
MAX_LINES = 950


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


def top_level_spans(lines: list[str]) -> list[tuple[int, int]]:
    tree = ast.parse("".join(lines))
    return [
        (node.lineno, node.end_lineno or node.lineno)
        for node in tree.body
        if not is_header_node(node)
    ]


def group_spans(
    spans: list[tuple[int, int]], *, max_lines: int = MAX_LINES
) -> list[list[tuple[int, int]]]:
    groups: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
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


def resplit_file(path: Path) -> None:
    lines = read_lines(path)
    if len(lines) <= 1000:
        return
    header = module_header(lines)
    header_lines = len(header.splitlines())
    spans = top_level_spans(lines)
    groups = group_spans(spans, max_lines=max(300, 900 - header_lines))
    if len(groups) < 2:
        midpoint = header_lines + (len(lines) - header_lines) // 2
        while midpoint < len(lines) and lines[midpoint - 1].strip():
            midpoint += 1
        groups = [
            [(header_lines + 1, midpoint)],
            [(midpoint + 1, len(lines))],
        ]
    parent = path.parent
    stem = path.stem
    names: list[str] = []
    for index, group in enumerate(groups, start=1):
        name = f"{stem}_{index}"
        names.append(name)
        start = group[0][0]
        end = group[-1][1]
        (parent / f"{name}.py").write_text(header + slice_lines(lines, start, end))
    import_base = "odoo_instance_sdk." + ".".join(path.relative_to(SRC).parent.parts)
    path.write_text(
        '"""Split module shim."""\n\nfrom __future__ import annotations\n\n'
        + "".join(f"from {import_base}.{name} import *  # noqa: F403\n" for name in names)
    )


def main() -> None:
    for path in sorted(SRC.rglob("*.py")):
        if len(read_lines(path)) > 1000:
            print("splitting", path)
            resplit_file(path)


if __name__ == "__main__":
    main()
