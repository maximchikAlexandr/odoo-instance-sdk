from __future__ import annotations

import json
import signal
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import psutil
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.context import ResolvedContext
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.execution import Command, JsonValue
from odoo_instance_sdk.internal.proc import is_process_alive, terminate_pid
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import DevelopmentEnvironment, StartConfig
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment import EnvironmentDatabaseMode, EnvironmentState
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.instance.runtime import _runtime_expectations, _RuntimeBinding


class _Catalog:
    def __init__(self, env_row: dict[str, object], runtime_row: dict[str, object] | None) -> None:
        self.env_row = env_row
        self.runtime_row = runtime_row
        self.project_runtime_row: dict[str, object] | None = None
        self.clear_calls: list[tuple[str, int, float]] = []

    def get_environment(self, _environment_id: str) -> dict[str, object]:
        return self.env_row

    def get_runtime(self, owner_kind: str, _owner_id: str) -> dict[str, object] | None:
        return self.project_runtime_row if owner_kind == "project" else self.runtime_row

    def _clear_runtime_if_matches(
        self,
        owner_kind: str,
        owner_id: str,
        *,
        root_pid: int,
        create_time: float,
    ) -> bool:
        row = self.project_runtime_row if owner_kind == "project" else self.runtime_row
        self.clear_calls.append((owner_id, root_pid, create_time))
        if row is None or row["root_pid"] != root_pid or row["create_time"] != create_time:
            return False
        if owner_kind == "project":
            self.project_runtime_row = None
        else:
            self.runtime_row = None
        return True


class _Client:
    def __init__(self, catalog: _Catalog) -> None:
        self.catalog = catalog

    def get_catalog(self) -> _Catalog:
        return self.catalog


def _instance(
    tmp_path: Path,
    *,
    runtime: bool = True,
    default_run_args: tuple[str, ...] = (),
    owner_kind: str = "environment",
) -> tuple[OdooInstance, _Catalog, str]:
    environment_id = str(uuid.uuid4())
    config_path = tmp_path / "odoo.conf"
    config_path.write_text("[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n")
    runtime_cwd = tmp_path / "worktree"
    runtime_cwd.mkdir()
    odoo_bin = tmp_path / "odoo-bin"
    odoo_bin.write_text("")
    env_row: dict[str, object] = {
        "id": environment_id,
        "repository_root": str(tmp_path),
        "git_common_dir": str(tmp_path / ".git"),
        "runtime_json": json.dumps({"odoo_bin": str(odoo_bin), "runtime_cwd": str(runtime_cwd)}),
        "generated_config_path": str(config_path),
        "python_environment_path": sys.executable,
        "python_environment_owned": 0,
    }
    runtime_row: dict[str, object] | None = (
        {"root_pid": 4242, "create_time": 12.5} if runtime else None
    )
    catalog = _Catalog(env_row, runtime_row)
    owner_id = environment_id
    project_id = f"project_{repo_key(tmp_path, tmp_path / '.git')}"
    binding = _RuntimeBinding(
        owner_kind="environment",
        owner_id=environment_id,
        project_id=project_id,
        repository_root=tmp_path,
        git_common_dir=tmp_path / ".git",
    )
    if owner_kind == "project":
        owner_id = project_id
        catalog.project_runtime_row = runtime_row
        catalog.runtime_row = None
        binding = _RuntimeBinding(
            owner_kind="project",
            owner_id=owner_id,
            project_id=project_id,
            repository_root=tmp_path,
            git_common_dir=tmp_path / ".git",
        )
    client = _Client(catalog)
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(config_path=str(config_path)),
            command_prefix=(sys.executable, str(odoo_bin)),
            default_cwd=runtime_cwd,
            default_run_args=default_run_args,
        ),
        _client=client,  # type: ignore[arg-type]
        _environment_id=environment_id if owner_kind == "environment" else None,
        _runtime_binding=binding,
    )
    return instance, catalog, owner_id


def _live_process(
    instance: OdooInstance,
    *,
    mismatch: str | None = None,
    extra_args: tuple[str, ...] = (),
) -> SimpleNamespace:
    catalog = instance._client.get_catalog()
    if instance._runtime_binding is not None and instance._runtime_binding.owner_kind == "project":
        expected_executable, expected_argv, expected_cwd, _config_path = (
            instance._project_runtime_expectations()
        )
    else:
        env_row = cast(
            "Mapping[str, JsonValue]", catalog.get_environment(str(instance._environment_id))
        )
        expected_executable, expected_argv, expected_cwd, _config_path = _runtime_expectations(
            env_row
        )
    argv = (*expected_argv, *instance.config.default_run_args, *extra_args)
    live = SimpleNamespace(
        create_time=lambda: 12.5,
        exe=lambda: expected_executable,
        cmdline=lambda: list(argv),
        cwd=lambda: expected_cwd,
    )
    if mismatch == "argv":
        live.cmdline = lambda: [*argv, "--config", "/wrong"]
    if mismatch == "cwd":
        live.cwd = lambda: "/other"
    if mismatch == "create_time":
        live.create_time = lambda: 99.0
    return live


def _stop_command(
    instance: OdooInstance, owner_kind: str, *, timeout: float = 10.0
) -> Command[dict[str, str | None]]:
    if owner_kind == "project":
        return instance.stop_runtime_command(timeout=timeout)
    return cast(
        "Command[dict[str, str | None]]", instance.stop_environment_command(timeout=timeout)
    )


def _runtime_row(catalog: _Catalog, owner_kind: str) -> dict[str, object] | None:
    return catalog.project_runtime_row if owner_kind == "project" else catalog.runtime_row


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
def test_stop_owned_runtime_revalidates_then_terminates_and_clears(
    tmp_path: Path, owner_kind: str
) -> None:
    instance, catalog, owner_id = _instance(tmp_path, owner_kind=owner_kind)
    calls: list[tuple[int, int | None, float]] = []
    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", return_value=4242),
        patch(
            "odoo_instance_sdk.resources.instance.planning.terminate_pid",
            side_effect=lambda pid, *, process_group_id, timeout, **_kwargs: calls.append(
                (pid, process_group_id, timeout)
            ),
        ),
        patch(
            "odoo_instance_sdk.resources.instance.runtime.is_process_alive",
            return_value=False,
        ),
        patch(
            "odoo_instance_sdk.internal.address.probe_address",
            side_effect=AssertionError("stop must not inspect port state"),
        ),
    ):
        result = _stop_command(instance, owner_kind, timeout=3.0).run()

    expected = {
        "status": "stopped",
        "environment_id": owner_id if owner_kind == "environment" else None,
    }
    if owner_kind == "project":
        expected.update({"owner_kind": "project", "owner_id": owner_id, "project_id": owner_id})
    assert result == expected
    assert calls == [(4242, 4242, 3.0)]
    assert catalog.clear_calls == [(owner_id, 4242, 12.5)]


@pytest.mark.unit
def test_stop_project_runtime_reuses_identity_boundary_and_preserves_owner_neutral_fields(
    tmp_path: Path,
) -> None:
    instance, catalog, project_id = _instance(tmp_path, owner_kind="project")
    calls: list[tuple[int, int | None, float]] = []
    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", return_value=4242),
        patch(
            "odoo_instance_sdk.resources.instance.planning.terminate_pid",
            side_effect=lambda pid, *, process_group_id, timeout, **_kwargs: calls.append(
                (pid, process_group_id, timeout)
            ),
        ),
        patch(
            "odoo_instance_sdk.resources.instance.runtime.is_process_alive",
            return_value=False,
        ),
    ):
        result = instance.stop_runtime_command(timeout=3.0).run()

    assert result == {
        "status": "stopped",
        "owner_kind": "project",
        "owner_id": project_id,
        "project_id": project_id,
        "environment_id": None,
    }
    assert calls == [(4242, 4242, 3.0)]
    assert catalog.project_runtime_row is None


@pytest.mark.unit
def test_stop_project_runtime_mismatch_fails_closed_and_retains_runtime(tmp_path: Path) -> None:
    instance, catalog, _project_id = _instance(tmp_path, owner_kind="project")
    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            return_value=_live_process(instance, mismatch="cwd"),
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", return_value=4242),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
        pytest.raises(RuntimeError, match="runtime identity mismatch"),
    ):
        instance.stop_runtime_command().run()
    terminate.assert_not_called()
    assert catalog.project_runtime_row is not None


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
@pytest.mark.parametrize("mismatch", ["argv", "cwd", "create_time", "process_group"])
def test_stop_mismatch_fails_closed_and_retains_runtime(
    tmp_path: Path, owner_kind: str, mismatch: str
) -> None:
    instance, catalog, _ = _instance(tmp_path, owner_kind=owner_kind)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            return_value=_live_process(instance, mismatch=mismatch),
        ),
        patch(
            "odoo_instance_sdk.resources.instance.identity.os.getpgid",
            return_value=99 if mismatch == "process_group" else 4242,
        ),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
        pytest.raises(RuntimeError, match="runtime identity mismatch"),
    ):
        _stop_command(instance, owner_kind).run()
    terminate.assert_not_called()
    assert _runtime_row(catalog, owner_kind) is not None
    assert catalog.clear_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
def test_stop_inaccessible_identity_fails_closed_and_retains_runtime(
    tmp_path: Path, owner_kind: str
) -> None:
    instance, catalog, _ = _instance(tmp_path, owner_kind=owner_kind)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            side_effect=psutil.AccessDenied(4242),
        ),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
        pytest.raises(RuntimeError, match="identity is inaccessible"),
    ):
        _stop_command(instance, owner_kind).run()
    terminate.assert_not_called()
    assert _runtime_row(catalog, owner_kind) is not None
    assert catalog.clear_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
def test_stop_win32_identity_mismatch_fails_closed_without_taskkill(
    tmp_path: Path, owner_kind: str
) -> None:
    instance, catalog, _ = _instance(tmp_path, owner_kind=owner_kind)
    with (
        patch("odoo_instance_sdk.resources.instance.identity.sys.platform", "win32"),
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            return_value=_live_process(instance, mismatch="create_time"),
        ),
        patch(
            "odoo_instance_sdk.resources.instance.identity.os.getpgid",
            side_effect=AssertionError("Windows must not require POSIX pgid"),
        ),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
        pytest.raises(RuntimeError, match="runtime identity mismatch"),
    ):
        _stop_command(instance, owner_kind).run()
    terminate.assert_not_called()
    assert _runtime_row(catalog, owner_kind) is not None
    assert catalog.clear_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
def test_stop_win32_inaccessible_identity_fails_closed_without_taskkill(
    tmp_path: Path, owner_kind: str
) -> None:
    instance, catalog, _ = _instance(tmp_path, owner_kind=owner_kind)
    with (
        patch("odoo_instance_sdk.resources.instance.identity.sys.platform", "win32"),
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            side_effect=psutil.AccessDenied(4242),
        ),
        patch(
            "odoo_instance_sdk.resources.instance.identity.os.getpgid",
            side_effect=AssertionError("Windows must not require POSIX pgid"),
        ),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
        pytest.raises(RuntimeError, match="identity is inaccessible"),
    ):
        _stop_command(instance, owner_kind).run()
    terminate.assert_not_called()
    assert _runtime_row(catalog, owner_kind) is not None
    assert catalog.clear_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
def test_stop_rejects_runtime_record_changed_after_planning(
    tmp_path: Path, owner_kind: str
) -> None:
    instance, catalog, _ = _instance(tmp_path, owner_kind=owner_kind)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", return_value=4242),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
    ):
        command = _stop_command(instance, owner_kind)
        row = _runtime_row(catalog, owner_kind)
        assert row is not None
        row["create_time"] = 99.0
        with pytest.raises(RuntimeError, match="changed after planning"):
            command.run()
    terminate.assert_not_called()
    assert _runtime_row(catalog, owner_kind) is not None
    assert catalog.clear_calls == []


@pytest.mark.unit
def test_stop_rejects_environment_evidence_changed_after_planning(tmp_path: Path) -> None:
    instance, catalog, _ = _instance(tmp_path)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", return_value=4242),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
    ):
        command = _stop_command(instance, "environment")
        catalog.env_row["runtime_json"] = json.dumps(
            {
                "odoo_bin": str(
                    Path(str(json.loads(str(catalog.env_row["runtime_json"]))["odoo_bin"]))
                ),
                "runtime_cwd": str(
                    Path(str(json.loads(str(catalog.env_row["runtime_json"]))["runtime_cwd"]))
                    / "changed"
                ),
            }
        )
        with pytest.raises(RuntimeError, match="changed after planning"):
            command.run()
    terminate.assert_not_called()
    assert catalog.runtime_row is not None
    assert catalog.clear_calls == []


@pytest.mark.unit
def test_stop_rejects_project_canonical_evidence_changed_after_planning(tmp_path: Path) -> None:
    instance, catalog, _ = _instance(tmp_path, owner_kind="project")
    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", return_value=4242),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
    ):
        command = instance.stop_runtime_command()
        object.__setattr__(instance.config, "default_cwd", tmp_path / "changed")
        with pytest.raises(RuntimeError, match="changed after planning"):
            command.run()
    terminate.assert_not_called()
    assert catalog.project_runtime_row is not None
    assert catalog.clear_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
def test_stop_rejects_live_evidence_changed_after_planning(tmp_path: Path, owner_kind: str) -> None:
    instance, catalog, _ = _instance(tmp_path, owner_kind=owner_kind)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            side_effect=[_live_process(instance), _live_process(instance, mismatch="create_time")],
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", side_effect=[4242, 4242]),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
    ):
        command = _stop_command(instance, owner_kind)
        with pytest.raises(RuntimeError, match="changed after planning"):
            command.run()
    terminate.assert_not_called()
    assert _runtime_row(catalog, owner_kind) is not None
    assert catalog.clear_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
@pytest.mark.parametrize("replacement_field", ["root_pid", "create_time"])
def test_stop_cleanup_race_preserves_replacement_runtime(
    tmp_path: Path, owner_kind: str, replacement_field: str
) -> None:
    instance, catalog, owner_id = _instance(tmp_path, owner_kind=owner_kind)
    replacement = {"root_pid": 5252, "create_time": 25.0}

    def replace_runtime(
        _pid: int, *, process_group_id: int | None, timeout: float, **_kwargs: object
    ) -> None:
        row = _runtime_row(catalog, owner_kind)
        assert row is not None
        row[replacement_field] = replacement[replacement_field]

    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", return_value=4242),
        patch(
            "odoo_instance_sdk.resources.instance.planning.terminate_pid",
            side_effect=replace_runtime,
        ),
        patch("odoo_instance_sdk.resources.instance.runtime.is_process_alive", return_value=False),
        pytest.raises(RuntimeError, match="changed before clearing its row"),
    ):
        _stop_command(instance, owner_kind).run()

    row = _runtime_row(catalog, owner_kind)
    assert row is not None
    assert row["root_pid"] == (replacement["root_pid"] if replacement_field == "root_pid" else 4242)
    assert row["create_time"] == (
        replacement["create_time"] if replacement_field == "create_time" else 12.5
    )
    assert catalog.clear_calls == [(owner_id, 4242, 12.5)]


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
def test_stop_allows_safe_default_launch_args(tmp_path: Path, owner_kind: str) -> None:
    instance, catalog, owner_id = _instance(
        tmp_path, default_run_args=("--dev",), owner_kind=owner_kind
    )
    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", return_value=4242),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
        patch(
            "odoo_instance_sdk.resources.instance.runtime.is_process_alive",
            return_value=False,
        ),
    ):
        result = _stop_command(instance, owner_kind).run()
    expected = {
        "status": "stopped",
        "environment_id": owner_id if owner_kind == "environment" else None,
    }
    if owner_kind == "project":
        expected.update({"owner_kind": "project", "owner_id": owner_id, "project_id": owner_id})
    assert result == expected
    terminate.assert_called_once_with(
        4242, process_group_id=4242, expected_create_time=12.5, timeout=10.0
    )
    assert catalog.clear_calls == [(owner_id, 4242, 12.5)]


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
def test_stop_no_row_is_idempotent(tmp_path: Path, owner_kind: str) -> None:
    instance, catalog, owner_id = _instance(tmp_path, runtime=False, owner_kind=owner_kind)
    with patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate:
        result = _stop_command(instance, owner_kind).run()
    expected = {
        "status": "already_stopped",
        "environment_id": owner_id if owner_kind == "environment" else None,
    }
    if owner_kind == "project":
        expected.update({"owner_kind": "project", "owner_id": owner_id, "project_id": owner_id})
    assert result == expected
    terminate.assert_not_called()
    assert catalog.clear_calls == []


@pytest.mark.unit
def test_terminate_pid_uses_bounded_term_then_kill_escalation() -> None:
    alive = iter((True, True, True, False, False))
    with (
        patch("odoo_instance_sdk.internal.proc.terminate.os.killpg") as killpg,
        patch(
            "odoo_instance_sdk.internal.proc.terminate.is_process_group_alive",
            return_value=False,
        ),
        patch(
            "odoo_instance_sdk.internal.proc.terminate.is_process_alive",
            side_effect=lambda _pid, **_kwargs: next(alive),
        ),
        patch(
            "odoo_instance_sdk.internal.proc.terminate.time.monotonic",
            side_effect=(0, 100, 100, 100, 100),
        ),
        patch("odoo_instance_sdk.internal.proc.terminate.time.sleep"),
    ):
        terminate_pid(4242, process_group_id=4242, timeout=5.0)
    assert [call.args[1] for call in killpg.call_args_list] == [
        signal.SIGTERM,
        signal.SIGKILL,
    ]


@pytest.mark.unit
def test_terminate_pid_kills_surviving_group_after_leader_exits() -> None:
    alive = iter((True, False, False, False, False))
    group_alive = iter((True, True, False, False))
    with (
        patch("odoo_instance_sdk.internal.proc.terminate.os.killpg") as killpg,
        patch(
            "odoo_instance_sdk.internal.proc.terminate.is_process_alive",
            side_effect=lambda _pid, **_kwargs: next(alive),
        ),
        patch(
            "odoo_instance_sdk.internal.proc.terminate.is_process_group_alive",
            side_effect=lambda _group_id: next(group_alive),
        ),
        patch("odoo_instance_sdk.internal.proc.terminate.time.sleep"),
    ):
        terminate_pid(4242, process_group_id=4242, timeout=5.0)
    assert [call.args[1] for call in killpg.call_args_list] == [
        signal.SIGTERM,
        signal.SIGKILL,
    ]


@pytest.mark.unit
def test_terminate_pid_retains_group_safety_when_leader_is_gone() -> None:
    with (
        patch("odoo_instance_sdk.internal.proc.terminate.os.killpg") as killpg,
        patch(
            "odoo_instance_sdk.internal.proc.terminate.is_process_alive",
            return_value=False,
        ),
        patch(
            "odoo_instance_sdk.internal.proc.terminate.is_process_group_alive",
            return_value=True,
        ),
        pytest.raises(RuntimeError, match="process group remains alive"),
    ):
        terminate_pid(4242, process_group_id=4242, expected_create_time=12.5)
    killpg.assert_not_called()


@pytest.mark.unit
def test_terminate_pid_win32_escalates_taskkill_and_verifies_exit() -> None:
    alive = iter((True, True, True, False))
    with (
        patch("odoo_instance_sdk.internal.proc.run.sys.platform", "win32"),
        patch("odoo_instance_sdk.internal.proc.terminate.SubprocessExecutor.execute") as execute,
        patch(
            "odoo_instance_sdk.internal.proc.terminate.is_process_alive",
            side_effect=lambda _pid, **_kwargs: next(alive),
        ),
        patch(
            "odoo_instance_sdk.internal.proc.terminate.time.monotonic",
            side_effect=(0.0, 100.0),
        ),
        patch("odoo_instance_sdk.internal.proc.terminate.time.sleep"),
    ):
        terminate_pid(4242, timeout=5.0)

    commands = [call.args[0].argv for call in execute.call_args_list]
    assert commands == [
        ("taskkill", "/T", "/PID", "4242"),
        ("taskkill", "/T", "/PID", "4242", "/F"),
    ]


@pytest.mark.unit
def test_is_process_alive_uses_portable_status_on_darwin() -> None:
    live_process = SimpleNamespace(
        create_time=lambda: 12.5,
        status=lambda: psutil.STATUS_RUNNING,
    )
    with (
        patch("odoo_instance_sdk.internal.proc.terminate.sys.platform", "darwin"),
        patch("odoo_instance_sdk.internal.proc.terminate.os.kill"),
        patch(
            "odoo_instance_sdk.internal.proc.terminate.psutil.Process",
            return_value=live_process,
        ),
    ):
        assert is_process_alive(4242)


@pytest.mark.unit
def test_terminate_pid_refuses_posix_kill_after_term_pid_reuse() -> None:
    expected_process = SimpleNamespace(
        create_time=lambda: 12.5,
        status=lambda: psutil.STATUS_RUNNING,
    )
    replacement_process = SimpleNamespace(
        create_time=lambda: 99.0,
        status=lambda: psutil.STATUS_RUNNING,
    )
    with (
        patch(
            "odoo_instance_sdk.internal.proc.terminate.psutil.Process",
            side_effect=[expected_process, expected_process, replacement_process],
        ),
        patch("odoo_instance_sdk.internal.proc.terminate.os.killpg") as killpg,
        patch(
            "odoo_instance_sdk.internal.proc.terminate.time.monotonic",
            side_effect=(0.0, 100.0),
        ),
        patch("odoo_instance_sdk.internal.proc.terminate.time.sleep"),
        pytest.raises(RuntimeError, match="identity changed"),
    ):
        terminate_pid(4242, process_group_id=4242, expected_create_time=12.5, timeout=5.0)
    assert [call.args[1] for call in killpg.call_args_list] == [signal.SIGTERM]


@pytest.mark.unit
def test_terminate_pid_refuses_win32_force_kill_after_term_pid_reuse() -> None:
    expected_process = SimpleNamespace(
        create_time=lambda: 12.5,
        status=lambda: psutil.STATUS_RUNNING,
    )
    replacement_process = SimpleNamespace(
        create_time=lambda: 99.0,
        status=lambda: psutil.STATUS_RUNNING,
    )
    with (
        patch("odoo_instance_sdk.internal.proc.terminate.sys.platform", "win32"),
        patch(
            "odoo_instance_sdk.internal.proc.terminate.psutil.Process",
            side_effect=[expected_process, expected_process, replacement_process],
        ),
        patch("odoo_instance_sdk.internal.proc.terminate.SubprocessExecutor.execute") as execute,
        patch(
            "odoo_instance_sdk.internal.proc.terminate.time.monotonic",
            side_effect=(0.0, 100.0),
        ),
        patch("odoo_instance_sdk.internal.proc.terminate.time.sleep"),
        pytest.raises(RuntimeError, match="identity changed"),
    ):
        terminate_pid(4242, expected_create_time=12.5, timeout=5.0)
    assert [call.args[0].argv for call in execute.call_args_list] == [
        ("taskkill", "/T", "/PID", "4242"),
    ]


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
def test_stop_vanished_process_clears_matching_row(tmp_path: Path, owner_kind: str) -> None:
    instance, catalog, owner_id = _instance(tmp_path, owner_kind=owner_kind)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            side_effect=[_live_process(instance), psutil.NoSuchProcess(4242)],
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", return_value=4242),
        patch(
            "odoo_instance_sdk.resources.instance.identity.is_process_group_alive",
            return_value=False,
        ),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
    ):
        result = _stop_command(instance, owner_kind).run()
    expected = {
        "status": "already_stopped",
        "environment_id": owner_id if owner_kind == "environment" else None,
    }
    if owner_kind == "project":
        expected.update({"owner_kind": "project", "owner_id": owner_id, "project_id": owner_id})
    assert result == expected
    terminate.assert_not_called()
    assert catalog.clear_calls == [(owner_id, 4242, 12.5)]


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
def test_stop_vanished_process_retains_row_when_group_survives(
    tmp_path: Path, owner_kind: str
) -> None:
    instance, catalog, _owner_id = _instance(tmp_path, owner_kind=owner_kind)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            side_effect=[_live_process(instance), psutil.NoSuchProcess(4242)],
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", return_value=4242),
        patch(
            "odoo_instance_sdk.resources.instance.identity.is_process_group_alive",
            return_value=True,
        ),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
        pytest.raises(RuntimeError, match="process group remains alive"),
    ):
        _stop_command(instance, owner_kind).run()
    terminate.assert_not_called()
    assert catalog.clear_calls == []
    assert _runtime_row(catalog, owner_kind) is not None


def _real_stop_context(
    tmp_path: Path, instance: OdooInstance, owner_kind: str, owner_id: str
) -> ResolvedContext:
    source: ProjectConfig | DevelopmentEnvironment
    if owner_kind == "project":
        source = ProjectConfig(
            repository_root=tmp_path,
            odoo_bin=tmp_path / "odoo-bin",
            python=sys.executable,
        )
    else:
        source = DevelopmentEnvironment(
            id=uuid.UUID(owner_id),
            name="demo",
            repository_root=str(tmp_path),
            git_common_dir=str(tmp_path / ".git"),
            branch="main",
            base_ref="HEAD",
            worktree_path=str(tmp_path / "worktree"),
            generated_config_path=str(tmp_path / "odoo.conf"),
            python_environment_path=sys.executable,
            python_environment_owned=False,
            dependency_lock_path=str(tmp_path / "uv.lock"),
            http_interface="127.0.0.1",
            http_port=8069,
            db_mode=EnvironmentDatabaseMode.SHARED,
            state=EnvironmentState.READY,
            created_at=datetime.now(UTC),
        )
    return ResolvedContext(
        client=cast("object", instance._client),  # type: ignore[arg-type]
        instance=instance,
        source=source,
        provenance="explicit",
    )


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
@pytest.mark.parametrize("mode", ["rich", "json", "toon"])
def test_stop_cli_real_resolved_context_owner_neutral_output(
    tmp_path: Path, owner_kind: str, mode: str
) -> None:
    instance, _catalog, owner_id = _instance(tmp_path, owner_kind=owner_kind)
    context = _real_stop_context(tmp_path, instance, owner_kind, owner_id)
    selector = ["--env", owner_id] if owner_kind == "environment" else ["--project", str(tmp_path)]
    argv = [*selector, "stop"]
    if mode != "rich":
        argv.extend(["--format", mode])

    with (
        patch(
            "odoo_instance_sdk.commands.cli_parts.callbacks._ready_instance", return_value=context
        ) as ready,
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", return_value=4242),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
        patch(
            "odoo_instance_sdk.resources.instance.runtime.is_process_alive",
            return_value=False,
        ),
    ):
        result = CliRunner().invoke(cli, argv)

    assert result.exit_code == 0, result.output
    resolved_cli = ready.call_args.args[0]
    assert resolved_cli.env == (owner_id if owner_kind == "environment" else None)
    assert resolved_cli.project == (str(tmp_path) if owner_kind == "project" else None)
    expected_identity = {
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "project_id": context.runtime.project_id,
        "environment_id": owner_id if owner_kind == "environment" else None,
        "environment_name": "demo" if owner_kind == "environment" else None,
    }
    if mode == "rich":
        for key, value in expected_identity.items():
            assert f"{key}={value}" in result.stdout
    elif mode == "json":
        document = json.loads(result.stdout)
        assert all(document["context"][key] == value for key, value in expected_identity.items())
        assert all(document["result"][key] == value for key, value in expected_identity.items())
    else:
        from toon import DecodeOptions, decode

        document = decode(result.stdout, DecodeOptions(indent=2, strict=True))
        assert all(document["context"][key] == value for key, value in expected_identity.items())
        assert all(document["result"][key] == value for key, value in expected_identity.items())
    terminate.assert_called_once()


@pytest.mark.unit
def test_stop_help_retains_explicit_env_selector_and_owner_neutral_description() -> None:
    result = CliRunner().invoke(cli, ["--env", str(uuid.uuid4()), "stop", "--help"])
    assert result.exit_code == 0, result.output
    assert "Stop the selected runtime." in result.stdout


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["rich", "json", "toon"])
@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("root_env", [False, True])
def test_stop_cli_output_parity_and_root_selector_without_signal_for_dry_run(
    tmp_path: Path, mode: str, dry_run: bool, root_env: bool
) -> None:
    instance, _catalog, environment_id = _instance(tmp_path)
    context = _real_stop_context(tmp_path, instance, "environment", environment_id)
    argv = ["--env", environment_id, "stop"] if root_env else ["stop"]
    if dry_run:
        argv.append("--dry-run")
    if mode != "rich":
        argv.extend(["--format", mode])
    with (
        patch(
            "odoo_instance_sdk.commands.cli_parts.callbacks._ready_instance", return_value=context
        ),
        patch("odoo_instance_sdk.resources.instance.planning.terminate_pid") as terminate,
        patch(
            "odoo_instance_sdk.resources.instance.identity.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.identity.os.getpgid", return_value=4242),
        patch(
            "odoo_instance_sdk.resources.instance.runtime.is_process_alive",
            return_value=False,
        ),
    ):
        result = CliRunner().invoke(cli, argv)
    assert result.exit_code == 0, result.output
    if mode == "rich":
        assert ("Plan: stop" if dry_run else "Stopped environment") in result.stdout
    else:
        assert result.stdout.count("schema_version") == 1
        if mode == "json":
            import json

            assert json.loads(result.stdout)["dry_run"] is dry_run
        else:
            from toon import DecodeOptions, decode

            assert decode(result.stdout, DecodeOptions(indent=2, strict=True))["dry_run"] is dry_run
    assert terminate.call_count == (0 if dry_run else 1)
