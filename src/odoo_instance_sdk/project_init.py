"""Public project initialization primitives."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import msgspec

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.dbprep.source import _planned_project_identity
from odoo_instance_sdk.internal.locks import exclusive_lock, project_manifest_lock_path
from odoo_instance_sdk.internal.project_init import (
    evaluate_init_completeness,
    fetch_remote_database_names_for_init,
    manifest_dict,
    merge_preserved_test_instance,
    read_project_env,
    register_initialized_project,
    remote_database_names_action,
    validate_generated_config_target,
    verify_project_owned_data_dir,
    write_project_env,
    write_project_generated_config,
)
from odoo_instance_sdk.internal.project_manifest import manifest_path, write_manifest
from odoo_instance_sdk.internal.venv_origin import (
    OriginStage,
    discard_origin_stage,
    origin_record_path,
    publish_origin,
    read_origin_bytes,
    restore_persisted_prepare_state,
    stage_origin_record,
)
from odoo_instance_sdk.project import (
    ProjectConfig,
    RemoteSourceConfig,
    TestInstanceProjectConfig,
    normalize_remote_name,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        PrivateJsonValue,
        ProcessResult,
        RunContext,
    )
    from odoo_instance_sdk.resources.postgres import PostgresCluster


@dataclass(frozen=True, slots=True)
class _ComposeFollowup:
    cluster: PostgresCluster
    temporary_path: Path
    steps: tuple[PreparedStep | PreparedAction, ...]


@dataclass(frozen=True, slots=True)
class _BootstrapFollowup:
    cluster: PostgresCluster
    spawn_step: PreparedStep
    probe_step: PreparedStep
    ready_step: PreparedStep
    verify_action: PreparedAction
    record_action: PreparedAction
    project_root: Path
    data_directory: Path

    @property
    def steps(self) -> tuple[PreparedStep | PreparedAction, ...]:
        return (
            self.spawn_step,
            self.probe_step,
            self.ready_step,
            self.verify_action,
            self.record_action,
        )


@dataclass(frozen=True, slots=True)
class _PrimaryVenvPreparation:
    """One captured primary-venv transaction shared by init and prepare-venv."""

    baseline: ProjectConfig
    requested_python: str | Path
    root: Path
    venv_path: Path
    interpreter: str
    lock_path: Path
    staged_lock_path: Path
    inputs: tuple[str, ...]
    steps: tuple[PreparedStep | PreparedAction, ...]
    origin_action: PreparedAction
    manifest_action: PreparedAction
    lock_action: PreparedAction | None

    def result(self) -> dict[str, JsonValue]:
        return {
            "python": str(self.requested_python),
            "requested_python": str(self.requested_python),
            "venv": str(self.venv_path),
            "interpreter": self.interpreter,
            "dependency_inputs": list(self.inputs),
            "manifest": str(manifest_path(self.root)),
        }

    def execute(self, context: RunContext[dict[str, JsonValue]]) -> dict[str, JsonValue]:  # noqa: C901
        from odoo_instance_sdk.internal.proc import PreparedAction

        lock_id = hashlib.sha256(str(self.root).encode()).hexdigest()
        staged_origin: OriginStage | None = None
        transaction_started = False
        with exclusive_lock(project_manifest_lock_path(lock_id)):
            manifest_file = manifest_path(self.root)
            manifest_before = manifest_file.read_bytes()
            lock_before = self.lock_path.read_bytes() if self.lock_path.is_file() else None
            origin_before = read_origin_bytes(self.root)
            try:
                current = ProjectConfig.load(self.root)
                if current.to_manifest() != self.baseline.to_manifest():
                    raise ConfigError(  # noqa: TRY301
                        "project manifest changed before primary venv preparation"
                    )
                from odoo_instance_sdk.internal.locks import python_env_lock_path

                with exclusive_lock(python_env_lock_path(str(self.venv_path))):
                    with contextlib.suppress(OSError):
                        self.staged_lock_path.unlink()
                    for step in self.steps:
                        if isinstance(step, PreparedAction):
                            continue
                        try:
                            result = cast("ProcessResult", context.process(step.step_id))
                        except Exception as error:
                            if step.step_id.endswith(".validate"):
                                raise ConfigError(
                                    "prepare-venv.validate failed: valid virtual environment required"
                                ) from error
                            raise
                        if result.returncode != 0:
                            stderr = (
                                result.stderr.decode(errors="replace")
                                if isinstance(result.stderr, bytes)
                                else result.stderr
                            )
                            detail = (stderr or "").strip()
                            if step.step_id.endswith(".validate"):
                                raise ConfigError(  # noqa: TRY301
                                    "prepare-venv.validate failed: valid virtual environment required"
                                )
                            raise ConfigError(  # noqa: TRY301
                                f"{step.step_id} failed: {detail}".strip()
                            )
                    updated = msgspec.structs.replace(current, python=self.interpreter)
                    updated_manifest = updated.to_manifest().encode("utf-8")
                    context.action(self.origin_action.step_id)
                    staged_origin = stage_origin_record(
                        self.root,
                        venv_root=self.venv_path,
                        requested=self.requested_python,
                        interpreter=self.interpreter,
                        manifest_bytes=updated_manifest,
                    )
                    context.action(self.manifest_action.step_id)
                    write_manifest(self.root, updated)
                    transaction_started = True
                    context.complete_action(self.manifest_action.step_id)
                    publish_origin(staged_origin)
                    discard_origin_stage(staged_origin)
                    staged_origin = None
                    context.complete_action(self.origin_action.step_id)
                    if self.lock_action is not None:
                        context.action(self.lock_action.step_id)
                        os.replace(self.staged_lock_path, self.lock_path)
                        context.complete_action(self.lock_action.step_id)
                    transaction_started = False
            except BaseException:
                if transaction_started:
                    restore_persisted_prepare_state(
                        self.root,
                        manifest_path=manifest_file,
                        manifest_before=manifest_before,
                        lock_path=self.lock_path,
                        lock_before=lock_before,
                        origin_before=origin_before,
                    )
                raise
            finally:
                if self.lock_action is not None:
                    with contextlib.suppress(OSError):
                        self.staged_lock_path.unlink()
                if staged_origin is not None:
                    discard_origin_stage(staged_origin)
        return self.result()


def _init_action_details(
    project_path: Path,
    effective_config: ProjectConfig,
    *,
    postgres_allocated: bool,
    local_config: bool,
    postgres_image: str | None,
    allow_partial: bool,
    resume_existing: bool,
    no_input: bool,
    dry_run: bool,
    remote_database_names: list[str] | None,
) -> dict[str, JsonValue]:
    """Expose only the target and a digest of sanitized manifest inputs."""
    from odoo_instance_sdk.internal.proc.redaction import (
        capture_sensitive_argv_indices,
        redacted_argv,
        redacted_projection,
    )

    manifest = manifest_dict(effective_config, postgres_allocated=postgres_allocated)
    default_run_args = tuple(effective_config.default_run_args)
    manifest["default_run_args"] = list(
        redacted_argv(
            default_run_args,
            sensitive_indices=capture_sensitive_argv_indices(default_run_args),
        )
    )
    projection: dict[str, JsonValue] = {
        "project_path": str(project_path.resolve()),
        "manifest": manifest,
        "postgres_image": postgres_image,
        "local_config": local_config,
        "allow_partial": allow_partial,
        "resume_existing": resume_existing,
        "no_input": no_input,
        "dry_run": dry_run,
        "remote_database_names": cast("JsonValue", remote_database_names),
    }
    redacted = redacted_projection(projection, field="init-manifest")
    digest = hashlib.sha256(
        json.dumps(redacted, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "project_path": projection["project_path"],
        "manifest_input_digest": digest,
    }


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
    resume_existing: bool,
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
    if missing and confirm_partial is not None and not resume_existing:
        confirm_partial(missing, details)
    if missing and no_input and not allow_partial and not resume_existing:
        raise InstanceConfigurationError(
            f"init_incomplete: missing capabilities {missing} ({details})"
        )
    if resume_existing:
        from odoo_instance_sdk.exceptions import StalePlanError

        root, common, _ = _planned_project_identity(project_path)
        lock_id = hashlib.sha256(str(common).encode()).hexdigest()
        with exclusive_lock(project_manifest_lock_path(lock_id)):
            current = ProjectConfig.load(root)
            if manifest_dict(current) != manifest_dict(effective_config):
                raise StalePlanError(
                    "project manifest changed before Compose init resume",
                    expected=manifest_dict(effective_config),
                    actual=manifest_dict(current),
                )
            context.action("init")
            register_initialized_project(root)
            context.complete_action("init")
            return manifest_dict(current)
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
    from odoo_instance_sdk.internal.dbprep.bootstrap import (
        BootstrapOutcome,
        record_bootstrap_event,
        run_bootstrap_tmp,
    )
    from odoo_instance_sdk.internal.project_init import verify_project_owned_data_dir

    verify_project_owned_data_dir(
        followup.project_root, followup.data_directory, require_exists=False
    )
    context.action(followup.verify_action.step_id)
    outcome = run_bootstrap_tmp(
        context, followup.spawn_step, followup.probe_step, followup.ready_step
    )
    context.complete_action(followup.verify_action.step_id)
    if outcome is BootstrapOutcome.CREATED:
        context.action(followup.record_action.step_id)
        data_directory = verify_project_owned_data_dir(
            followup.project_root, followup.data_directory
        )
        record_bootstrap_event(followup.cluster, data_directory)
        context.complete_action(followup.record_action.step_id)
    for step in followup.steps:
        if context.planned(step.step_id) and not context.consumed(step.step_id):
            context.skip(step.step_id)


def init_project_command(  # noqa: C901
    project_path: Path,
    config: ProjectConfig,
    *,
    postgres_allocated: bool,
    local_config: bool = False,
    postgres_image: str | None = None,
    existing_test_instance: TestInstanceProjectConfig | None = None,
    remote_instances: tuple[RemoteSourceConfig, ...] | None = None,
    allow_partial: bool = False,
    resume_existing: bool = False,
    no_input: bool = False,
    dry_run: bool = False,
    remote_database_names: list[str] | None = None,
    confirm_partial: Callable[[list[str], dict[str, str]], None] | None = None,
    venv: str | Path | None = None,
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
    if remote_instances is not None:
        config = msgspec.structs.replace(config, remote_instances=remote_instances)
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
        and not resume_existing
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

    primary_preparation: _PrimaryVenvPreparation | None = None
    if venv is not None:
        if effective_config.python is None:
            raise ConfigError("init --venv requires an effective Python selector; pass --python")
        primary_preparation = _capture_primary_venv_preparation(
            effective_config,
            python=effective_config.python,
            venv=venv,
        )

    compose_followup: _ComposeFollowup | None = None
    bootstrap_followup: _BootstrapFollowup | None = None
    if effective_config.postgres is not None and effective_config.postgres.mode == "compose":
        compose_followup, bootstrap_followup = _compose_followup_steps(
            project_path,
            effective_config,
            dry_run=dry_run,
            python=(primary_preparation.interpreter if primary_preparation is not None else None),
        )

    init_action = PreparedAction(
        step_id="init",
        action="verify-init-manifest" if resume_existing else "init",
        description=(
            "Verify existing project manifest" if resume_existing else "Write project manifest"
        ),
        details=cast(
            "PrivateJsonValue",
            _init_action_details(
                project_path,
                effective_config,
                postgres_allocated=postgres_allocated,
                local_config=local_config,
                postgres_image=postgres_image,
                allow_partial=allow_partial,
                resume_existing=resume_existing,
                no_input=no_input,
                dry_run=dry_run,
                remote_database_names=remote_database_names,
            ),
        ),
        read_only=resume_existing,
        mutating=not resume_existing,
    )
    prepared_steps: tuple[PreparedAction | PreparedStep, ...] = (
        *((remote_names_step,) if remote_names_step is not None else ()),
        init_action,
        *(primary_preparation.steps if primary_preparation is not None else ()),
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
            resume_existing=resume_existing,
        )
        if primary_preparation is not None:
            result = {
                **result,
                **primary_preparation.execute(context),
            }
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
    python: str | None = None,
) -> tuple[_ComposeFollowup, _BootstrapFollowup]:
    """Return postgres ensure-running and bootstrap ``tmp`` steps for compose init."""
    from odoo_instance_sdk.internal.dbprep.bootstrap import (
        bootstrap_record_action,
        bootstrap_tmp_steps,
    )
    from odoo_instance_sdk.internal.generated_config import project_generated_config_path
    from odoo_instance_sdk.internal.postgres_compose import ensure_password_file
    from odoo_instance_sdk.internal.project_init import verify_project_owned_data_dir
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
    data_directory = root / ".odcli" / "filestore"
    verify_project_owned_data_dir(root, data_directory, require_exists=False)
    start_config.data_dir = str(data_directory)
    command_prefix = _planned_command_prefix(root, config, python=python)
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
            cluster=cluster,
            spawn_step=spawn_step,
            probe_step=probe_step,
            ready_step=ready_step,
            verify_action=ready_action,
            record_action=bootstrap_record_action(),
            project_root=root,
            data_directory=data_directory,
        ),
    )


def _planned_command_prefix(
    root: Path, config: ProjectConfig, *, python: str | None = None
) -> tuple[str, ...]:
    python = python or str(config.python or "python3")
    odoo_bin = config.odoo_bin if config.odoo_bin is not None else root / "odoo-bin"
    return (python, str(odoo_bin))


def _ensure_project_owned_data_directory(project_path: Path, config: ProjectConfig) -> None:
    if config.managed_filestore is None:
        return
    from odoo_instance_sdk.project import managed_filestore_path

    data_directory = managed_filestore_path(config)
    verify_project_owned_data_dir(project_path, data_directory, require_exists=False)
    data_directory.mkdir(parents=True, exist_ok=True)
    verify_project_owned_data_dir(project_path, data_directory)


def init_project(  # noqa: C901
    project_path: Path,
    config: ProjectConfig,
    *,
    postgres_allocated: bool,
    local_config: bool = False,
    postgres_image: str | None = None,
    existing_test_instance: TestInstanceProjectConfig | None = None,
    remote_instances: tuple[RemoteSourceConfig, ...] | None = None,
    allow_partial: bool = False,
    no_input: bool = False,
    dry_run: bool = False,
    remote_database_names: list[str] | None = None,
    missing: list[str] | None = None,
    details: dict[str, str] | None = None,
    venv: str | Path | None = None,
) -> dict[str, JsonValue]:
    """Write init artifacts and register the canonical project.

    On dry-run, no manifest/config/dotenv writes occur and the completeness
    check does not call ``DatabaseResource.names()``. On execute, the
    completeness check MAY use the provided ``remote_database_names`` list to
    drop ``test_database`` from the missing set when exactly one name exists.
    """
    from odoo_instance_sdk.exceptions import InstanceConfigurationError

    if venv is not None:
        command = init_project_command(
            project_path,
            config,
            postgres_allocated=postgres_allocated,
            local_config=local_config,
            postgres_image=postgres_image,
            existing_test_instance=existing_test_instance,
            remote_instances=remote_instances,
            allow_partial=allow_partial,
            no_input=no_input,
            dry_run=dry_run,
            remote_database_names=remote_database_names,
            venv=venv,
        )
        if dry_run:
            return manifest_dict(config, postgres_allocated=postgres_allocated)
        return command.run()

    resolved_existing: TestInstanceProjectConfig | None = None
    if isinstance(existing_test_instance, TestInstanceProjectConfig):
        resolved_existing = existing_test_instance
    if remote_instances is not None:
        config = msgspec.structs.replace(config, remote_instances=remote_instances)
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
    _ensure_project_owned_data_directory(project_path, effective_config)
    if effective_config.postgres is not None and effective_config.postgres.mode == "compose":
        write_project_generated_config(project_path, effective_config)
        if effective_config.test_instance is not None:
            existing_env = read_project_env(project_path)
            write_project_env(
                project_path,
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
    remote_instances: tuple[RemoteSourceConfig, ...] | None = None,
    dry_run: bool,
    remote_database_names: list[str] | None,
    postgres_allocated: bool,
) -> dict[str, JsonValue]:
    """Project init manifest output including completeness metadata."""
    if remote_instances is not None:
        config = msgspec.structs.replace(config, remote_instances=remote_instances)
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


def list_remote_sources(project: ProjectConfig | str | Path) -> tuple[RemoteSourceConfig, ...]:
    """List named sources in deterministic manifest order."""
    config = project if isinstance(project, ProjectConfig) else ProjectConfig.load(project)
    return tuple(sorted(config.remote_instances, key=lambda source: source.name))


def _manifest_snapshot(
    project: ProjectConfig | str | Path,
) -> tuple[ProjectConfig, Path, Path, str]:
    config = project if isinstance(project, ProjectConfig) else ProjectConfig.load(project)
    root = config.repository_root.resolve()
    manifest = manifest_path(root)
    content = manifest.read_bytes() if manifest.is_file() else config.to_manifest().encode()
    from odoo_instance_sdk.internal.dbprep.source import _planned_project_identity

    canonical_root, common, _ = _planned_project_identity(root)
    return config, canonical_root, common, hashlib.sha256(content).hexdigest()


def _assert_manifest_snapshot(*, root: Path, common: Path, fingerprint: str) -> ProjectConfig:
    from odoo_instance_sdk.exceptions import StalePlanError
    from odoo_instance_sdk.internal.dbprep.source import _planned_project_identity

    current_root, current_common, _ = _planned_project_identity(root)
    if current_root != root or current_common != common:
        raise StalePlanError("project repository identity changed before manifest update")
    manifest = manifest_path(root)
    if not manifest.is_file():
        raise StalePlanError("project manifest disappeared before update")
    current_bytes = manifest.read_bytes()
    current_fingerprint = hashlib.sha256(current_bytes).hexdigest()
    if current_fingerprint != fingerprint:
        raise StalePlanError(
            "project manifest changed before update",
            expected=fingerprint,
            actual=current_fingerprint,
        )
    return ProjectConfig.load(root)


def _remote_config_command(
    project: ProjectConfig | str | Path,
    *,
    source: RemoteSourceConfig | None,
    name: str | None,
    replace: bool,
) -> Command[tuple[RemoteSourceConfig, ...]]:
    from odoo_instance_sdk.execution import Command, ExecutionPlan
    from odoo_instance_sdk.internal.proc import PreparedAction, SubprocessExecutor, prepared_command

    baseline, root, common, fingerprint = _manifest_snapshot(project)
    if source is not None:
        source = RemoteSourceConfig(
            name=source.name,
            base_url=source.base_url,
            database=source.database,
            git_branch=source.git_branch,
        )
        action = "project.remote.configure"
        description = "Configure a named remote source"
    else:
        assert name is not None
        normalized_name = normalize_remote_name(name)
        action = "project.remote.remove"
        description = "Remove a named remote source"

    planned_sources = {item.name: item for item in baseline.remote_instances}
    if source is not None:
        planned_sources[source.name] = source
    else:
        planned_sources.pop(normalized_name, None)
    planned_config = msgspec.structs.replace(
        baseline,
        remote_instances=tuple(sorted(planned_sources.values(), key=lambda item: item.name)),
    )

    step = PreparedAction(
        step_id=action,
        action=action,
        description=description,
        details={
            "repository_root": str(root),
            "git_common_dir": str(common),
            "manifest_fingerprint": fingerprint,
            "resulting_manifest": planned_config.to_manifest(),
        },
        mutating=True,
    )

    def run(context: RunContext[tuple[RemoteSourceConfig, ...]]) -> tuple[RemoteSourceConfig, ...]:
        context.action(action)
        with exclusive_lock(
            project_manifest_lock_path(hashlib.sha256(str(common).encode()).hexdigest())
        ):
            current = _assert_manifest_snapshot(root=root, common=common, fingerprint=fingerprint)
            sources = {item.name: item for item in current.remote_instances}
            if source is not None:
                existing = sources.get(source.name)
                if existing is not None and not replace and existing != source:
                    raise ValueError(
                        f"remote source {source.name!r} exists; pass replace=True to update it"
                    )
                if existing == source:
                    result = tuple(sorted(sources.values(), key=lambda item: item.name))
                else:
                    sources[source.name] = source
                    updated = msgspec.structs.replace(
                        current,
                        remote_instances=tuple(
                            sorted(sources.values(), key=lambda item: item.name)
                        ),
                    )
                    write_manifest(root, updated)
                    result = updated.remote_instances
            else:
                sources.pop(normalized_name, None)
                if len(sources) != len(current.remote_instances):
                    updated = msgspec.structs.replace(
                        current,
                        remote_instances=tuple(
                            sorted(sources.values(), key=lambda item: item.name)
                        ),
                    )
                    write_manifest(root, updated)
                    result = updated.remote_instances
                else:
                    result = current.remote_instances
        context.complete_action(action)
        return result

    return Command.from_prepared(
        ExecutionPlan(steps=(step.public_projection(),)),
        prepared_command(run, (step,), executor=SubprocessExecutor()),
    )


def configure_remote_source_command(
    project: ProjectConfig | str | Path,
    source: RemoteSourceConfig,
    *,
    replace: bool = False,
) -> Command[tuple[RemoteSourceConfig, ...]]:
    """Capture an atomic named-source add/update operation."""
    return _remote_config_command(project, source=source, name=None, replace=replace)


def configure_remote_source(
    project: ProjectConfig | str | Path,
    source: RemoteSourceConfig,
    *,
    replace: bool = False,
) -> tuple[RemoteSourceConfig, ...]:
    return configure_remote_source_command(project, source, replace=replace).run()


def remove_remote_source_command(
    project: ProjectConfig | str | Path, name: str
) -> Command[tuple[RemoteSourceConfig, ...]]:
    """Capture an atomic named-source removal operation."""
    return _remote_config_command(project, source=None, name=name, replace=False)


def remove_remote_source(
    project: ProjectConfig | str | Path, name: str
) -> tuple[RemoteSourceConfig, ...]:
    return remove_remote_source_command(project, name).run()


def _capture_primary_venv_preparation(
    project: ProjectConfig,
    *,
    python: str | Path,
    venv: str | Path,
) -> _PrimaryVenvPreparation:
    """Capture the shared primary-venv transaction without executing it."""
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep
    from odoo_instance_sdk.resources.environment.checkout_artifacts import _owned_python_executable
    from odoo_instance_sdk.resources.environment.checkout_planning import discover_dependency_inputs

    baseline = project
    root = baseline.repository_root.resolve()
    venv_path = Path(venv)
    if not venv_path.is_absolute():
        venv_path = root / venv_path
    venv_path = venv_path.resolve(strict=False)
    interpreter = _owned_python_executable(venv_path)
    inputs = discover_dependency_inputs(baseline, root, root)
    lock_path = root / ".odcli" / "requirements.lock"
    staged_lock_path = lock_path.with_name(f"{lock_path.name}.prepare")
    create_venv = not Path(interpreter).is_file()
    steps: list[PreparedStep | PreparedAction] = []
    if create_venv:
        steps.append(
            PreparedStep(
                step_id="prepare-venv.venv",
                argv=("uv", "venv", str(venv_path), "--python", str(python)),
                cwd=str(root),
                mode="captured",
                mutating=True,
            )
        )
    steps.append(
        PreparedStep(
            step_id="prepare-venv.validate",
            argv=(
                interpreter,
                "-c",
                "import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)",
            ),
            cwd=str(root),
            mode="captured",
            read_only=True,
        )
    )
    if inputs:
        steps.extend(
            (
                PreparedStep(
                    step_id="prepare-venv.dependencies.compile",
                    argv=(
                        "uv",
                        "pip",
                        "compile",
                        "--python",
                        interpreter,
                        *inputs,
                        "-o",
                        str(staged_lock_path),
                    ),
                    cwd=str(root),
                    mode="captured",
                    mutating=True,
                ),
                PreparedStep(
                    step_id="prepare-venv.dependencies.install",
                    argv=("uv", "pip", "sync", "--python", interpreter, str(staged_lock_path)),
                    cwd=str(root),
                    mode="captured",
                    mutating=True,
                ),
            )
        )
        lock_action = PreparedAction(
            step_id="prepare-venv.dependencies.lock",
            action="publish-primary-dependency-lock",
            description="Publish the prepared dependency lock",
            details={"path": str(lock_path)},
            mutating=True,
        )
    else:
        lock_action = None
    manifest_action = PreparedAction(
        step_id="prepare-venv.manifest",
        action="persist-primary-interpreter",
        description="Persist the prepared primary venv interpreter",
        details={
            "python": str(python),
            "venv": str(venv_path),
            "interpreter": interpreter,
            "dependency_inputs": tuple(inputs),
            "manifest": str(manifest_path(root)),
        },
        mutating=True,
    )
    origin_action = PreparedAction(
        step_id="prepare-venv.origin",
        action="publish-primary-venv-origin",
        description="Publish trusted primary venv provenance",
        details={"path": str(origin_record_path(root)), "venv": str(venv_path)},
        mutating=True,
    )
    steps.append(origin_action)
    steps.append(manifest_action)
    if lock_action is not None:
        steps.append(lock_action)
    prepared_steps = tuple(steps)
    return _PrimaryVenvPreparation(
        baseline=baseline,
        requested_python=python,
        root=root,
        venv_path=venv_path,
        interpreter=interpreter,
        lock_path=lock_path,
        staged_lock_path=staged_lock_path,
        inputs=inputs,
        steps=prepared_steps,
        origin_action=origin_action,
        manifest_action=manifest_action,
        lock_action=lock_action,
    )


def prepare_venv_command(
    project: ProjectConfig | str | Path,
    *,
    python: str | Path,
    venv: str | Path,
) -> Command[dict[str, JsonValue]]:
    """Capture immutable preparation of the initialized project's primary venv."""
    from odoo_instance_sdk.execution import Command, ExecutionPlan
    from odoo_instance_sdk.internal.proc import SubprocessExecutor, prepared_command

    baseline = project if isinstance(project, ProjectConfig) else ProjectConfig.load(project)
    preparation = _capture_primary_venv_preparation(baseline, python=python, venv=venv)
    public_plan = ExecutionPlan(steps=tuple(step.public_projection() for step in preparation.steps))
    return Command.from_prepared(
        public_plan,
        prepared_command(
            preparation.execute,
            preparation.steps,
            executor=SubprocessExecutor(),
        ),
    )


def prepare_venv(
    project: ProjectConfig | str | Path,
    *,
    python: str | Path,
    venv: str | Path,
) -> dict[str, JsonValue]:
    """Prepare the primary project venv and persist its interpreter on success."""
    return prepare_venv_command(project, python=python, venv=venv).run()
