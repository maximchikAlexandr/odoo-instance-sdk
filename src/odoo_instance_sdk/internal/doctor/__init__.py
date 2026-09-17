"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

from importlib import import_module

_SUBMODULES = ["manifest_1", "manifest_2", "runtime"]

for _module_name in _SUBMODULES:
    _module = import_module(f"odoo_instance_sdk.internal.doctor.{_module_name}")
    for _key, _value in _module.__dict__.items():
        if _key.startswith("__"):
            continue
        globals()[_key] = _value

from odoo_instance_sdk.internal.doctor.manifest_1 import (
    STATUS_ERROR,
    STATUS_INFO,
    STATUS_OK,
    STATUS_WARN,
    CheckResult,
    DoctorRemediation,
    DoctorReport,
    _check_environment_runtime,
    _check_project_runtime,
    _database_status,
    run_doctor,
)
from odoo_instance_sdk.internal.postgres_compose import docker_available as docker_available

__all__ = [
    "STATUS_ERROR",
    "STATUS_INFO",
    "STATUS_OK",
    "STATUS_WARN",
    "CheckResult",
    "DoctorRemediation",
    "DoctorReport",
    "_check_environment_runtime",
    "_check_project_runtime",
    "_database_status",
    "docker_available",
    "run_doctor",
]
