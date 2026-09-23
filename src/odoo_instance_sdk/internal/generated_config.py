from __future__ import annotations

import configparser
import contextlib
import errno
import io
import os
import stat
import uuid
from pathlib import Path


def project_generated_config_path(project_root: str | Path) -> Path:
    """Return the project-owned runtime config location."""
    return Path(project_root).resolve() / ".odcli" / "odoo.conf"


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


def render_config(
    source_config: Path | None,
    dest: Path,
    *,
    repo_root: Path,
    worktree: Path,
    http_interface: str,
    http_port: int,
    db_name: str,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
) -> str:
    src = configparser.RawConfigParser(interpolation=None)
    if source_config is not None:
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
    for key, value in (
        ("db_host", db_host),
        ("db_port", str(db_port) if db_port is not None else None),
        ("db_user", db_user),
        ("db_password", db_password),
    ):
        if value is not None:
            options[key] = value
    # An isolated environment always owns its logfile: rewrite any explicit
    # logfile to <environment-root>/odoo.log, and inject one when the source
    # config had none so detached launch and `logs` share one resolved path.
    options["logfile"] = str((dest.parent / "odoo.log").resolve())

    output = io.StringIO()
    src.write(output)
    return output.getvalue()


def generate_config(
    source_config: Path | None,
    dest: Path,
    *,
    repo_root: Path,
    worktree: Path,
    http_interface: str,
    http_port: int,
    db_name: str,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
) -> None:
    content = render_config(
        source_config,
        dest,
        repo_root=repo_root,
        worktree=worktree,
        http_interface=http_interface,
        http_port=http_port,
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
    parent_fd = os.open(dest.parent, directory_flags)
    tmp_name: str | None = None
    fd: int | None = None
    try:
        parent_stat = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != os.getuid()
            or parent_stat.st_mode & 0o022
        ):
            raise OSError(  # noqa: TRY301
                errno.EPERM, f"unsafe generated-config parent: {dest.parent}"
            )
        try:
            existing = os.stat(dest.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode)
        ):
            raise OSError(  # noqa: TRY301
                errno.ELOOP, f"refusing non-regular generated config: {dest}"
            )
        tmp_name = f".{dest.name}.{uuid.uuid4().hex}.tmp"
        fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None
            f.write(content)
            f.flush()
            os.fchmod(f.fileno(), 0o600)
        os.replace(tmp_name, dest.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        tmp_name = None
    except BaseException:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name, dir_fd=parent_fd)
        raise
    finally:
        os.close(parent_fd)
