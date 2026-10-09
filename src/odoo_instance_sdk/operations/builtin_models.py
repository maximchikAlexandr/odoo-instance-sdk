"""Concrete built-in operation request/result/error projections."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import msgspec

if TYPE_CHECKING:
    from .contracts import OperationParameter, PublicLeafCase


_NO_DEFAULT = object()


def _p(
    name: str,
    schema: str,
    *,
    required: bool = False,
    default: Any = _NO_DEFAULT,
) -> OperationParameter:
    from .contracts import _NO_DEFAULT as CONTRACT_NO_DEFAULT, OperationParameter

    if default is _NO_DEFAULT:
        default = CONTRACT_NO_DEFAULT

    return OperationParameter(name, schema, required=required, default=default)


def _format(*parameters: OperationParameter) -> tuple[OperationParameter, ...]:
    return (*parameters, _p("output_format", "string|null", default=None))


_PARAMETER_SPECS: dict[tuple[str, ...], tuple[OperationParameter, ...]] = {
    ("env", "show"): _format(
        _p("environment", "string|null", default=None),
        _p("field_selection", "string|null", default=None),
    ),
    ("env", "path"): _format(_p("environment", "string|null", default=None)),
    ("env", "sync"): _format(
        _p("environment", "string|null", default=None),
        _p("upgrade", "boolean", default=False),
        _p("hash_lock", "string|null", default=None),
        _p("hash_lock_sha256", "string|null", default=None),
        _p("dry_run", "boolean", default=False),
    ),
    ("env", "create"): _format(
        _p("ticket", "string", required=True),
        _p("base_ref", "string|null", default=None),
        _p("config_path", "string|null", default=None),
        _p("db_mode", "string", default="shared"),
        _p("source_database", "string|null", default=None),
        _p("remote_name", "string|null", default=None),
        _p("backup_id", "string|null", default=None),
        _p("target_database", "string|null", default=None),
        _p("odoo_bin", "string|null", default=None),
        _p("python", "string|null", default=None),
        _p("create_venv", "boolean", default=False),
        _p("hash_lock", "string|null", default=None),
        _p("hash_lock_sha256", "string|null", default=None),
        _p("http_port", "integer|null", default=None),
        _p("dry_run", "boolean", default=False),
    ),
    ("env", "ls"): _format(
        _p("all_envs", "boolean", default=False),
        _p("all_projects", "boolean", default=False),
        _p("watch", "boolean", default=False),
        _p("interval", "number", default=2.0),
        _p("field_selection", "string|null", default=None),
    ),
    ("env", "rm"): _format(
        _p("environments", "string[]", default=()),
        _p("dry_run", "boolean", default=False),
        _p("yes", "boolean", default=False),
        _p("force_connections", "boolean", default=False),
    ),
    ("remote", "ls"): _format(),
    ("remote", "add"): _format(
        _p("name", "string", required=True),
        _p("base_url", "string", required=True),
        _p("database", "string", required=True),
        _p("git_branch", "string", required=True),
        _p("dry_run", "boolean", default=False),
    ),
    ("remote", "update"): _format(
        _p("name", "string", required=True),
        _p("base_url", "string|null", default=None),
        _p("database", "string|null", default=None),
        _p("git_branch", "string|null", default=None),
        _p("dry_run", "boolean", default=False),
    ),
    ("remote", "remove"): _format(
        _p("name", "string", required=True), _p("dry_run", "boolean", default=False)
    ),
    ("backup", "ls"): _format(
        _p("source_base_url", "string|null", default=None),
        _p("database_name", "string|null", default=None),
        _p("include_all_states", "boolean", default=False),
        _p("all_projects", "boolean", default=False),
        _p("limit", "integer", default=100),
        _p("cursor", "string|null", default=None),
        _p("field_selection", "string|null", default=None),
    ),
    ("backup", "retention"): _format(
        _p("retention_days", "integer|null", default=None),
        _p("auto_prune", "boolean|null", default=None),
        _p("dry_run", "boolean", default=False),
    ),
    ("backup", "pin"): _format(
        _p("backup_id", "string", required=True), _p("dry_run", "boolean", default=False)
    ),
    ("backup", "unpin"): _format(
        _p("backup_id", "string", required=True), _p("dry_run", "boolean", default=False)
    ),
    ("backup", "prune"): _format(
        _p("dry_run", "boolean", default=False), _p("yes", "boolean", default=False)
    ),
    ("backup", "inspect"): _format(
        _p("backup_id", "string", required=True), _p("field_selection", "string|null", default=None)
    ),
    ("backup", "validate"): _format(_p("backup_id", "string", required=True)),
    ("backup", "rm"): _format(
        _p("backup_ids", "string[]", default=()),
        _p("dry_run", "boolean", default=False),
        _p("yes", "boolean", default=False),
    ),
    ("db", "refresh"): _format(
        _p("restore", "boolean", default=False),
        _p("show_command_output", "boolean", default=False),
        _p("reset_admin_password", "boolean", default=False),
        _p("source_branch", "string|null", default=None),
        _p("remote_name", "string|null", default=None),
        _p("dry_run", "boolean", default=False),
        _p("no_input", "boolean", default=False),
    ),
    ("db", "restore"): _format(
        _p("backup_uuid", "string|null", default=None),
        _p("archive_file", "string|null", default=None),
        _p("target_database", "string|null", default=None),
        _p("reset_admin_password", "boolean", default=False),
        _p("yes", "boolean", default=False),
        _p("replace_environment", "boolean", default=False),
        _p("dry_run", "boolean", default=False),
        _p("no_input", "boolean", default=False),
    ),
    ("db", "ls"): _format(
        _p("tracked", "boolean", default=False), _p("field_selection", "string|null", default=None)
    ),
    ("db", "reset-admin-password"): _format(
        _p("dry_run", "boolean", default=False),
        _p("no_input", "boolean", default=False),
        _p("yes", "boolean", default=False),
    ),
    ("db", "rm"): _format(
        _p("databases", "string[]", default=()),
        _p("force_default", "boolean", default=False),
        _p("force_connections", "boolean", default=False),
        _p("yes", "boolean", default=False),
        _p("dry_run", "boolean", default=False),
    ),
    ("db", "locks"): _format(
        _p("database", "string", required=True),
        _p("top", "integer", default=20),
        _p("timeout", "number", default=30.0),
        _p("field_selection", "string|null", default=None),
    ),
    ("db", "stats"): _format(
        _p("database", "string", required=True),
        _p("top", "integer", default=20),
        _p("timeout", "number", default=30.0),
        _p("field_selection", "string|null", default=None),
    ),
    ("db", "bloat"): _format(
        _p("database", "string", required=True),
        _p("top", "integer", default=20),
        _p("exact_max_scan_mb", "integer", default=64),
        _p("timeout", "number", default=30.0),
        _p("field_selection", "string|null", default=None),
    ),
    ("db", "init-monitoring"): _format(
        _p("database", "string", required=True),
        _p("yes", "boolean", default=False),
        _p("timeout", "number", default=30.0),
        _p("dry_run", "boolean", default=False),
    ),
    ("eval",): _format(
        _p("expression", "string", required=True),
        _p("commit", "boolean", default=False),
        _p("dry_run", "boolean", default=False),
    ),
    ("exec",): _format(
        _p("script", "string", required=True),
        _p("script_args", "string[]", default=()),
        _p("commit", "boolean", default=False),
        _p("dry_run", "boolean", default=False),
    ),
    ("test",): _format(),
    ("module", "ls"): _format(
        _p("modules", "string[]", default=()),
        _p("state", "string|null", default=None),
        _p("dry_run", "boolean", default=False),
        _p("field_selection", "string|null", default=None),
    ),
    ("module", "update"): _format(
        _p("modules", "string[]", default=()),
        _p("changed", "boolean", default=False),
        _p("base", "string|null", default=None),
        _p("dry_run", "boolean", default=False),
        _p("yes", "boolean", default=False),
    ),
    ("module", "test"): _format(
        _p("modules", "string[]", required=True),
        _p("test_tags", "string", required=True),
        _p("reload_tests", "boolean", default=False),
        _p("allow_empty", "boolean", default=False),
        _p("dry_run", "boolean", default=False),
    ),
    ("module", "info"): _format(_p("module", "string|null", default=None)),
    ("module", "where"): _format(_p("module", "string|null", default=None)),
    ("module", "deps"): _format(_p("module", "string|null", default=None)),
    ("module", "install-order"): _format(_p("modules", "string[]", required=True)),
    ("translations", "export"): _format(
        _p("modules", "string[]", required=True),
        _p("languages", "string[]", required=True),
        _p("dry_run", "boolean", default=False),
    ),
    ("deps", "verify"): _format(_p("dry_run", "boolean", default=False)),
    ("vscode", "generate"): _format(
        _p("write_file", "boolean", default=False), _p("dry_run", "boolean", default=False)
    ),
    ("postgres", "approve-image"): _format(
        _p("image_digest", "string", required=True),
        _p("timeout", "number", default=60.0),
        _p("dry_run", "boolean", default=False),
    ),
    ("postgres", "ps"): _format(),
    ("postgres", "up"): _format(
        _p("wait_timeout", "number", default=60.0), _p("dry_run", "boolean", default=False)
    ),
    ("postgres", "stop"): _format(
        _p("timeout", "number", default=30.0), _p("dry_run", "boolean", default=False)
    ),
    ("psql",): (_p("psql_args", "string[]", default=()), _p("dry_run", "boolean", default=False)),
    ("resource", "doctor"): _format(_p("field_selection", "string|null", default=None)),
    ("resource", "ls"): _format(
        _p("all_projects", "boolean", default=False),
        _p("field_selection", "string|null", default=None),
    ),
    ("ps",): (),
    ("stop",): _format(_p("dry_run", "boolean", default=False)),
    ("run",): _format(
        _p("detach", "boolean", default=False),
        _p("wait_ready", "boolean", default=False),
        _p("readiness_timeout", "number|null", default=None),
        _p("odoo_args", "string[]", default=()),
        _p("dry_run", "boolean", default=False),
    ),
    ("logs",): (_p("tail", "integer", default=100), _p("follow", "boolean", default=False)),
    ("shell",): _format(
        _p("odoo_args", "string[]", default=()), _p("dry_run", "boolean", default=False)
    ),
    ("monitor",): (
        _p("headless", "boolean", default=False),
        _p("host", "string", default="127.0.0.1"),
        _p("port", "integer|null", default=None),
        _p("no_open", "boolean", default=False),
    ),
    ("git", "commit"): _format(
        _p("description", "string", required=True),
        _p("ticket", "string|null", default=None),
        _p("tag", "string|null", default=None),
        _p("dry_run", "boolean", default=False),
        _p("yes", "boolean", default=False),
    ),
    ("git", "check"): _format(_p("base", "string|null", default=None)),
    ("git", "absorb"): _format(
        _p("base", "string|null", default=None),
        _p("and_rebase", "boolean", default=False),
        _p("dry_run", "boolean", default=False),
        _p("yes", "boolean", default=False),
    ),
    ("git", "sync"): _format(
        _p("base", "string|null", default=None),
        _p("push", "boolean", default=False),
        _p("dry_run", "boolean", default=False),
        _p("yes", "boolean", default=False),
    ),
    ("bug-report", "init"): _format(
        _p("title", "string", required=True),
        _p("kind", "string", default="bug"),
        _p("dry_run", "boolean", default=False),
    ),
    ("bug-report", "submit"): _format(
        _p("report_id", "string", required=True), _p("dry_run", "boolean", default=False)
    ),
    ("update",): _format(_p("dry_run", "boolean", default=False)),
    ("contract", "export"): (_p("output_format", "string", default="json"),),
    ("init",): _format(
        _p("odoo_bin", "string|null", default=None),
        _p("python", "string|null", default=None),
        _p("source_config", "string|null", default=None),
        _p("default_source_database", "string|null", default=None),
        _p("preferred_http_port", "integer|null", default=None),
        _p("requirements", "string[]", default=()),
        _p("run_args", "string[]", default=()),
        _p("runtime_cwd", "string|null", default=None),
        _p("from_vscode", "string|null", default=None),
        _p("launch_name", "string|null", default=None),
        _p("postgres_mode", "string", default="external"),
        _p("postgres_image", "string|null", default=None),
        _p("postgres_port", "integer|null", default=None),
        _p("postgres_user", "string|null", default=None),
        _p("no_input", "boolean", default=False),
        _p("yes", "boolean", default=False),
        _p("dry_run", "boolean", default=False),
        _p("test_url", "string|null", default=None),
        _p("test_database", "string|null", default=None),
        _p("test_branch", "string|null", default=None),
        _p("remote_entries", "string[]", default=()),
        _p("local_config", "boolean", default=False),
        _p("allow_partial", "boolean", default=False),
        _p("project_path", "string|null", default=None),
    ),
    ("doctor",): _format(_p("remote_name", "string|null", default=None)),
}


def _python_type(schema: str) -> Any:
    base, _, nullable = schema.partition("|")
    type_map: dict[str, Any] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "string[]": tuple[str, ...],
    }
    value_type = type_map[base]
    return value_type | type(None) if nullable == "null" else value_type


def _operation_parameters(case: PublicLeafCase) -> tuple[OperationParameter, ...]:
    return _PARAMETER_SPECS.get(case.path, ())


def _operation_stem(path: Sequence[str]) -> str:
    return "".join(part.replace("-", " ").title().replace(" ", "") for part in path)


def _request_type(
    case: PublicLeafCase, parameters: tuple[OperationParameter, ...]
) -> type[msgspec.Struct] | None:
    if case.classification in {"native-passthrough", "jsonl-stream", "rich-live"}:
        return None
    fields: list[tuple[Any, ...]] = []
    for parameter in parameters:
        field_type = _python_type(parameter.schema)
        fields.append(
            (parameter.name, field_type)
            if parameter.required
            else (parameter.name, field_type, parameter.default)
        )
    return msgspec.defstruct(
        f"{_operation_stem(case.path)}Request",
        fields,
        module="odoo_instance_sdk.operations.contracts",
        frozen=True,
        kw_only=True,
        forbid_unknown_fields=True,
    )


def _failure_type(case: PublicLeafCase) -> type[msgspec.Struct] | None:
    if case.classification in {"native-passthrough", "jsonl-stream", "rich-live"}:
        return None
    return msgspec.defstruct(
        f"{_operation_stem(case.path)}Failure",
        [
            ("operation", str, case.path[-1]),
            ("code", str),
            ("message", str),
            ("retryable", bool, False),
        ],
        module="odoo_instance_sdk.operations.contracts",
        frozen=True,
        kw_only=True,
        forbid_unknown_fields=True,
    )


_RESULT_FIELDS: dict[tuple[str, ...], tuple[tuple[Any, ...], ...]] = {
    ("doctor",): (("checks", tuple[str, ...]), ("healthy", bool)),
    ("resource", "ls"): (("resources", tuple[str, ...]), ("count", int)),
    ("resource", "doctor"): (("checks", tuple[str, ...]), ("healthy", bool)),
    ("env", "path"): (("environment", str), ("path", str)),
    ("env", "show"): (("environment", str), ("state", str)),
    ("env", "sync"): (("environment", str), ("changed", bool)),
    ("env", "rm"): (("environment_ids", tuple[str, ...]), ("removed", bool)),
    ("init",): (("project_path", str), ("initialized", bool)),
    ("remote", "ls"): (("remotes", tuple[str, ...]),),
    ("remote", "add"): (("name", str), ("changed", bool)),
    ("remote", "update"): (("name", str), ("changed", bool)),
    ("remote", "remove"): (("name", str), ("changed", bool)),
    ("backup", "ls"): (("backups", tuple[str, ...]), ("count", int)),
    ("module", "ls"): (("modules", tuple[str, ...]), ("count", int)),
    ("module", "info"): (("module", str), ("manifest", str | None, None)),
    ("module", "where"): (("module", str), ("path", str | None, None)),
    ("module", "deps"): (("module", str), ("dependencies", tuple[str, ...])),
    ("eval",): (("value", str | None, None), ("committed", bool, False)),
    ("exec",): (("stdout", str), ("stderr", str), ("returncode", int)),
    ("translations", "export"): (
        ("modules", tuple[str, ...]),
        ("languages", tuple[str, ...]),
        ("written", bool),
    ),
    ("vscode", "generate"): (("path", str | None, None), ("written", bool, False)),
    ("postgres", "approve-image"): (("image_digest", str), ("approved", bool)),
    ("postgres", "ps"): (("state", str), ("ready", bool)),
    ("postgres", "up"): (("state", str), ("changed", bool)),
    ("postgres", "stop"): (("state", str), ("changed", bool)),
    ("contract", "export"): (
        ("contract_version", int),
        ("entry_point_group", str),
        ("operation_count", int),
        ("bundle_sha256", str),
    ),
}


def _result_type(case: PublicLeafCase) -> type[msgspec.Struct] | None:
    if case.classification in {"native-passthrough", "jsonl-stream", "rich-live"}:
        return None
    existing: dict[tuple[str, ...], type[msgspec.Struct]] = {}
    from odoo_instance_sdk.models.backup import (
        AdminPasswordResetResult,
        BackupDeletionResult,
        BackupInspectResult,
        BackupPinResult,
        BackupPruneResult,
        BackupRetentionUpdateResult,
        BackupValidationResult,
        DatabasePreparationResult,
        EnvironmentCheckoutResult,
    )
    from odoo_instance_sdk.models.bug_report import BugReportInitResult, BugReportSubmitResult
    from odoo_instance_sdk.models.command import DropResult, OdooTestResult, RestoreResult
    from odoo_instance_sdk.models.database_inventory import DatabaseInventoryResult
    from odoo_instance_sdk.models.deps import DepsVerifyResult
    from odoo_instance_sdk.models.git import (
        GitAbsorbResult,
        GitCheckResult,
        GitCommitContext,
        GitSyncResult,
    )
    from odoo_instance_sdk.models.module import ModuleInstallOrder, ModuleUpdateResult
    from odoo_instance_sdk.models.monitor import CheckoutInventory
    from odoo_instance_sdk.models.postgres import (
        LocksResult,
        MonitoringInitializationResult,
        PostgresBloatResult,
        PostgresStatsResult,
    )
    from odoo_instance_sdk.models.process_inventory import ProcessInventory
    from odoo_instance_sdk.models.runtime import StopEnvironmentResult
    from odoo_instance_sdk.models.update import UpdateResult

    existing.update(
        {
            ("env", "create"): EnvironmentCheckoutResult,
            ("env", "ls"): CheckoutInventory,
            ("db", "refresh"): DatabasePreparationResult,
            ("db", "restore"): RestoreResult,
            ("db", "ls"): DatabaseInventoryResult,
            ("db", "reset-admin-password"): AdminPasswordResetResult,
            ("db", "rm"): DropResult,
            ("db", "locks"): LocksResult,
            ("db", "stats"): PostgresStatsResult,
            ("db", "bloat"): PostgresBloatResult,
            ("db", "init-monitoring"): MonitoringInitializationResult,
            ("test",): OdooTestResult,
            ("module", "update"): ModuleUpdateResult,
            ("module", "test"): OdooTestResult,
            ("module", "install-order"): ModuleInstallOrder,
            ("deps", "verify"): DepsVerifyResult,
            ("git", "commit"): GitCommitContext,
            ("git", "check"): GitCheckResult,
            ("git", "absorb"): GitAbsorbResult,
            ("git", "sync"): GitSyncResult,
            ("ps",): ProcessInventory,
            ("bug-report", "init"): BugReportInitResult,
            ("bug-report", "submit"): BugReportSubmitResult,
            ("update",): UpdateResult,
            ("stop",): StopEnvironmentResult,
            ("backup", "validate"): BackupValidationResult,
            ("backup", "inspect"): BackupInspectResult,
            ("backup", "rm"): BackupDeletionResult,
            ("backup", "pin"): BackupPinResult,
            ("backup", "unpin"): BackupPinResult,
            ("backup", "prune"): BackupPruneResult,
            ("backup", "retention"): BackupRetentionUpdateResult,
        }
    )
    if case.path in existing:
        return existing[case.path]
    if case.path in _RESULT_FIELDS:
        return msgspec.defstruct(
            f"{_operation_stem(case.path)}Result",
            list(_RESULT_FIELDS[case.path]),
            module="odoo_instance_sdk.operations.contracts",
            frozen=True,
            kw_only=True,
            forbid_unknown_fields=True,
        )
    fields: list[Any] = [("value", str | None, None), ("changed", bool, False)]
    return msgspec.defstruct(
        f"{_operation_stem(case.path)}Result",
        fields,
        module="odoo_instance_sdk.operations.contracts",
        frozen=True,
        kw_only=True,
        forbid_unknown_fields=True,
    )
