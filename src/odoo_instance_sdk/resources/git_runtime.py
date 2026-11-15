from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import GitScopeError, GitWorkflowError
from odoo_instance_sdk.models import CommandResult

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, RunContext
    from odoo_instance_sdk.models import StagedPatch
    from odoo_instance_sdk.resources.git import GitResource
    from odoo_instance_sdk.resources.instance import OdooInstance

_PATCH_LIMIT = 262144
_SECRET_LINE = re.compile(r"BEGIN PRIVATE KEY|AKIA|api_key=|token=", re.IGNORECASE)


def worktree(instance: OdooInstance) -> Path:
    value = getattr(instance.config, "default_cwd", None)
    if value is None:
        start = getattr(instance.config, "start_config", None)
        config_path = getattr(start, "config_path", None)
        value = Path(config_path).parent if config_path else Path.cwd()
    root = Path(value).resolve()
    if not root.is_dir():
        raise GitWorkflowError(f"Git worktree is unavailable: {root}")
    return root


def command_result(value: ProcessResult, step: PreparedStep) -> CommandResult:
    """Project one captured Git process result without its secrets."""
    from odoo_instance_sdk.internal.proc.redaction import redacted_projection

    secrets = step.secret_values
    stdout = redacted_projection(_decode(value.stdout), secrets=secrets, field="stdout")
    stderr = redacted_projection(_decode(value.stderr), secrets=secrets, field="stderr")
    return CommandResult(
        args=list(step.public_projection().argv),
        returncode=value.returncode,
        stdout=stdout if isinstance(stdout, str) else str(stdout),
        stderr=stderr if isinstance(stderr, str) else str(stderr),
        duration=value.duration,
        cwd=value.cwd,
        environment=step.public_projection().environment_overrides,
    )


def build_staged_patch_command(instance: OdooInstance) -> Command[StagedPatch]:
    """Capture one read-only staged diff whose public plan omits the patch body."""
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import ProcessResult, SubprocessExecutor
    from odoo_instance_sdk.models import StagedPatch
    from odoo_instance_sdk.resources.git import _plan, _step

    root = worktree(instance)
    step = _step(root, "git.staged-patch", ("diff", "--cached", "--no-ext-diff", "--unified=3"))

    def callback(context: RunContext[StagedPatch]) -> StagedPatch:
        result = context.process(step.step_id)
        if not isinstance(result, ProcessResult) or result.returncode != 0:
            raise GitWorkflowError("unable to read the staged patch")
        text = _redact_patch(_decode(result.stdout))
        if not text.strip():
            raise GitScopeError("git commit requires staged changes")
        encoded = text.encode("utf-8")
        truncated = len(encoded) > _PATCH_LIMIT
        shown = encoded[:_PATCH_LIMIT].decode("utf-8", errors="ignore") if truncated else text
        return StagedPatch(text=shown, truncated=truncated, byte_length=len(encoded))

    return Command.create(_plan((step,)), callback, (step,), executor=SubprocessExecutor())


def listed_files(root: Path, relative: str) -> tuple[str, ...]:
    """Return tracked paths from one captured ``git ls-files`` snapshot."""
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import ProcessResult, SubprocessExecutor
    from odoo_instance_sdk.resources.git import _plan, _step

    step = _step(root, "git.ls-files", ("ls-files", "-z", "--", relative))

    def callback(context: RunContext[tuple[str, ...]]) -> tuple[str, ...]:
        result = context.process(step.step_id)
        if not isinstance(result, ProcessResult) or result.returncode != 0:
            raise GitWorkflowError("unable to list tracked files")
        return tuple(item for item in _decode(result.stdout).split("\0") if item)

    return Command.create(_plan((step,)), callback, (step,), executor=SubprocessExecutor()).run()


def repository_resource(root: Path) -> GitResource:
    """Build a Git resource for one local checkout without starting Odoo."""
    from types import SimpleNamespace
    from typing import cast

    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.models import StartConfig
    from odoo_instance_sdk.resources.git import GitResource
    from odoo_instance_sdk.resources.module import ModuleResource

    instance = SimpleNamespace(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            default_cwd=root.resolve(),
            start_config=StartConfig(addons_path=["addons"]),
        )
    )
    typed = cast("OdooInstance", instance)
    instance.modules = ModuleResource(typed)
    return GitResource(typed)


def _decode(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _redact_patch(value: str) -> str:
    from odoo_instance_sdk.internal.proc.redaction import redacted_projection

    projected = redacted_projection(value, field="stdout")
    text = projected if isinstance(projected, str) else str(projected)
    lines = ["<redacted>" if _SECRET_LINE.search(line) else line for line in text.splitlines()]
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + suffix
