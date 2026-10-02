from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import EnvironmentConflictError, StalePlanError
from odoo_instance_sdk.internal.proc import PreparedProcess, ProcessResult, RecordingExecutor
from odoo_instance_sdk.models import EnvironmentState
from odoo_instance_sdk.resources.environment import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
)


def test_adopt_command_captures_external_checkout_without_git_creation(
    env_client: object, project_manifest: Path, fake_python: Path, tmp_path: Path
) -> None:
    subprocess.run(
        ["git", "-C", str(project_manifest), "remote", "add", "origin", str(project_manifest)],
        check=True,
        capture_output=True,
        text=True,
    )
    checkout = tmp_path / "adopted"
    subprocess.run(
        ["git", "clone", str(project_manifest), str(checkout)],
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text()
        + "\n[remote_instances.staging]\n"
        + 'base_url = "https://staging.example"\n'
        + 'database = "comerta"\n'
        + 'git_branch = "main"\n'
    )
    command = env_client.environments.adopt_command(  # type: ignore[attr-defined]
        project_manifest,
        checkout,
        options=EnvironmentCheckoutOptions(
            db_mode=EnvironmentDatabaseMode.COPY,
            base_ref="main",
            remote_name="staging",
            python=str(fake_python),
        ),
    )
    plan = command._private_projection()  # type: ignore[attr-defined]
    assert plan is not None
    assert all(step.step_id != "checkout.worktree" for step in command._prepared().steps)  # type: ignore[attr-defined]
    assert checkout.is_dir()


def _add_named_source(project_manifest: Path) -> None:
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text()
        + "\n[remote_instances.staging]\n"
        + 'base_url = "https://staging.example"\n'
        + 'database = "comerta"\n'
        + 'git_branch = "main"\n'
    )


def _external_checkout(project_manifest: Path, tmp_path: Path, *, linked: bool) -> Path:
    subprocess.run(
        ["git", "-C", str(project_manifest), "remote", "add", "origin", str(project_manifest)],
        check=True,
        capture_output=True,
        text=True,
    )
    checkout = tmp_path / ("linked" if linked else "clone")
    if linked:
        subprocess.run(
            [
                "git",
                "-C",
                str(project_manifest),
                "worktree",
                "add",
                "-b",
                "adopted",
                str(checkout),
                "main",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        subprocess.run(
            ["git", "clone", str(project_manifest), str(checkout)],
            check=True,
            capture_output=True,
            text=True,
        )
    _add_named_source(project_manifest)
    return checkout


def _process_result(step: PreparedProcess, stdout: str = "", stderr: str = "") -> ProcessResult:
    text = getattr(step, "text", False)
    return ProcessResult(
        argv=step.argv,
        returncode=0,
        stdout=stdout if text else stdout.encode(),
        stderr=stderr if text else stderr.encode(),
        duration=0.0,
        cwd=step.cwd,
        environment=step.environment,
    )


def _capture_adoption_command(  # noqa: C901
    env_client: object,
    project_manifest: Path,
    checkout: Path,
    fake_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    restore_error: BaseException | None = None,
    name: str | None = None,
) -> tuple[object, object, list[uuid.UUID]]:
    resource = env_client.environments  # type: ignore[attr-defined]
    original = type(resource)._command_from_snapshot
    captured: dict[str, object] = {}
    restored: list[uuid.UUID] = []

    def build(self: object, snapshot: object, *, executor: object | None = None) -> object:
        captured["snapshot"] = snapshot

        def result_for(step: PreparedProcess) -> ProcessResult:
            plan = snapshot.private  # type: ignore[attr-defined]
            if step.step_id == "checkout.validate.git.toplevel":
                return _process_result(step, str(plan.repo_root))
            if step.step_id == "checkout.validate.git.common-dir":
                return _process_result(step, plan.git_common_dir)
            if step.step_id == "checkout.validate.git.base":
                value = subprocess.check_output(
                    ["git", "-C", str(plan.worktree), "rev-parse", plan.base_ref], text=True
                )
                return _process_result(step, value)
            if step.step_id == "checkout.validate.git.head":
                value = subprocess.check_output(
                    ["git", "-C", str(plan.worktree), "rev-parse", "HEAD"], text=True
                )
                return _process_result(step, value)
            if step.step_id == "checkout.validate.git.status":
                value = subprocess.check_output(
                    ["git", "-C", str(plan.worktree), "status", "--porcelain"], text=True
                )
                return _process_result(step, value)
            return _process_result(step)

        recording = RecordingExecutor(result_factory=result_for)
        captured["executor"] = recording
        return original(self, snapshot, executor=recording)

    def restore(_self: object, **_kwargs: object) -> uuid.UUID:
        context = _kwargs.get("context")
        if context is not None:
            for step_id in (
                "database.backup.transfer",
                "database.backup.wait",
                "database.restore.exists-before",
                "database.restore.exists-after",
            ):
                if context.planned(step_id):
                    context.skip(step_id)
        if restore_error is not None:
            raise restore_error
        value = uuid.uuid4()
        restored.append(value)
        return value

    monkeypatch.setattr(type(resource), "_command_from_snapshot", build)
    monkeypatch.setattr(type(resource), "_copy_auxiliary_session", lambda *_args: None)
    monkeypatch.setattr(type(resource), "_preflight_copy_checkout", lambda *_args: None)
    monkeypatch.setattr(type(resource), "_do_copy_restore", restore)
    command = resource.adopt_command(  # type: ignore[attr-defined]
        project_manifest,
        checkout,
        options=EnvironmentCheckoutOptions(
            db_mode=EnvironmentDatabaseMode.COPY,
            base_ref="main",
            remote_name="staging",
            python=str(fake_python),
            name=name,
        ),
    )
    return command, captured, restored


@pytest.mark.parametrize("linked", [True, False], ids=["linked-worktree", "independent-clone"])
def test_adoption_executes_copy_pipeline_without_taking_code_ownership(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    linked: bool,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=linked)
    command, captured, restored = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )

    environment = command.run()  # type: ignore[attr-defined]
    plan = command._private_projection()  # type: ignore[attr-defined]
    step_ids = tuple(step.step_id for step in captured["executor"].executed)  # type: ignore[attr-defined]
    assert environment.state is EnvironmentState.READY
    assert environment.code_ownership.value == "caller_owned"
    assert environment.checkout_repository_root == str(checkout)
    assert restored
    assert plan is not None
    assert plan.python_mode.value == "reuse"
    assert "checkout.worktree" not in {step.step_id for step in command.plan.steps}  # type: ignore[attr-defined]
    assert {"checkout.validate.git.head", "checkout.validate.git.status"} <= set(step_ids)
    assert "secret" not in repr(plan)
    assert checkout.is_dir()


def test_matching_ready_adoption_retries_by_uuid_without_copying_again(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    first, _, restored = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )
    expected = first.run()  # type: ignore[attr-defined]
    second, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )

    actual = second.run()  # type: ignore[attr-defined]
    assert actual.id == expected.id
    assert len(restored) == 1


def test_ready_adoption_identity_binds_resolved_base_and_source_evidence(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    first, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )
    expected = first.run()  # type: ignore[attr-defined]

    changed_source, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )
    moved_source = project_manifest / ".odcli" / "project.toml"
    moved_source.write_text(moved_source.read_text().replace("staging.example", "changed.example"))
    with pytest.raises(StalePlanError, match="source identity changed"):
        changed_source.run()  # type: ignore[attr-defined]

    changed_ready, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )
    with pytest.raises(EnvironmentConflictError, match="different inputs"):
        changed_ready.run()  # type: ignore[attr-defined]

    moved_source.write_text(moved_source.read_text().replace("changed.example", "staging.example"))
    ordinary_edit = checkout / "ordinary-edit.txt"
    ordinary_edit.write_text("uncommitted development edit")
    edited_retry, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )
    assert edited_retry.run().id == expected.id  # type: ignore[attr-defined]
    subprocess.run(["git", "add", "ordinary-edit.txt"], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-m", "ordinary development commit"], cwd=checkout, check=True)
    committed_retry, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )
    assert committed_retry.run().id == expected.id  # type: ignore[attr-defined]


def test_concurrent_adoption_reservation_conflict_and_recovery_are_explicit(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A durable creating reservation blocks a concurrent second execution."""
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    first, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )
    environment = first.run()  # type: ignore[attr-defined]
    conflicting, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch, name="different"
    )
    conflicting_plan = conflicting._private_projection()  # type: ignore[attr-defined]
    assert conflicting_plan is not None
    with pytest.raises(EnvironmentConflictError, match="different inputs"):
        conflicting.run()  # type: ignore[attr-defined]

    env_client.get_catalog().update_environment_state(  # type: ignore[attr-defined]
        str(environment.id), EnvironmentState.CREATING.value
    )
    recovering, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )
    with pytest.raises(EnvironmentConflictError, match="recover or remove it"):
        recovering.run()  # type: ignore[attr-defined]


def test_adoption_rejects_dirty_or_moved_base_and_allows_retry_after_revert(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    command, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )
    marker = checkout / "caller-owned.txt"
    marker.write_text("ordinary edit")
    with pytest.raises(EnvironmentConflictError, match="uncommitted changes"):
        command.run()  # type: ignore[attr-defined]
    marker.unlink()
    expected = command.run()  # type: ignore[attr-defined]

    moved, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )
    (checkout / "base-moved.txt").write_text("new base")
    subprocess.run(["git", "add", "base-moved.txt"], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-m", "move base"], cwd=checkout, check=True)
    assert moved.run().id == expected.id  # type: ignore[attr-defined]


def test_adoption_failure_cleans_only_sdk_artifacts(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    caller_file = checkout / "keep-me.txt"
    caller_file.write_text("caller code")
    subprocess.run(["git", "add", "keep-me.txt"], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-m", "caller code"], cwd=checkout, check=True)
    command, _, _ = _capture_adoption_command(
        env_client,
        project_manifest,
        checkout,
        fake_python,
        monkeypatch,
        restore_error=RuntimeError("restore failed"),
    )

    with pytest.raises(RuntimeError, match="restore failed"):
        command.run()  # type: ignore[attr-defined]
    assert caller_file.read_text() == "caller code"
    assert checkout.is_dir()


def test_adoption_rejects_wrong_repository_before_execution(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
) -> None:
    wrong = tmp_path / "wrong"
    subprocess.run(["git", "init", "-b", "main", str(wrong)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(wrong), "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", str(wrong), "config", "user.name", "Test"], check=True)
    (wrong / "README.md").write_text("wrong")
    subprocess.run(["git", "-C", str(wrong), "add", "."], check=True)
    subprocess.run(["git", "-C", str(wrong), "commit", "-m", "wrong"], check=True)
    with pytest.raises(EnvironmentConflictError, match="no comparable configured origin remote"):
        env_client.environments.adopt_command(  # type: ignore[attr-defined]
            project_manifest,
            wrong,
            options=EnvironmentCheckoutOptions(
                db_mode=EnvironmentDatabaseMode.COPY,
                base_ref="main",
                remote_name="staging",
                python=str(fake_python),
            ),
        )
