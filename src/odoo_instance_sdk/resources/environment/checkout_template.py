"""Opt-in PostgreSQL template clone for one local COPY checkout."""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import msgspec

from odoo_instance_sdk.exceptions import ConfigError, DatabaseAlreadyExistsError
from odoo_instance_sdk.internal.db_name import validate_db_name, validate_filestore_containment
from odoo_instance_sdk.internal.pg.builder import build_psql_specification
from odoo_instance_sdk.models import DatabaseCopyStrategy, EnvironmentDatabaseMode
from odoo_instance_sdk.resources.environment.checkout_planning import (
    CopyCleanupPlan,
    EnvironmentCheckoutOptions,
)
from odoo_instance_sdk.storage.backup_catalog import CopyJournalStage

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedCommand,
        PreparedStep,
        ProcessExecutor,
        ProcessResult,
        RunContext,
    )
    from odoo_instance_sdk.models.backup import DevelopmentEnvironment
    from odoo_instance_sdk.resources.environment.checkout_planning import _CheckoutPlan
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

_LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})
_NEUTRALIZE_SOURCE = (
    "from odoo.modules.neutralize import neutralize_database\nneutralize_database(env.cr)\n"
)
_PREFLIGHT_SQL = (
    "SELECT sessions || '|' || source_present || '|' || target_present || '|' || "
    "system_identifier FROM ("
    "SELECT (SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = :'source_db' AND pid <> pg_backend_pid()) AS sessions, "
    "(SELECT count(*) FROM pg_database WHERE datname = :'source_db') AS source_present, "
    "(SELECT count(*) FROM pg_database WHERE datname = :'target_db') AS target_present, "
    "(SELECT system_identifier::text FROM pg_control_system()) AS system_identifier) AS probe"
)
_CREATE_SQL = (
    "SELECT format('CREATE DATABASE %I WITH TEMPLATE %I', :'target_db', :'source_db')\n\\gexec"
)
_DROP_SQL = (
    "SELECT format('DROP DATABASE %I', :'target_db') "
    "WHERE EXISTS (SELECT 1 FROM pg_database WHERE datname = :'target_db')\n\\gexec"
)
_TERMINATE_SQL = (
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
    "WHERE datname = :'target_db' AND pid <> pg_backend_pid()"
)
_OWNED_DROP_SQL = _TERMINATE_SQL + ";\n" + _DROP_SQL
_MARKER_SQL = (
    "SELECT COALESCE((SELECT value FROM ir_config_parameter "
    "WHERE key = 'database.is_neutralized'), '')"
)


def reject_shared_filestore_mode(mode: str | None) -> None:
    """Refuse a production shared filestore request."""
    if mode == "shared":
        raise ConfigError("shared filestore mode is not available")


def validate_copy_strategy(options: EnvironmentCheckoutOptions, host: str | None) -> None:
    """Reject template checkout unless it is a local COPY without a backup source."""
    if not isinstance(options, EnvironmentCheckoutOptions):
        raise ConfigError("checkout options are invalid")
    if options.db_copy_strategy is DatabaseCopyStrategy.BACKUP:
        return
    if options.db_mode is not EnvironmentDatabaseMode.COPY:
        raise ConfigError("template copy strategy requires copy mode")
    if options.remote_name is not None or options.backup_id is not None:
        raise ConfigError("template copy strategy cannot use a remote or a catalog backup")
    if (host or "").strip().lower() not in _LOCAL_HOSTS:
        raise ConfigError("template copy strategy requires a local PostgreSQL host")


def database_copy_steps(plan: _CheckoutPlan) -> tuple[PreparedStep | PreparedAction, ...]:
    """Return the captured database steps for the selected COPY strategy."""
    if plan.options.db_copy_strategy is DatabaseCopyStrategy.TEMPLATE:
        return _template_steps(plan)
    return _backup_steps(plan)


def recorded_template_filestore(plan: _CheckoutPlan) -> str:
    """Return the exact contained filestore path a template row must record."""
    if plan.target_database is None or plan.source_database is None:
        raise ConfigError("template checkout requires source and target databases")
    data_dir = _data_dir(plan)
    validate_filestore_containment(data_dir, plan.source_database)
    return str(validate_filestore_containment(data_dir, plan.target_database))


def consume_template_preflight(
    context: RunContext[DevelopmentEnvironment], plan: _CheckoutPlan
) -> str:
    """Consume the captured preflight and return the source cluster identity."""
    sessions, source_present, target_present, identity = _parse_probe(
        _captured(context, "checkout.template.preflight")
    )
    if source_present != 1 or not identity:
        raise ConfigError("template source database is not on the connected local cluster")
    if target_present != 0:
        raise DatabaseAlreadyExistsError(f"Target database {plan.target_database!r} already exists")
    if sessions != 0:
        raise ConfigError("template source database has active sessions")
    return identity


def run_template_clone(
    context: RunContext[DevelopmentEnvironment],
    catalog: BackupCatalog,
    plan: _CheckoutPlan,
    *,
    cluster_identity: str,
) -> None:
    """Clone the source database, publish an isolated filestore, and require neutralization."""
    if plan.source_database is None or plan.target_database is None:
        raise ConfigError("template checkout requires source and target databases")
    _journal(catalog, plan, CopyJournalStage.PREPARED)
    created = _captured(context, "checkout.template.create")
    if created.returncode != 0:
        raise ConfigError("template database clone failed")
    _journal(catalog, plan, CopyJournalStage.RESTORE_PENDING)
    _verify_clone(context, cluster_identity)
    warning = _publish_filestore(context, plan)
    if warning is not None:
        catalog.add_environment_event(str(plan.env_id), "checkout", "started", message=warning)
    else:
        _record_owned_filestore(catalog, plan)
    neutralized = _captured(context, "checkout.template.neutralize")
    if neutralized.returncode != 0:
        raise ConfigError("template neutralization failed")
    marker = _text(_captured(context, "checkout.template.marker"))
    if marker.strip() != "true":
        raise ConfigError("template database neutralization marker is not true")
    _journal(catalog, plan, CopyJournalStage.RESTORED)


def publish_isolated_filestore(
    data_dir: Path, source_db: str, target_db: str, environment_id: UUID
) -> tuple[Path, str | None]:
    """Copy one filestore into a temporary sibling and publish it with rename."""
    validate_db_name(source_db)
    validate_db_name(target_db)
    source = validate_filestore_containment(data_dir, source_db)
    target = validate_filestore_containment(data_dir, target_db)
    if source.is_symlink() or target.is_symlink():
        raise ConfigError("template filestore path escapes through a symlink")
    if not source.exists():
        return target, f"source filestore {source_db} is absent; nothing was copied"
    if target.exists():
        raise ConfigError("template target filestore already exists")
    _reject_symlinks(source)
    temporary = data_dir / "filestore" / f".odcli-{environment_id}"
    if temporary.exists() or temporary.is_symlink():
        raise ConfigError("template filestore temporary directory already exists")
    try:
        shutil.copytree(source, temporary, symlinks=False, copy_function=_copy_regular_file)
        os.rename(temporary, target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target, None


def remove_recorded_filestore(recorded: str, data_dir: Path, database: str) -> None:
    """Delete only the exact filestore path recorded for this template operation."""
    expected = validate_filestore_containment(data_dir, database)
    if os.path.abspath(recorded) != os.path.abspath(expected):
        raise ConfigError("recorded filestore path is not the owned template target")
    if expected.is_symlink():
        raise ConfigError("recorded filestore path is a symlink")
    if expected.exists():
        shutil.rmtree(expected)


def rollback_template_checkout(
    catalog: BackupCatalog,
    environment_id: UUID,
    *,
    context: RunContext[DevelopmentEnvironment] | None,
) -> bool | None:
    """Drop only a template target this checkout created.

    Returns:
        None when the row is not a template clone. True when the owned database
        or its recorded filestore could not be removed. False when cleanup finished.
    """
    row = catalog.get_environment(str(environment_id))
    if row is None or _text_cell(row, "db_copy_strategy") != DatabaseCopyStrategy.TEMPLATE.value:
        return None
    journal = catalog.get_copy_journal(str(environment_id))
    stage_value = None if journal is None else _text_cell(journal, "stage")
    if stage_value not in {CopyJournalStage.RESTORE_PENDING.value, CopyJournalStage.RESTORED.value}:
        return False
    if not _drop_created_database(context):
        return True
    recorded = _text_cell(row, "target_filestore_path")
    database = _text_cell(row, "target_db_name")
    if not recorded or not database:
        return False
    try:
        data_dir = Path(recorded).parent.parent
        remove_recorded_filestore(recorded, data_dir, database)
        temporary = data_dir / "filestore" / f".odcli-{environment_id}"
        if temporary.is_dir() and not temporary.is_symlink():
            shutil.rmtree(temporary)
    except (ConfigError, OSError):
        return True
    return False


def _backup_steps(plan: _CheckoutPlan) -> tuple[PreparedStep | PreparedAction, ...]:
    from odoo_instance_sdk.internal.proc import PreparedAction
    from odoo_instance_sdk.resources.environment.checkout_filestore import copy_attachment_probe

    steps: list[PreparedStep | PreparedAction] = []
    if plan.options.remote_name is not None:
        steps.extend(
            (
                PreparedAction(
                    step_id="database.backup.wait",
                    action="database.backup.wait",
                    description="Wait for named remote backup response",
                    mutating=True,
                ),
                PreparedAction(
                    step_id="database.backup.transfer",
                    action="database.backup.transfer",
                    description="Transfer named remote backup bytes",
                    mutating=True,
                ),
            )
        )
    import odoo_instance_sdk.resources.database.backup_restore_parts.backup as restore

    steps.extend(
        restore._protect_restore_oid_probe(
            _psql(
                plan,
                step_id=step_id,
                database="postgres",
                args=("-c", restore._restore_probe_query(plan.target_database or "", step_id)),
                trusted=("-t", "-A"),
            ),
            step_id,
        )
        for step_id in ("database.restore.exists-before", "database.restore.exists-after")
    )
    steps.append(copy_attachment_probe(plan))
    return tuple(steps)


def _template_steps(plan: _CheckoutPlan) -> tuple[PreparedStep | PreparedAction, ...]:
    from odoo_instance_sdk.internal.proc import PreparedAction

    if plan.source_database is None or plan.target_database is None:
        raise ConfigError("template checkout requires source and target databases")
    recorded_template_filestore(plan)
    variables = (
        "-v",
        f"source_db={plan.source_database}",
        "-v",
        f"target_db={plan.target_database}",
    )
    return (
        _psql(
            plan,
            step_id="checkout.template.preflight",
            database="postgres",
            args=(*variables, "-c", _PREFLIGHT_SQL),
            trusted=("-t", "-A", "-v", "ON_ERROR_STOP=1"),
            read_only=True,
        ),
        _psql(
            plan,
            step_id="checkout.template.create",
            database="postgres",
            args=variables,
            stdin=_CREATE_SQL.encode(),
            trusted=("-v", "ON_ERROR_STOP=1"),
            read_only=False,
            mutating=True,
            timeout=None,
        ),
        _psql(
            plan,
            step_id="checkout.template.verify",
            database="postgres",
            args=(*variables, "-c", _PREFLIGHT_SQL),
            trusted=("-t", "-A", "-v", "ON_ERROR_STOP=1"),
            read_only=True,
        ),
        _psql(
            plan,
            step_id="checkout.template.connect",
            database=plan.target_database,
            args=("-c", "SELECT 1"),
            trusted=("-t", "-A", "-v", "ON_ERROR_STOP=1"),
            read_only=True,
        ),
        PreparedAction(
            step_id="checkout.template.filestore",
            action="publish_template_filestore",
            description="Publish an isolated filestore copy for the template database",
            details={"database": plan.target_database},
            mutating=True,
        ),
        _neutralize_step(plan),
        _psql(
            plan,
            step_id="checkout.template.marker",
            database=plan.target_database,
            args=("-c", _MARKER_SQL),
            trusted=("-t", "-A", "-v", "ON_ERROR_STOP=1"),
            read_only=True,
        ),
        _psql(
            plan,
            step_id="checkout.template.drop",
            database="postgres",
            args=("-v", f"target_db={plan.target_database}"),
            stdin=_OWNED_DROP_SQL.encode(),
            trusted=("-v", "ON_ERROR_STOP=1"),
            read_only=False,
            mutating=True,
            timeout=None,
        ),
    )


def _neutralize_step(plan: _CheckoutPlan) -> PreparedStep:
    from odoo_instance_sdk.models.config import StartConfig
    from odoo_instance_sdk.resources.instance.auxiliary_restore import _build_shell_script_step

    if plan.source_config is None:
        raise ConfigError("template checkout requires a source config")
    config = StartConfig.from_odoo_config(plan.source_config)
    config = msgspec.structs.replace(
        config,
        db_name=plan.target_database,
        config_path=str(plan.generated_config),
        data_dir=str(_data_dir(plan)),
    )
    prefix = (plan.python_path, plan.odoo_bin) if plan.python_path else (plan.odoo_bin,)
    step, _snapshot, _secret_path, _secrets = _build_shell_script_step(
        config,
        executable_prefix=prefix,
        default_cwd=Path(plan.runtime_cwd),
        source=_NEUTRALIZE_SOURCE,
        commit=True,
        nonce=plan.env_id.hex,
    )
    return replace(step, step_id="checkout.template.neutralize")


def _psql(
    plan: _CheckoutPlan | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    password: str | None = None,
    step_id: str,
    database: str,
    args: tuple[str, ...] = (),
    stdin: bytes | None = None,
    trusted: tuple[str, ...] = (),
    read_only: bool = True,
    mutating: bool = False,
    timeout: float | None = 30.0,
) -> PreparedStep:
    if plan is not None:
        host = plan.config_values.get("db_host")
        port = _port(plan)
        user = plan.config_values.get("db_user")
        password = plan.config_values.get("db_password")
    return build_psql_specification(
        step_id=step_id,
        host=host,
        port=port or 5432,
        user=user,
        password=password,
        database=database,
        args=args,
        stdin=stdin,
        timeout=timeout,
        _trusted_args=trusted,
        _require_binary=False,
        _read_only=read_only,
        _mutating=mutating,
    ).prepared_step


def _publish_filestore(
    context: RunContext[DevelopmentEnvironment], plan: _CheckoutPlan
) -> str | None:
    context.action("checkout.template.filestore")
    if plan.source_database is None or plan.target_database is None:
        raise ConfigError("template checkout requires source and target databases")
    _path, warning = publish_isolated_filestore(
        _data_dir(plan), plan.source_database, plan.target_database, plan.env_id
    )
    context.complete_action("checkout.template.filestore")
    return warning


def _verify_clone(context: RunContext[DevelopmentEnvironment], cluster_identity: str) -> None:
    _sessions, source_present, target_present, identity = _parse_probe(
        _captured(context, "checkout.template.verify")
    )
    connected = _captured(context, "checkout.template.connect")
    if (
        source_present != 1
        or target_present != 1
        or identity != cluster_identity
        or connected.returncode != 0
        or _text(connected).strip() != "1"
    ):
        raise ConfigError("template database clone postcondition failed")


def _journal(catalog: BackupCatalog, plan: _CheckoutPlan, stage: CopyJournalStage) -> None:
    if plan.target_database is None:
        raise ConfigError("template checkout requires a target database")
    catalog.upsert_copy_journal(
        str(plan.env_id),
        target_database=plan.target_database,
        db_host=plan.config_values.get("db_host"),
        db_port=_port(plan),
        db_user=plan.config_values.get("db_user"),
        backup_id=None,
        stage=stage,
        backup_ownership="unknown",
    )


def _drop_created_database(context: RunContext[DevelopmentEnvironment] | None) -> bool:
    if context is None or not context.planned("checkout.template.drop"):
        return False
    if context.consumed("checkout.template.drop"):
        return False
    dropped = _captured(context, "checkout.template.drop")
    return dropped.returncode == 0


def _record_owned_filestore(catalog: BackupCatalog, plan: _CheckoutPlan) -> None:
    row = catalog.get_environment(str(plan.env_id))
    recorded = recorded_template_filestore(plan)
    if row is None or _text_cell(row, "state") != "creating":
        raise ConfigError("template filestore can only be recorded for a creating environment")
    current = _text_cell(row, "target_filestore_path")
    if current not in (None, recorded):
        raise ConfigError("template filestore path does not match the plan")
    catalog.update_environment(str(plan.env_id), {"target_filestore_path": recorded})


def _parse_probe(result: ProcessResult) -> tuple[int, int, int, str]:
    if result.returncode != 0:
        raise ConfigError("template cluster probe failed")
    parts = _text(result).strip().split("|")
    if len(parts) != 4 or any(not parts[index].isdigit() for index in range(3)) or not parts[3]:
        raise ConfigError("template cluster probe returned an unexpected result")
    return int(parts[0]), int(parts[1]), int(parts[2]), parts[3]


def _captured(context: RunContext[DevelopmentEnvironment], step_id: str) -> ProcessResult:
    from odoo_instance_sdk.internal.proc import ProcessResult

    result = context.process(step_id)
    if not isinstance(result, ProcessResult):
        raise ConfigError(f"{step_id} returned an invalid result")
    return result


def _text(result: ProcessResult) -> str:
    stdout = result.stdout
    if isinstance(stdout, bytes):
        return stdout.decode()
    return str(stdout or "")


def _data_dir(plan: _CheckoutPlan) -> Path:
    if plan.data_dir is not None:
        return plan.data_dir
    raw = plan.config_values.get("data_dir")
    if not raw:
        raise ConfigError("template checkout requires a data_dir")
    return Path(raw).expanduser().absolute()


def _port(plan: _CheckoutPlan) -> int:
    raw = plan.config_values.get("db_port")
    try:
        return int(raw) if raw else 5432
    except ValueError:
        return 5432


def _text_cell(row: sqlite3.Row, key: str) -> str | None:
    value = row[key]
    if value is None:
        return None
    return str(value)


def _reject_symlinks(root: Path) -> None:
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        for name in (*dirnames, *filenames):
            if (Path(directory) / name).is_symlink():
                raise ConfigError("template filestore contains a symlink")


def template_cleanup_plan(
    env: DevelopmentEnvironment, instance: OdooInstance, journal: sqlite3.Row
) -> CopyCleanupPlan:
    """Return owned template cleanup, or the backup error when strategy is not template."""
    from odoo_instance_sdk.exceptions import EnvironmentConflictError

    if env.db_copy_strategy is not DatabaseCopyStrategy.TEMPLATE:
        raise EnvironmentConflictError("copy_backup_missing", "journal backup is absent")
    database = str(journal["target_database"])
    recorded = env.target_filestore_path
    rollback = None
    if recorded:
        expected = validate_filestore_containment(Path(recorded).parent.parent, database)
        if os.path.abspath(recorded) != os.path.abspath(expected) or Path(recorded).is_symlink():
            raise EnvironmentConflictError(
                "unsafe_environment_path", "template filestore path is not owned"
            )
        rollback = expected
    return CopyCleanupPlan(
        target_database=database,
        backup_id=None,
        instance=instance,
        backup=None,
        stage=CopyJournalStage.RESTORED,
        backup_ownership="unknown",
        rollback_filestore=rollback,
    )


def template_database_drop_command(
    env: DevelopmentEnvironment,
    *,
    executor: ProcessExecutor | None,
    force_connections: bool,
    step_prefix: str,
) -> PreparedCommand[None] | None:
    """Capture a maintenance DROP for the recorded template database only."""
    from odoo_instance_sdk.execution import Command, ExecutionPlan
    from odoo_instance_sdk.internal.odoo_config import get_admin_passwd, parse_odoo_config
    from odoo_instance_sdk.internal.proc import ProcessResult, SubprocessExecutor

    if env.db_copy_strategy is not DatabaseCopyStrategy.TEMPLATE or env.target_db_name is None:
        return None
    config_path = Path(env.generated_config_path)
    if not config_path.is_file():
        return None
    values = parse_odoo_config(config_path)
    if get_admin_passwd(values) is None:
        return None
    raw_port = values.get("db_port")
    try:
        port = int(raw_port) if raw_port else 5432
    except ValueError:
        port = 5432
    prefix = f"{step_prefix}checkout.template.remove."
    steps: list[PreparedStep] = []
    if force_connections:
        steps.append(
            _psql(
                host=values.get("db_host"),
                port=port,
                user=values.get("db_user"),
                password=values.get("db_password"),
                step_id=f"{prefix}terminate",
                database="postgres",
                args=("-v", f"target_db={env.target_db_name}", "-c", _TERMINATE_SQL),
                trusted=("-v", "ON_ERROR_STOP=1"),
                read_only=False,
                mutating=True,
            )
        )
    drop_id = f"{prefix}drop"
    steps.append(
        _psql(
            host=values.get("db_host"),
            port=port,
            user=values.get("db_user"),
            password=values.get("db_password"),
            step_id=drop_id,
            database="postgres",
            args=("-v", f"target_db={env.target_db_name}"),
            stdin=_DROP_SQL.encode(),
            trusted=("-v", "ON_ERROR_STOP=1"),
            read_only=False,
            mutating=True,
            timeout=None,
        )
    )

    def run(context: RunContext[None]) -> None:
        if force_connections:
            context.process(f"{prefix}terminate")
        dropped = context.process(drop_id)
        if not isinstance(dropped, ProcessResult) or dropped.returncode != 0:
            raise ConfigError("template database drop failed")

    selected_executor = executor if executor is not None else SubprocessExecutor()
    command: Command[None] = Command.create(
        ExecutionPlan(steps=tuple(step.public_projection() for step in steps)),
        run,
        steps,
        executor=selected_executor,
    )
    return command._prepared()


def _copy_regular_file(source: str, destination: str) -> str:
    source_stat = os.lstat(source)
    if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISREG(source_stat.st_mode):
        raise ConfigError("template filestore entry is not a regular file")
    shutil.copyfile(source, destination)
    return destination
