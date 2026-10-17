"""Private shared planning and execution for the primary project venv."""

from __future__ import annotations

import contextlib
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import msgspec

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.locks import (
    exclusive_lock,
    project_manifest_lock_path,
    python_env_lock_path,
)
from odoo_instance_sdk.internal.project_init import manifest_dict
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
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessResult,
        RunContext,
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
                if manifest_dict(current) != manifest_dict(self.baseline):
                    raise ConfigError(  # noqa: TRY301
                        "project manifest changed before primary venv preparation"
                    )
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
    return _PrimaryVenvPreparation(
        baseline=baseline,
        requested_python=python,
        root=root,
        venv_path=venv_path,
        interpreter=interpreter,
        lock_path=lock_path,
        staged_lock_path=staged_lock_path,
        inputs=inputs,
        steps=tuple(steps),
        origin_action=origin_action,
        manifest_action=manifest_action,
        lock_action=lock_action,
    )
