from __future__ import annotations

import contextlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.internal.dbprep.bootstrap import (
    BOOTSTRAP_DATABASE,
    BootstrapFailedError,
    bootstrap_tmp_steps,
    tmp_bootstrap_command,
)
from odoo_instance_sdk.internal.odoo_config import parse_odoo_config
from odoo_instance_sdk.internal.proc import ProcessHandle, ProcessResult, RecordingExecutor
from odoo_instance_sdk.internal.project_init import (
    _INIT_REMOTE_NAMES_ACTION_ID,
    evaluate_init_completeness,
    project_owned_data_dir,
    verify_project_owned_data_dir,
)
from odoo_instance_sdk.models import DatabaseRefreshOptions, StartConfig
from odoo_instance_sdk.project import (
    PostgresProjectConfig,
    ProjectConfig,
)
from odoo_instance_sdk.project import (
    TestInstanceProjectConfig as RemoteTestInstanceConfig,
)
from odoo_instance_sdk.project_init import init_project, init_project_command
from odoo_instance_sdk.resources.database import DatabaseResource
from odoo_instance_sdk.resources.instance import OdooInstance, auxiliary_restore_session
from odoo_instance_sdk.resources.postgres import PostgresCluster


@pytest.fixture(autouse=True)
def _cleanup_magicmock_cwd_artifacts() -> object:
    """Remove accidental MagicMock path strings written as files in cwd."""
    yield
    for path in Path.cwd().glob("<MagicMock*"):
        if path.is_file():
            path.unlink()


@pytest.fixture
def stub_psql_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Inject a disposable native-client path for public CLI command tests."""
    psql = tmp_path / "psql"
    psql.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    psql.chmod(0o755)
    path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", os.pathsep.join((str(tmp_path), path)))


@pytest.fixture
def stub_compose_init_followup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose init chains postgres start and tmp bootstrap after scaffold."""

    def _noop_ensure_running(
        self: PostgresCluster,
        timeout: float = 60.0,
        *,
        temporary_path: Path | None = None,
        step_ids: object = None,
    ) -> None:
        return None

    def _skip_bootstrap_tmp(
        context: object,
        spawn_step: object,
        probe_step: object,
        ready_step: object,
    ) -> bool:
        context.skip(spawn_step.step_id)  # type: ignore[attr-defined]
        context.skip(probe_step.step_id)  # type: ignore[attr-defined]
        context.skip(ready_step.step_id)  # type: ignore[attr-defined]
        return True

    monkeypatch.setattr(PostgresCluster, "_ensure_running_impl", _noop_ensure_running)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.run_bootstrap_tmp",
        _skip_bootstrap_tmp,
    )


def _test_instance(**kwargs: object) -> RemoteTestInstanceConfig:
    return RemoteTestInstanceConfig(**cast("Any", kwargs))


def _base_args(
    tmp_path: Path, *, json_output: bool = False, allow_partial: bool = False
) -> list[str]:
    args = [
        "init",
        "--no-input",
        "--odoo-bin",
        "/opt/odoo/odoo-bin",
        "--python",
        "python3",
        "--project",
        str(tmp_path),
    ]
    if allow_partial:
        args.insert(2, "--allow-partial")
    if json_output:
        args.extend(["--format", "json"])
    return args


def _compose_config(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        source_config=tmp_path / ".odcli" / "odoo.conf",
        postgres=PostgresProjectConfig(
            mode="compose",
            image="pgvector/pgvector:pg16",
            port=5468,
            user="odoo",
        ),
        test_instance=_test_instance(
            base_url="http://example.test:8069",
            database="remote_db",
            git_branch="main",
        ),
    )


def _ready_probe_result(step: object) -> ProcessResult:
    return ProcessResult(
        argv=step.argv,  # type: ignore[attr-defined]
        returncode=0,
        stdout="installed",
        stderr="",
        duration=0.0,
        cwd=getattr(step, "cwd", None),
        environment=cast(
            "tuple[tuple[str, str], ...]",
            getattr(step, "environment", ()),
        ),
    )


def _failed_probe_result(step: object) -> ProcessResult:
    return ProcessResult(
        argv=step.argv,  # type: ignore[attr-defined]
        returncode=0,
        stdout="uninstalled",
        stderr="",
        duration=0.0,
        cwd=getattr(step, "cwd", None),
        environment=cast(
            "tuple[tuple[str, str], ...]",
            getattr(step, "environment", ()),
        ),
    )


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_no_input_incomplete_fails_before_writes(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--yes",
        ],
    )
    assert result.exit_code != 0
    assert result.exception is not None
    assert "init_incomplete" in str(result.exception)
    assert not (tmp_path / ".odcli" / "project.toml").exists()


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_allow_partial_proceeds_with_warning(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path, json_output=True),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
            "--allow-partial",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["result"]["partial"] is True
    assert "test_url" in payload["result"]["missing"]


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_full_self_contained_writes_data_dir_and_dotenv(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
            "--test-url",
            "http://127.0.0.1:18069",
            "--test-database",
            "remote_db",
            "--test-branch",
            "main",
            "--local-config",
            "--allow-partial",
        ],
    )
    assert result.exit_code == 0, result.output
    generated = parse_odoo_config(tmp_path / ".odcli" / "odoo.conf")
    assert generated["data_dir"] == str(project_owned_data_dir(tmp_path))
    dotenv = tmp_path / ".odcli" / ".env"
    assert dotenv.is_file()
    assert dotenv.stat().st_mode & 0o777 == 0o600
    assert "ODCLI_TEST_INSTANCE_ORIGIN_PINS=" in dotenv.read_text()
    assert "ODCLI_TEST_MASTER_PASSWORD=" in dotenv.read_text()


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_rerun_preserves_existing_test_instance(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    first = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--test-url",
            "http://127.0.0.1:18069",
            "--test-database",
            "remote_db",
            "--test-branch",
            "main",
            "--allow-partial",
        ],
    )
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
        ],
    )
    assert second.exit_code == 0, second.output
    manifest = (tmp_path / ".odcli" / "project.toml").read_text()
    assert "http://127.0.0.1:18069" in manifest
    assert 'database = "remote_db"' in manifest


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_explicit_test_instance_options_replace_existing_atomically(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    first = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--test-url",
            "http://127.0.0.1:18069",
            "--test-database",
            "remote_db",
            "--test-branch",
            "main",
            "--allow-partial",
        ],
    )
    assert first.exit_code == 0, first.output
    replacement = ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        source_config=tmp_path / ".odcli" / "odoo.conf",
        postgres=PostgresProjectConfig(
            mode="compose",
            image="pgvector/pgvector:pg16",
            port=5468,
            user="odoo",
        ),
        test_instance=_test_instance(
            base_url="http://replacement.test:9070",
            database="replacement_db",
            git_branch="develop",
        ),
    )
    init_project(
        tmp_path,
        replacement,
        postgres_allocated=False,
        local_config=True,
        allow_partial=True,
    )
    manifest = (tmp_path / ".odcli" / "project.toml").read_text()
    assert "http://replacement.test:9070" in manifest
    assert 'database = "replacement_db"' in manifest
    assert 'git_branch = "develop"' in manifest
    assert "http://127.0.0.1:18069" not in manifest
    assert 'database = "remote_db"' not in manifest


def test_execute_init_drops_test_database_when_remote_list_has_one_name(tmp_path: Path) -> None:
    config = ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        test_instance=_test_instance(
            base_url="http://example.test:8069",
            git_branch="main",
        ),
    )
    missing, details = evaluate_init_completeness(
        project_root=tmp_path,
        config=config,
        local_config=True,
        postgres_image="pgvector/pgvector:pg16",
        existing_test_instance=None,
        dry_run=False,
        remote_database_names=["only_db"],
    )
    assert "test_database" not in missing
    assert "only_db" in details["test_database"]


def test_init_command_resolves_remote_database_via_action_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        test_instance=_test_instance(
            base_url="http://example.test:8069",
            git_branch="main",
        ),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.project_init.fetch_remote_database_names_for_init",
        lambda _cfg: ["only_db"],
    )
    command = init_project_command(tmp_path, config, postgres_allocated=False, local_config=True)
    step_ids = [step.step_id for step in command.plan.steps]
    assert _INIT_REMOTE_NAMES_ACTION_ID in step_ids
    missing, details = evaluate_init_completeness(
        project_root=tmp_path,
        config=config,
        local_config=True,
        postgres_image=None,
        existing_test_instance=None,
        dry_run=False,
        remote_database_names=["only_db"],
    )
    assert "test_database" not in missing
    assert "only_db" in details["test_database"]


def test_init_command_uses_database_resource_names_in_action_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        test_instance=_test_instance(
            base_url="http://example.test:8069",
            git_branch="main",
        ),
    )
    observed: list[str] = []

    def fake_names(self: DatabaseResource) -> tuple[str, ...]:
        observed.append(self.base_url)
        return ("resolved_db",)

    monkeypatch.setattr(DatabaseResource, "names", fake_names)
    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        local_config=True,
        no_input=True,
        allow_partial=False,
    )
    assert _INIT_REMOTE_NAMES_ACTION_ID in [step.step_id for step in command.plan.steps]
    with pytest.raises(InstanceConfigurationError, match="init_incomplete"):
        command.run()
    assert observed == ["http://example.test:8069"]


def test_dry_run_lists_test_database_missing_without_names_call(tmp_path: Path) -> None:
    config = ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        test_instance=_test_instance(
            base_url="http://example.test:8069",
            git_branch="main",
        ),
    )
    missing, _ = evaluate_init_completeness(
        project_root=tmp_path,
        config=config,
        local_config=True,
        postgres_image="pgvector/pgvector:pg16",
        existing_test_instance=None,
        dry_run=True,
        remote_database_names=None,
    )
    assert "test_database" in missing
    command = init_project_command(
        tmp_path, config, postgres_allocated=False, local_config=True, dry_run=True
    )
    assert _INIT_REMOTE_NAMES_ACTION_ID in [step.step_id for step in command.plan.steps]


def test_external_postgres_requires_allow_partial_without_test_instance(tmp_path: Path) -> None:
    config = ProjectConfig(
        repository_root=tmp_path.resolve(),
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="python3",
        source_config=tmp_path / "odoo.conf",
    )
    missing, _ = evaluate_init_completeness(
        project_root=tmp_path,
        config=config,
        local_config=False,
        postgres_image=None,
        existing_test_instance=None,
        dry_run=False,
        remote_database_names=None,
    )
    assert "test_url" in missing
    with pytest.raises(InstanceConfigurationError, match="init_incomplete"):
        init_project(
            tmp_path,
            config,
            postgres_allocated=False,
            no_input=True,
            allow_partial=False,
        )


@pytest.mark.usefixtures("stub_compose_init_followup")
def test_init_dry_run_shows_bootstrap_step_without_spawn(tmp_path: Path) -> None:
    config = _compose_config(tmp_path)
    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        local_config=True,
        dry_run=True,
    )
    assert any(step.step_id == "init.bootstrap.tmp" for step in command.plan.steps)


def test_auxiliary_restore_uses_bootstrap_database_in_argv(tmp_path: Path) -> None:
    config_path = tmp_path / ".odcli" / "odoo.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[options]\nhttp_port = 8069\n")
    start_config = StartConfig(config_path=str(config_path), http_port=8069)
    spawn_step, _, _, _ = bootstrap_tmp_steps(
        command_prefix=("/usr/bin/python", "/opt/odoo/odoo-bin"),
        start_config=start_config,
        db_host="127.0.0.1",
        db_port=5468,
        db_user="odoo",
        db_password="secret",
        default_cwd=tmp_path,
    )
    assert f"--database {BOOTSTRAP_DATABASE}" in " ".join(spawn_step.argv)
    assert "--database=__odcli_restore__" not in spawn_step.argv
    assert "--db-filter=^$" not in spawn_step.argv


def test_verify_project_owned_data_dir_rejects_symlink(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    external = tmp_path / "external"
    external.mkdir()
    data_dir = project_root / ".odcli" / "filestore"
    data_dir.parent.mkdir(parents=True)
    data_dir.symlink_to(external, target_is_directory=True)
    with pytest.raises(InstanceConfigurationError, match="regular directory"):
        verify_project_owned_data_dir(project_root, data_dir)


def test_bootstrap_tmp_command_records_odoo_argv(tmp_path: Path) -> None:
    config_path = tmp_path / ".odcli" / "odoo.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[options]\nhttp_port = 8069\n", encoding="utf-8")
    start_config = StartConfig(config_path=str(config_path), http_port=8069)

    def bootstrap_results(step: object) -> ProcessResult:
        step_id = step.step_id  # type: ignore[attr-defined]
        if step_id in {"init.bootstrap.tmp.probe", "init.bootstrap.tmp.ready"}:
            if step_id == "init.bootstrap.tmp.probe":
                return _failed_probe_result(step)
            return _ready_probe_result(step)
        return ProcessResult(
            argv=step.argv,  # type: ignore[attr-defined]
            returncode=0,
            stdout="",
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd", None),
            environment=cast(
                "tuple[tuple[str, str], ...]",
                getattr(step, "environment", ()),
            ),
        )

    executor = RecordingExecutor(result_factory=bootstrap_results)
    command = tmp_bootstrap_command(
        command_prefix=("/usr/bin/python3", "/opt/odoo/odoo-bin"),
        start_config=start_config,
        db_host="127.0.0.1",
        db_port=5468,
        db_user="odoo",
        db_password="secret",
        default_cwd=tmp_path,
        executor=executor,
    )
    assert command.plan.steps[0].step_id == "init.bootstrap.tmp"
    assert command.run() is True
    executed = [step for step in executor.executed if step.step_id == "init.bootstrap.tmp"]
    assert executed
    argv = executed[0].argv
    assert "--database" in argv and "tmp" in argv
    assert "--init" in argv and "base" in argv
    assert "--stop-after-init" in argv


def test_bootstrap_valid_tmp_skips_odoo_spawn(tmp_path: Path) -> None:
    config_path = tmp_path / ".odcli" / "odoo.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[options]\nhttp_port = 8069\n", encoding="utf-8")
    start_config = StartConfig(config_path=str(config_path), http_port=8069)
    executor = RecordingExecutor(result_factory=_ready_probe_result)
    command = tmp_bootstrap_command(
        command_prefix=("/usr/bin/python3", "/opt/odoo/odoo-bin"),
        start_config=start_config,
        db_host="127.0.0.1",
        db_port=5468,
        db_user="odoo",
        db_password="secret",
        default_cwd=tmp_path,
        executor=executor,
    )
    assert command.run() is True
    assert executor.spawned == []
    assert any(step.step_id == "init.bootstrap.tmp.probe" for step in executor.executed)


def test_bootstrap_invalid_tmp_raises_init_bootstrap_failed(tmp_path: Path) -> None:
    config_path = tmp_path / ".odcli" / "odoo.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[options]\nhttp_port = 8069\n", encoding="utf-8")
    start_config = StartConfig(config_path=str(config_path), http_port=8069)

    def invalid_bootstrap_results(step: object) -> ProcessResult:
        step_id = step.step_id  # type: ignore[attr-defined]
        if step_id == "init.bootstrap.tmp.probe":
            return _failed_probe_result(step)
        if step_id == "init.bootstrap.tmp.ready":
            return _failed_probe_result(step)
        return ProcessResult(
            argv=step.argv,  # type: ignore[attr-defined]
            returncode=0,
            stdout="",
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd", None),
            environment=cast(
                "tuple[tuple[str, str], ...]",
                getattr(step, "environment", ()),
            ),
        )

    executor = RecordingExecutor(result_factory=invalid_bootstrap_results)
    command = tmp_bootstrap_command(
        command_prefix=("/usr/bin/python3", "/opt/odoo/odoo-bin"),
        start_config=start_config,
        db_host="127.0.0.1",
        db_port=5468,
        db_user="odoo",
        db_password="secret",
        default_cwd=tmp_path,
        executor=executor,
    )
    with pytest.raises(BootstrapFailedError, match=r"init_bootstrap_failed|base module"):
        command.run()
    assert sum(1 for step in executor.executed if step.step_id == "init.bootstrap.tmp") == 1


def test_init_bootstrap_failure_prevents_successful_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    def _noop_ensure_running(
        self: PostgresCluster,
        timeout: float = 60.0,
        *,
        temporary_path: Path | None = None,
        step_ids: object = None,
    ) -> None:
        return None

    def _fail_bootstrap(
        context: object,
        spawn_step: object,
        probe_step: object,
        ready_step: object,
    ) -> bool:
        raise BootstrapFailedError("tmp bootstrap failed: invalid database")

    monkeypatch.setattr(PostgresCluster, "_ensure_running_impl", _noop_ensure_running)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.run_bootstrap_tmp",
        _fail_bootstrap,
    )
    config = _compose_config(tmp_path)
    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        local_config=True,
        allow_partial=True,
    )
    with pytest.raises(BootstrapFailedError, match=r"init_bootstrap_failed|tmp bootstrap failed"):
        command.run()


def test_first_run_triggers_bootstrap_when_no_valid_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.internal.dbprep.bootstrap import ensure_project_bootstrap_tmp
    from odoo_instance_sdk.internal.proc import RunContext

    config_path = tmp_path / ".odcli" / "odoo.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[options]\nhttp_port = 8069\n", encoding="utf-8")
    client = MagicMock()
    client.config.http_timeout_seconds = 10.0
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(config_path=str(config_path), http_port=8069, db_user="odoo"),
            command_prefix=("/usr/bin/python3", "/opt/odoo/odoo-bin"),
            default_cwd=tmp_path,
        ),
        _client=client,
    )
    cluster = MagicMock()
    cluster.owned = True
    cluster.endpoint_host = "127.0.0.1"
    cluster.endpoint_port = 5468
    instance._postgres_cluster = cluster
    calls: list[bool] = []

    def _track_bootstrap(
        context: object,
        spawn_step: object,
        probe_step: object,
        ready_step: object,
    ) -> bool:
        calls.append(True)
        context.skip(spawn_step.step_id)  # type: ignore[attr-defined]
        context.skip(probe_step.step_id)  # type: ignore[attr-defined]
        context.skip(ready_step.step_id)  # type: ignore[attr-defined]
        return True

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.run_bootstrap_tmp",
        _track_bootstrap,
    )
    context = MagicMock(spec=RunContext)
    context.action = MagicMock()
    context.complete_action = MagicMock()
    context.skip = MagicMock()
    ensure_project_bootstrap_tmp(instance, context)
    assert calls == [True]
    context.action.assert_called_once_with("init.bootstrap.tmp.verify")


@pytest.mark.parametrize("odoo_version", ["13.0", "19.0"])
@pytest.mark.usefixtures("stub_psql_resolution")
def test_self_contained_restore_regression_compose_tmp_restore_default_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    odoo_version: str,
) -> None:
    """Compose cluster, tmp bootstrap, stopped-project restore, 303, default switch."""
    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator
    from odoo_instance_sdk.internal.project_manifest import write_manifest

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_name = source\n"
        "admin_passwd = local-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo_bin = tmp_path / "odoo-bin"
    odoo_bin.write_text("#!/bin/sh\n")
    odoo_bin.chmod(0o755)
    remote_database = f"remote_test_{odoo_version.replace('.', '_')}"
    project = ProjectConfig(
        repository_root=tmp_path,
        python=python,
        odoo_bin=odoo_bin,
        source_config=source,
        default_source_database="old",
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=5468, user="odoo"),
        test_instance=RemoteTestInstanceConfig(
            base_url="https://example.test",
            database=remote_database,
            git_branch=f"main-{odoo_version}",
        ),
    )
    write_manifest(tmp_path, project)
    backup_file = tmp_path / "remote.zip"
    backup_file.write_bytes(b"zip-backup")
    from odoo_instance_sdk.models import BackupFormat
    from tests.fixtures import make_backup

    backup = make_backup(
        source_base_url="https://example.test",
        database_name=remote_database,
        path=str(backup_file),
        filename=backup_file.name,
        size_bytes=backup_file.stat().st_size,
        format=BackupFormat.ZIP,
        filestore_requested=True,
        source_git_branch=f"develop-{odoo_version}",
    )

    client = MagicMock()
    auxiliary = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(config_path=str(source), http_port=8069),
            command_prefix=(str(python), str(odoo_bin)),
            default_cwd=tmp_path,
        ),
        _client=client,
    )
    local = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(config_path=str(source), http_port=8069),
        ),
        _client=client,
    )
    remote = MagicMock()
    remote.databases.backup.return_value = backup
    client.instance.from_project.return_value = auxiliary
    client.instance.from_config.return_value = local
    client.instance.return_value = remote
    client.unregister_process.return_value = (None, None)

    session = auxiliary_restore_session(auxiliary)
    handle = ProcessHandle(
        process=MagicMock(),
        argv=session.start_step.argv,
        process_group_id=123,
        session_id=123,
        inherited_stdio=False,
    )
    executor = RecordingExecutor(handles={session.start_step.step_id: handle})
    bootstrap_spawned: list[bool] = []

    def _track_bootstrap(
        context: object,
        spawn_step: object,
        probe_step: object,
        ready_step: object,
    ) -> bool:
        bootstrap_spawned.append(True)
        context.skip(spawn_step.step_id)  # type: ignore[attr-defined]
        context.skip(probe_step.step_id)  # type: ignore[attr-defined]
        context.skip(ready_step.step_id)  # type: ignore[attr-defined]
        return True

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap.run_bootstrap_tmp",
        _track_bootstrap,
    )
    PostgresCluster._from_config(
        project,
        repository_root=tmp_path,
        compose_runner=None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._assert_http_port_free",
        lambda _config: None,
    )
    monkeypatch.setattr(
        OdooInstance,
        "wait_ready",
        lambda _self, _proc, *, timeout, version_info=False, database_manager=False: MagicMock(
            ok=True
        ),
    )
    restore = MagicMock()
    monkeypatch.setattr(DatabaseResource, "restore", restore)
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.status_code = 303
    response.headers = {"location": "/web/database/manager"}
    response.json.return_value = {"result": ["source"]}

    @contextlib.contextmanager
    def fake_http(_self: DatabaseResource, timeout: float | None = None) -> Any:
        del timeout
        yield MagicMock(post=MagicMock(return_value=response))

    monkeypatch.setattr(DatabaseResource, "_http", fake_http)

    def command_factory(
        project_path: Path,
        *,
        options: DatabaseRefreshOptions,
        **kwargs: object,
    ) -> object:
        return DatabasePreparationCoordinator(client).refresh_database_command(
            project_path,
            options=options,
            executor=executor,
            admin_password=cast("str | None", kwargs.get("admin_password")),
        )

    client.environments.refresh_database_command.side_effect = command_factory
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", "https://example.test:443")
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    monkeypatch.setattr(PostgresCluster, "_ensure_running_impl", lambda *a, **k: None)
    init_command = init_project_command(
        tmp_path,
        project,
        postgres_allocated=False,
        local_config=True,
        allow_partial=True,
    )
    step_ids = [step.step_id for step in init_command.plan.steps]
    assert "init.bootstrap.tmp" in step_ids
    assert "init.bootstrap.tmp.probe" in step_ids
    init_command.run()
    assert bootstrap_spawned == [True]

    result = CliRunner().invoke(cli, ["db", "refresh", "--restore", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["result"]["default_switched"] is True
    assert backup.database_name == remote_database
    assert backup.source_git_branch == f"develop-{odoo_version}"
    restore.assert_called_once()
    assert [step.step_id for step in executor.spawned] == [session.start_step.step_id]
    client.unregister_process.assert_called_once()


@pytest.mark.usefixtures("stub_compose_init_followup")
@pytest.mark.usefixtures("stub_psql_resolution")
def test_init_master_env_db_refresh_dry_run_flow(tmp_path: Path) -> None:
    odoo_bin = tmp_path / "odoo-bin"
    odoo_bin.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    odoo_bin.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = CliRunner()
    init_result = runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            str(odoo_bin),
            "--python",
            "python3",
            "--project",
            str(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
            "--test-url",
            "http://127.0.0.1:18069",
            "--test-database",
            "remote_db",
            "--test-branch",
            "main",
            "--local-config",
            "--allow-partial",
        ],
    )
    assert init_result.exit_code == 0, init_result.output
    env_path = tmp_path / ".odcli" / ".env"
    env_path.write_text(
        env_path.read_text().replace(
            "ODCLI_TEST_MASTER_PASSWORD=",
            "ODCLI_TEST_MASTER_PASSWORD=remote-secret",
        )
    )
    refresh_result = runner.invoke(
        cli,
        [
            "--project",
            str(tmp_path),
            "db",
            "refresh",
            "--restore",
            "--dry-run",
            "--format",
            "json",
        ],
    )
    assert refresh_result.exit_code == 0, refresh_result.output
    payload = json.loads(refresh_result.output)
    assert payload["dry_run"] is True
