"""Minimal self-check for server lifecycle (start/stop/status)."""

import socket
import subprocess
import sys
import time
import uuid

from odoo_instance_sdk import StartConfig
from odoo_instance_sdk.exceptions import (
    ProcessExitedBeforeReady,
    ProcessNotFoundError,
)
from odoo_instance_sdk.models import OdooProcess
from tests._helpers import make_client


def test_start_structure() -> None:
    """Verify start() returns a properly structured OdooProcess."""
    client = make_client()

    proc = client.server.start(StartConfig(http_port=9999), cwd="/tmp")

    assert proc.id and len(proc.id) == 32, f"bad id: {proc.id}"
    assert proc.pid > 0, f"bad pid: {proc.pid}"
    assert "--http-port" in proc.args, f"--http-port not in {proc.args}"
    assert proc.started_at > 0, f"bad started_at: {proc.started_at}"

    proc_handle = client.server.get_handle(proc.id)
    assert proc_handle is not None
    proc_handle.wait(timeout=3)

    client.server.stop(proc)
    print("test_start_structure PASSED")


def test_lifecycle() -> None:
    """Start a long-running python process, check status, stop it."""
    client = make_client()

    proc_handle = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )

    odoo_proc = OdooProcess(
        id=uuid.uuid4().hex,
        pid=proc_handle.pid,
        args=[sys.executable, "-c", "time.sleep(30)"],
        started_at=time.perf_counter(),
    )
    client.server.register(odoo_proc, proc_handle)

    status = client.server.status(odoo_proc)
    assert status.state == "running", f"expected running, got {status.state}"
    assert status.returncode is None, f"unexpected returncode: {status.returncode}"

    client.server.stop(odoo_proc, timeout=3.0)

    # After stop, process is removed from registry — status raises
    try:
        client.server.status(odoo_proc)
        assert False, "Should have raised after stop"
    except ProcessNotFoundError:
        pass

    # Double stop should be idempotent
    client.server.stop(odoo_proc)
    print("test_lifecycle PASSED")


def test_status_exited_process() -> None:
    """Check status of a process that exited on its own (before stop)."""
    client = make_client()

    proc_handle = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(42)"],
        start_new_session=True,
    )

    odoo_proc = OdooProcess(
        id=uuid.uuid4().hex,
        pid=proc_handle.pid,
        args=[sys.executable, "-c", "sys.exit(42)"],
        started_at=time.perf_counter(),
    )
    client.server.register(odoo_proc, proc_handle)
    proc_handle.wait()

    status = client.server.status(odoo_proc)
    assert status.state == "exited", f"expected exited, got {status.state}"
    assert status.returncode == 42, f"expected 42, got {status.returncode}"

    client.server.stop(odoo_proc)
    print("test_status_exited_process PASSED")


def test_status_unknown_process() -> None:
    client = make_client()

    fake = OdooProcess(id="nope", pid=9999, args=["test"], started_at=0.0)

    try:
        client.server.status(fake)
        assert False, "Should have raised"
    except ProcessNotFoundError:
        print("test_status_unknown_process PASSED")


def test_stop_unknown_process_is_idempotent() -> None:
    """stop() on an unknown process should not raise (fully idempotent)."""
    client = make_client()

    fake = OdooProcess(id="nope", pid=9999, args=["test"], started_at=0.0)

    # Should not raise
    client.server.stop(fake)
    print("test_stop_unknown_process_is_idempotent PASSED")


def test_double_stop_idempotent() -> None:
    client = make_client()

    proc_handle = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )

    odoo_proc = OdooProcess(
        id=uuid.uuid4().hex,
        pid=proc_handle.pid,
        args=[sys.executable, "-c", "time.sleep(30)"],
        started_at=time.perf_counter(),
    )
    client.server.register(odoo_proc, proc_handle)

    client.server.stop(odoo_proc, timeout=3.0)
    client.server.stop(odoo_proc)
    print("test_double_stop_idempotent PASSED")


def test_wait_ready_raises_when_process_exited() -> None:
    """If linked process exits before readiness, raise ProcessExitedBeforeReady."""
    client = make_client(base_url="http://127.0.0.1:1")

    proc = OdooProcess(
        id="test-dead-proc",
        pid=99999,
        args=[],
        started_at=time.time(),
    )
    client._processes[proc.id] = proc

    try:
        client.server.wait_ready(proc, timeout=1.0)
        assert False, "Should have raised ProcessExitedBeforeReady"
    except ProcessExitedBeforeReady:
        pass
    finally:
        client._processes.pop(proc.id, None)
    print("test_wait_ready_raises_when_process_exited PASSED")


def test_cross_client_isolation() -> None:
    """Process registered in client A is not visible to client B."""
    client_a = make_client(base_url="http://localhost:8069")
    client_b = make_client(base_url="http://localhost:8069")

    proc = OdooProcess(
        id="isolation-test",
        pid=99999,
        args=[],
        started_at=time.time(),
    )
    client_a._processes[proc.id] = proc

    assert client_a._get_process(proc.id) is proc

    try:
        client_b._get_process(proc.id)
        assert False, "Should have raised ProcessNotFoundError"
    except ProcessNotFoundError:
        pass

    client_a._processes.pop(proc.id, None)
    print("test_cross_client_isolation PASSED")


def test_two_concurrent_starts() -> None:
    """Two concurrent start() calls register two distinct processes."""
    client = make_client()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s1:
        s1.bind(("", 0))
        port1 = s1.getsockname()[1]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
        s2.bind(("", 0))
        port2 = s2.getsockname()[1]

    proc1 = client.server.start(StartConfig(http_port=port1), cwd="/tmp")
    proc2 = client.server.start(StartConfig(http_port=port2), cwd="/tmp")

    assert proc1.id != proc2.id
    assert proc1.pid != proc2.pid

    assert client._get_process(proc1.id) is proc1
    assert client._get_process(proc2.id) is proc2

    client.server.stop(proc1, timeout=3.0)
    client.server.stop(proc2, timeout=3.0)
    print("test_two_concurrent_starts PASSED")


def test_odoo_process_repr_masks_db_password() -> None:
    """OdooProcess.__repr__ must redact --db-password value."""
    proc = OdooProcess(id="x", pid=1, args=["odoo", "--db-password", "s3cret"], started_at=0.0)
    r = repr(proc)
    assert "s3cret" not in r, f"db_password value leaked: {r!r}"
    assert "<redacted>" in r, f"no redaction marker in {r!r}"
    print("test_odoo_process_repr_masks_db_password PASSED")


if __name__ == "__main__":
    test_start_structure()
    test_lifecycle()
    test_status_exited_process()
    test_status_unknown_process()
    test_stop_unknown_process_is_idempotent()
    test_double_stop_idempotent()
    test_wait_ready_raises_when_process_exited()
    test_cross_client_isolation()
    test_two_concurrent_starts()
    test_odoo_process_repr_masks_db_password()
