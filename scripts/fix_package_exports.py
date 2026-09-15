#!/usr/bin/env python3
"""Ensure split packages re-export private names for compatibility shims."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/odoo_instance_sdk"

PACKAGES = [
    "internal/dbprep",
    "internal/dbreplace",
    "internal/doctor",
    "resources/monitor",
    "resources/database",
    "resources/postgres",
    "commands/env",
    "commands/cli_parts",
]

SHIMS = {
    "internal/database_preparation.py": "internal.dbprep",
    "internal/database_replacement.py": "internal.dbreplace",
    "internal/doctor.py": "internal.doctor",
    "resources/monitor.py": "resources.monitor",
    "resources/database.py": "resources.database",
    "resources/postgres.py": "resources.postgres",
    "commands/env.py": "commands.env",
    "cli.py": "commands.cli_parts",
}

MIXIN_SHIMS = {
    "storage/backup_catalog.py": "storage.catalog",
    "resources/environment.py": "resources.environment",
    "resources/instance.py": "resources.instance",
}


def package_init(package_rel: str) -> None:
    package_dir = SRC / package_rel
    if not package_dir.is_dir():
        return
    preferred_order = {
        "commands/cli_parts": ["registration", "callbacks_a"],
        "commands/env": ["checkout", "list", "show", "remove", "sync"],
    }
    discovered = [
        path.stem
        for path in package_dir.glob("*.py")
        if path.name != "__init__.py" and not path.stem.startswith("_")
    ]
    order = preferred_order.get(package_rel)
    modules = order if order else sorted(discovered)
    if order:
        modules = [name for name in order if name in discovered] + sorted(
            set(discovered) - set(order)
        )
    if not modules:
        return
    import_base = "odoo_instance_sdk." + package_rel.replace("/", ".")
    lines = [
        '"""Split package; public imports preserved via re-exports."""',
        "",
        "from __future__ import annotations",
        "",
        "import importlib",
        "",
        f"_SUBMODULES = {modules!r}",
        "",
        "for _module_name in _SUBMODULES:",
        f'    _module = importlib.import_module(f"{import_base}.{{_module_name}}")',
        "    for _key, _value in _module.__dict__.items():",
        '        if _key.startswith("__"):',
        "            continue",
        "        globals()[_key] = _value",
        "",
    ]
    (package_dir / "__init__.py").write_text("\n".join(lines))


def compatibility_shim(shim_rel: str, package_import: str) -> None:
    path = SRC / shim_rel
    if not path.exists():
        return
    lines = [
        '"""Compatibility re-export shim."""',
        "",
        "from __future__ import annotations",
        "",
        "import importlib",
        "",
        f"_package = importlib.import_module({package_import!r})",
        "for _key, _value in _package.__dict__.items():",
        '    if _key.startswith("__"):',
        "        continue",
        "    globals()[_key] = _value",
        "",
    ]
    path.write_text("\n".join(lines))


def mixin_shim(shim_rel: str, package_import: str) -> None:
    path = SRC / shim_rel
    if not path.exists():
        return
    lines = [
        '"""Compatibility re-export shim."""',
        "",
        "from __future__ import annotations",
        "",
        f"from odoo_instance_sdk.{package_import.replace('/', '.')} import *  # noqa: F403",
        "",
    ]
    path.write_text("\n".join(lines))


def main() -> None:
    for package_rel in PACKAGES:
        package_init(package_rel)
    for shim_rel, package_import in SHIMS.items():
        compatibility_shim(shim_rel, f"odoo_instance_sdk.{package_import.replace('/', '.')}")
    for shim_rel, package_import in MIXIN_SHIMS.items():
        mixin_shim(shim_rel, package_import)
    print("Fixed package exports")


if __name__ == "__main__":
    main()
