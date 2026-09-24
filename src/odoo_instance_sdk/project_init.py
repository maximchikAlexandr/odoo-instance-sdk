"""Public project initialization primitives."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.internal.project_init import (
    evaluate_init_completeness,
    fetch_remote_database_names_for_init,
    manifest_dict,
    merge_preserved_test_instance,
    read_project_env,
    register_initialized_project,
    remote_database_names_action,
    validate_generated_config_target,
    write_project_env,
    write_project_generated_config,
)
from odoo_instance_sdk.internal.project_manifest import write_manifest
from odoo_instance_sdk.project import ProjectConfig, TestInstanceProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep, RunContext
    from odoo_instance_sdk.resources.postgres import PostgresCluster


@dataclass(frozen=True, slots=True)
class _ComposeFollowup:
    cluster: PostgresCluster
    temporary_path: Path
    steps: tuple[PreparedStep | PreparedAction, ...]


@dataclass(frozen=True, slots=True)
class _BootstrapFollowup:
    spawn_step: PreparedStep
    probe_step: PreparedStep
    ready_step: PreparedStep
    verify_action: PreparedAction

    @property
    def steps(self) -> tuple[PreparedStep | PreparedAction, ...]:
        return (self.spawn_step, self.probe_step, self.ready_step, self.verify_action)


def _execute_remote_database_names_phase(
    context: RunContext[dict[str, JsonValue]],
    *,
    remote_names_step: PreparedAction | None,
    effective_config: ProjectConfig,
    resolved_existing: TestInstanceProjectConfig | None,
    remote_database_names: list[str] | None,
) -> list[str] | None:
    if remote_names_step is None:
        return remote_database_names
    context.action(remote_names_step.step_id)
    test_instance = effective_config.test_instance or resolved_existing
    resolved_names = (
        fetch_remote_database_names_for_init(test_instance)
        if test_instance is not None
        else remote_database_names
    )
    context.complete_action(remote_names_step.step_id)
    return resolved_names


def _execute_manifest_phase(
    context: RunContext[dict[str, JsonValue]],
    *,
    project_path: Path,
    effective_config: ProjectConfig,
    postgres_allocated: bool,
    local_config: bool,
    postgres_image: str | None,
    resolved_existing: TestInstanceProjectConfig | None,
    allow_partial: bool,
    no_input: bool,
    confirm_partial: Callable[[list[str], dict[str, str]], None] | None,
    remote_database_names: list[str] | None,
) -> dict[str, JsonValue]:
    from odoo_instance_sdk.exceptions import InstanceConfigurationError

    missing, details = evaluate_init_completeness(
        project_root=project_path.resolve(),
        config=effective_config,
        local_config=local_config,
        postgres_image=postgres_image,
        existing_test_instance=resolved_existing,
        dry_run=False,
        remote_database_names=remote_database_names,
    )
    if missing and confirm_partial is not None:
        confirm_partial(missing, details)
    if missing and no_input and not allow_partial:
        raise InstanceConfigurationError(
            f"init_incomplete: missing capabilities {missing} ({details})"
        )
    context.action("init")
    result = init_project(
        project_path,
        effective_config,
        postgres_allocated=postgres_allocated,
        local_config=local_config,
        postgres_image=postgres_image,
        existing_test_instance=resolved_existing,
        allow_partial=allow_partial,
        no_input=no_input,
        dry_run=False,
        remote_database_names=remote_database_names,
        missing=missing,
        details=details,
    )
    context.complete_action("init")
    return result


def _execute_compose_followup_phase(
    context: RunContext[dict[str, JsonValue]], followup: _ComposeFollowup
) -> None:
    followup.cluster.execute_ensure_running_plan(
        context,
        timeout=60.0,
        temporary_path=followup.temporary_path,
        steps=followup.steps,
    )


def _execute_bootstrap_followup_phase(
    context: RunContext[dict[str, JsonValue]], followup: _BootstrapFollowup
) -> None:
    from odoo_instance_sdk.internal.dbprep.bootstrap import run_bootstrap_tmp

    context.action(followup.verify_action.step_id)
    run_bootstrap_tmp(context, followup.spawn_step, followup.probe_step, followup.ready_step)
    context.complete_action(followup.verify_action.step_id)
    for step in followup.steps:
        if context.planned(step.step_id) and not context.consumed(step.step_id):
            context.skip(step.step_id)


def init_project_command(
    project_path: Path,
    config: ProjectConfig,
    *,
    postgres_allocated: bool,
    local_config: bool = False,
    postgres_image: str | None = None,
    existing_test_instance: TestInstanceProjectConfig | None = None,
    allow_partial: bool = False,
    no_input: bool = False,
    dry_run: bool = False,
    remote_database_names: list[str] | None = None,
    confirm_partial: Callable[[list[str], dict[str, str]], None] | None = None,
) -> Command[dict[str, JsonValue]]:
    """Capture one immutable init command for preview and execution."""
    from odoo_instance_sdk.execution import Command, ExecutionPlan
    from odoo_instance_sdk.internal.generated_config import project_generated_config_path
    from odoo_instance_sdk.internal.proc import PreparedAction, SubprocessExecutor, prepared_command

    resolved_existing = (
        existing_test_instance
        if isinstance(existing_test_instance, TestInstanceProjectConfig)
        else None
    )
    effective_config = merge_preserved_test_instance(config, resolved_existing)
    show_remote_names_step = (
        effective_config.test_instance is not None
        and effective_config.test_instance.database is None
        and remote_database_names is None
    )
    remote_names_step = remote_database_names_action() if show_remote_names_step else None
    preliminary_missing, _ = evaluate_init_completeness(
        project_root=project_path.resolve(),
        config=effective_config,
        local_config=local_config,
        postgres_image=postgres_image,
        existing_test_instance=resolved_existing,
        dry_run=dry_run,
        remote_database_names=remote_database_names,
    )
    if (
        preliminary_missing
        and no_input
        and not allow_partial
        and not (show_remote_names_step and not dry_run)
    ):
        from odoo_instance_sdk.exceptions import InstanceConfigurationError

        raise InstanceConfigurationError(
            f"init_incomplete: missing capabilities {preliminary_missing}"
        )

    if effective_config.postgres is not None and effective_config.postgres.mode == "compose":
        validate_generated_config_target(
            project_generated_config_path(project_path), project_root=project_path.resolve()
        )

    compose_followup: _ComposeFollowup | None = None
    bootstrap_followup: _BootstrapFollowup | None = None
    if effective_config.postgres is not None and effective_config.postgres.mode == "compose":
        compose_followup, bootstrap_followup = _compose_followup_steps(
            project_path,
            effective_config,
            dry_run=dry_run,
        )

    init_action = PreparedAction(
        step_id="init",
        action="init",
        description="Write project manifest",
        mutating=True,
    )
    prepared_steps: tuple[PreparedAction | PreparedStep, ...] = (
        *((remote_names_step,) if remote_names_step is not None else ()),
        init_action,
        *(compose_followup.steps if compose_followup is not None else ()),
        *(bootstrap_followup.steps if bootstrap_followup is not None else ()),
    )

    def run(context: RunContext[dict[str, JsonValue]]) -> dict[str, JsonValue]:
        resolved_remote_names = _execute_remote_database_names_phase(
            context,
            remote_names_step=remote_names_step,
            effective_config=effective_config,
            resolved_existing=resolved_existing,
            remote_database_names=remote_database_names,
        )
        result = _execute_manifest_phase(
            context,
            project_path=project_path,
            effective_config=effective_config,
            postgres_allocated=postgres_allocated,
            local_config=local_config,
            postgres_image=postgres_image,
            resolved_existing=resolved_existing,
            allow_partial=allow_partial,
            no_input=no_input,
            confirm_partial=confirm_partial,
            remote_database_names=resolved_remote_names,
        )
        if compose_followup is not None:
            _execute_compose_followup_phase(context, compose_followup)
        if bootstrap_followup is not None:
            _execute_bootstrap_followup_phase(context, bootstrap_followup)
        return result

    return Command.from_prepared(
        ExecutionPlan(steps=tuple(item.public_projection() for item in prepared_steps)),
        prepared_command(run, prepared_steps, executor=SubprocessExecutor()),
    )


def _compose_followup_steps(
    project_path: Path,
    config: ProjectConfig,
    *,
    dry_run: bool,
) -> tuple[_ComposeFollowup, _BootstrapFollowup]:
    """Return postgres ensure-running and bootstrap ``tmp`` steps for compose init."""
    from odoo_instance_sdk.internal.dbprep.bootstrap import bootstrap_tmp_steps
    from odoo_instance_sdk.internal.generated_config import project_generated_config_path
    from odoo_instance_sdk.internal.postgres_compose import ensure_password_file
    from odoo_instance_sdk.internal.project_runtime import resolve_project_http_port
    from odoo_instance_sdk.models import StartConfig
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    root = project_path.resolve()
    cluster = PostgresCluster._from_config(
        config,
        repository_root=root,
        compose_runner=None,
    )
    temporary_path = cluster.compose_file.parent / f".compose-{uuid.uuid4().hex}.yaml.tmp"
    postgres_steps = cluster._ensure_running_steps(60.0, temporary_path=temporary_path)

    generated = project_generated_config_path(root)
    source_path = config.source_config if config.source_config is not None else root / "odoo.conf"
    source_start = (
        StartConfig.from_odoo_config(source_path) if Path(source_path).is_file() else StartConfig()
    )
    if dry_run and not cluster.password_file.is_file():
        password = "dry-run-placeholder"
    else:
        password = ensure_password_file(cluster.password_file)
    start_config = copy.deepcopy(source_start)
    start_config.config_path = str(generated)
    start_config.http_port = resolve_project_http_port(
        config.preferred_http_port, source_start.http_port
    )
    start_config.db_name = config.default_source_database or source_start.db_name or ""
    start_config.db_host = cluster.endpoint_host
    start_config.db_port = cluster.endpoint_port
    start_config.db_user = config.postgres.user if config.postgres is not None else "odoo"
    start_config.db_password = password
    from odoo_instance_sdk.internal.project_init import project_owned_data_dir

    start_config.data_dir = str(project_owned_data_dir(root))
    command_prefix = _planned_command_prefix(root, config)
    spawn_step, probe_step, ready_step, ready_action = bootstrap_tmp_steps(
        command_prefix=command_prefix,
        start_config=start_config,
        db_host=cluster.endpoint_host,
        db_port=cluster.endpoint_port,
        db_user=start_config.db_user or "odoo",
        db_password=password,
        default_cwd=root,
    )
    return (
        _ComposeFollowup(
            cluster=cluster,
            temporary_path=temporary_path,
            steps=postgres_steps,
        ),
        _BootstrapFollowup(
            spawn_step=spawn_step,
            probe_step=probe_step,
            ready_step=ready_step,
            verify_action=ready_action,
        ),
    )


def _planned_command_prefix(root: Path, config: ProjectConfig) -> tuple[str, ...]:
    python = str(config.python or "python3")
    odoo_bin = config.odoo_bin if config.odoo_bin is not None else root / "odoo-bin"
    return (python, str(odoo_bin))


def init_project(
    project_path: Path,
    config: ProjectConfig,
    *,
    postgres_allocated: bool,
    local_config: bool = False,
    postgres_image: str | None = None,
    existing_test_instance: TestInstanceProjectConfig | None = None,
    allow_partial: bool = False,
    no_input: bool = False,
    dry_run: bool = False,
    remote_database_names: list[str] | None = None,
    missing: list[str] | None = None,
    details: dict[str, str] | None = None,
) -> dict[str, JsonValue]:
    """Write init artifacts and register the canonical project.

    On dry-run, no manifest/config/dotenv writes occur and the completeness
    check does not call ``DatabaseResource.names()``. On execute, the
    completeness check MAY use the provided ``remote_database_names`` list to
    drop ``test_database`` from the missing set when exactly one name exists.
    """
    from odoo_instance_sdk.exceptions import InstanceConfigurationError

    resolved_existing: TestInstanceProjectConfig | None = None
    if isinstance(existing_test_instance, TestInstanceProjectConfig):
        resolved_existing = existing_test_instance
    effective_config = merge_preserved_test_instance(config, resolved_existing)
    if missing is None or details is None:
        missing, details = evaluate_init_completeness(
            project_root=project_path.resolve(),
            config=effective_config,
            local_config=local_config,
            postgres_image=postgres_image,
            existing_test_instance=resolved_existing,
            dry_run=dry_run,
            remote_database_names=remote_database_names,
        )
    if missing and no_input and not allow_partial:
        raise InstanceConfigurationError(
            f"init_incomplete: missing capabilities {missing} ({details})"
        )

    if dry_run:
        result = manifest_dict(effective_config, postgres_allocated=postgres_allocated)
        if missing:
            result["partial"] = True
            result["missing"] = list(missing)
            result["missing_details"] = dict(details)
        return result

    write_manifest(project_path, effective_config)
    if effective_config.postgres is not None and effective_config.postgres.mode == "compose":
        write_project_generated_config(project_path, effective_config)
        origin_pins = ""
        if effective_config.test_instance is not None:
            from odoo_instance_sdk.internal.urls import canonical_origin

            try:
                origin_pins = canonical_origin(effective_config.test_instance.base_url)
            except Exception:
                origin_pins = ""
        existing_env = read_project_env(project_path)
        write_project_env(
            project_path,
            origin_pins=origin_pins or None,
            master_password=existing_env.get("ODCLI_TEST_MASTER_PASSWORD"),
        )
    register_initialized_project(project_path)
    result = manifest_dict(effective_config, postgres_allocated=postgres_allocated)
    if missing:
        result["partial"] = True
        result["missing"] = list(missing)
        result["missing_details"] = dict(details)
        result["warning"] = f"partial initialization: missing {', '.join(missing)}"
    return result


def init_completeness_preview(
    project_path: Path,
    config: ProjectConfig,
    *,
    local_config: bool,
    postgres_image: str | None,
    existing_test_instance: TestInstanceProjectConfig | None,
    dry_run: bool,
    remote_database_names: list[str] | None,
    postgres_allocated: bool,
) -> dict[str, JsonValue]:
    """Project init manifest output including completeness metadata."""
    effective_config = merge_preserved_test_instance(config, existing_test_instance)
    missing, details = evaluate_init_completeness(
        project_root=project_path.resolve(),
        config=effective_config,
        local_config=local_config,
        postgres_image=postgres_image,
        existing_test_instance=existing_test_instance,
        dry_run=dry_run,
        remote_database_names=remote_database_names,
    )
    result = manifest_dict(effective_config, postgres_allocated=postgres_allocated)
    if missing:
        result["partial"] = True
        result["missing"] = list(missing)
        result["missing_details"] = dict(details)
    return result
