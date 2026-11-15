"""Lazy OpenSpec checkout binding for the core environment CLI."""

from __future__ import annotations

import importlib
import re
from typing import cast

from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputMode,
    emit,
    fail,
    model_to_dict,
    success_document,
)
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.models import DevelopmentEnvironment

_CHANGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
INSTALL_HINT = (
    "odcli-openspec is not installed. "
    "Install it with: uv tool install --with-executables-from odcli-openspec odoo-instance-sdk"
)


def guard_openspec_checkout(change: str | None) -> None:
    """Load the extension only when checkout requested a change id.

    Args:
        change: Optional OpenSpec change id from the CLI.
    """
    if change is not None:
        require_openspec_extension(change)


def attach_openspec_checkout(
    command: Command[DevelopmentEnvironment],
    change: str | None,
) -> Command[DevelopmentEnvironment] | Command[object]:
    """Keep the core command, or append OpenSpec steps to the same snapshot.

    Args:
        command: Captured core checkout command.
        change: Optional OpenSpec change id.

    Returns:
        The command whose preview and execution stay identical.
    """
    if change is None:
        return command
    return compose_openspec_checkout(command, change)


def finish_openspec_checkout(
    change: str | None,
    captured: object,
    *,
    plan: ExecutionPlan,
    output_mode: OutputMode,
    provenance: JsonObject | None,
) -> None:
    """Present an OpenSpec checkout result when the flag was set.

    Args:
        change: Optional OpenSpec change id.
        captured: Command result.
        plan: Combined execution plan.
        output_mode: CLI output mode.
        provenance: Ticket provenance.

    Raises:
        SystemExit: The OpenSpec result was emitted and the core result must not follow.
    """
    if change is None:
        return
    present_openspec_checkout(
        captured,
        plan=plan,
        output_mode=output_mode,
        provenance=provenance,
    )
    raise SystemExit(0)


def require_openspec_extension(change: str) -> None:
    """Reject a bad change id or a missing extension before checkout planning.

    Args:
        change: Requested OpenSpec change id.

    Raises:
        ConfigError: The id is a path, or ``odcli-openspec`` is not installed.
    """
    if _CHANGE_ID.fullmatch(change) is None or ".." in change:
        raise ConfigError("OpenSpec change id must be a single change name, not a path")
    try:
        importlib.import_module("odcli_openspec.checkout")
    except ImportError as exc:
        raise ConfigError(INSTALL_HINT) from exc


def compose_openspec_checkout(
    command: Command[DevelopmentEnvironment],
    change: str,
) -> Command[object]:
    """Append OpenSpec validation to a captured checkout command.

    Args:
        command: Core checkout command captured before any mutation.
        change: OpenSpec change id already accepted by ``require_openspec_extension``.

    Returns:
        The combined command snapshot.
    """
    from odcli_openspec.checkout import compose_checkout_command

    return cast("Command[object]", compose_checkout_command(command, change))


def present_openspec_checkout(
    captured: object,
    *,
    plan: ExecutionPlan,
    output_mode: OutputMode,
    provenance: JsonObject | None,
) -> None:
    """Emit a successful binding or a partial failure that keeps the worktree.

    Args:
        captured: Result of the combined checkout command.
        plan: Combined plan that preview and execution shared.
        output_mode: CLI output mode.
        provenance: Ticket provenance already collected for checkout.
    """
    from odcli_openspec.models import OpenSpecCheckoutResult

    if not isinstance(captured, OpenSpecCheckoutResult):
        raise TypeError("OpenSpec checkout returned an unexpected result")
    payload: JsonObject = {
        "environment_created": captured.environment_created,
        "binding_failed": captured.binding_failed,
        "environment_id": captured.environment_id,
        "environment_name": captured.environment_name,
        "worktree_path": captured.worktree_path,
        "change_id": captured.change_id,
        "reason": captured.reason,
        "plan": model_to_dict(plan),
    }
    if captured.binding_failed:
        fail(
            output_mode,
            "env.checkout",
            (f"OpenSpec binding failed after creating the environment at {captured.worktree_path}"),
            dry_run=False,
            error_code="binding_failed",
            details=payload,
        )
    emit(
        success_document(
            command="env.checkout",
            result=payload,
            provenance=provenance,
            dry_run=False,
        ),
        output_mode,
    )
