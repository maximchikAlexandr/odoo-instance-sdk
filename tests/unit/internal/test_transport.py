from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from odoo_instance_sdk.internal.transport import (
    OdooHttpClient,
    TransportStatusError,
    TransportUnavailableError,
)
from odoo_instance_sdk.internal.transport.factory import open_odoo_http_client
from tests.fixtures.transport import make_http_client, make_response, patch_open_odoo_http_client


def test_open_odoo_http_client_delegates_to_for_origin() -> None:
    with patch(
        "odoo_instance_sdk.internal.transport.factory.OdooHttpClient.for_origin",
        return_value=MagicMock(),
    ) as factory:
        client = open_odoo_http_client("http://127.0.0.1:8069", timeout=3.0)
    factory.assert_called_once_with("http://127.0.0.1:8069", timeout=3.0)
    assert client is factory.return_value


def test_post_performs_single_network_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = MagicMock()
    raw.status_code = 200
    raw.is_error = False
    client = MagicMock()
    client.post.return_value = raw
    http = OdooHttpClient(base_url="http://127.0.0.1:8069")
    monkeypatch.setattr(http, "_ensure_client", lambda: client)
    response = http.post("http://127.0.0.1:8069/web/health")
    assert response.status_code == 200
    assert client.post.call_count == 1
    client.post.side_effect = TransportUnavailableError("down")
    with pytest.raises(TransportUnavailableError):
        http.post("http://127.0.0.1:8069/web/health")
    assert client.post.call_count == 2


def test_stream_converts_read_errors_to_transport_errors() -> None:
    raw = MagicMock()
    raw.status_code = 200

    def broken_iter(**_: object):
        yield b"partial"
        import httpx

        raise httpx.ReadError("stream broke")

    raw.iter_bytes.side_effect = broken_iter
    client = MagicMock()
    stream_cm = MagicMock()
    stream_cm.__enter__.return_value = raw
    stream_cm.__exit__.return_value = None
    client.stream.return_value = stream_cm
    http = OdooHttpClient(base_url="http://127.0.0.1:8069")
    with (
        patch.object(http, "_ensure_client", return_value=client),
        pytest.raises(TransportUnavailableError),
        http.stream("POST", "http://127.0.0.1:8069/web/database/backup") as response,
    ):
        list(response.iter_bytes())


def test_status_error_conversion_preserves_status_code() -> None:
    import httpx

    request = httpx.Request("POST", "http://127.0.0.1:8069/web/database/drop")
    response = httpx.Response(502, request=request)
    failure = httpx.HTTPStatusError("bad gateway", request=request, response=response)
    client = MagicMock()
    client.post.side_effect = failure
    http = OdooHttpClient(base_url="http://127.0.0.1:8069")
    with (
        patch.object(http, "_ensure_client", return_value=client),
        pytest.raises(TransportStatusError) as raised,
    ):
        http.post("http://127.0.0.1:8069/web/database/drop")
    assert raised.value.status_code == 502
    assert isinstance(raised.value.__cause__, httpx.HTTPStatusError)


def test_cli_import_does_not_load_httpx() -> None:
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "httpx" or name.startswith("httpx.")
    }
    for name in saved:
        del sys.modules[name]
    import odoo_instance_sdk.cli  # noqa: F401

    assert "httpx" not in sys.modules
    sys.modules.update(saved)


def test_injected_transport_boundary_is_used_for_health() -> None:
    from odoo_instance_sdk.internal.health import poll_health

    response = make_response(json_data={"jsonrpc": "2.0", "result": []})
    http = make_http_client(post_side_effect=[TransportUnavailableError("retry"), response])
    with patch_open_odoo_http_client(http):
        result = poll_health(
            "http://127.0.0.1:8069",
            timeout=1.0,
            poll_interval=0.0,
            database_manager=True,
        )
    assert result.ok is True
    assert result.attempts == 2
