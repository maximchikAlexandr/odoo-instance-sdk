from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.project import ProjectConfig
from scripts.real_odoo_pins import E2E_PINS

from .conftest import E2ERuntime
from .focused_support import focused_project as _focused_project_fixture

focused_project = _focused_project_fixture

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


def _assert_selected_user_session(
    instance: Any,
    selected_user_id: int,
    database: str,
    session_store: Path,
) -> tuple[Any, int]:
    session = instance.create_browser_session_command(selected_user_id).run()
    assert session_store.is_dir()
    status, response = _post_json(
        instance.config.base_url + "/web/session/get_session_info",
        {"jsonrpc": "2.0", "method": "call", "params": {}},
        session.sid,
    )
    assert status == 200
    result = response["result"]
    assert result["uid"] == selected_user_id
    assert result["db"] == database
    return session, result["uid"]


def _assert_logout_invalidates_session(instance: Any, sid: str) -> tuple[int, object]:
    logout_status, _ = _post_json(
        instance.config.base_url + "/web/session/destroy",
        {"jsonrpc": "2.0", "method": "call", "params": {}},
        sid,
    )
    assert logout_status == 200
    stale_status, stale_response = _post_json(
        instance.config.base_url + "/web/session/get_session_info",
        {"jsonrpc": "2.0", "method": "call", "params": {}},
        sid,
    )
    assert stale_status == 200
    stale_uid = (stale_response.get("result") or {}).get("uid")
    assert stale_uid in (None, False)
    return logout_status, stale_uid


def _assert_inactive_user_rejected(instance: Any, selected_user_id: int) -> None:
    deactivate_user = """import sys
user = env['res.users'].sudo().browse(int(sys.argv[1])).exists()
if user:
    user.write({'active': False})
env.cr.commit()
"""
    instance.run_shell_script_command(
        deactivate_user, argv=(str(selected_user_id),), timeout=30, commit=True
    ).run()
    with pytest.raises(RuntimeError, match="session creation failed") as failure:
        instance.create_browser_session_command(selected_user_id).run()
    assert "sid" not in str(failure.value).lower()


@pytest.fixture
def browser_session_runtime(
    target_runtime: E2ERuntime,
    focused_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    runtime = target_runtime
    for key, value in runtime.environment.items():
        monkeypatch.setenv(key, value)
    database = runtime.topology.target_sentinel_database
    project = ProjectConfig.load(focused_project)
    selector = focused_project / ".odcli" / "e2e-environment-id"
    environment_id = selector.read_text(encoding="ascii").strip()
    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    environment = client.environments.get(environment_id)
    instance = client.instance.from_environment(environment)
    start_config = instance.config.start_config
    assert start_config is not None
    assert project.python is not None and Path(project.python).is_file()
    assert project.odoo_bin is not None and Path(project.odoo_bin).is_file()
    command_prefix = instance.config.command_prefix
    assert command_prefix is not None and len(command_prefix) == 2
    assert command_prefix[0] == str(project.python)
    bound_odoo_bin = Path(command_prefix[1]).resolve()
    assert bound_odoo_bin == (focused_project / "odoo-bin").resolve()
    assert Path(project.odoo_bin).resolve().parent == Path(environment.worktree_path).resolve()
    assert start_config.db_name == database
    assert start_config.data_dir is not None
    session_store = Path(start_config.data_dir) / "sessions"
    assert Path(environment.generated_config_path).is_file()
    assert Path(environment.worktree_path).is_dir()
    pinned_source = subprocess.run(
        ["git", "-C", environment.worktree_path, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert pinned_source == E2E_PINS.odoo_source_commit
    bound_source = subprocess.run(
        ["git", "-C", str(bound_odoo_bin.parent), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert bound_source == E2E_PINS.odoo_source_commit
    bound_source_root = bound_odoo_bin.parent
    probe_environment = os.environ.copy()
    probe_environment["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(bound_source_root), probe_environment.get("PYTHONPATH", ""))
        if value
    )
    runtime_probe = subprocess.run(
        [
            command_prefix[0],
            "-c",
            (
                "import json, pathlib, sys, odoo; "
                "import odoo.release as odoo_release; "
                "from odoo.release import version_info; "
                "print(json.dumps({'python': list(sys.version_info[:3]), "
                "'odoo_major': version_info[0], 'odoo_file': str(pathlib.Path(odoo_release.__file__).resolve())}))"
            ),
        ],
        cwd=bound_source_root,
        env=probe_environment,
        capture_output=True,
        text=True,
        check=True,
    )
    runtime_identity = json.loads(runtime_probe.stdout)
    assert runtime_identity["python"] == [int(part) for part in E2E_PINS.cpython.split(".")]
    assert runtime_identity["odoo_major"] == 19
    assert Path(runtime_identity["odoo_file"]).is_relative_to(bound_source_root)
    cli_probe = subprocess.run(
        [
            *command_prefix,
            "server",
            "--config",
            environment.generated_config_path,
            "--help",
        ],
        cwd=bound_source_root,
        env=probe_environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--config" in cli_probe.stdout
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
        'group_ids': [(6, 0, [env.ref('base.group_user').id])],
    })
env.cr.commit()
print(user.id)
"""
    cleanup_user = """import sys
env['res.users'].sudo().search([('login', '=', sys.argv[1])]).unlink()
env.cr.commit()
"""

    def cleanup_selected_user() -> None:
        instance.run_shell_script_command(
            cleanup_user, argv=(login,), timeout=30, commit=True
        ).run()

    process = None
    try:
        instance.run_shell_script_command(create_user, argv=(login,), timeout=30, commit=True).run()
        users = instance.list_browser_login_users_command(login).run()
        assert len(users) == 1
        selected = users[0]
        process = instance.start()
        instance.wait_ready(process, timeout=120)
        yield {
            "runtime": runtime,
            "database": database,
            "instance": instance,
            "session_store": session_store,
            "selected": selected,
            "login": login,
            "cleanup_user": cleanup_selected_user,
        }
    finally:
        try:
            if process is not None:
                instance.stop(process)
        finally:
            cleanup_selected_user()
            assert instance.list_browser_login_users_command(login).run() == ()


def test_source_browser_selected_user_session(
    browser_session_runtime: dict[str, Any],
) -> None:
    case = browser_session_runtime
    _assert_selected_user_session(
        case["instance"], case["selected"].id, case["database"], case["session_store"]
    )


def test_source_browser_logout_invalidates_stale_sid(
    browser_session_runtime: dict[str, Any],
) -> None:
    case = browser_session_runtime
    session, _uid = _assert_selected_user_session(
        case["instance"], case["selected"].id, case["database"], case["session_store"]
    )
    _assert_logout_invalidates_session(case["instance"], session.sid)


def test_source_browser_rejects_inactive_user(
    browser_session_runtime: dict[str, Any],
) -> None:
    case = browser_session_runtime
    _assert_inactive_user_rejected(case["instance"], case["selected"].id)


def test_source_browser_fixture_cleans_up_created_user(
    browser_session_runtime: dict[str, Any],
) -> None:
    case = browser_session_runtime
    assert case["instance"].list_browser_login_users_command(case["login"]).run() == (
        case["selected"],
    )
    case["cleanup_user"]()
    assert case["instance"].list_browser_login_users_command(case["login"]).run() == ()


@pytest.mark.timeout(180)
def test_source_backed_selected_user_session_and_logout(
    browser_session_runtime: dict[str, Any],
    record_property: Any,
) -> None:
    case = browser_session_runtime
    selected = case["selected"]
    session, _session_uid = _assert_selected_user_session(
        case["instance"], selected.id, case["database"], case["session_store"]
    )
    logout_status, stale_uid = _assert_logout_invalidates_session(case["instance"], session.sid)
    _assert_inactive_user_rejected(case["instance"], selected.id)
    record_property(
        "browser_login_selected_session",
        json.dumps(
            {
                "uid": selected.id,
                "database": case["database"],
                "logout_status": logout_status,
                "stale_uid": stale_uid,
                "inactive_user_rejected": True,
            }
        ),
    )
