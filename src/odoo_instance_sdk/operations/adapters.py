"""Typed, lazy adapters for the built-in operation contract bindings."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from odoo_instance_sdk.execution import JsonValue

if TYPE_CHECKING:
    from odoo_instance_sdk.commands.context import OperationContext
    from odoo_instance_sdk.resources.instance import OdooInstance

    from .contracts import FactoryResult, PublicLeafCase


type OperationValues = dict[str, JsonValue]
type BuiltinAdapter = Callable[[PublicLeafCase, OperationValues, OperationContext], FactoryResult]


def _instance(context: OperationContext) -> OdooInstance:
    resolved = context.resolved
    if resolved is None:
        raise RuntimeError("operation context has no resolved runtime")
    return resolved.instance


def _project(context: OperationContext) -> Path | str:
    return context.project_selector or context.cwd


def _target_project(values: OperationValues, context: OperationContext) -> Path:
    raw_project = values.get("project_path")
    if raw_project is None:
        return Path(_project(context))
    if not isinstance(raw_project, str):
        raise TypeError("project_path must be a string or null")
    return Path(raw_project)


def _text(values: OperationValues, name: str) -> str:
    return str(values[name])


def _text_or_empty(values: OperationValues, name: str) -> str:
    return str(values[name] or "")


def _optional_text(values: OperationValues, name: str) -> str | None:
    return cast("str | None", values.get(name))


def _optional_path(values: OperationValues, name: str) -> Path | None:
    raw = values.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise TypeError(f"{name} must be a path string or null")
    return Path(raw)


def _optional_path_or_text(values: OperationValues, name: str) -> str | Path | None:
    return cast("str | Path | None", values.get(name))


def _optional_value_text(value: JsonValue | None) -> str | None:
    return cast("str | None", value)


def _optional_int(values: OperationValues, name: str) -> int | None:
    return cast("int | None", values.get(name))


def _optional_bool(values: OperationValues, name: str) -> bool | None:
    return cast("bool | None", values.get(name))


def _number(values: OperationValues, name: str, default: float) -> float:
    return cast("float", values.get(name, default))


def _optional_number(values: OperationValues, name: str) -> float | None:
    return cast("float | None", values.get(name))


def _integer(values: OperationValues, name: str, default: int) -> int:
    return cast("int", values.get(name, default))


def _texts(
    values: OperationValues, name: str, default: list[JsonValue] | tuple[()] = ()
) -> tuple[str, ...]:
    raw = values.get(name, default)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item) for item in raw)


def _list_remote_sources(
    _case: PublicLeafCase, _values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.project_init import list_remote_sources

    return cast("FactoryResult", list_remote_sources(_project(context)))


def _remote_source(
    case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.project import RemoteSourceConfig
    from odoo_instance_sdk.project_init import (
        configure_remote_source_command,
        remove_remote_source_command,
    )

    project = _project(context)
    if case.sdk_primitive == "remove_remote_source_command":
        return cast("FactoryResult", remove_remote_source_command(project, _text(values, "name")))
    source = RemoteSourceConfig(
        name=_text(values, "name"),
        base_url=_text_or_empty(values, "base_url"),
        database=_text_or_empty(values, "database"),
        git_branch=_text_or_empty(values, "git_branch"),
    )
    return cast(
        "FactoryResult",
        configure_remote_source_command(project, source, replace=case.path == ("remote", "update")),
    )


def _export_translations(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.internal.automation import export_translations_command

    return export_translations_command(
        _instance(context),
        _texts(values, "modules"),
        _texts(values, "languages"),
        worktree_root=context.cwd,
    )


def _environment_checkout(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.resources.environment import (
        EnvironmentCheckoutOptions,
        EnvironmentDatabaseMode,
        EnvironmentResource,
    )

    options = EnvironmentCheckoutOptions(
        base_ref=_optional_text(values, "base_ref"),
        config_path=_optional_path(values, "config_path"),
        db_mode=EnvironmentDatabaseMode(str(values.get("db_mode", "shared"))),
        source_database=_optional_text(values, "source_database"),
        target_database=_optional_text(values, "target_database"),
        odoo_bin=_optional_path(values, "odoo_bin"),
        python=_optional_path_or_text(values, "python"),
        create_venv=bool(values.get("create_venv", False)),
        http_port=_optional_int(values, "http_port"),
        hash_lock=_optional_path_or_text(values, "hash_lock"),
        hash_lock_sha256=_optional_text(values, "hash_lock_sha256"),
        remote_name=_optional_text(values, "remote_name"),
        backup_id=_optional_text(values, "backup_id"),
    )
    return cast(
        "FactoryResult",
        EnvironmentResource(_client=_instance(context)._client).checkout_command(
            Path(_project(context)), str(values.get("base_ref") or "HEAD"), options=options
        ),
    )


def _environment_sync(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.resources.environment import EnvironmentResource

    selector = _optional_value_text(values.pop("environment", None)) or context.environment_selector
    if selector is None:
        raise ValueError("environment selector is required")
    return cast(
        "FactoryResult",
        EnvironmentResource(_client=_instance(context)._client).sync_python_command(
            selector,
            upgrade=bool(values.get("upgrade", False)),
            hash_lock=_optional_path_or_text(values, "hash_lock"),
            hash_lock_sha256=_optional_text(values, "hash_lock_sha256"),
        ),
    )


def _environment_remove(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.resources.environment import EnvironmentResource

    selectors = _texts(values, "environments")
    selector = selectors[0] if selectors else context.environment_selector
    if selector is None:
        raise ValueError("environment selector is required")
    return cast(
        "FactoryResult",
        EnvironmentResource(_client=_instance(context)._client).remove_command(
            selector, force_connections=bool(values.get("force_connections", False))
        ),
    )


def _eval_expression(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.internal.automation import eval_expression_command

    return eval_expression_command(
        _instance(context), _text(values, "expression"), commit=bool(values.get("commit", False))
    )


def _exec_script(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.internal.automation import exec_script_command

    return exec_script_command(
        _instance(context),
        _text(values, "script"),
        _texts(values, "script_args", []),
        commit=bool(values.get("commit", False)),
    )


def _list_modules(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.internal.automation import list_modules_command

    return list_modules_command(
        _instance(context), _texts(values, "modules", []), state=_optional_text(values, "state")
    )


def _run_odoo_tests(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.internal.automation import run_odoo_tests_command
    from odoo_instance_sdk.models.command import OdooTestSpec

    return run_odoo_tests_command(
        _instance(context),
        OdooTestSpec(
            modules=_texts(values, "modules", []),
            test_tags=str(values.get("test_tags", "/")),
            reload_tests=bool(values.get("reload_tests", False)),
            allow_empty=bool(values.get("allow_empty", False)),
        ),
        http_interface=context.runtime.http_interface,
        http_port=context.runtime.http_port,
    )


def _verify_deps(
    _case: PublicLeafCase, _values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.resources.deps import verify_deps_command

    return verify_deps_command(
        recorded_python=context.runtime.python_path, worktree_root=context.runtime.root
    )


def _init_project(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
    from odoo_instance_sdk.project_init import init_project_command

    project = _target_project(values, context)
    postgres = PostgresProjectConfig(
        mode=cast("Literal['external', 'compose']", str(values.get("postgres_mode", "external"))),
        image=_optional_text(values, "postgres_image"),
        port=_optional_int(values, "postgres_port"),
        user=_optional_text(values, "postgres_user"),
    )
    config = ProjectConfig(
        repository_root=project,
        odoo_bin=Path(_text(values, "odoo_bin")) if values.get("odoo_bin") else None,
        python=_optional_text(values, "python"),
        source_config=(
            Path(_text(values, "source_config")) if values.get("source_config") else None
        ),
        default_source_database=_optional_text(values, "default_source_database"),
        preferred_http_port=_optional_int(values, "preferred_http_port"),
        requirements=_texts(values, "requirements"),
        default_run_args=_texts(values, "run_args"),
        runtime_cwd=Path(_text(values, "runtime_cwd")) if values.get("runtime_cwd") else None,
        postgres=postgres,
    )
    return cast(
        "FactoryResult",
        init_project_command(
            project,
            config,
            postgres_allocated=bool(values.get("postgres_allocated", False)),
            local_config=bool(values.get("local_config", False)),
            postgres_image=_optional_text(values, "postgres_image"),
            allow_partial=bool(values.get("allow_partial", False)),
            no_input=bool(values.get("no_input", False)),
            dry_run=bool(values.get("dry_run", False)),
        ),
    )


def _doctor(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.internal.doctor import run_doctor

    return cast(
        "FactoryResult",
        run_doctor(
            _instance(context)._client,
            Path(_project(context)),
            remote_name=_optional_text(values, "remote_name"),
        ),
    )


def _bug_report_init(
    _case: PublicLeafCase, values: OperationValues, _context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.bug_report import bug_report_init_command
    from odoo_instance_sdk.models.bug_report import BugReportKind

    return cast(
        "FactoryResult",
        bug_report_init_command(
            title=_text(values, "title"),
            kind=cast("BugReportKind", str(values.get("kind", "bug"))),
        ),
    )


def _bug_report_submit(
    _case: PublicLeafCase, values: OperationValues, _context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.bug_report import bug_report_submit_command

    return cast(
        "FactoryResult",
        bug_report_submit_command(
            _text(values, "report_id"), dry_run=bool(values.get("dry_run", False))
        ),
    )


def _update(
    _case: PublicLeafCase, values: OperationValues, _context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.internal.self_update import update_command

    return cast("FactoryResult", update_command(dry_run=bool(values.get("dry_run", False))))


def _stop_runtime(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    return _instance(context).stop_runtime_command(timeout=_number(values, "timeout", 10.0))


def _checkout_inventory(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

    return EnvironmentMonitor().checkout_inventory_command(
        project_id=context.project_selector, include_removed=bool(values.get("all_envs", False))
    )


def _backup_retention(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.resources.backup import BackupResource

    return BackupResource(_client=_instance(context)._client).set_retention_command(
        retention_days=_optional_int(values, "retention_days"),
        auto_prune=_optional_bool(values, "auto_prune"),
    )


def _backup_pinned(
    case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.resources.backup import BackupResource

    return BackupResource(_client=_instance(context)._client).set_pinned_command(
        _text(values, "backup_id"), case.path == ("backup", "pin")
    )


def _backup_prune(
    _case: PublicLeafCase, _values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.resources.backup import BackupResource

    return BackupResource(_client=_instance(context)._client).prune_command(_project(context))


def _backup_inspect(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.resources.backup import BackupResource

    return BackupResource(_client=_instance(context)._client).inspect_command(
        _text(values, "backup_id")
    )


def _backup_validate_or_delete(
    case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.resources.backup import BackupResource

    instance = _instance(context)
    projection = instance._client.get_catalog()._resolve_backup_projection(
        _text(values, "backup_id")
    )
    resource = BackupResource(_client=instance._client)
    if case.sdk_primitive == "BackupResource.validate_command":
        return resource.validate_command(projection.backup)
    return resource.delete_command(projection.backup)


def _refresh_database(
    case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.models import DatabaseRefreshOptions
    from odoo_instance_sdk.resources.environment import EnvironmentResource

    options = DatabaseRefreshOptions(
        restore=case.path == ("db", "restore"),
        source_branch=_optional_text(values, "source_branch"),
        remote_name=_optional_text(values, "remote_name"),
        reset_admin_password=bool(values.get("reset_admin_password", False)),
    )
    return EnvironmentResource(_client=_instance(context)._client).refresh_database_command(
        Path(_project(context)),
        options=options,
        target_database=_optional_text(values, "target_database"),
    )


def _list_database_inventory(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    return _instance(context).databases.list_inventory_command(
        context.runtime.root, tracked=bool(values.get("tracked", False))
    )


def _reset_admin_password(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    return _instance(context).databases.reset_admin_password_command(
        admin_password=_text(values, "admin_password"),
        provenance=str(values.get("provenance", "environment")),
    )


def _database_inspection(
    case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    instance = _instance(context)
    database = _text(values, "database")
    if case.sdk_primitive == "DatabaseResource.locks_command":
        return instance.databases.locks_command(
            database, top=_integer(values, "top", 20), timeout=_number(values, "timeout", 30.0)
        )
    if case.sdk_primitive == "DatabaseResource.stats_command":
        return instance.databases.stats_command(
            database, top=_integer(values, "top", 20), timeout=_number(values, "timeout", 30.0)
        )
    if case.sdk_primitive == "DatabaseResource.bloat_command":
        return instance.databases.bloat_command(
            database,
            top=_integer(values, "top", 20),
            exact_max_scan_mb=_integer(values, "exact_max_scan_mb", 64),
            timeout=_number(values, "timeout", 30.0),
        )
    return instance.databases.init_monitoring_command(
        database, timeout=_number(values, "timeout", 30.0)
    )


def _module_update(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    return _instance(context).modules.update_command(_texts(values, "modules"))


def _module_info(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    return _instance(context).modules.info(_optional_text(values, "module"))


def _module_where(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    return str(_instance(context).modules.where(_optional_text(values, "module")))


def _module_dependencies(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    return _instance(context).modules.dependencies(_optional_text(values, "module"))


def _module_install_order(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    return _instance(context).modules.install_order_command(_texts(values, "modules"))


def _postgres_cluster(
    case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    cluster = _instance(context)._postgres_cluster
    if cluster is None:
        raise RuntimeError("PostgreSQL cluster is unavailable")
    if case.sdk_primitive == "PostgresCluster.approve_image_command":
        return cluster.approve_image_command(
            _text(values, "image_digest"), timeout=_optional_number(values, "timeout")
        )
    if case.sdk_primitive == "PostgresCluster.status_command":
        return cluster.status_command()
    if case.sdk_primitive == "PostgresCluster.ensure_running_command":
        return cluster.ensure_running_command(_number(values, "wait_timeout", 60.0))
    return cluster.stop_command(_number(values, "timeout", 30.0))


def _git_commit(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    return _instance(context).git.commit_command(
        description=_text(values, "description"),
        ticket=_optional_text(values, "ticket"),
        tag=_optional_text(values, "tag"),
    )


def _git_check(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    return _instance(context).git.check_command(base=_optional_text(values, "base"))


def _git_absorb(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    return _instance(context).git.absorb_command(
        base=_optional_text(values, "base"),
        dry_run=bool(values.get("dry_run", False)),
        and_rebase=bool(values.get("and_rebase", False)),
    )


def _git_sync(
    _case: PublicLeafCase, values: OperationValues, context: OperationContext
) -> FactoryResult:
    return _instance(context).git.sync_command(
        base=_optional_text(values, "base"), push=bool(values.get("push", False))
    )


def _monitor_processes(
    _case: PublicLeafCase, _values: OperationValues, context: OperationContext
) -> FactoryResult:
    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

    return EnvironmentMonitor().processes_command(project_id=context.project_selector)


BUILTIN_ADAPTERS: dict[str, BuiltinAdapter] = {
    "init_project_command": _init_project,
    "run_doctor": _doctor,
    "list_remote_sources": _list_remote_sources,
    "configure_remote_source_command": _remote_source,
    "remove_remote_source_command": _remote_source,
    "export_translations_command": _export_translations,
    "EnvironmentResource.checkout_command": _environment_checkout,
    "EnvironmentResource.sync_python_command": _environment_sync,
    "EnvironmentResource.remove_command": _environment_remove,
    "eval_expression_command": _eval_expression,
    "exec_script_command": _exec_script,
    "list_modules_command": _list_modules,
    "run_odoo_tests_command": _run_odoo_tests,
    "verify_deps_command": _verify_deps,
    "OdooInstance.stop_runtime_command": _stop_runtime,
    "EnvironmentResource.checkout_inventory_command": _checkout_inventory,
    "BackupResource.set_retention_command": _backup_retention,
    "BackupResource.set_pinned_command": _backup_pinned,
    "BackupResource.prune_command": _backup_prune,
    "BackupResource.inspect_command": _backup_inspect,
    "BackupResource.validate_command": _backup_validate_or_delete,
    "BackupResource.delete_command": _backup_validate_or_delete,
    "EnvironmentResource.refresh_database_command": _refresh_database,
    "DatabaseResource.list_inventory_command": _list_database_inventory,
    "DatabaseResource.reset_admin_password_command": _reset_admin_password,
    "DatabaseResource.locks_command": _database_inspection,
    "DatabaseResource.stats_command": _database_inspection,
    "DatabaseResource.bloat_command": _database_inspection,
    "DatabaseResource.init_monitoring_command": _database_inspection,
    "ModuleResource.update_command": _module_update,
    "ModuleResource.info": _module_info,
    "ModuleResource.where": _module_where,
    "ModuleResource.dependencies": _module_dependencies,
    "ModuleResource.install_order_command": _module_install_order,
    "PostgresCluster.approve_image_command": _postgres_cluster,
    "PostgresCluster.status_command": _postgres_cluster,
    "PostgresCluster.ensure_running_command": _postgres_cluster,
    "PostgresCluster.stop_command": _postgres_cluster,
    "GitResource.commit_command": _git_commit,
    "GitResource.check_command": _git_check,
    "GitResource.absorb_command": _git_absorb,
    "GitResource.sync_command": _git_sync,
    "EnvironmentMonitor.processes_command": _monitor_processes,
    "bug_report_init_command": _bug_report_init,
    "bug_report_submit_command": _bug_report_submit,
    "update_command": _update,
}


__all__ = ["BUILTIN_ADAPTERS", "BuiltinAdapter"]
