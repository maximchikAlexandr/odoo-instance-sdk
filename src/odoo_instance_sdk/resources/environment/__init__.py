from __future__ import annotations

# ruff: noqa: F401
import configparser
import contextlib
import hashlib
import importlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, TypeVar, Union, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    ConfigError,
    DatabaseAlreadyExistsError,
    EnvironmentConflictError,
    EnvironmentNotFoundError,
    EnvironmentResolutionError,
    InstanceConfigurationError,
    MasterPasswordRequiredError,
    PgAdminDatabaseNotFoundError,
    PgAdminEnvironmentNotFoundError,
    PgAdminError,
    PgAdminNotEligibleError,
    PgAdminUnavailableError,
    PlanError,
    PlanValidationError,
    PostgresClusterError,
    RestoreFailedError,
    StalePlanError,
)
from odoo_instance_sdk.internal import paths as _paths
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.applied_settings import (
    AppliedSettingsError,
    decode_applied_settings,
    encode_applied_settings,
)
from odoo_instance_sdk.internal.database_preparation import (
    classify_freshness,
    compare_provenance,
)
from odoo_instance_sdk.internal.db_name import validate_db_name, validate_filestore_containment
from odoo_instance_sdk.internal.dependency_sync import (
    build_trusted_sync_argv,
    resolve_hash_lock,
    revalidate_hash_lock,
)
from odoo_instance_sdk.internal.generated_config import generate_config
from odoo_instance_sdk.internal.locks import (
    environment_lock_path,
    exclusive_lock,
    provisioning_lock_path,
    python_env_lock_path,
)
from odoo_instance_sdk.internal.odoo_config import (
    get_admin_passwd,
    infer_base_url,
    parse_db_names,
    parse_odoo_config,
)
from odoo_instance_sdk.internal.pgadmin import PgAdminPhaseHandle
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.internal.urls import assert_local
from odoo_instance_sdk.models import (
    Backup,
    BackupFormat,
    BackupFreshness,
    BackupProvenanceComparison,
    BackupProvenanceStatus,
    DatabasePreparationAction,
    DatabasePreparationResult,
    DatabaseRefreshOptions,
    EnvironmentCheckoutPlan,
    EnvironmentCheckoutResult,
    EnvironmentPythonMode,
    PgAdminOpenResult,
    PostgresClusterState,
)
from odoo_instance_sdk.models import (
    DevelopmentEnvironment as _DevelopmentEnvironment,
)
from odoo_instance_sdk.models import (
    EnvironmentDatabaseMode as _EnvironmentDatabaseMode,
)
from odoo_instance_sdk.models import (
    EnvironmentState as _EnvironmentState,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.storage.backup_catalog import CopyJournalStage, normalize_db_host

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
    from odoo_instance_sdk.internal.database_preparation import _RestoreSource
    from odoo_instance_sdk.internal.pgadmin import _PgAdminReconciliationCarrier
    from odoo_instance_sdk.internal.pgadmin_files import (
        PgAdminFingerprintInputs,
        PgAdminPaths,
        PostgresIdentity,
    )
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedCommand,
        PreparedStep,
        ProcessExecutor,
        ProcessResult,
        RunContext,
        Step,
    )
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.resources.postgres import PostgresCluster
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, CatalogValue
from odoo_instance_sdk.resources.environment import helpers as _helpers
from odoo_instance_sdk.resources.environment.helpers import *  # noqa: F403

globals().update(
    {name: value for name, value in _helpers.__dict__.items() if not name.startswith("__")}
)
from odoo_instance_sdk.resources.environment.checkout import _CheckoutMixin
from odoo_instance_sdk.resources.environment.cleanup import _CleanupMixin
from odoo_instance_sdk.resources.environment.pgadmin import _PgadminMixin
from odoo_instance_sdk.resources.environment.settings import _SettingsMixin

for _module_name in ("checkout", "settings", "cleanup", "pgadmin"):
    _module = importlib.import_module(f"odoo_instance_sdk.resources.environment.{_module_name}")
    for _key, _value in _module.__dict__.items():
        if _key.startswith("__"):
            continue
        globals()[_key] = _value


@dataclass(slots=True, kw_only=True)
class EnvironmentResource(_CheckoutMixin, _SettingsMixin, _CleanupMixin, _PgadminMixin):
    _client: OdooClient


from odoo_instance_sdk.models.backup import (
    DevelopmentEnvironment,
    EnvironmentDatabaseMode,
    EnvironmentState,
)
from odoo_instance_sdk.resources.environment.helpers_1 import (
    EnvironmentCheckoutOptions,
    _checkout_public_plan,
)

__all__ = [
    "DevelopmentEnvironment",
    "EnvironmentCheckoutOptions",
    "EnvironmentDatabaseMode",
    "EnvironmentResource",
    "EnvironmentState",
    "_checkout_public_plan",
]
