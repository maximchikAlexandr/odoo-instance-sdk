"""Structured Odoo run logfile snapshot shared by foreground and detached launch."""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from odoo_instance_sdk.exceptions import InstanceConfigurationError, ProjectManifestNotFoundError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.logs.store import finish_pid, write_run_meta
from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep, ProcessHandle
from odoo_instance_sdk.internal.server import wait_foreground_process
from odoo_instance_sdk.project import OdooLoggingConfig, ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance


class _ActionContext(Protocol):
    """The action calls a structured run needs from the command context."""

    def action(self, step_id: str) -> PreparedAction:
        """Start one planned action."""
        ...

    def complete_action(self, step_id: str) -> None:
        """Complete one started action."""
        ...


@dataclass(slots=True)
class ForegroundRun:
    """Structured-log binding captured once for a foreground command."""

    instance: OdooInstance
    launch: StructuredLaunch | None
    argv: tuple[str, ...]
    opened: bool = False
    exit_code: int | None = None

    @staticmethod
    def bind(instance: OdooInstance) -> ForegroundRun:
        """Choose the run logfile and logging argv for one foreground snapshot."""
        launch = structured_launch(instance, action_id="instance.foreground.runlog")
        argv = logging_argv(instance)
        if launch is not None:
            argv = ("--logfile", str(launch.logfile), *argv)
        return ForegroundRun(instance, launch, argv)

    def prefix(
        self, *steps: PreparedStep | PreparedAction
    ) -> tuple[PreparedStep | PreparedAction, ...]:
        """Prepend the run-log action when this launch owns a structured file."""
        if self.launch is None:
            return steps
        return (
            PreparedAction(
                step_id=self.launch.action_id,
                action=self.launch.action_id,
                description="Create the private Odoo run logfile",
                details={"run_id": self.launch.run_id, "logfile": str(self.launch.logfile)},
                mutating=True,
            ),
            *steps,
        )

    def open(self, context: _ActionContext) -> None:
        """Create the run directory while the planned action is active."""
        if self.launch is None:
            return
        context.action(self.launch.action_id)
        prepare_structured_run(self.launch)
        self.opened = True
        context.complete_action(self.launch.action_id)

    def record(self, pid: int | None) -> None:
        """Attach the spawned process when a structured run was opened."""
        if self.launch is None or pid is None:
            return
        self.instance._record_structured_process(self.launch, pid)

    def wait(self, handle: ProcessHandle) -> int:
        """Wait for the foreground process and remember its exit code."""
        self.exit_code = wait_foreground_process(handle)
        return self.exit_code

    def close(self) -> None:
        """Record a terminal state once, and only while the run is still running."""
        if self.launch is None or not self.opened:
            return
        state = "finished" if self.exit_code == 0 else "interrupted"
        with contextlib.suppress(BaseException):
            finish_structured_run(self.launch, state=state, exit_code=self.exit_code)


@dataclass(frozen=True, slots=True)
class StructuredLaunch:
    """One run directory chosen while the command snapshot is built."""

    run_id: str
    logfile: Path
    environment_root: Path
    environment_id: str
    project_id: str
    action_id: str


def project_logging(instance: OdooInstance) -> OdooLoggingConfig | None:
    """Return the manifest logging table when the instance has a project binding."""
    binding = instance._runtime_binding
    if binding is None:
        return None
    try:
        project = ProjectConfig.load(binding.repository_root)
    except ProjectManifestNotFoundError:
        return None
    return project.odoo_logging


def logging_argv(instance: OdooInstance) -> tuple[str, ...]:
    """Return Odoo log-level and log-handler arguments from the manifest."""
    config = project_logging(instance)
    if config is None:
        return ()
    argv: list[str] = []
    if config.level is not None:
        argv.extend(("--log-level", config.level))
    for handler in config.handlers:
        argv.extend(("--log-handler", handler))
    return tuple(argv)


def structured_launch(instance: OdooInstance, *, action_id: str) -> StructuredLaunch | None:
    """Choose a private run logfile without creating it."""
    config = project_logging(instance)
    if config is None or not config.structured:
        return None
    binding = instance._runtime_binding
    start = instance.config.start_config
    if (
        binding is None
        or binding.owner_kind != "environment"
        or start is None
        or not start.config_path
    ):
        raise InstanceConfigurationError("structured Odoo logs require a ready environment")
    root = Path(start.config_path).resolve().parent
    run_id = f"run_{uuid.uuid4().hex}"
    return StructuredLaunch(
        run_id=run_id,
        logfile=root / "logs" / run_id / "odoo.log",
        environment_root=root,
        environment_id=binding.owner_id,
        project_id=binding.project_id,
        action_id=action_id,
    )


def prepare_structured_run(launch: StructuredLaunch) -> None:
    """Create the private run directory, logfile, and running metadata."""
    logs = launch.logfile.parent.parent
    run_dir = launch.logfile.parent
    if any(path.is_symlink() for path in (logs, run_dir, launch.logfile)):
        raise InstanceConfigurationError("structured log path is a symlink")
    logs.mkdir(parents=True, exist_ok=True)
    os.chmod(logs, 0o700)
    run_dir.mkdir(exist_ok=True)
    os.chmod(run_dir, 0o700)
    fd = os.open(launch.logfile, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    os.chmod(launch.logfile, 0o600)
    write_run_meta(
        run_dir,
        {
            "schema_version": 1,
            "run_id": launch.run_id,
            "environment_id": launch.environment_id,
            "project_id": launch.project_id,
            "branch": None,
            "commit": None,
            "started_at": _now(),
            "ended_at": None,
            "exit_code": None,
            "pid": None,
            "process_create_time": None,
            "state": "running",
        },
    )


def record_structured_process(
    launch: StructuredLaunch,
    *,
    pid: int,
    create_time: float | None,
    branch: str | None,
    commit: str | None,
) -> None:
    """Attach process and git identity to the run metadata."""
    path = launch.logfile.parent / "meta.json"
    if path.is_symlink():
        raise InstanceConfigurationError("structured log path is a symlink")
    payload = _read(path)
    payload["pid"] = pid
    payload["process_create_time"] = create_time
    payload["branch"] = branch
    payload["commit"] = commit
    payload["state"] = "running"
    write_run_meta(launch.logfile.parent, payload)


def finish_structured_run(launch: StructuredLaunch, *, state: str, exit_code: int | None) -> None:
    """Record a terminal state on the run created by this launch."""
    path = launch.logfile.parent / "meta.json"
    if path.is_symlink() or not path.is_file():
        return
    payload = _read(path)
    if payload.get("state") != "running":
        return
    payload["state"] = state
    payload["exit_code"] = exit_code
    payload["ended_at"] = _now()
    write_run_meta(launch.logfile.parent, payload)


def recorded_checkout(row: Mapping[str, JsonValue] | None) -> tuple[str | None, str | None]:
    """Return git identity stored on a runtime row, omitting unknown values."""
    if row is None:
        return None, None
    return _clean(row.get("checkout_branch")), _clean(row.get("commit_sha"))


def finish_environment_pid(root: Path, pid: int, *, state: str) -> None:
    """Record a terminal state for whichever owned run has this process id."""
    finish_pid(root, pid, state=state, exit_code=None)


def _read(path: Path) -> dict[str, JsonValue]:
    if path.is_symlink() or not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}
    return {str(key): cast("JsonValue", value) for key, value in loaded.items()}


def _clean(value: JsonValue) -> str | None:
    if not isinstance(value, str) or value in {"", "unknown"}:
        return None
    return value


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
