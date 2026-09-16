from __future__ import annotations

# ruff: noqa: F821
import configparser
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import (
    ConfigError,
)
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.applied_settings import (
    AppliedSettingsError,
)
from odoo_instance_sdk.internal.doctor.manifest_1 import (
    STATUS_INFO,
    STATUS_OK,
    STATUS_WARN,
    CheckResult,
    DoctorRemediation,
    DoctorReport,
)
from odoo_instance_sdk.internal.git_worktree import (
    worktree_list_porcelain,
)
from odoo_instance_sdk.models import DevelopmentEnvironment

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.resources.environment import DevelopmentEnvironment


def _project_environment_drift(
    env: DevelopmentEnvironment, applied_settings_json: str | None
) -> _EnvironmentDrift:
    from odoo_instance_sdk.internal.doctor import manifest_1 as _manifest_1
    from odoo_instance_sdk.internal.doctor import manifest_2 as _manifest_2

    try:
        evidence = _manifest_1._current_drift_components(env)
    except (AppliedSettingsError, ConfigError, OSError, UnicodeError):
        evidence = _manifest_1._CurrentDriftEvidence(components={}, reasons={})
    stored = _manifest_2._stored_drift_components(applied_settings_json)
    components = tuple(
        _manifest_2._drift_component(
            name,
            evidence.components.get(name),
            stored.get(name) if stored else None,
            reason=evidence.reasons.get(name),
        )
        for name in ("python", "dependencies", "odoo_config", "addons", "git_provenance")
    )
    return _manifest_1._EnvironmentDrift(
        environment_id=str(env.id),
        environment_name=env.name,
        components=components,
        git_context=_manifest_2._git_context(Path(env.worktree_path), env.base_ref),
    )


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
                remediations=(
                    DoctorRemediation(
                        description="Synchronize the environment dependencies",
                        argv=("odcli", "env", "sync", "--dry-run"),
                        mutating=True,
                        dry_run_supported=True,
                    ),
                ),
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
    state = probe_address(env.http_interface or "127.0.0.1", env.http_port)
    if state is AddressState.FREE:
        report.checks.append(
            CheckResult(
                "port",
                STATUS_OK,
                f"port-free ({env.http_port})",
                environment_id=eid,
                environment_name=ename,
            )
        )
    elif state is AddressState.OCCUPIED:
        report.checks.append(
            CheckResult(
                "port",
                STATUS_INFO,
                f"port-occupied ({env.http_port})",
                environment_id=eid,
                environment_name=ename,
            )
        )
    else:
        report.checks.append(
            CheckResult(
                "port",
                STATUS_WARN,
                f"port-unknown ({env.http_port})",
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


__all__ = [
    "_check_backup",
    "_check_dependencies",
    "_check_generated_config",
    "_check_port",
    "_check_python",
    "_check_worktree",
]
