from __future__ import annotations

from unittest.mock import patch

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.exceptions import (
    ProcessExitedBeforeReady,
    ProcessNotFoundError,
    ReadinessTimeoutError,
)
from odoo_instance_sdk.models import CommandResult, OdooProcess, ReadinessResult, StartConfig


def _make_client() -> OdooClient:
    return OdooClient(config=OdooClientConfig(executable="python3"))


def test_instance_start_stop() -> None:
    client = _make_client()
    inst = client.instance(base_url="http://localhost:8069")
    mock_handle = object()

    with patch("odoo_instance_sdk.resources.instance.start_process") as mock_start:
        fake_proc = OdooProcess(id="test-start", pid=12345, args=[], started_at=0.0)
        mock_start.return_value = (fake_proc, mock_handle, None)

        proc = inst.start(StartConfig(http_port=9999))

        assert proc.id in client._processes
        assert proc.id in client._handles
        assert client._handles[proc.id] is mock_handle

    with patch("odoo_instance_sdk.resources.instance.stop_process") as mock_stop:
        inst.stop(proc)

        assert proc.id not in client._processes
        assert proc.id not in client._handles
        mock_stop.assert_called_once()


def test_instance_status() -> None:
    client = _make_client()
    inst = client.instance(base_url="http://localhost:8069")
    mock_handle = object()

    fake_proc = OdooProcess(id="test-status", pid=12345, args=[], started_at=0.0)
    client._processes[fake_proc.id] = fake_proc
    client._handles[fake_proc.id] = mock_handle

    with patch("odoo_instance_sdk.resources.instance.get_process_status") as mock_status:
        mock_status.return_value = object()
        inst.status(fake_proc)
        mock_status.assert_called_once_with(mock_handle)


def test_instance_status_unregistered() -> None:
    client = _make_client()
    inst = client.instance(base_url="http://localhost:8069")
    fake = OdooProcess(id="unknown", pid=9999, args=[], started_at=0.0)

    with pytest.raises(ProcessNotFoundError):
        inst.status(fake)


def test_readiness_success() -> None:
    client = _make_client()
    inst = client.instance(base_url="http://localhost:8069")
    fake_proc = OdooProcess(id="test-ready", pid=12345, args=[], started_at=0.0)
    client._processes[fake_proc.id] = fake_proc
    client._handles[fake_proc.id] = object()

    with patch("odoo_instance_sdk.internal.health.poll_health") as mock_poll:
        mock_poll.return_value = ReadinessResult(
            ok=True, elapsed=0.1, attempts=1, final_status="pass"
        )
        result = inst.wait_ready(fake_proc, timeout=5.0)
        assert result.ok is True
        assert result.final_status == "pass"
        assert result.attempts == 1


def test_readiness_process_exit() -> None:
    client = _make_client()
    inst = client.instance(base_url="http://localhost:8069")
    fake_proc = OdooProcess(id="test-exit", pid=12345, args=[], started_at=0.0)
    client._processes[fake_proc.id] = fake_proc

    with patch("odoo_instance_sdk.internal.health.poll_health") as mock_poll:
        mock_poll.side_effect = ProcessExitedBeforeReady("exited")
        with pytest.raises(ProcessExitedBeforeReady):
            inst.wait_ready(fake_proc, timeout=5.0)


def test_readiness_timeout() -> None:
    client = _make_client()
    inst = client.instance(base_url="http://localhost:8069")
    fake_proc = OdooProcess(id="test-timeout", pid=12345, args=[], started_at=0.0)
    client._processes[fake_proc.id] = fake_proc
    client._handles[fake_proc.id] = object()

    with patch("odoo_instance_sdk.internal.health.poll_health") as mock_poll:
        mock_poll.side_effect = ReadinessTimeoutError(timeout=1.0)
        with pytest.raises(ReadinessTimeoutError):
            inst.wait_ready(fake_proc, timeout=1.0)


def test_shared_registry() -> None:
    client = _make_client()
    inst_a = client.instance(base_url="http://localhost:8069")
    inst_b = client.instance(base_url="http://127.0.0.1:8070")

    assert inst_a._client is inst_b._client

    mock_handle = object()
    fake_proc = OdooProcess(id="shared", pid=12345, args=[], started_at=0.0)

    with patch("odoo_instance_sdk.resources.instance.start_process") as mock_start:
        mock_start.return_value = (fake_proc, mock_handle, None)
        inst_a.start(StartConfig(http_port=9999))

    assert inst_b._client.get_process("shared") is fake_proc
    assert inst_b._client._handles["shared"] is mock_handle


def test_instance_repr() -> None:
    client = _make_client()
    inst = client.instance(base_url="http://localhost:8069")
    r = repr(inst)
    assert "base_url" in r
    assert "http://localhost:8069" in r


def test_instance_run_executes_subprocess() -> None:
    client = _make_client()
    inst = client.instance(base_url="http://localhost:8069")
    result = inst.run(["-c", "import sys; sys.exit(0)"])
    assert isinstance(result, CommandResult)
    assert result.returncode == 0
    assert result.args[0] == client.config.executable


def test_from_config_empty_db_host_does_not_default_port(tmp_path) -> None:
    """MT-2: empty db_host in configparser must not set db_port to 5432."""
    path = tmp_path / "odoo.conf"
    path.write_text(
        "[options]\n"
        "http_port = 8069\n"
        "http_interface = 127.0.0.1\n"
        "admin_passwd = mypass\n"
        "db_host = \n"
    )
    client = _make_client()
    inst = client.instance.from_config(path)
    assert inst.config.db_host is None
    assert inst.config.db_port is None


def test_from_config_db_host_sets_default_port(tmp_path) -> None:
    """MT-2 (control): non-empty db_host should default port to 5432."""
    path = tmp_path / "odoo.conf"
    path.write_text(
        "[options]\n"
        "http_port = 8069\n"
        "http_interface = 127.0.0.1\n"
        "admin_passwd = mypass\n"
        "db_host = localhost\n"
    )
    client = _make_client()
    inst = client.instance.from_config(path)
    assert inst.config.db_host == "localhost"
    assert inst.config.db_port == 5432


if __name__ == "__main__":
    pytest.main([__file__])
