from __future__ import annotations

from pathlib import Path

import msgspec


class TestCommandSnapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Immutable selection and provenance facts captured before test execution."""

    worktree: Path | None
    git_head: str | None
    git_base: str | None
    changed_files: tuple[str, ...]
    modules: tuple[str, ...]
    database_names: tuple[str, ...]
    database_identity: tuple[str | None, int | None, str | None]
    interface: str
    port: int
