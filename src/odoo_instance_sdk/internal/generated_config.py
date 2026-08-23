from __future__ import annotations

import configparser
import contextlib
import os
import tempfile
from pathlib import Path


def _split_list(value: str) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


def _join_list(items: list[str]) -> str:
    return ",".join(items)


def _rebase_path(entry: str, repo_root: Path, worktree: Path) -> str:
    p = Path(entry)
    candidate = (repo_root / p).resolve() if not p.is_absolute() else p.resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError:
        return entry
    rel = candidate.relative_to(repo_root.resolve())
    return str((worktree / rel).resolve())


def generate_config(
    source_config: Path,
    dest: Path,
    *,
    repo_root: Path,
    worktree: Path,
    http_interface: str,
    http_port: int,
    db_name: str,
) -> None:
    src = configparser.RawConfigParser(interpolation=None)
    src.read(str(source_config))
    if not src.has_section("options"):
        src.add_section("options")
    options = src["options"]

    repo_root_resolved = repo_root.resolve()
    worktree_resolved = worktree.resolve()

    for list_key in ("addons_path", "upgrade_path"):
        if list_key in options:
            rebased = [
                _rebase_path(e, repo_root_resolved, worktree_resolved)
                for e in _split_list(options[list_key])
            ]
            options[list_key] = _join_list(rebased)

    if "http_interface" not in options or not options["http_interface"].strip():
        options["http_interface"] = http_interface
    options["http_port"] = str(http_port)
    options["db_name"] = db_name
    options["dbfilter"] = db_name
    if options.get("logfile", "").strip():
        options["logfile"] = str((dest.parent / "odoo.log").resolve())

    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(dest.parent), prefix=dest.name + ".", suffix=".tmp")
    try:
        with open(fd, "w") as f:
            src.write(f)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, str(dest))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
