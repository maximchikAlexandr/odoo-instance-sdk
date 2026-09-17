from unittest.mock import MagicMock, patch

import httpx

from odoo_instance_sdk.internal.health import poll_health


def test_database_manager_readiness_retries_and_accepts_empty_list() -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = {"jsonrpc": "2.0", "result": []}
    http = MagicMock(spec=httpx.Client)
    http.post.side_effect = [httpx.ConnectError("not ready"), response]
    context = MagicMock()
    context.__enter__.return_value = http

    with patch("httpx.Client", return_value=context):
        result = poll_health(
            "http://127.0.0.1:8069",
            timeout=1.0,
            poll_interval=0.0,
            database_manager=True,
        )

    assert result.ok is True
    assert result.attempts == 2
    assert result.final_status == "pass"
    http.post.assert_called_with(
        "http://127.0.0.1:8069/web/database/list",
        json={"jsonrpc": "2.0", "method": "call", "params": {}},
    )
