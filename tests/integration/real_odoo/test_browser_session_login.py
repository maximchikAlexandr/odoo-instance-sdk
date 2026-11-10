from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig

from .conftest import E2ERuntime

pytestmark = [pytest.mark.real_odoo, pytest.mark.e2e_full, pytest.mark.serial]


def _post_json(url: str, payload: dict[str, Any], sid: str) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Cookie": f"session_id={sid}",
        },
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read()
        return response.status, json.loads(content) if content else {}


@pytest.mark.timeout(180)
def test_source_backed_selected_user_session_and_logout(
    target_runtime: E2ERuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Any,
) -> None:
    runtime = target_runtime
    for key, value in runtime.environment.items():
        monkeypatch.setenv(key, value)
    database = runtime.topology.target_sentinel_database
    config_text = runtime.config_file.read_text(encoding="utf-8")
    config_text += f"\ndb_name = {database}\ndbfilter = ^{database}$\n"
    config_path = tmp_path / "browser-login-odoo.conf"
    config_path.write_text(config_text, encoding="utf-8")
    config_path.chmod(0o600)

    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    instance = client.instance.from_config(config_path)
    login = f"odcli_open_as_{runtime.run_id[:12]}"
    create_user = """import sys
login = sys.argv[1]
users = env['res.users'].sudo()
user = users.search([('login', '=', login)], limit=1)
if not user:
    user = users.create({
        'login': login,
        'name': 'OdCLI Browser Session Fixture',
        'active': True,
        'share': False,
        'groups_id': [(6, 0, [env.ref('base.group_user').id])],
    })
env.cr.commit()
print(user.id)
"""
    cleanup_user = """import sys
env['res.users'].sudo().search([('login', '=', sys.argv[1])]).unlink()
env.cr.commit()
"""
    process = None
    try:
        instance.run_shell_script_command(create_user, argv=(login,), timeout=30, commit=True).run()
        users = instance.list_browser_login_users_command(login).run()
        assert len(users) == 1
        selected = users[0]
        process = instance.start()
        instance.wait_ready(process, timeout=120)
        session = instance.create_browser_session_command(selected.id).run()
        status, response = _post_json(
            instance.config.base_url + "/web/session/get_session_info",
            {"jsonrpc": "2.0", "method": "call", "params": {}},
            session.sid,
        )
        assert status == 200
        result = response["result"]
        assert result["uid"] == selected.id
        assert result["db"] == database
        logout_status, _ = _post_json(
            instance.config.base_url + "/web/session/destroy",
            {"jsonrpc": "2.0", "method": "call", "params": {}},
            session.sid,
        )
        assert logout_status == 200
        stale_status, stale_response = _post_json(
            instance.config.base_url + "/web/session/get_session_info",
            {"jsonrpc": "2.0", "method": "call", "params": {}},
            session.sid,
        )
        assert stale_status == 200
        stale_info = stale_response.get("result") or {}
        assert stale_info.get("uid") in (None, False)
        deactivate_user = """import sys
user = env['res.users'].sudo().browse(int(sys.argv[1])).exists()
if user:
    user.write({'active': False})
env.cr.commit()
"""
        instance.run_shell_script_command(
            deactivate_user, argv=(str(selected.id),), timeout=30, commit=True
        ).run()
        with pytest.raises(RuntimeError, match="session creation failed") as failure:
            instance.create_browser_session_command(selected.id).run()
        assert "sid" not in str(failure.value).lower()
        record_property(
            "browser_login_selected_session",
            json.dumps(
                {
                    "uid": selected.id,
                    "database": database,
                    "logout_status": logout_status,
                    "stale_uid": stale_info.get("uid"),
                    "inactive_user_rejected": True,
                }
            ),
        )
    finally:
        if process is not None:
            instance.stop(process)
        instance.run_shell_script_command(
            cleanup_user, argv=(login,), timeout=30, commit=True
        ).run()
