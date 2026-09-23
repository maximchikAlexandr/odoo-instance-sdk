from odoo_instance_sdk.internal.health import poll_health
from odoo_instance_sdk.internal.transport import TransportUnavailableError
from tests.fixtures.transport import make_http_client, make_response, patch_open_odoo_http_client


def test_database_manager_readiness_retries_and_accepts_empty_list() -> None:
    response = make_response(json_data={"jsonrpc": "2.0", "result": []})
    http = make_http_client(post_side_effect=[TransportUnavailableError("not ready"), response])

    with patch_open_odoo_http_client(http):
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
