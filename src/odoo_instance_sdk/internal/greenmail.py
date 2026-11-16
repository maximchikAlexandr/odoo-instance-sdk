"""User-level GreenMail paths, Docker argv, and mailbox credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import psutil

from odoo_instance_sdk.exceptions import (
    MailConnectionError,
    MailTimeoutError,
)
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.paths import get_mail_root

IMAGE = "greenmail/standalone:2.1.15"
DOCKER_HOSTNAME = "odcli-greenmail"
BIND_HOST = "127.0.0.1"
SMTP_CONTAINER_PORT = 3025
IMAP_CONTAINER_PORT = 3143
API_CONTAINER_PORT = 8080
READY_TIMEOUT = 30.0
LIMITATION = (
    "GreenMail accepts mail locally and does not validate provider delivery, SPF, DKIM, or DMARC."
)
_LABEL_MANAGED = "odcli.mail.managed"
_LABEL_OWNER = "odcli.mail.owner"
_GREENMAIL_OPTS = "-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0"


@dataclass(frozen=True, slots=True)
class PublishedPorts:
    """Host ports published only on loopback."""

    smtp: int
    imap: int
    api: int


@dataclass(frozen=True, slots=True)
class InspectedContainer:
    """Ownership and publish facts read from ``docker inspect``."""

    owned: bool
    running: bool
    loopback: bool
    ports: PublishedPorts | None


def owner_token() -> str:
    """Return the OS user id that owns this GreenMail service."""
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        return str(getuid())
    return str(os.environ.get("USERNAME", "windows"))


def container_name() -> str:
    """Return the GreenMail container name reserved for this OS user."""
    return f"odcli-greenmail-{owner_token()}"


def network_name() -> str:
    """Return the Docker network reserved for this OS user."""
    return f"odcli-greenmail-{owner_token()}"


def slug(value: str) -> str:
    """Return a mailbox-local token made only of lowercase letters, digits, and dashes."""
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    collapsed = "-".join(part for part in cleaned.split("-") if part)
    return (collapsed or "odcli")[:40]


def mailbox_address(database: str) -> str:
    """Return the stable IMAP mailbox for one Odoo database."""
    return f"odcli-{slug(database)}@example.test"


def mail_recipient(project: str, run_id: str) -> str:
    """Return an isolated recipient for one project run.

    Args:
        project: Project slug used in the local part.
        run_id: Run identifier that keeps parallel tests apart.
    """
    token = hashlib.sha256(f"{project}\n{run_id}".encode()).hexdigest()[:8]
    return f"{slug(project)}-{slug(run_id)[:24]}-{token}@example.test"


def _identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def secret_path() -> Path:
    """Return the owner-only HMAC key path without creating it."""
    return get_mail_root(ensure_exists=False) / "secret"


def credentials_path(email: str) -> Path:
    """Return the owner-only credential file for one mailbox."""
    return get_mail_root(ensure_exists=False) / "mailboxes" / f"{_identity(email)}.json"


def snapshot_path(database: str, host: str | None, port: int) -> Path:
    """Return the active-flag snapshot for one database on one server."""
    identity = _identity(f"{host or ''}:{port}:{database}")
    return get_mail_root(ensure_exists=False) / "snapshots" / f"{identity}.json"


def request_path(database: str, operation: str) -> Path:
    """Return the shell request path for one database operation."""
    return (
        get_mail_root(ensure_exists=False) / "requests" / f"{_identity(database)}-{operation}.json"
    )


def service_path() -> Path:
    """Return the secret-free published-port record."""
    return get_mail_root(ensure_exists=False) / "service.json"


def write_private(path: Path, text: str) -> None:
    """Write text to an owner-only file."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)


def load_secret() -> bytes:
    """Return the user HMAC key, creating it on first use."""
    path = secret_path()
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    secret = os.urandom(32)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, secret)
    finally:
        os.close(descriptor)
    return secret


def mailbox_password(email: str) -> str:
    """Return the repeatable password for one mailbox."""
    digest = hmac.new(load_secret(), email.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def store_credentials(email: str) -> Path:
    """Persist the repeatable mailbox password and return its path."""
    path = credentials_path(email)
    write_private(path, json.dumps({"email": email, "password": mailbox_password(email)}))
    return path


def store_service(ports: PublishedPorts) -> None:
    """Record the loopback ports without credentials."""
    write_private(
        service_path(),
        json.dumps(
            {
                "image": IMAGE,
                "smtp_port": ports.smtp,
                "imap_port": ports.imap,
                "api_port": ports.api,
            },
            sort_keys=True,
        ),
    )


def load_service() -> PublishedPorts | None:
    """Return the last recorded loopback ports, if any."""
    path = get_mail_root(ensure_exists=False) / "service.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PublishedPorts(
        smtp=int(payload["smtp_port"]),
        imap=int(payload["imap_port"]),
        api=int(payload["api_port"]),
    )


def allocate_ports() -> PublishedPorts:
    """Reserve three distinct loopback ports."""
    sockets: list[socket.socket] = []
    chosen: list[int] = []
    try:
        for preferred in (13025, 13143, 18080):
            held = socket.socket()
            held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                held.bind((BIND_HOST, preferred))
            except OSError:
                held.bind((BIND_HOST, 0))
            chosen.append(int(held.getsockname()[1]))
            sockets.append(held)
    finally:
        for held in sockets:
            held.close()
    return PublishedPorts(smtp=chosen[0], imap=chosen[1], api=chosen[2])


def _labels() -> tuple[str, ...]:
    return (
        "--label",
        f"{_LABEL_MANAGED}=true",
        "--label",
        f"{_LABEL_OWNER}={owner_token()}",
    )


def inspect_argv() -> tuple[str, ...]:
    """Return the read-only container inspect argv."""
    return ("docker", "inspect", container_name())


def network_inspect_argv() -> tuple[str, ...]:
    """Return the read-only network inspect argv."""
    return ("docker", "network", "inspect", network_name())


def network_create_argv() -> tuple[str, ...]:
    """Return the argv that creates the user-owned GreenMail network."""
    return ("docker", "network", "create", *_labels(), network_name())


def remove_argv() -> tuple[str, ...]:
    """Return the argv that removes only the named GreenMail container."""
    return ("docker", "rm", "--force", container_name())


def run_argv(ports: PublishedPorts) -> tuple[str, ...]:
    """Return the argv that publishes GreenMail on loopback only."""
    return (
        "docker",
        "run",
        "--detach",
        "--name",
        container_name(),
        "--restart",
        "no",
        "--memory",
        "512m",
        "--network",
        network_name(),
        "--network-alias",
        DOCKER_HOSTNAME,
        "--publish",
        f"{BIND_HOST}:{ports.smtp}:{SMTP_CONTAINER_PORT}",
        "--publish",
        f"{BIND_HOST}:{ports.imap}:{IMAP_CONTAINER_PORT}",
        "--publish",
        f"{BIND_HOST}:{ports.api}:{API_CONTAINER_PORT}",
        *_labels(),
        "--env",
        f"GREENMAIL_OPTS={_GREENMAIL_OPTS}",
        "--env",
        "JAVA_OPTS=-Djava.net.preferIPv4Stack=true -Xmx256m",
        IMAGE,
    )


def docker_ps_argv(http_port: int) -> tuple[str, ...]:
    """Return the read-only process list for one published HTTP port."""
    return (
        "docker",
        "ps",
        "--filter",
        f"publish={http_port}",
        "--format",
        "{{json .}}",
    )


def _json_value(stdout: str) -> JsonValue:
    """Parse one Docker JSON document or the first element of a JSON array."""
    text = stdout.strip()
    if not text:
        return None
    if text.startswith(("{", "[")):
        payload = cast("JsonValue", json.loads(text))
        if isinstance(payload, list):
            return payload[0] if payload else None
        return payload
    rows = [cast("JsonValue", json.loads(line)) for line in text.splitlines() if line.strip()]
    return rows or None


def _label_map(payload: dict[str, JsonValue]) -> dict[str, str]:
    config = payload.get("Config")
    if not isinstance(config, dict):
        config = payload
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        return {}
    return {str(key): str(value) for key, value in labels.items()}


def _owned(labels: dict[str, str]) -> bool:
    return labels.get(_LABEL_MANAGED) == "true" and labels.get(_LABEL_OWNER) == owner_token()


def _published_port(payload: dict[str, JsonValue], container_port: int) -> tuple[str, int] | None:
    settings = payload.get("NetworkSettings")
    if not isinstance(settings, dict):
        return None
    ports = settings.get("Ports")
    if not isinstance(ports, dict):
        return None
    bindings = ports.get(f"{container_port}/tcp")
    if not isinstance(bindings, list) or not bindings or not isinstance(bindings[0], dict):
        return None
    host_ip = str(bindings[0].get("HostIp", ""))
    host_port = bindings[0].get("HostPort")
    if not isinstance(host_port, str) or not host_port.isdigit():
        return None
    return host_ip, int(host_port)


def parse_inspect(stdout: str) -> InspectedContainer | None:
    """Parse one container inspect document."""
    try:
        payload = _json_value(stdout)
    except json.JSONDecodeError as exc:
        raise MailConnectionError("GreenMail inspect returned invalid JSON") from exc
    if not isinstance(payload, dict):
        return None
    labels = _label_map(payload)
    state = payload.get("State")
    running = isinstance(state, dict) and bool(state.get("Running"))
    published = (
        _published_port(payload, SMTP_CONTAINER_PORT),
        _published_port(payload, IMAP_CONTAINER_PORT),
        _published_port(payload, API_CONTAINER_PORT),
    )
    if any(item is None for item in published):
        return InspectedContainer(_owned(labels), running, False, None)
    smtp, imap, api = published
    assert smtp is not None and imap is not None and api is not None
    loopback = all(item[0] in {BIND_HOST, "::1"} for item in (smtp, imap, api))
    return InspectedContainer(
        owned=_owned(labels),
        running=running,
        loopback=loopback,
        ports=PublishedPorts(smtp=smtp[1], imap=imap[1], api=api[1]),
    )


def parse_network_owned(stdout: str) -> bool | None:
    """Return whether the GreenMail network exists and is owned."""
    try:
        payload = _json_value(stdout)
    except json.JSONDecodeError as exc:
        raise MailConnectionError("GreenMail network inspect returned invalid JSON") from exc
    if not isinstance(payload, dict):
        return None
    return _owned(_label_map(payload))


def odoo_container_ids(stdout: str) -> tuple[str, ...]:
    """Return Odoo container ids from ``docker ps`` JSON lines."""
    found: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            continue
        name = str(item.get("Names", "")).lower()
        identifier = item.get("ID", item.get("Id"))
        if "odcli-greenmail" not in name and isinstance(identifier, str) and identifier:
            found.append(identifier)
    return tuple(found)


def host_database_running(database: str) -> bool:
    """Return whether a host Odoo process argv contains the database name."""
    try:
        processes = psutil.process_iter(["cmdline"])
    except (OSError, psutil.Error):
        return False
    for process in processes:
        cmdline = process.info.get("cmdline") or []
        if database in cmdline and any("odoo" in part.lower() for part in cmdline):
            return True
    return False


def http_status(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    timeout: float = 3,
) -> int:
    """Perform one GreenMail HTTP call and return its status code."""
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            return int(response.status)
    except urllib.error.HTTPError as exc:
        exc.read()
        return int(exc.code)
    except urllib.error.URLError as exc:
        raise MailConnectionError("GreenMail API is unreachable") from exc


def create_mailbox(api_port: int, email: str, password: str) -> None:
    """Create one GreenMail user, replacing only that user when it already exists."""
    url = f"http://{BIND_HOST}:{api_port}/api/user"
    body = json.dumps({"email": email, "login": email, "password": password}).encode("utf-8")
    if http_status(url, method="POST", body=body) == 200:
        return
    quoted = urllib.parse.quote(email, safe="@")
    http_status(f"{url}/{quoted}", method="DELETE")
    if http_status(url, method="POST", body=body) != 200:
        raise MailConnectionError("GreenMail rejected the mailbox")


def wait_ready(api_port: int, deadline: float) -> None:
    """Wait until the readiness endpoint returns 200.

    Connection loss and HTTP 503 stay retryable until the deadline. Any other
    HTTP status is a connection failure.
    """
    url = f"http://{BIND_HOST}:{api_port}/api/service/readiness"
    while True:
        try:
            status = http_status(url)
        except MailConnectionError:
            status = 503
        if status == 200:
            return
        if status != 503:
            raise MailConnectionError("GreenMail readiness failed")
        if time.monotonic() >= deadline:
            raise MailTimeoutError("GreenMail did not become ready before the deadline")
        time.sleep(0.2)
