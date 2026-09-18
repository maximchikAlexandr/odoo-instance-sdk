from __future__ import annotations

# ruff: noqa: F401
import contextlib
import copy
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TextIO, TypeVar, cast

import psutil

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    LogfileAccessError,
    NonLocalInstanceError,
)
from odoo_instance_sdk.internal.generated_config import project_generated_config_path
from odoo_instance_sdk.internal.locks import environment_lock_path, exclusive_lock, shared_lock
from odoo_instance_sdk.internal.odoo_config import (
    infer_base_url,
    parse_db_names,
    parse_odoo_config,
)
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessExecutor,
    ProcessHandle,
    ProcessResult,
    SubprocessExecutor,
    is_process_alive,
    terminate,
    terminate_pid,
    wait_foreground,
)
from odoo_instance_sdk.internal.process_env import (
    captured_child_environment,
    sanitized_child_environment,
)
from odoo_instance_sdk.internal.project_env import (
    MASTER_PASSWORD_KEY,
    load_project_environment,
    project_environment_secret_values,
)
from odoo_instance_sdk.internal.project_runtime import resolve_project_http_port
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.internal.server import (
    _write_secret_config,
    cleanup_secret_config,
    get_process_status,
)
from odoo_instance_sdk.internal.urls import assert_local, normalize_base_url
from odoo_instance_sdk.models import (
    CommandResult,
    OdooProcess,
    ProcessStatus,
    ReadinessResult,
    StartConfig,
)
from odoo_instance_sdk.resources.database import DatabaseResource

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import (
        Command,
        ExecutionPlan,
        JsonValue,
        PlanObservation,
        SemanticPlanObservation,
    )
    from odoo_instance_sdk.internal.proc import PrivateJsonValue, RunContext
    from odoo_instance_sdk.internal.project_runtime import DeferredProjectRuntime
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.environment import DevelopmentEnvironment
    from odoo_instance_sdk.resources.git import GitResource
    from odoo_instance_sdk.resources.module import ModuleResource
    from odoo_instance_sdk.resources.postgres import PostgresCluster
import importlib

for _module_name in ("helpers_1", "helpers_2"):
    _module = importlib.import_module(f"odoo_instance_sdk.resources.instance.{_module_name}")
    for _key, _value in _module.__dict__.items():
        if _key.startswith("__"):
            continue
        globals()[_key] = _value
from odoo_instance_sdk.resources.instance.helpers_1 import (
    _LOGFILE_SENTINEL_BYTES as _LOGFILE_SENTINEL_BYTES,
)
from odoo_instance_sdk.resources.instance.helpers_1 import (
    InstanceFactory as InstanceFactory,
)
from odoo_instance_sdk.resources.instance.helpers_1 import (
    _RuntimeBinding as _RuntimeBinding,
)
from odoo_instance_sdk.resources.instance.helpers_2 import (
    AuxiliaryRestoreSession as AuxiliaryRestoreSession,
)
from odoo_instance_sdk.resources.instance.helpers_2 import (
    _build_shell_script_step as _build_shell_script_step,
)
from odoo_instance_sdk.resources.instance.helpers_2 import (
    _command_result as _command_result,
)
from odoo_instance_sdk.resources.instance.helpers_2 import (
    _project_runtime_owns_port as _project_runtime_owns_port,
)
from odoo_instance_sdk.resources.instance.helpers_2 import (
    _validate_runtime_args as _validate_runtime_args,
)
from odoo_instance_sdk.resources.instance.helpers_2 import (
    activate_auxiliary_restore_session as activate_auxiliary_restore_session,
)
from odoo_instance_sdk.resources.instance.helpers_2 import (
    active_auxiliary_restore_session as active_auxiliary_restore_session,
)
from odoo_instance_sdk.resources.instance.helpers_2 import (
    auxiliary_restore_session as auxiliary_restore_session,
)
from odoo_instance_sdk.resources.instance.helpers_2 import (
    reset_auxiliary_restore_session as reset_auxiliary_restore_session,
)
from odoo_instance_sdk.resources.instance.helpers_2 import (
    resolve_runtime_argv as resolve_runtime_argv,
)
from odoo_instance_sdk.resources.instance.identity import _IdentityMixin
from odoo_instance_sdk.resources.instance.planning import _PlanningMixin


@dataclass(slots=True, kw_only=True)
class OdooInstance(_IdentityMixin, _PlanningMixin):
    config: InstanceConfig
    _client: OdooClient
    databases: DatabaseResource = field(init=False)
    modules: ModuleResource = field(init=False)
    git: GitResource = field(init=False)
    _artifact_lock_path: Path | None = field(default=None, repr=False)
    _postgres_cluster: PostgresCluster | None = field(default=None, repr=False)
    _environment_id: str | None = field(default=None, repr=False)
    _runtime_binding: _RuntimeBinding | None = field(default=None, repr=False)


import odoo_instance_sdk.resources.instance.helpers_1 as _helpers_1_module

_helpers_1_module.OdooInstance = OdooInstance


__all__ = [
    "AuxiliaryRestoreSession",
    "InstanceFactory",
    "OdooInstance",
    "activate_auxiliary_restore_session",
    "active_auxiliary_restore_session",
    "auxiliary_restore_session",
    "reset_auxiliary_restore_session",
    "resolve_runtime_argv",
]
