"""Small, read-only capability probes for optional host executables."""

from __future__ import annotations

import shutil
from pathlib import Path

import msgspec


class OptionalExecutable(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """A secret-free snapshot of one optional executable lookup."""

    name: str
    path: str | None

    @property
    def available(self) -> bool:
        return self.path is not None


def resolve_optional_executable(name: str) -> OptionalExecutable:
    """Resolve an optional tool once, retaining its absolute path if present."""
    if not name or Path(name).name != name:
        raise ValueError("optional executable name must be a bare command")
    located = shutil.which(name)
    return OptionalExecutable(name=name, path=str(Path(located).resolve()) if located else None)


resolve_executable = resolve_optional_executable

__all__ = ["OptionalExecutable", "resolve_executable", "resolve_optional_executable"]
