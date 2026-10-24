from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    PreparedStep,
    ProcessResult,
    RecordingExecutor,
    RunContext,
)
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.project_init import (
    _BootstrapFollowup,
    _ComposeFollowup,
    init_project_command,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster

DIGEST = "postgres@sha256:" + "a" * 64
OLD_DIGEST = "postgres@sha256:" + "b" * 64


@pytest.fixture
def image_executor(monkeypatch: pytest.MonkeyPatch) -> RecordingExecutor:
    def result(step: PreparedProcess) -> ProcessResult:
        step = cast("PreparedStep", step)
        return ProcessResult(
            argv=step.argv,
            returncode=0,
            stdout=DIGEST + "\n" if "inspect" in step.argv else "",
            stderr="",
            duration=0,
            cwd=step.cwd,
            environment=step.environment,
        )

    executor = RecordingExecutor(result_factory=result)
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    def skip(
        context: RunContext[dict[str, JsonValue]], followup: _ComposeFollowup | _BootstrapFollowup
    ) -> None:
        steps = (
            followup.steps
            if isinstance(followup, _ComposeFollowup)
            else followup.plan.execution_steps
        )
        for step in steps:
            context.skip(step.step_id)

    monkeypatch.setattr("odoo_instance_sdk.project_init._execute_compose_followup_phase", skip)
    monkeypatch.setattr("odoo_instance_sdk.project_init._execute_bootstrap_followup_phase", skip)
    return executor


def project(root: Path) -> ProjectConfig:
    return ProjectConfig(
        repository_root=root,
        python=sys.executable,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=55432),
    )


def test_init_confirms_image_before_writing_project(
    tmp_path: Path, image_executor: RecordingExecutor
) -> None:
    config = project(tmp_path)
    cluster = PostgresCluster._from_config(config, repository_root=tmp_path, compose_runner=None)
    confirmations: list[tuple[str, str]] = []

    def confirm(image: str, digest: str) -> None:
        assert not (tmp_path / ".odcli/project.toml").exists()
        assert not cluster.password_file.exists()
        confirmations.append((image, digest))

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        local_config=True,
        allow_partial=True,
        confirm_postgres_image=confirm,
    )
    assert not (tmp_path / ".odcli").exists()
    assert not cluster.password_file.exists()
    assert not image_executor.executed
    command.run()
    assert confirmations == [("postgres:16", DIGEST)]
    assert cluster._approved_image_digest() == DIGEST
    assert (tmp_path / ".odcli/project.toml").exists()
    assert command.plan.steps[0].step_id == "postgres.image.pull"


def test_init_confirms_image_before_writing_project_already_approved(
    tmp_path: Path, image_executor: RecordingExecutor
) -> None:
    config = project(tmp_path)
    cluster = PostgresCluster._from_config(config, repository_root=tmp_path, compose_runner=None)
    cluster._approve_image(DIGEST)
    confirmations: list[tuple[str, str]] = []

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        local_config=True,
        allow_partial=True,
        confirm_postgres_image=lambda image, digest: confirmations.append((image, digest)),
    )
    command.run()
    assert confirmations == []
    assert cluster._approved_image_digest() == DIGEST
    assert (tmp_path / ".odcli/project.toml").exists()


def test_init_confirms_image_before_writing_project_changed_approval(
    tmp_path: Path, image_executor: RecordingExecutor
) -> None:
    config = project(tmp_path)
    cluster = PostgresCluster._from_config(config, repository_root=tmp_path, compose_runner=None)
    cluster._approve_image(OLD_DIGEST)
    confirmations: list[tuple[str, str]] = []

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        local_config=True,
        allow_partial=True,
        confirm_postgres_image=lambda image, digest: confirmations.append((image, digest)),
    )
    command.run()
    assert confirmations == [("postgres:16", DIGEST)]
    assert cluster._approved_image_digest() == DIGEST


@pytest.mark.parametrize("venv", [None, ".venv"])
def test_declined_image_leaves_project_and_trust_unchanged(
    tmp_path: Path, image_executor: RecordingExecutor, venv: str | None
) -> None:
    config = project(tmp_path)
    cluster = PostgresCluster._from_config(config, repository_root=tmp_path, compose_runner=None)
    cluster._approve_image(OLD_DIGEST)

    def decline(image: str, digest: str) -> None:
        raise ValueError("declined image")

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        local_config=True,
        allow_partial=True,
        confirm_postgres_image=decline,
        venv=venv,
    )
    with pytest.raises(ValueError, match="declined image"):
        command.run()
    assert not (tmp_path / ".odcli").exists()
    assert not cluster.password_file.exists()
    assert cluster._approved_image_digest() == OLD_DIGEST
    assert not (tmp_path / ".venv").exists()


def init_args(root: Path) -> list[str]:
    return [
        "init",
        "--project",
        str(root),
        "--odoo-bin",
        "/opt/odoo/odoo-bin",
        "--python",
        sys.executable,
        "--postgres",
        "compose",
        "--postgres-image",
        "postgres:16",
        "--postgres-port",
        "55432",
        "--local-config",
        "--allow-partial",
    ]


@pytest.mark.parametrize("output", ["rich", "json", "toon"])
def test_init_yes_dry_run_does_not_approve_image(
    tmp_path: Path, image_executor: RecordingExecutor, output: str
) -> None:
    result = CliRunner().invoke(
        cli,
        [*init_args(tmp_path), "--no-input", "--yes", "--dry-run", "--format", output],
    )
    assert result.exit_code == 0, result.output
    assert not image_executor.executed
    assert not (tmp_path / ".odcli").exists()
    cluster = PostgresCluster._from_config(
        project(tmp_path), repository_root=tmp_path, compose_runner=None
    )
    assert not cluster._trust_file().exists()


@pytest.mark.parametrize("output", ["rich", "toon"])
def test_init_yes_approves_image_in_one_command(
    tmp_path: Path, image_executor: RecordingExecutor, output: str
) -> None:
    result = CliRunner().invoke(
        cli,
        [*init_args(tmp_path), "--no-input", "--yes", "--format", output],
    )
    assert result.exit_code == 0, result.output
    assert "Approve PostgreSQL image" not in result.output
    cluster = PostgresCluster._from_config(
        project(tmp_path), repository_root=tmp_path, compose_runner=None
    )
    assert cluster._approved_image_digest() == DIGEST
    assert cluster._trust_file().stat().st_mode & 0o777 == 0o600


def test_init_yes_approves_image_in_one_command_json(
    tmp_path: Path, image_executor: RecordingExecutor
) -> None:
    result = CliRunner().invoke(
        cli,
        [*init_args(tmp_path), "--no-input", "--yes", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True
    assert "Approve PostgreSQL image" not in result.output
    cluster = PostgresCluster._from_config(
        project(tmp_path), repository_root=tmp_path, compose_runner=None
    )
    assert cluster._approved_image_digest() == DIGEST
    assert cluster._trust_file().stat().st_mode & 0o777 == 0o600


def test_interactive_init_confirms_exact_image_accepts(
    tmp_path: Path, image_executor: RecordingExecutor
) -> None:
    result = CliRunner().invoke(cli, init_args(tmp_path), input="y\n")
    assert f"Approve PostgreSQL image postgres:16 resolved to {DIGEST}?" in result.output
    cluster = PostgresCluster._from_config(
        project(tmp_path), repository_root=tmp_path, compose_runner=None
    )
    assert result.exit_code == 0, result.output
    assert cluster._approved_image_digest() == DIGEST
    assert (tmp_path / ".odcli/project.toml").exists()


def test_interactive_init_confirms_exact_image_declines(
    tmp_path: Path, image_executor: RecordingExecutor
) -> None:
    result = CliRunner().invoke(cli, init_args(tmp_path), input="n\n")
    assert f"Approve PostgreSQL image postgres:16 resolved to {DIGEST}?" in result.output
    cluster = PostgresCluster._from_config(
        project(tmp_path), repository_root=tmp_path, compose_runner=None
    )
    assert result.exit_code != 0
    assert not cluster._trust_file().exists()
    assert not (tmp_path / ".odcli").exists()
    assert not cluster.password_file.exists()


@pytest.mark.parametrize("failed_step", ["postgres.image.pull", "postgres.image.inspect"])
def test_failed_image_resolution_does_not_approve_or_initialize(
    tmp_path: Path, image_executor: RecordingExecutor, failed_step: str
) -> None:
    factory = image_executor.result_factory
    assert factory is not None

    def fail(step: PreparedProcess) -> ProcessResult:
        from dataclasses import replace

        result = factory(step)
        assert isinstance(result, ProcessResult)
        return (
            replace(result, returncode=1, stderr="image unavailable")
            if step.step_id == failed_step
            else result
        )

    image_executor.result_factory = fail
    result = CliRunner().invoke(cli, [*init_args(tmp_path), "--yes", "--no-input"])
    assert result.exit_code != 0
    cluster = PostgresCluster._from_config(
        project(tmp_path), repository_root=tmp_path, compose_runner=None
    )
    assert not cluster._trust_file().exists()
    assert not cluster.password_file.exists()
    assert not (tmp_path / ".odcli").exists()


def test_external_init_never_resolves_or_confirms_image(
    tmp_path: Path, image_executor: RecordingExecutor
) -> None:
    config = ProjectConfig(
        repository_root=tmp_path,
        python=sys.executable,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        postgres=PostgresProjectConfig(mode="external"),
    )

    def unexpected_confirmation(image: str, digest: str) -> None:
        pytest.fail("external PostgreSQL must not ask for image consent")

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        confirm_postgres_image=unexpected_confirmation,
    )
    command.run()
    assert not image_executor.executed
    assert (tmp_path / ".odcli/project.toml").exists()
