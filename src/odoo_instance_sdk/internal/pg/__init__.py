"""PostgreSQL-only implementation package; import its focused submodules directly."""

from __future__ import annotations

from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str | None]] = {
    "shutil": ("shutil", None),
}


def __getattr__(name: str) -> Any:
    spec = _LAZY_EXPORTS.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = spec
    from importlib import import_module

    module = import_module(module_name)
    value = module if attr_name is None else getattr(module, attr_name)
    globals()[name] = value
    return value
