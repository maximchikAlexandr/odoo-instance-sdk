#!/usr/bin/env python3
"""Split one module with an oversized class into a package using mixins."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/odoo_instance_sdk"
MAX_METHOD_GROUP_LINES = 800


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


def module_sections(lines: list[str], class_name: str) -> tuple[str, str, str, ast.ClassDef]:
    tree = ast.parse("".join(lines))
    class_node = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    header_parts: list[str] = []
    before_parts: list[str] = []
    after_parts: list[str] = []
    for node in tree.body:
        text = slice_lines(lines, node.lineno, node.end_lineno or node.lineno)
        if node is class_node:
            continue
        if is_header_node(node):
            header_parts.append(text)
        elif (node.end_lineno or node.lineno) < class_node.lineno:
            before_parts.append(text)
        else:
            after_parts.append(text)
    return "".join(header_parts), "".join(before_parts), "".join(after_parts), class_node


def class_decorators(class_node: ast.ClassDef, lines: list[str]) -> str:
    start = class_node.lineno
    while start > 1 and lines[start - 2].strip().startswith("@"):
        start -= 1
    if start == class_node.lineno:
        return ""
    return slice_lines(lines, start, class_node.lineno - 1)


def class_field_lines(class_node: ast.ClassDef, lines: list[str]) -> str:
    chunks: list[str] = []
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        chunks.append(slice_lines(lines, node.lineno, node.end_lineno or node.lineno))
    return "".join(chunks)


def method_spans(class_node: ast.ClassDef) -> list[tuple[int, int, str]]:
    return [
        (node.lineno, node.end_lineno or node.lineno, node.name)
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def group_method_spans(spans: list[tuple[int, int, str]]) -> list[list[tuple[int, int, str]]]:
    groups: list[list[tuple[int, int, str]]] = []
    current: list[tuple[int, int, str]] = []
    total = 0
    for span in spans:
        size = span[1] - span[0] + 1
        if current and total + size > MAX_METHOD_GROUP_LINES:
            groups.append(current)
            current = []
            total = 0
        current.append(span)
        total += size
    if current:
        groups.append(current)
    return groups


def mixin_name(module_name: str) -> str:
    parts = [part.capitalize() for part in module_name.split("_")]
    return f"_{''.join(parts)}Mixin"


def split_oversized_class_module(
    source: Path,
    target_dir: Path,
    class_name: str,
    mixin_names: list[str],
) -> None:
    lines = read_lines(source)
    header, before_class, after_class, class_node = module_sections(lines, class_name)
    method_groups = group_method_spans(method_spans(class_node))
    if len(mixin_names) < len(method_groups):
        raise ValueError(f"{source}: need {len(method_groups)} mixin names, got {len(mixin_names)}")
    names = mixin_names[: len(method_groups)]
    target_dir.mkdir(parents=True, exist_ok=True)
    import_base = "odoo_instance_sdk." + ".".join(target_dir.relative_to(SRC).parts)

    support_parts = [part for part in (before_class, after_class) if part.strip()]
    if support_parts:
        (target_dir / "helpers.py").write_text(header + "".join(support_parts))

    support_import = ""
    if support_parts:
        support_import = (
            f"from {import_base} import helpers as _helpers\n"
            "globals().update(\n"
            '    {name: value for name, value in _helpers.__dict__.items() if not name.startswith("__")}\n'
            ")\n\n"
        )
    for name, group in zip(names, method_groups, strict=True):
        start = group[0][0]
        end = group[-1][1]
        mixin = mixin_name(name)
        (target_dir / f"{name}.py").write_text(
            header + support_import + f"class {mixin}:\n" + slice_lines(lines, start, end),
        )

    mixin_imports = "\n".join(
        f"from {import_base}.{name} import {mixin_name(name)}" for name in names
    )
    decorators = class_decorators(class_node, lines)
    fields = class_field_lines(class_node, lines)
    mixin_refs = ", ".join(mixin_name(name) for name in names)
    init = header
    if support_parts:
        init += (
            f"from {import_base}.helpers import *  # noqa: F403\n"
            f"from {import_base} import helpers as _helpers\n"
        )
    init += mixin_imports + "\n\n"
    init += decorators + f"class {class_name}({mixin_refs}):\n" + fields
    if not fields.strip():
        init += "    pass\n"
    init += "\n"
    (target_dir / "__init__.py").write_text(init)
    source.write_text(
        f'"""Compatibility re-export shim."""\n\nfrom __future__ import annotations\n\n'
        f"from {import_base} import *  # noqa: F403\n"
    )
    _split_helpers_if_needed(target_dir, import_base)


def _split_helpers_if_needed(target_dir: Path, import_base: str) -> None:
    helpers = target_dir / "helpers.py"
    if not helpers.exists():
        return
    lines = read_lines(helpers)
    if len(lines) <= MAX_METHOD_GROUP_LINES + 200:
        return
    tree = ast.parse("".join(lines))
    header = import_header_from_tree(lines, tree)
    spans = [
        (node.lineno, node.end_lineno or node.lineno)
        for node in tree.body
        if not is_header_node(node)
    ]
    midpoint = len(spans) // 2
    first = spans[:midpoint]
    second = spans[midpoint:]
    helpers.unlink()
    for index, group in enumerate((first, second), start=1):
        start = group[0][0]
        end = group[-1][1]
        (target_dir / f"helpers_{index}.py").write_text(header + slice_lines(lines, start, end))
    shim = (
        f'"""Split helper modules."""\n\nfrom __future__ import annotations\n\n'
        f"from {import_base}.helpers_1 import *  # noqa: F403\n"
        f"from {import_base}.helpers_2 import *  # noqa: F403\n"
    )
    helpers.write_text(shim)
    for path in target_dir.glob("*.py"):
        if path.name in {"__init__.py", "helpers.py"}:
            continue
        text = path.read_text()
        if f"from {import_base}.helpers import" in text:
            path.write_text(
                text.replace(
                    f"from {import_base}.helpers import *  # noqa: F403",
                    f"from {import_base}.helpers_1 import *  # noqa: F403\n"
                    f"from {import_base}.helpers_2 import *  # noqa: F403",
                )
            )


def import_header_from_tree(lines: list[str], tree: ast.Module) -> str:
    parts: list[str] = []
    for node in tree.body:
        if is_header_node(node):
            parts.append(slice_lines(lines, node.lineno, node.end_lineno or node.lineno))
    return "".join(parts)


def main() -> None:
    jobs = (
        (
            "storage/backup_catalog.py",
            "storage/catalog",
            "BackupCatalog",
            ["backup", "environment", "runtime", "cluster"],
        ),
        (
            "resources/environment.py",
            "resources/environment",
            "EnvironmentResource",
            ["checkout", "settings", "cleanup", "pgadmin", "helpers"],
        ),
        (
            "resources/instance.py",
            "resources/instance",
            "OdooInstance",
            ["identity", "planning", "foreground", "logs", "restore_session"],
        ),
    )
    for rel, package, cls, names in jobs:
        path = SRC / rel
        node = next(
            n
            for n in ast.parse(path.read_text()).body
            if isinstance(n, ast.ClassDef) and n.name == cls
        )
        print(f"{rel}: {len(group_method_spans(method_spans(node)))} groups")
        split_oversized_class_module(path, SRC / package, cls, names)


if __name__ == "__main__":
    main()
