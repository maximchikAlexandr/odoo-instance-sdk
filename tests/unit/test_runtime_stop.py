from __future__ import annotations

import json
import signal
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import psutil
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.internal.proc.executor import terminate_pid
from odoo_instance_sdk.internal.server import _build_cli_args
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance


class _Catalog:
    def __init__(self, env_row: dict[str, object], runtime_row: dict[str, object] | None) -> None:
        self.env_row = env_row
        self.runtime_row = runtime_row
        self.clear_calls: list[tuple[str, int, float]] = []

    def get_environment(self, _environment_id: str) -> dict[str, object]:
        return self.env_row

    def get_environment_runtime(self, _environment_id: str) -> dict[str, object] | None:
        return self.runtime_row

    def _clear_environment_runtime_if_matches(
        self, environment_id: str, *, root_pid: int, create_time: float
    ) -> bool:
        self.clear_calls.append((environment_id, root_pid, create_time))
        if self.runtime_row is None:
            return False
        if (
            self.runtime_row["root_pid"] != root_pid
            or self.runtime_row["create_time"] != create_time
        ):
            return False
        self.runtime_row = None
        return True


class _Client:
    def __init__(self, catalog: _Catalog) -> None:
        self.catalog = catalog

    def get_catalog(self) -> _Catalog:
        return self.catalog


def _instance(
    tmp_path: Path, *, runtime: bool = True, default_run_args: tuple[str, ...] = ()
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
        "runtime_json": json.dumps({"odoo_bin": str(odoo_bin), "runtime_cwd": str(runtime_cwd)}),
        "generated_config_path": str(config_path),
        "python_environment_path": sys.executable,
        "python_environment_owned": False,
    }
    runtime_row: dict[str, object] | None = (
        {"root_pid": 4242, "create_time": 12.5} if runtime else None
    )
    catalog = _Catalog(env_row, runtime_row)
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
        _environment_id=environment_id,
    )
    return instance, catalog, environment_id


def _live_process(
    instance: OdooInstance,
    *,
    mismatch: str | None = None,
    extra_args: tuple[str, ...] = (),
) -> SimpleNamespace:
    catalog = instance._client.get_catalog()
    env_row = cast("Mapping[str, object]", catalog.get_environment(str(instance._environment_id)))
    config = StartConfig.from_odoo_config(str(env_row["generated_config_path"]))
    runtime = json.loads(str(env_row["runtime_json"]))
    argv = (
        str(env_row["python_environment_path"]),
        str(runtime["odoo_bin"]),
        *_build_cli_args(config),
        *instance.config.default_run_args,
        *extra_args,
    )
    live = SimpleNamespace(
        create_time=lambda: 12.5,
        exe=lambda: argv[0],
        cmdline=lambda: list(argv),
        cwd=lambda: runtime["runtime_cwd"],
    )
    if mismatch == "argv":
        live.cmdline = lambda: [*argv, "--config", "/wrong"]
    if mismatch == "cwd":
        live.cwd = lambda: "/other"
    if mismatch == "create_time":
        live.create_time = lambda: 99.0
    return live


@pytest.mark.unit
def test_stop_owned_runtime_revalidates_then_terminates_and_clears(
    tmp_path: Path,
) -> None:
    instance, catalog, environment_id = _instance(tmp_path)
    calls: list[tuple[int, int | None, float]] = []
    with (
        patch(
            "odoo_instance_sdk.resources.instance.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.os.getpgid", return_value=4242),
        patch(
            "odoo_instance_sdk.resources.instance.terminate_pid",
            side_effect=lambda pid, *, process_group_id, timeout: calls.append(
                (pid, process_group_id, timeout)
            ),
        ),
        patch("odoo_instance_sdk.resources.instance.is_process_alive", return_value=False),
        patch(
            "odoo_instance_sdk.internal.address.probe_address",
            side_effect=AssertionError("stop must not inspect port state"),
        ),
    ):
        result = instance._stop_environment_command(timeout=3.0).run()

    assert result == {"status": "stopped", "environment_id": environment_id}
    assert calls == [(4242, 4242, 3.0)]
    assert catalog.clear_calls == [(environment_id, 4242, 12.5)]


@pytest.mark.unit
@pytest.mark.parametrize("mismatch", ["argv", "cwd", "create_time", "process_group"])
def test_stop_mismatch_fails_closed_and_retains_runtime(tmp_path: Path, mismatch: str) -> None:
    instance, catalog, _ = _instance(tmp_path)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.psutil.Process",
            return_value=_live_process(instance, mismatch=mismatch),
        ),
        patch(
            "odoo_instance_sdk.resources.instance.os.getpgid",
            return_value=99 if mismatch == "process_group" else 4242,
        ),
        patch("odoo_instance_sdk.resources.instance.terminate_pid") as terminate,
        pytest.raises(RuntimeError, match="runtime identity mismatch"),
    ):
        instance._stop_environment_command().run()
    terminate.assert_not_called()
    assert catalog.runtime_row is not None
    assert catalog.clear_calls == []


@pytest.mark.unit
def test_stop_inaccessible_identity_fails_closed_and_retains_runtime(tmp_path: Path) -> None:
    instance, catalog, _ = _instance(tmp_path)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.psutil.Process",
            side_effect=psutil.AccessDenied(4242),
        ),
        patch("odoo_instance_sdk.resources.instance.terminate_pid") as terminate,
        pytest.raises(RuntimeError, match="identity is inaccessible"),
    ):
        instance._stop_environment_command().run()
    terminate.assert_not_called()
    assert catalog.runtime_row is not None
    assert catalog.clear_calls == []


@pytest.mark.unit
def test_stop_rejects_runtime_record_changed_after_planning(tmp_path: Path) -> None:
    instance, catalog, _ = _instance(tmp_path)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.os.getpgid", return_value=4242),
        patch("odoo_instance_sdk.resources.instance.terminate_pid") as terminate,
    ):
        command = instance._stop_environment_command()
        assert catalog.runtime_row is not None
        catalog.runtime_row["create_time"] = 99.0
        with pytest.raises(RuntimeError, match="changed after planning"):
            command.run()
    terminate.assert_not_called()
    assert catalog.runtime_row is not None
    assert catalog.clear_calls == []


@pytest.mark.unit
def test_stop_rejects_environment_evidence_changed_after_planning(tmp_path: Path) -> None:
    instance, catalog, _ = _instance(tmp_path)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.os.getpgid", return_value=4242),
        patch("odoo_instance_sdk.resources.instance.terminate_pid") as terminate,
    ):
        command = instance._stop_environment_command()
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
def test_stop_rejects_live_evidence_changed_after_planning(tmp_path: Path) -> None:
    instance, catalog, _ = _instance(tmp_path)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.psutil.Process",
            side_effect=[_live_process(instance), _live_process(instance, mismatch="create_time")],
        ),
        patch("odoo_instance_sdk.resources.instance.os.getpgid", side_effect=[4242, 4242]),
        patch("odoo_instance_sdk.resources.instance.terminate_pid") as terminate,
    ):
        command = instance._stop_environment_command()
        with pytest.raises(RuntimeError, match="changed after planning"):
            command.run()
    terminate.assert_not_called()
    assert catalog.runtime_row is not None
    assert catalog.clear_calls == []


@pytest.mark.unit
def test_stop_allows_safe_default_launch_args(tmp_path: Path) -> None:
    instance, catalog, environment_id = _instance(tmp_path, default_run_args=("--dev",))
    with (
        patch(
            "odoo_instance_sdk.resources.instance.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.os.getpgid", return_value=4242),
        patch("odoo_instance_sdk.resources.instance.terminate_pid") as terminate,
        patch("odoo_instance_sdk.resources.instance.is_process_alive", return_value=False),
    ):
        result = instance._stop_environment_command().run()
    assert result == {"status": "stopped", "environment_id": environment_id}
    terminate.assert_called_once_with(4242, process_group_id=4242, timeout=10.0)
    assert catalog.clear_calls == [(environment_id, 4242, 12.5)]


@pytest.mark.unit
def test_stop_no_row_is_idempotent(tmp_path: Path) -> None:
    instance, catalog, environment_id = _instance(tmp_path, runtime=False)
    with patch("odoo_instance_sdk.resources.instance.terminate_pid") as terminate:
        result = instance._stop_environment_command().run()
    assert result == {"status": "already_stopped", "environment_id": environment_id}
    terminate.assert_not_called()
    assert catalog.clear_calls == []


@pytest.mark.unit
def test_terminate_pid_uses_bounded_term_then_kill_escalation() -> None:
    alive = iter((True, True, True, False, False))
    with (
        patch("odoo_instance_sdk.internal.proc.executor.os.killpg") as killpg,
        patch(
            "odoo_instance_sdk.internal.proc.executor.is_process_alive",
            side_effect=lambda _pid: next(alive),
        ),
        patch(
            "odoo_instance_sdk.internal.proc.executor.time.monotonic",
            side_effect=(0, 100, 100, 100, 100),
        ),
        patch("odoo_instance_sdk.internal.proc.executor.time.sleep"),
    ):
        terminate_pid(4242, process_group_id=4242, timeout=5.0)
    assert [call.args[1] for call in killpg.call_args_list] == [
        signal.SIGTERM,
        signal.SIGKILL,
    ]


@pytest.mark.unit
def test_stop_vanished_process_clears_matching_row(tmp_path: Path) -> None:
    instance, catalog, environment_id = _instance(tmp_path)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.psutil.Process",
            side_effect=[_live_process(instance), psutil.NoSuchProcess(4242)],
        ),
        patch("odoo_instance_sdk.resources.instance.os.getpgid", return_value=4242),
        patch("odoo_instance_sdk.resources.instance.terminate_pid") as terminate,
    ):
        result = instance._stop_environment_command().run()
    assert result == {"status": "already_stopped", "environment_id": environment_id}
    terminate.assert_not_called()
    assert catalog.clear_calls == [(environment_id, 4242, 12.5)]


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["rich", "json", "toon"])
@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("root_env", [False, True])
def test_stop_cli_output_parity_and_root_selector_without_signal_for_dry_run(
    tmp_path: Path, mode: str, dry_run: bool, root_env: bool
) -> None:
    instance, _catalog, environment_id = _instance(tmp_path)
    source = SimpleNamespace(
        id=uuid.UUID(environment_id), name="demo", worktree_path=str(tmp_path / "worktree")
    )
    context = SimpleNamespace(
        require_environment=lambda: source,
        is_environment=True,
        instance=instance,
        output_provenance={"project_source": "null", "environment_source": "explicit"},
    )
    argv = ["--env", environment_id, "stop"] if root_env else ["stop"]
    if dry_run:
        argv.append("--dry-run")
    if mode != "rich":
        argv.extend(["--format", mode])
    with (
        patch("odoo_instance_sdk.commands.context.ready_instance", return_value=context),
        patch("odoo_instance_sdk.cli.cli_context.ready_instance", return_value=context),
        patch("odoo_instance_sdk.resources.instance.terminate_pid") as terminate,
        patch(
            "odoo_instance_sdk.resources.instance.psutil.Process",
            return_value=_live_process(instance),
        ),
        patch("odoo_instance_sdk.resources.instance.os.getpgid", return_value=4242),
        patch("odoo_instance_sdk.resources.instance.is_process_alive", return_value=False),
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
