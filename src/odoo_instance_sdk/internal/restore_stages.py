"""Canonical long-restore stage publication through the shared StepObserver.

Long-running restore operations publish a small set of stable stage ids so the
Rich Live indicator can show current stage, elapsed time, and percent without
polluting JSON/TOON machine stdout.  The observer is only active for Rich
terminal progress; when no observer is present these helpers are inert.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import StepObserver

RESTORE_STAGE_IDS: tuple[str, ...] = (
    "backup_prepare",
    "auxiliary_start",
    "db_restore",
    "db_verify",
    "filestore_restore",
    "admin_reset",
    "default_switch",
)

_HEARTBEAT_INTERVAL = 0.5


def _active_observer() -> StepObserver | None:
    """Return the observer bound to the active command context, if any."""
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    return context.observer if context is not None else None


def publish_stage(
    stage_id: str,
    *,
    kind: str,
    observer: StepObserver | None = None,
    elapsed: float | None = None,
    error: str | None = None,
    completed_units: float | None = None,
    total_units: float | None = None,
) -> None:
    """Emit one canonical restore stage event through the active observer.

    The stage ids are the stable public surface; internal step ids remain
    unchanged.  When no observer is active (JSON/TOON, dry-run, or non-Rich)
    this is a no-op, so machine stdout stays a single document.
    """
    from odoo_instance_sdk.internal.proc import StepEvent

    target = observer if observer is not None else _active_observer()
    if target is None:
        return
    event = StepEvent(
        step_id=stage_id,
        kind=kind,  # type: ignore[arg-type]
        operation=stage_id,
        target=stage_id,
        elapsed=elapsed,
        error=error,
        completed_units=completed_units,
        total_units=total_units,
    )
    try:
        target(event)
    except Exception:
        return


def attach_restore_stage_failure(
    error: BaseException,
    *,
    stage_id: str | None,
    elapsed: float | None,
) -> None:
    """Preserve the safe primary cause, last stage id, and elapsed seconds.

    The values are attached as plain attributes on the exception so the CLI
    failure projection can surface them without inventing a new failure type.
    Existing ``failure_context`` projections remain authoritative for retained
    artifact identifiers; this only adds the restore-stage provenance.
    """
    if stage_id is not None:
        setattr(error, "restore_stage_id", stage_id)
    if elapsed is not None:
        setattr(error, "restore_stage_elapsed", float(elapsed))


@contextlib.contextmanager
def restore_stage(
    stage_id: str,
    *,
    observer: StepObserver | None = None,
) -> Iterator[None]:
    """Publish started/completed events for one restore stage boundary.

    On failure, the safe primary cause is captured into the raised exception
    via :func:`attach_restore_stage_failure` so the CLI can preserve the last
    stage id and elapsed seconds instead of a generic readiness message.
    """
    started = time.monotonic()
    publish_stage(stage_id, kind="started", observer=observer, elapsed=0.0)
    try:
        yield
    except BaseException as error:
        elapsed = time.monotonic() - started
        from odoo_instance_sdk.internal.sanitize import sanitize_event_message

        publish_stage(
            stage_id,
            kind="failed",
            observer=observer,
            elapsed=elapsed,
            error=sanitize_event_message(str(error)) or "restore stage failed",
        )
        attach_restore_stage_failure(error, stage_id=stage_id, elapsed=elapsed)
        raise
    else:
        elapsed = time.monotonic() - started
        publish_stage(stage_id, kind="completed", observer=observer, elapsed=elapsed)


@contextlib.contextmanager
def restore_stage_heartbeat(
    stage_id: str,
    *,
    observer: StepObserver | None = None,
    interval: float = _HEARTBEAT_INTERVAL,
    completed_units: float | None = None,
    total_units: float | None = None,
) -> Iterator[None]:
    """Emit a progress StepEvent every 0.5 s during blocking work.

    Used for blocking actions that produce no child stdout (HTTP restore,
    auxiliary readiness poll).  The heartbeat stops on context exit; percent
    is shown only when ``total_units`` is provided.
    """
    active = observer if observer is not None else _active_observer()
    if active is None:
        yield
        return
    started = time.monotonic()
    publish_stage(stage_id, kind="started", observer=active, elapsed=0.0)
    stop = threading.Event()

    def tick() -> None:
        while not stop.wait(interval):
            publish_stage(
                stage_id,
                kind="progress",
                observer=active,
                elapsed=time.monotonic() - started,
                completed_units=completed_units,
                total_units=total_units,
            )

    thread = threading.Thread(target=tick, daemon=True, name=f"odcli-restore-{stage_id}")
    thread.start()
    try:
        yield
    except BaseException as error:
        stop.set()
        elapsed = time.monotonic() - started
        from odoo_instance_sdk.internal.sanitize import sanitize_event_message

        publish_stage(
            stage_id,
            kind="failed",
            observer=active,
            elapsed=elapsed,
            error=sanitize_event_message(str(error)) or "restore stage failed",
        )
        attach_restore_stage_failure(error, stage_id=stage_id, elapsed=elapsed)
        raise
    else:
        stop.set()
        elapsed = time.monotonic() - started
        publish_stage(stage_id, kind="completed", observer=active, elapsed=elapsed)


__all__ = [
    "RESTORE_STAGE_IDS",
    "attach_restore_stage_failure",
    "publish_stage",
    "restore_stage",
    "restore_stage_heartbeat",
]
