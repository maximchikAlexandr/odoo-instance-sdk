from __future__ import annotations

import configparser
import shutil
import socket
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.internal.git_worktree import worktree_list_porcelain
from odoo_instance_sdk.internal.paths import (
    get_environments_root,
    get_legacy_catalog_path,
)
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.resources.environment import DevelopmentEnvironment

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_ERROR = "error"
STATUS_INFO = "info"

_ORDER = {STATUS_OK: 0, STATUS_INFO: 1, STATUS_WARN: 2, STATUS_ERROR: 3}


@dataclass(slots=True)
class CheckResult:
    name: str
    status: str
    detail: str
    environment_id: str | None = None
    environment_name: str | None = None


@dataclass(slots=True)
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)
    context: dict[str, str | None] = field(default_factory=lambda: {"project_source": None})

    @property
    def ok(self) -> bool:
        return not any(c.status == STATUS_ERROR for c in self.checks)

    @property
    def warnings(self) -> list[str]:
        return [c.detail for c in self.checks if c.status == STATUS_WARN]


def run_doctor(client: OdooClient, project_path: Path | None) -> DoctorReport:
    report = DoctorReport()

    project_root = _resolve_project_root(project_path)
    if project_root is None:
        report.context["project_source"] = None
        report.checks.append(
            CheckResult(
                "manifest",
                STATUS_ERROR,
                "no .odcli/project.toml found; run odcli init or pass --project PATH",
            )
        )
        envs = client.environments.list()
    else:
        report.context["project_source"] = "explicit" if project_path is not None else "cwd"
        _check_manifest(report, project_root)
        envs = client.environments.list(project=project_root, include_removed=True)

    _check_uv(report)
    _check_catalog(report, client)
    _check_legacy(report, client)
    _check_orphaned(report, client)

    for env in envs:
        _check_environment(report, client, env)

    report.checks.sort(key=lambda c: _ORDER.get(c.status, 0))
    return report


def _resolve_project_root(project_path: Path | None) -> Path | None:
    if project_path is not None:
        return Path(project_path).resolve()
    current = Path.cwd().resolve()
    seen: set[Path] = set()
    while current not in seen:
        seen.add(current)
        candidate = current / ".odcli" / "project.toml"
        if candidate.is_file():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def _check_manifest(report: DoctorReport, project_root: Path) -> None:
    manifest = project_root / ".odcli" / "project.toml"
    if not manifest.is_file():
        report.checks.append(CheckResult("manifest", STATUS_ERROR, f"manifest missing: {manifest}"))
        return
    try:
        ProjectConfig.load(project_root)
    except Exception as e:
        report.checks.append(CheckResult("manifest", STATUS_ERROR, f"manifest unparseable: {e}"))
        return
    report.checks.append(CheckResult("manifest", STATUS_OK, str(manifest)))


def _check_uv(report: DoctorReport) -> None:
    uv = shutil.which("uv")
    if uv is None:
        report.checks.append(CheckResult("uv", STATUS_WARN, "uv not found in PATH"))
    else:
        report.checks.append(CheckResult("uv", STATUS_OK, uv))


def _check_catalog(report: DoctorReport, client: OdooClient) -> None:
    from odoo_instance_sdk.internal.paths import get_catalog_path

    catalog_path = get_catalog_path()
    if not catalog_path.exists():
        report.checks.append(
            CheckResult("catalog", STATUS_ERROR, f"catalog missing: {catalog_path}")
        )
        return
    conn = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        user_version = int(row[0]) if row is not None else 0
    except sqlite3.Error as e:
        report.checks.append(CheckResult("catalog", STATUS_ERROR, f"catalog unreadable: {e}"))
        return
    finally:
        conn.close()
    if user_version == 3:
        report.checks.append(
            CheckResult("catalog", STATUS_OK, f"{catalog_path} (user_version={user_version})")
        )
    else:
        report.checks.append(
            CheckResult(
                "catalog",
                STATUS_ERROR,
                f"catalog user_version={user_version}, expected 3",
            )
        )


def _check_legacy(report: DoctorReport, client: OdooClient) -> None:
    legacy = get_legacy_catalog_path()
    if not legacy.exists():
        return
    from odoo_instance_sdk.internal.paths import get_catalog_path

    durable = get_catalog_path()
    if durable.exists():
        report.checks.append(
            CheckResult(
                "legacy",
                STATUS_INFO,
                "both durable and legacy catalogs exist; durable is authoritative, "
                "automatic merge forbidden",
            )
        )
        report.checks.append(
            CheckResult(
                "legacy",
                STATUS_INFO,
                f"migrated legacy artifact: {legacy}",
            )
        )
    else:
        report.checks.append(
            CheckResult("legacy", STATUS_INFO, f"migrated legacy artifact: {legacy}")
        )


def _check_orphaned(report: DoctorReport, client: OdooClient) -> None:
    environments_root = get_environments_root()
    if not environments_root.is_dir():
        return
    catalog = client.get_catalog()
    known_rows = catalog.list_environments(include_removed=True)
    known_ids = {str(row["id"]) for row in known_rows}
    for repo_key_dir in environments_root.iterdir():
        if not repo_key_dir.is_dir():
            continue
        for env_id_dir in repo_key_dir.iterdir():
            if not env_id_dir.is_dir():
                continue
            try:
                uuid.UUID(env_id_dir.name)
            except ValueError:
                continue
            if env_id_dir.name not in known_ids:
                report.checks.append(
                    CheckResult(
                        "orphaned",
                        STATUS_WARN,
                        f"orphaned artifact: {env_id_dir} (no matching catalog row)",
                    )
                )


def _check_environment(
    report: DoctorReport, client: OdooClient, env: DevelopmentEnvironment
) -> None:
    eid = str(env.id)
    ename = env.name

    _check_worktree(report, env, eid, ename)
    _check_python(report, env, eid, ename)
    _check_dependencies(report, env, eid, ename)
    _check_generated_config(report, env, eid, ename)
    _check_port(report, env, eid, ename)
    if env.db_mode == "copy" and env.backup_id is not None:
        _check_backup(report, client, env, eid, ename)


def _check_worktree(
    report: DoctorReport, env: DevelopmentEnvironment, eid: str, ename: str
) -> None:
    worktree = Path(env.worktree_path)
    if not worktree.is_dir():
        report.checks.append(
            CheckResult(
                "worktree",
                STATUS_WARN,
                f"worktree missing: {worktree}",
                environment_id=eid,
                environment_name=ename,
            )
        )
        return
    repo_root = Path(env.repository_root)
    if repo_root.is_dir():
        try:
            porcelain = worktree_list_porcelain(repo_root)
            paths = {Path(w.worktree).resolve() for w in porcelain}
        except Exception:
            paths = set()
        if worktree.resolve() not in paths:
            report.checks.append(
                CheckResult(
                    "worktree",
                    STATUS_WARN,
                    f"worktree not registered in git: {worktree}",
                    environment_id=eid,
                    environment_name=ename,
                )
            )
            return
    report.checks.append(
        CheckResult(
            "worktree",
            STATUS_OK,
            str(worktree),
            environment_id=eid,
            environment_name=ename,
        )
    )


def _check_python(report: DoctorReport, env: DevelopmentEnvironment, eid: str, ename: str) -> None:
    py_path = Path(env.python_environment_path)
    env_root = Path(env.worktree_path).parent
    if env.python_environment_owned:
        try:
            contained = py_path.resolve().is_relative_to(env_root.resolve())
        except OSError:
            contained = False
        if not contained:
            report.checks.append(
                CheckResult(
                    "python",
                    STATUS_WARN,
                    f"ownership mismatch: owned python path outside env root: {py_path}",
                    environment_id=eid,
                    environment_name=ename,
                )
            )
            return
        if not py_path.is_dir():
            report.checks.append(
                CheckResult(
                    "python",
                    STATUS_WARN,
                    f"recorded Python missing: {py_path}",
                    environment_id=eid,
                    environment_name=ename,
                )
            )
            return
    else:
        if not py_path.exists():
            report.checks.append(
                CheckResult(
                    "python",
                    STATUS_WARN,
                    f"recorded Python missing: {py_path}",
                    environment_id=eid,
                    environment_name=ename,
                )
            )
            return
    report.checks.append(
        CheckResult(
            "python",
            STATUS_OK,
            str(py_path),
            environment_id=eid,
            environment_name=ename,
        )
    )


def _check_dependencies(
    report: DoctorReport, env: DevelopmentEnvironment, eid: str, ename: str
) -> None:
    lock = Path(env.dependency_lock_path)
    if not lock.is_file():
        report.checks.append(
            CheckResult(
                "dependencies",
                STATUS_WARN,
                f"requirements.lock missing: {lock}",
                environment_id=eid,
                environment_name=ename,
            )
        )
        return
    report.checks.append(
        CheckResult(
            "dependencies",
            STATUS_OK,
            str(lock),
            environment_id=eid,
            environment_name=ename,
        )
    )


def _check_generated_config(
    report: DoctorReport, env: DevelopmentEnvironment, eid: str, ename: str
) -> None:
    cfg = Path(env.generated_config_path)
    if not cfg.is_file():
        report.checks.append(
            CheckResult(
                "config",
                STATUS_WARN,
                f"generated config missing: {cfg}",
                environment_id=eid,
                environment_name=ename,
            )
        )
        return
    parser = configparser.ConfigParser()
    try:
        parser.read(cfg)
        if not parser.has_section("options"):
            report.checks.append(
                CheckResult(
                    "config",
                    STATUS_WARN,
                    f"generated config has no [options] section: {cfg}",
                    environment_id=eid,
                    environment_name=ename,
                )
            )
            return
    except configparser.Error as e:
        report.checks.append(
            CheckResult(
                "config",
                STATUS_WARN,
                f"generated config unreadable: {e}",
                environment_id=eid,
                environment_name=ename,
            )
        )
        return
    report.checks.append(
        CheckResult(
            "config",
            STATUS_OK,
            str(cfg),
            environment_id=eid,
            environment_name=ename,
        )
    )


def _check_port(report: DoctorReport, env: DevelopmentEnvironment, eid: str, ename: str) -> None:
    interface = env.http_interface or "127.0.0.1"
    host = "127.0.0.1" if interface in ("0.0.0.0", "") else interface
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    free = False
    try:
        s.bind((host, env.http_port))
        free = True
    except OSError:
        free = False
    finally:
        s.close()
    if free:
        report.checks.append(
            CheckResult(
                "port",
                STATUS_OK,
                f"port-free ({env.http_port})",
                environment_id=eid,
                environment_name=ename,
            )
        )
    else:
        report.checks.append(
            CheckResult(
                "port",
                STATUS_INFO,
                f"port-occupied ({env.http_port})",
                environment_id=eid,
                environment_name=ename,
            )
        )


def _check_backup(
    report: DoctorReport,
    client: OdooClient,
    env: DevelopmentEnvironment,
    eid: str,
    ename: str,
) -> None:
    catalog = client.get_catalog()
    row = catalog.get_by_id(str(env.backup_id)) if env.backup_id is not None else None
    if row is None:
        report.checks.append(
            CheckResult(
                "backup",
                STATUS_WARN,
                f"owned backup missing in catalog: {env.backup_id}",
                environment_id=eid,
                environment_name=ename,
            )
        )
        return
    path_raw = row["path"]
    if not path_raw or not Path(str(path_raw)).is_file():
        report.checks.append(
            CheckResult(
                "backup",
                STATUS_WARN,
                f"owned backup file missing: {path_raw}",
                environment_id=eid,
                environment_name=ename,
            )
        )
        return
    report.checks.append(
        CheckResult(
            "backup",
            STATUS_OK,
            str(path_raw),
            environment_id=eid,
            environment_name=ename,
        )
    )


__all__ = ["CheckResult", "DoctorReport", "run_doctor"]
