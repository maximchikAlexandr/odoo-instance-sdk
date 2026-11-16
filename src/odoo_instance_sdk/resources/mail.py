"""Shared user-level GreenMail commands."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, NoReturn, Protocol, TypeVar, cast

from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    MailConfigureError,
    MailConnectionError,
    MailCredentialsError,
    MailFetchmailMissingError,
    MailInboundModelError,
    MailNotOwnedError,
    MailRuntimeAmbiguousError,
    MailSnapshotMissingError,
    MailTimeoutError,
)
from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
from odoo_instance_sdk.internal.greenmail import (
    BIND_HOST,
    DOCKER_HOSTNAME,
    IMAGE,
    IMAP_CONTAINER_PORT,
    LIMITATION,
    SMTP_CONTAINER_PORT,
    PublishedPorts,
    allocate_ports,
    container_name,
    create_mailbox,
    docker_ps_argv,
    host_database_running,
    inspect_argv,
    load_service,
    mail_recipient,
    mailbox_address,
    mailbox_password,
    network_create_argv,
    network_inspect_argv,
    network_name,
    odoo_container_ids,
    owner_token,
    parse_inspect,
    parse_network_owned,
    remove_argv,
    request_path,
    run_argv,
    snapshot_path,
    store_credentials,
    store_service,
    wait_ready,
    write_private,
)
from odoo_instance_sdk.internal.greenmail_shell import ODOO_MAIL_SCRIPT
from odoo_instance_sdk.internal.locks import exclusive_lock, mail_lock_path
from odoo_instance_sdk.internal.pg.context import resolve_database_context
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessExecutor,
    ProcessResultLike,
    RunContext,
)
from odoo_instance_sdk.internal.server import parse_payload
from odoo_instance_sdk.models.command import CommandResult
from odoo_instance_sdk.models.mail import (
    MailConfigureResult,
    MailEndpoint,
    MailPlacementKind,
    MailState,
    MailStatus,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance

Prepared = PreparedStep | PreparedAction
_ResultT = TypeVar("_ResultT")
_LockedT = TypeVar("_LockedT")
_SHELL_ERRORS = (
    ("mail_timeout", MailTimeoutError, "the Odoo runtime timed out reaching GreenMail"),
    ("mail_connection", MailConnectionError, "the Odoo runtime cannot reach GreenMail"),
    ("mail_fetchmail_missing", MailFetchmailMissingError, "fetchmail is not installed"),
    ("mail_inbound_model_missing", MailInboundModelError, "no inbound mail model is installed"),
    ("mail_snapshot_missing", MailSnapshotMissingError, "this database has no mail snapshot"),
    ("mail_credentials_missing", MailCredentialsError, "the mailbox credential file is missing"),
)


def _plan(steps: tuple[Prepared, ...]) -> ExecutionPlan:
    return ExecutionPlan(
        steps=tuple(step.public_projection() for step in steps),
        warnings=(LIMITATION,),
    ).with_fingerprint()


def _text(value: str | bytes | None) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return ""


class _CapturedOutput(Protocol):
    """Process result fields read by mail commands."""

    returncode: int
    stdout: str | bytes | None
    stderr: str | bytes | None


def _output(result: ProcessResultLike) -> tuple[int, str, str]:
    captured = cast("_CapturedOutput", result)
    return captured.returncode, _text(captured.stdout), _text(captured.stderr)


def _lock_action() -> PreparedAction:
    return PreparedAction(
        step_id="mail.lock",
        action="lock",
        description="Lock the user GreenMail state",
        mutating=False,
        read_only=True,
    )


def _inspect_step() -> PreparedStep:
    return PreparedStep(
        step_id="mail.docker.inspect",
        argv=inspect_argv(),
        mutating=False,
        read_only=True,
        timeout=30,
    )


def _service_steps(ports: PublishedPorts) -> tuple[Prepared, ...]:
    return (
        _lock_action(),
        _inspect_step(),
        PreparedStep(
            step_id="mail.docker.network.inspect",
            argv=network_inspect_argv(),
            mutating=False,
            read_only=True,
            timeout=30,
        ),
        PreparedStep(
            step_id="mail.docker.network.create",
            argv=network_create_argv(),
            mutating=True,
            timeout=30,
        ),
        PreparedStep(
            step_id="mail.docker.remove",
            argv=remove_argv(),
            mutating=True,
            timeout=30,
        ),
        PreparedStep(
            step_id="mail.docker.run",
            argv=run_argv(ports),
            mutating=True,
            timeout=60,
        ),
        PreparedAction(
            step_id="mail.readiness",
            action="wait",
            description="Wait until GreenMail is ready on loopback",
            mutating=True,
        ),
    )


def _require_step(
    context: RunContext[_ResultT],
    step_id: str,
    message: str,
    *,
    exists_ok: bool = False,
) -> None:
    code, _stdout, stderr = _output(context.process(step_id))
    if code == 0 or (exists_ok and "already exists" in stderr.lower()):
        return
    raise MailConnectionError(message)


def _ensure_network(context: RunContext[_ResultT]) -> None:
    code, stdout, _stderr = _output(context.process("mail.docker.network.inspect"))
    owned = parse_network_owned(stdout) if code == 0 else None
    if owned is True:
        context.skip("mail.docker.network.create")
        return
    if owned is False:
        raise MailNotOwnedError("GreenMail network is not owned by this user")
    _require_step(
        context,
        "mail.docker.network.create",
        "could not create the GreenMail network",
        exists_ok=True,
    )


def _reconcile(
    context: RunContext[_ResultT], planned: PublishedPorts
) -> tuple[PublishedPorts, bool]:
    code, stdout, _stderr = _output(context.process("mail.docker.inspect"))
    container = None if code != 0 else parse_inspect(stdout)
    if container is not None and not container.owned:
        raise MailNotOwnedError("GreenMail container is not owned by this user")
    healthy = bool(container and container.running and container.loopback and container.ports)
    _ensure_network(context)
    if healthy and container is not None and container.ports is not None:
        context.skip("mail.docker.remove")
        context.skip("mail.docker.run")
        return container.ports, True
    if container is None:
        context.skip("mail.docker.remove")
    else:
        _require_step(context, "mail.docker.remove", "could not replace GreenMail")
    _require_step(context, "mail.docker.run", "could not start GreenMail")
    return planned, False


def _ready(context: RunContext[_ResultT], ports: PublishedPorts) -> None:
    context.action("mail.readiness")
    wait_ready(ports.api, time.monotonic() + 30)
    store_service(ports)
    context.complete_action("mail.readiness")


def _under_lock(context: RunContext[_ResultT], body: Callable[[], _LockedT]) -> _LockedT:
    context.action("mail.lock")
    with exclusive_lock(mail_lock_path()):
        ports = body()
    context.complete_action("mail.lock")
    return ports


def _endpoint(port: int | None) -> MailEndpoint | None:
    if port is None:
        return None
    return MailEndpoint(host=BIND_HOST, port=port)


def _status(
    *,
    state: MailState,
    reused: bool,
    ports: PublishedPorts | None,
) -> MailStatus:
    visible = state == "running"
    return MailStatus(
        state=state,
        reused=reused,
        container_name=container_name(),
        owner=owner_token(),
        bind_host=BIND_HOST,
        smtp=_endpoint(ports.smtp if visible and ports else None),
        imap=_endpoint(ports.imap if visible and ports else None),
        api=_endpoint(ports.api if visible and ports else None),
        docker_hostname=DOCKER_HOSTNAME,
        docker_network=network_name(),
        image=IMAGE,
        limitation=LIMITATION,
    )


class _StatusMarker(Exception):
    """Internal marker for a non-running status outcome."""

    def __init__(self, state: MailState) -> None:
        self.state = state


def _observed_state(code: int, stdout: str, stderr: str) -> PublishedPorts:
    if code != 0:
        missing = "no such object" in stderr.lower() or "not found" in stderr.lower()
        raise _StatusMarker("stopped" if missing else "unavailable")
    container = parse_inspect(stdout)
    if container is None:
        raise _StatusMarker("stopped")
    if not container.owned:
        raise _StatusMarker("not_owned")
    if not container.running:
        raise _StatusMarker("stopped")
    if not container.loopback or container.ports is None:
        raise _StatusMarker("exposed")
    return container.ports


def _read_status(context: RunContext[MailStatus]) -> MailStatus:
    """Read status and close the lock action for every non-error outcome."""

    def read() -> PublishedPorts:
        code, stdout, stderr = _output(context.process("mail.docker.inspect"))
        context.action("mail.status")
        context.complete_action("mail.status")
        return _observed_state(code, stdout, stderr)

    try:
        ports = _under_lock(context, read)
    except _StatusMarker as marker:
        context.complete_action("mail.lock")
        return _status(state=marker.state, reused=False, ports=None)
    return _status(state="running", reused=False, ports=ports)


def mail_status_command(
    *,
    executor: ProcessExecutor | None = None,
) -> Command[MailStatus]:
    """Return the secret-free GreenMail status command.

    Args:
        executor: Optional process executor used by tests.
    """
    steps = (
        _lock_action(),
        _inspect_step(),
        PreparedAction(
            step_id="mail.status",
            action="read",
            description="Read GreenMail status",
            mutating=False,
            read_only=True,
        ),
    )

    def execute(context: RunContext[MailStatus]) -> MailStatus:
        return _read_status(context)

    return Command.create(_plan(steps), execute, steps, executor=executor)


def mail_up_command(*, executor: ProcessExecutor | None = None) -> Command[MailStatus]:
    """Return the command that starts or reuses this user's GreenMail.

    Args:
        executor: Optional process executor used by tests.
    """
    ports = allocate_ports()
    steps = _service_steps(ports)

    def execute(context: RunContext[MailStatus]) -> MailStatus:
        def start() -> tuple[PublishedPorts, bool]:
            actual, reused = _reconcile(context, ports)
            _ready(context, actual)
            return actual, reused

        actual, reused = _under_lock(context, start)
        return _status(state="running", reused=reused, ports=actual)

    return Command.create(_plan(steps), execute, steps, executor=executor)


def mail_down_command(*, executor: ProcessExecutor | None = None) -> Command[MailStatus]:
    """Return the command that stops only this user's GreenMail.

    Args:
        executor: Optional process executor used by tests.
    """
    steps = (
        _lock_action(),
        _inspect_step(),
        PreparedStep(
            step_id="mail.docker.remove",
            argv=remove_argv(),
            mutating=True,
            timeout=30,
        ),
    )

    def execute(context: RunContext[MailStatus]) -> MailStatus:
        def stop() -> PublishedPorts:
            code, stdout, stderr = _output(context.process("mail.docker.inspect"))
            if code != 0:
                context.skip("mail.docker.remove")
                if "no such object" in stderr.lower() or "not found" in stderr.lower():
                    return PublishedPorts(0, 0, 0)
                raise MailConnectionError("could not inspect GreenMail")
            container = parse_inspect(stdout)
            if container is None:
                context.skip("mail.docker.remove")
                return PublishedPorts(0, 0, 0)
            if not container.owned:
                raise MailNotOwnedError("GreenMail container is not owned by this user")
            _require_step(context, "mail.docker.remove", "could not stop GreenMail")
            return container.ports or PublishedPorts(0, 0, 0)

        _under_lock(context, stop)
        return _status(state="stopped", reused=False, ports=None)

    return Command.create(_plan(steps), execute, steps, executor=executor)


def _raise_shell(stdout: str, stderr: str) -> NoReturn:
    text = f"{stdout}\n{stderr}"
    for code, error, message in _SHELL_ERRORS:
        if code in text:
            raise error(message)
    raise MailConfigureError("mail configuration failed")


def _converted(database: str, operation: str, outcome: CommandResult) -> MailConfigureResult:
    stdout, stderr = _text(outcome.stdout), _text(outcome.stderr)
    if outcome.returncode not in (0, None):
        _raise_shell(stdout, stderr)
    payload = parse_payload(stdout)
    if not isinstance(payload, dict):
        _raise_shell(stdout, stderr)
    if payload.get("ok") is not True:
        _raise_shell(stdout, stderr)
    result = payload.get("result")
    if not isinstance(result, dict) or "password" in result:
        raise MailConfigureError("mail configuration returned an unexpected result")
    request = json.loads(request_path(database, operation).read_text(encoding="utf-8"))
    placement = request["placement"]
    if placement not in ("host", "docker"):
        raise MailConfigureError("mail configuration returned an unexpected result")
    return MailConfigureResult(
        database=str(result.get("database", database)),
        mailbox=str(request["mailbox"]),
        credentials_path=str(request["credentials_path"]),
        smtp_host=str(request["smtp_host"]),
        smtp_port=int(request["smtp_port"]),
        imap_host=str(request["imap_host"]),
        imap_port=int(request["imap_port"]),
        placement=placement,
        undone=operation == "undo",
        limitation=LIMITATION,
    )


def _write_request(database: str, operation: str, payload: dict[str, JsonValue]) -> None:
    write_private(
        request_path(database, operation),
        json.dumps(payload, sort_keys=True),
    )


def _request_action(
    context: RunContext[MailConfigureResult],
    database: str,
    operation: str,
    payload: dict[str, JsonValue],
) -> None:
    context.action("mail.request.write")
    _write_request(database, operation, payload)
    context.complete_action("mail.request.write")


def _attach(context: RunContext[MailConfigureResult], container_id: str) -> None:
    result = context.process_dynamic(
        PreparedStep(
            step_id="mail.runtime.connect.dynamic",
            argv=("docker", "network", "connect", network_name(), container_id),
            mutating=True,
            timeout=30,
        )
    )
    _code, _stdout, stderr = _output(result)
    if _code != 0 and "already exists" not in stderr.lower():
        raise MailConnectionError("could not attach the Odoo container to GreenMail")


def _probe_container(context: RunContext[MailConfigureResult], container_id: str) -> None:
    code = (
        "import socket\n"
        f"socket.create_connection(({DOCKER_HOSTNAME!r}, {SMTP_CONTAINER_PORT}), 5).close()\n"
        f"socket.create_connection(({DOCKER_HOSTNAME!r}, {IMAP_CONTAINER_PORT}), 5).close()\n"
    )
    result = context.process_dynamic(
        PreparedStep(
            step_id="mail.runtime.probe.dynamic",
            argv=("docker", "exec", container_id, "python3", "-c", code),
            mutating=False,
            read_only=True,
            timeout=20,
        )
    )
    _code, _stdout, stderr = _output(result)
    if _code == 0:
        return
    if "timed out" in stderr.lower() or "timeout" in stderr.lower():
        raise MailTimeoutError("the Odoo container timed out reaching GreenMail")
    raise MailConnectionError("the Odoo container cannot reach GreenMail")


def _placement(
    context: RunContext[MailConfigureResult],
    database: str,
    ports: PublishedPorts,
) -> tuple[MailPlacementKind, str, int, int, bool]:
    code, stdout, _stderr = _output(context.process("mail.runtime.docker"))
    context.action("mail.runtime.host")
    on_host = host_database_running(database) or code != 0
    context.complete_action("mail.runtime.host")
    host: tuple[MailPlacementKind, str, int, int, bool] = (
        "host",
        BIND_HOST,
        ports.smtp,
        ports.imap,
        True,
    )
    identifiers = () if on_host else odoo_container_ids(stdout)
    if len(identifiers) > 1:
        raise MailRuntimeAmbiguousError("more than one Odoo container publishes this HTTP port")
    if len(identifiers) == 1:
        context.action("mail.runtime.attach")
        _attach(context, identifiers[0])
        _probe_container(context, identifiers[0])
        context.complete_action("mail.runtime.attach")
        return ("docker", DOCKER_HOSTNAME, SMTP_CONTAINER_PORT, IMAP_CONTAINER_PORT, False)
    context.skip("mail.runtime.attach")
    return host


def _configure_steps(ports: PublishedPorts, http_port: int, *, undo: bool) -> tuple[Prepared, ...]:
    if undo:
        return (
            PreparedAction(
                step_id="mail.request.write",
                action="write",
                description="Write the mail undo request",
                mutating=True,
            ),
        )
    return (
        *_service_steps(ports),
        PreparedStep(
            step_id="mail.runtime.docker",
            argv=docker_ps_argv(http_port),
            mutating=False,
            read_only=True,
            timeout=30,
        ),
        PreparedAction(
            step_id="mail.runtime.host",
            action="read",
            description="Check for a host Odoo process for this database",
            mutating=False,
            read_only=True,
        ),
        PreparedAction(
            step_id="mail.runtime.attach",
            action="connect",
            description="Attach the discovered Odoo container to the GreenMail network",
            mutating=True,
        ),
        PreparedAction(
            step_id="mail.mailbox.ensure",
            action="create",
            description=f"Create or reuse the database mailbox. {LIMITATION}",
            mutating=True,
        ),
        PreparedAction(
            step_id="mail.request.write",
            action="write",
            description="Write the mail configuration request",
            mutating=True,
        ),
    )


def mail_configure_command(
    instance: OdooInstance,
    *,
    undo: bool = False,
    executor: ProcessExecutor | None = None,
) -> Command[MailConfigureResult]:
    """Return the command that points one database at GreenMail, or restores it.

    Args:
        instance: Selected Odoo instance. Only its database is changed.
        undo: Restore the saved active flags and remove managed mail rows.
        executor: Optional process executor used by tests.
    """
    selected = resolve_database_context(instance)
    database = selected.database
    if instance.config.start_config is None:
        raise InstanceConfigurationError("mail configure requires an Odoo start configuration")
    http_port = int(instance.config.start_config.http_port)
    email = mailbox_address(database)
    operation = "undo" if undo else "configure"
    ports = allocate_ports()
    extra = _configure_steps(ports, http_port, undo=undo)

    def preflight(context: RunContext[MailConfigureResult]) -> None:
        if undo:
            stored = load_service()
            _request_action(
                context,
                database,
                operation,
                {
                    "database": database,
                    "mailbox": email,
                    "credentials_path": "",
                    "smtp_host": "",
                    "smtp_port": stored.smtp if stored else 0,
                    "imap_host": "",
                    "imap_port": stored.imap if stored else 0,
                    "placement": "host",
                    "probe": False,
                    "operation": operation,
                    "snapshot_path": str(snapshot_path(database, selected.host, selected.port)),
                },
            )
            return

        def prepare() -> None:
            actual, _reused = _reconcile(context, ports)
            _ready(context, actual)
            kind, host, smtp, imap, probe = _placement(context, database, actual)
            context.action("mail.mailbox.ensure")
            credentials = store_credentials(email)
            create_mailbox(actual.api, email, mailbox_password(email))
            context.complete_action("mail.mailbox.ensure")
            _request_action(
                context,
                database,
                operation,
                {
                    "database": database,
                    "mailbox": email,
                    "credentials_path": str(credentials),
                    "smtp_host": host,
                    "smtp_port": smtp,
                    "imap_host": host,
                    "imap_port": imap,
                    "placement": kind,
                    "probe": probe,
                    "operation": operation,
                    "snapshot_path": str(snapshot_path(database, selected.host, selected.port)),
                },
            )

        _under_lock(context, prepare)

    return instance._shell_script_command(
        ODOO_MAIL_SCRIPT,
        argv=(str(request_path(database, operation)),),
        commit=True,
        exclusive=True,
        extra_steps=extra,
        preflight=preflight,
        result_converter=lambda outcome: _converted(database, operation, outcome),
        executor=executor,
    )


__all__ = [
    "mail_configure_command",
    "mail_down_command",
    "mail_recipient",
    "mail_status_command",
    "mail_up_command",
]
