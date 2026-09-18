"""Add missing sibling imports for symbols flagged by ruff F821 in split packages."""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "odoo_instance_sdk"


def _defined_names(path: Path) -> set[str]:
    text = path.read_text()
    names: set[str] = set()
    for match in re.finditer(r"^(?:async )?def ([A-Za-z_]\w*)", text, re.M):
        names.add(match.group(1))
    for match in re.finditer(r"^class ([A-Za-z_]\w*)", text, re.M):
        names.add(match.group(1))
    for match in re.finditer(r"^([A-Za-z_]\w*)\s*(?:=|:)", text, re.M):
        names.add(match.group(1))
    return names


def _package_dirs(path: Path) -> list[Path]:
    dirs = [path.parent]
    parent = path.parent
    if parent.name.endswith("_parts") or parent.name in {
        "dbprep",
        "dbreplace",
        "doctor",
        "catalog",
    }:
        dirs.append(parent.parent)
    return dirs


def _relative_import(from_path: Path, to_path: Path) -> str:
    from_parts = from_path.parent.parts
    to_parts = to_path.with_suffix("").parts
    common = 0
    for left, right in zip(from_parts, to_parts, strict=False):
        if left != right:
            break
        common += 1
    ups = len(from_parts) - common
    tail = to_parts[common:]
    prefix = "." * (ups + 1)
    return prefix + ".".join(tail) if tail else f"{prefix}{to_path.stem}"


def _import_insert_index(lines: list[str]) -> int:
    index = 0
    if lines and lines[0].startswith("from __future__"):
        index = 1
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == "" or stripped.startswith(("import ", "from ")):
            index += 1
            continue
        break
    return index


def main() -> None:  # noqa: C901
    proc = subprocess.run(
        ["uv", "run", "ruff", "check", str(SRC), "--select", "F821", "--output-format", "json"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    issues = json.loads(proc.stdout or "[]")
    by_file: dict[Path, set[str]] = defaultdict(set)
    for issue in issues:
        path = Path(issue["filename"])
        name = issue["message"].removeprefix("Undefined name `").removesuffix("`")
        by_file[path].add(name)

    updated = 0
    for path, names in sorted(by_file.items()):
        search_dirs = _package_dirs(path)
        imports: dict[str, str] = {}
        for name in sorted(names):
            for directory in search_dirs:
                for candidate in sorted(directory.glob("*.py")):
                    if candidate.name == "__init__.py" or candidate == path:
                        continue
                    if name not in _defined_names(candidate):
                        continue
                    module = _relative_import(path, candidate)
                    if module.lstrip(".") == path.stem:
                        continue
                    imports[name] = module
                    break
                if name in imports:
                    break

        if not imports:
            continue

        text = path.read_text()
        lines = text.splitlines(keepends=True)
        grouped: dict[str, list[str]] = defaultdict(list)
        for name, module in imports.items():
            grouped[module].append(name)

        import_lines = [
            f"from {module} import {', '.join(sorted(grouped[module]))}\n"
            for module in sorted(grouped)
        ]
        if any(line.strip() in text for line in import_lines):
            continue

        insert_at = _import_insert_index(lines)
        lines[insert_at:insert_at] = ["\n", *import_lines]
        path.write_text("".join(lines))
        updated += 1
        print(f"updated {path.relative_to(ROOT)}")

    print(f"files_updated={updated}")


if __name__ == "__main__":
    main()
