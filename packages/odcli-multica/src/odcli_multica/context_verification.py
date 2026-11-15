"""Typed validators for immutable Multica task context snapshots."""

from __future__ import annotations

import configparser
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from multica_py.models.project_resources import GithubRepoResourceRef, ProjectResourceRecord

from odcli_multica.models import ContextVerificationError
from odoo_instance_sdk import ProjectConfig

if TYPE_CHECKING:
    from multica_py import Page, TaskRun
    from multica_py.models.system import DaemonStatus


def _find_run(page: Page[TaskRun], run_id: str, issue_id: str) -> TaskRun:
    if (
        page.has_more is not False
        or page.next_cursor is not None
        or page.offset not in (None, 0)
        or page.total is None
        or page.total != len(page.items)
    ):
        raise ContextVerificationError("run evidence is paginated and incomplete")
    for run in page.items:
        if run.id == run_id:
            if run.issue_id != issue_id:
                raise ContextVerificationError("run issue identity conflicts with the request")
            return run
    raise ContextVerificationError("run is not present in the selected issue")


def _required(value: str | None, label: str) -> str:
    if value is None or not value.strip():
        raise ContextVerificationError(f"{label} is unavailable")
    return value


def _task_root(run: TaskRun, checkout: Path) -> Path:
    candidates = (run.work_dir, run.durable_work_dir)
    for value in candidates:
        if value is None:
            continue
        root = Path(value)
        if not root.is_absolute():
            continue
        try:
            canonical = root.resolve(strict=True)
        except OSError:
            continue
        if checkout == canonical or checkout.is_relative_to(canonical):
            return canonical
    raise ContextVerificationError(
        "checkout is not contained by an absolute current or durable task directory"
    )


def _verify_core_project(path: Path) -> str:
    if not path.is_dir():
        raise ContextVerificationError("core project path is not a directory")
    try:
        project = ProjectConfig.load(path)
    except Exception as exc:
        raise ContextVerificationError("core project repository identity is unavailable") from exc
    if project.repository_root.resolve() != path.resolve():
        raise ContextVerificationError("core project repository identity conflicts with its path")
    try:
        config_path = _git_config_path(path)
        parser = configparser.ConfigParser(interpolation=None)
        if not config_path.is_file():
            raise ContextVerificationError("core project Git identity is unavailable")
        parser.read(config_path, encoding="utf-8")
        origin = parser.get('remote "origin"', "url", fallback="").strip()
    except (OSError, configparser.Error) as exc:
        raise ContextVerificationError("core project Git identity is unavailable") from exc
    if not origin:
        raise ContextVerificationError("core project Git identity is unavailable")
    return origin


def _git_config_path(path: Path) -> Path:
    git_marker = path / ".git"
    if git_marker.is_dir():
        return git_marker / "config"
    if not git_marker.is_file():
        raise ContextVerificationError("core project Git identity is unavailable")
    marker = git_marker.read_text(encoding="utf-8").strip()
    if not marker.lower().startswith("gitdir:"):
        raise ContextVerificationError("core project Git identity is unavailable")
    git_dir = (path / marker.split(":", 1)[1].strip()).resolve()
    common_dir = git_dir / "commondir"
    if common_dir.is_file():
        git_dir = (git_dir / common_dir.read_text(encoding="utf-8").strip()).resolve()
    return git_dir / "config"


def _repository_url(
    page: Page[ProjectResourceRecord], project_id: str, core_url: str, selected: str | None
) -> str:
    if page.has_more or page.next_cursor is not None:
        raise ContextVerificationError("project repository evidence is paginated and incomplete")
    if page.offset not in (None, 0) or page.total is None or page.total != len(page.items):
        raise ContextVerificationError("project repository evidence is incomplete")
    values = tuple(
        resource.resource_ref.url
        for resource in page.items
        if resource.project_id == project_id
        and resource.resource_type == "github_repo"
        and isinstance(resource.resource_ref, GithubRepoResourceRef)
    )
    if len(values) != 1:
        raise ContextVerificationError("repository identity is absent or ambiguous")
    native = values[0]
    if _canonical_repository_url(core_url) != _canonical_repository_url(native):
        raise ContextVerificationError("core repository does not match the native repository")
    if selected is not None and _canonical_repository_url(selected) != _canonical_repository_url(
        core_url
    ):
        raise ContextVerificationError("selected repository does not match the core repository")
    return native


def _verify_run_snapshot(run: TaskRun, project_id: str, repository_url: str) -> None:
    if run.project_id is not None and run.project_id != project_id:
        raise ContextVerificationError("run project identity conflicts with the request")
    if not run.project_resources:
        return
    values = tuple(
        resource.resource_ref
        for resource in run.project_resources
        if resource.resource_type in {"github_repo", "git_repository", "repository"}
        and isinstance(resource.resource_ref, str)
    )
    if len(values) != 1 or _canonical_repository_url(values[0]) != _canonical_repository_url(
        repository_url
    ):
        raise ContextVerificationError("run repository snapshot conflicts with project resources")


def _repository_authority(host: str, port: int | None) -> str:
    host = host.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}" if port is not None else host


def _canonical_repository_url(value: str) -> str:
    raw = value.strip()
    if raw.startswith("git@") and ":" in raw:
        authority, path = raw.split(":", 1)
        host = authority[4:]
        if not host or "/" in host or not path or path.startswith("/"):
            raise ContextVerificationError("repository URL is unsupported")
        return f"{host.lower()}/{path.strip('/').removesuffix('.git').lower()}"

    parsed = urlsplit(raw)
    if parsed.scheme not in {"https", "ssh"} or not parsed.netloc:
        raise ContextVerificationError("repository URL is unsupported")
    if parsed.query or parsed.fragment:
        raise ContextVerificationError("repository URL is unsupported")
    try:
        parsed_host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ContextVerificationError("repository URL is unsupported") from exc
    if parsed_host is None or not parsed.path.strip("/"):
        raise ContextVerificationError("repository URL is unsupported")
    if parsed.scheme == "https":
        if parsed.username is not None or parsed.password is not None:
            raise ContextVerificationError("repository URL userinfo is unsupported")
    elif parsed.username != "git" or parsed.password is not None:
        raise ContextVerificationError("repository URL userinfo is unsupported")
    return f"{_repository_authority(parsed_host, port)}/{parsed.path.strip('/').removesuffix('.git').lower()}"


def _canonical_server_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ContextVerificationError("daemon server identity is unsupported")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ContextVerificationError("daemon server identity is unsupported")
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ContextVerificationError("daemon server identity is unsupported") from exc
    if host is None:
        raise ContextVerificationError("daemon server identity is unsupported")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{_repository_authority(host, port)}{path}"


def _verify_daemon(
    daemon: DaemonStatus,
    workspace_id: str,
    runtime_id: str,
    task_root: Path,
    checkout: Path,
    *,
    expected_server_url: str | None,
) -> None:
    _verify_daemon_identity(daemon, expected_server_url)
    _verify_daemon_filesystem(daemon, workspace_id, runtime_id, task_root, checkout)


def _verify_daemon_identity(daemon: DaemonStatus, expected_server_url: str | None) -> None:
    if daemon.status.lower() not in {"ready", "running", "active"}:
        raise ContextVerificationError("owning daemon is not ready")
    if daemon.cli_version is None:
        raise ContextVerificationError("daemon CLI compatibility evidence is unavailable")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", daemon.cli_version)
    if match is None or tuple(int(part) for part in match.groups()) < (0, 5, 3):
        raise ContextVerificationError("native Multica CLI is below the supported floor")
    if daemon.server_url is None or not daemon.server_url.strip():
        raise ContextVerificationError("daemon server identity is unavailable")
    if expected_server_url is not None and _canonical_server_url(
        daemon.server_url
    ) != _canonical_server_url(expected_server_url):
        raise ContextVerificationError("daemon server identity conflicts with the client scope")


def _verify_daemon_filesystem(
    daemon: DaemonStatus,
    workspace_id: str,
    runtime_id: str,
    task_root: Path,
    checkout: Path,
) -> None:
    if daemon.workspaces is None:
        raise ContextVerificationError("daemon filesystem evidence is unavailable")
    workspace = next((item for item in daemon.workspaces if item.id == workspace_id), None)
    if workspace is None or runtime_id not in workspace.runtimes:
        raise ContextVerificationError("daemon does not own the selected runtime")
    root_value = os.environ.get("MULTICA_TASK_WORKSPACES_ROOT", "").strip()
    if not root_value or not Path(root_value).is_absolute():
        raise ContextVerificationError("daemon task workspaces root is unavailable")
    try:
        workspaces_root = Path(root_value).resolve(strict=True)
    except OSError as exc:
        raise ContextVerificationError("daemon task workspaces root is unavailable") from exc
    if not workspaces_root.is_dir():
        raise ContextVerificationError("daemon task workspaces root is unavailable")
    if not task_root.is_relative_to(workspaces_root):
        raise ContextVerificationError("task directory is outside the daemon workspaces root")
    if not checkout.is_relative_to(task_root):
        raise ContextVerificationError("checkout is outside the verified task directory")
