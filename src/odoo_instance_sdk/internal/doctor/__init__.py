"""Doctor diagnostics package."""

from __future__ import annotations

import shutil

from odoo_instance_sdk.internal.doctor.manifest import (
    STATUS_ERROR,
    STATUS_INFO,
    STATUS_OK,
    STATUS_WARN,
    CheckResult,
    DoctorRemediation,
    DoctorReport,
    _check_catalog,
    _check_environment,
    _check_environment_runtime,
    _check_manifest,
    _check_optional_executables,
    _check_orphaned,
    _check_postgres,
    _check_project_runtime,
    _check_uv,
    _database_available,
    _database_status,
    _http_available,
    run_doctor,
)
from odoo_instance_sdk.internal.postgres_compose import docker_available

__all__ = [
    "STATUS_ERROR",
    "STATUS_INFO",
    "STATUS_OK",
    "STATUS_WARN",
    "CheckResult",
    "DoctorRemediation",
    "DoctorReport",
    "_check_catalog",
    "_check_environment",
    "_check_environment_runtime",
    "_check_manifest",
    "_check_optional_executables",
    "_check_orphaned",
    "_check_postgres",
    "_check_project_runtime",
    "_check_uv",
    "_database_available",
    "_database_status",
    "_http_available",
    "docker_available",
    "run_doctor",
    "shutil",
]
