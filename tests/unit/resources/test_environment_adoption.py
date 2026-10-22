from __future__ import annotations

import subprocess
import traceback
import uuid
from pathlib import Path
from typing import cast

import pytest

from odoo_instance_sdk.exceptions import EnvironmentConflictError, StalePlanError
from odoo_instance_sdk.internal.paths import get_catalog_path
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    ProcessResult,
    RecordingExecutor,
)
from odoo_instance_sdk.models import EnvironmentState
from odoo_instance_sdk.resources.environment import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
)
from odoo_instance_sdk.resources.environment.checkout_planning import _CheckoutSnapshot
from tests.unit.resources.adoption_test_support import _external_checkout, _process_result


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
    plan = command._private_projection()
    assert plan is not None
    assert all(step.step_id != "checkout.worktree" for step in command._prepared().steps)
    assert checkout.is_dir()


@pytest.mark.parametrize(
    ("configured_remote", "checkout_remote"),
    [
        ("ssh://git@github.com/org/repository.git", "git@GITHUB.com:org/repository.git"),
        ("git@github.com:org/repository.git", "ssh://git@GitHub.com/org/repository.git"),
    ],
)
def test_adopt_command_accepts_transport_equivalent_repository_origins(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    configured_remote: str,
    checkout_remote: str,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    for path, remote in ((project_manifest, configured_remote), (checkout, checkout_remote)):
        subprocess.run(
            ["git", "-C", str(path), "remote", "set-url", "origin", remote],
            check=True,
            capture_output=True,
            text=True,
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
    assert command._private_projection() is not None


@pytest.mark.parametrize(
    ("difference", "configured_remote", "checkout_remote"),
    [
        (
            "host",
            "ssh://git@github.com/org/repository.git",
            "git@other.example:org/repository.git",
        ),
        (
            "explicit port",
            "ssh://git@github.com/org/repository.git",
            "ssh://git@github.com:2222/org/repository.git",
        ),
        (
            "path",
            "ssh://git@github.com/org/repository.git",
            "git@github.com:org/other-repository.git",
        ),
        (
            "transport",
            "https://github.com/org/repository.git",
            "git@github.com:org/repository.git",
        ),
    ],
)
def test_adopt_command_rejects_non_equivalent_origins_before_mutation(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    difference: str,
    configured_remote: str,
    checkout_remote: str,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    for path, remote in ((project_manifest, configured_remote), (checkout, checkout_remote)):
        subprocess.run(
            ["git", "-C", str(path), "remote", "set-url", "origin", remote],
            check=True,
            capture_output=True,
            text=True,
        )
    catalog_path = get_catalog_path(ensure_exists=False)
    catalog_before = catalog_path.read_bytes() if catalog_path.exists() else None

    with pytest.raises(EnvironmentConflictError, match="does not match the selected project"):
        env_client.environments.adopt_command(  # type: ignore[attr-defined]
            project_manifest,
            checkout,
            options=EnvironmentCheckoutOptions(
                db_mode=EnvironmentDatabaseMode.COPY,
                base_ref="main",
                remote_name="staging",
                python=str(fake_python),
            ),
        )

    assert difference
    assert checkout.is_dir()
    assert (catalog_path.read_bytes() if catalog_path.exists() else None) == catalog_before


def test_adopt_command_rejects_unsafe_repository_origin_before_mutation(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    subprocess.run(
        [
            "git",
            "-C",
            str(project_manifest),
            "remote",
            "set-url",
            "origin",
            "https://github.com/org/repository.git?token=secret",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    with pytest.raises(EnvironmentConflictError, match="does not match the selected project"):
        env_client.environments.adopt_command(  # type: ignore[attr-defined]
            project_manifest,
            checkout,
            options=EnvironmentCheckoutOptions(
                db_mode=EnvironmentDatabaseMode.COPY,
                base_ref="main",
                remote_name="staging",
                python=str(fake_python),
            ),
        )

    assert checkout.is_dir()
    assert env_client.environments.list(project=project_manifest) == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("configured_remote", "checkout_remote"),
    [
        (
            "ssh://git@github.com/org/repository.git",
            "git@github.com:/org/repository.git",
        ),
        (
            "ssh://git@github.com/org/repository.git",
            "ssh://git@github.com//org/repository.git",
        ),
        (
            "git@github.com:org/repository.git",
            "git@github.com:/org/repository.git",
        ),
        (
            "git@github.com:org/repository.git",
            "git@github.com://org/repository.git",
        ),
    ],
    ids=["relative-vs-absolute-scp", "single-vs-double-uri", "relative-scp", "double-leading-scp"],
)
def test_adopt_command_rejects_distinct_repository_path_forms_before_mutation(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    configured_remote: str,
    checkout_remote: str,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    for path, remote in ((project_manifest, configured_remote), (checkout, checkout_remote)):
        subprocess.run(
            ["git", "-C", str(path), "remote", "set-url", "origin", remote],
            check=True,
            capture_output=True,
            text=True,
        )
    catalog_path = get_catalog_path(ensure_exists=False)
    catalog_before = catalog_path.read_bytes() if catalog_path.exists() else None

    with pytest.raises(EnvironmentConflictError, match="does not match the selected project"):
        env_client.environments.adopt_command(  # type: ignore[attr-defined]
            project_manifest,
            checkout,
            options=EnvironmentCheckoutOptions(
                db_mode=EnvironmentDatabaseMode.COPY,
                base_ref="main",
                remote_name="staging",
                python=str(fake_python),
            ),
        )

    assert checkout.is_dir()
    assert (catalog_path.read_bytes() if catalog_path.exists() else None) == catalog_before


@pytest.mark.parametrize(
    ("remote", "marker"),
    [
        pytest.param(
            "https://git\N{FULLWIDTH COLON}authority-redaction-marker@example.com/org/repository.git",
            "authority-redaction-marker",
            id="invalid-unicode-authority",
        ),
        pytest.param(
            "ssh://git@example.com:port-redaction-marker/org/repository.git",
            "port-redaction-marker",
            id="invalid-port",
        ),
    ],
)
def test_adopt_command_sanitizes_invalid_authority_before_mutation(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    remote: str,
    marker: str,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    subprocess.run(
        [
            "git",
            "-C",
            str(project_manifest),
            "remote",
            "set-url",
            "origin",
            remote,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    catalog_path = get_catalog_path(ensure_exists=False)
    catalog_before = catalog_path.read_bytes() if catalog_path.exists() else None

    with pytest.raises(EnvironmentConflictError) as raised:
        env_client.environments.adopt_command(  # type: ignore[attr-defined]
            project_manifest,
            checkout,
            options=EnvironmentCheckoutOptions(
                db_mode=EnvironmentDatabaseMode.COPY,
                base_ref="main",
                remote_name="staging",
                python=str(fake_python),
            ),
        )

    rendered = "".join(traceback.format_exception(raised.value))
    assert raised.value.code == "repository_identity_mismatch"
    assert marker not in str(raised.value)
    assert raised.value.__cause__ is not None
    assert marker not in repr(raised.value.__cause__)
    assert raised.value.__cause__.__cause__ is None
    assert marker not in rendered
    assert checkout.is_dir()
    assert (catalog_path.read_bytes() if catalog_path.exists() else None) == catalog_before


def _capture_adoption_command(  # noqa: C901
    env_client: object,
    project_manifest: Path,
    checkout: Path,
    fake_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    restore_error: BaseException | None = None,
    name: str | None = None,
) -> tuple[object, dict[str, object], list[uuid.UUID]]:
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
                "database.restore.attachments",
            ):
                if context.planned(step_id):  # type: ignore[attr-defined]
                    if step_id == "database.restore.attachments":
                        context.process(step_id)  # type: ignore[attr-defined]
                    else:
                        context.skip(step_id)  # type: ignore[attr-defined]
        if restore_error is not None:
            raise restore_error
        value = uuid.uuid4()
        restored.append(value)
        return value

    monkeypatch.setattr(type(resource), "_command_from_snapshot", build)
    monkeypatch.setattr(type(resource), "_copy_auxiliary_session", lambda *_args: None)
    monkeypatch.setattr(type(resource), "_preflight_copy_checkout", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(type(resource), "_do_copy_restore", restore)
    command = resource.adopt_command(
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
    executor = cast("RecordingExecutor", captured["executor"])
    step_ids = tuple(step.step_id for step in executor.executed)
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


@pytest.mark.parametrize("linked", [True, False], ids=["linked-worktree", "independent-clone"])
@pytest.mark.parametrize("absolute", [True, False], ids=["absolute", "relative"])
@pytest.mark.parametrize(
    "external_relative", [False, True], ids=["external-absolute", "external-relative"]
)
def test_adoption_rebases_project_paths_and_preserves_external_paths(
    env_client: object,
    project_manifest: Path,
    source_config: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    linked: bool,
    absolute: bool,
    external_relative: bool,
) -> None:
    from odoo_instance_sdk.internal.odoo_config import parse_odoo_config

    checkout = _external_checkout(project_manifest, tmp_path, linked=linked)
    external = tmp_path / "platform" / "addons"
    external_upgrade = tmp_path / "platform" / "upgrades"
    external.mkdir(parents=True)
    external_upgrade.mkdir(parents=True)
    addons = str(project_manifest / "addons") if absolute else "addons"
    upgrades = str(project_manifest / "upgrades") if absolute else "upgrades"
    external_entry = "../platform/addons" if external_relative else str(external)
    upgrade_external_entry = "../platform/upgrades" if external_relative else str(external)
    source_config.write_text(
        source_config.read_text()
        + f"addons_path = {addons},{external_entry}\n"
        + f"upgrade_path = {upgrades},{upgrade_external_entry}\n"
    )
    original = source_config.read_bytes()
    command, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )

    environment = command.run()  # type: ignore[attr-defined]
    generated = parse_odoo_config(Path(environment.generated_config_path))

    assert generated["addons_path"].split(",") == [str(checkout / "addons"), str(external)]
    expected_upgrade_external = external_upgrade if external_relative else external
    assert generated["upgrade_path"].split(",") == [
        str(checkout / "upgrades"),
        str(expected_upgrade_external),
    ]
    assert source_config.read_bytes() == original


@pytest.mark.parametrize("linked", [True, False], ids=["linked-worktree", "independent-clone"])
def test_adoption_rejects_explicit_source_http_port_before_provisioning(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    linked: bool,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=linked)

    with pytest.raises(EnvironmentConflictError) as raised:
        env_client.environments.adopt_command(  # type: ignore[attr-defined]
            project_manifest,
            checkout,
            options=EnvironmentCheckoutOptions(
                db_mode=EnvironmentDatabaseMode.COPY,
                base_ref="main",
                remote_name="staging",
                python=str(fake_python),
                http_port=8069,
            ),
        )

    assert raised.value.code == "port_in_use"
    assert raised.value.details["port"] == 8069
    assert env_client.environments.list(project=project_manifest) == []  # type: ignore[attr-defined]
    assert checkout.is_dir()


def test_adoption_plan_publishes_captured_base_provenance(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    command, captured, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )

    public = command._private_projection()  # type: ignore[attr-defined]
    snapshot = cast("_CheckoutSnapshot", captured["snapshot"])
    assert public is not None
    assert public.provenance.resolved_base_revision == snapshot.private.base_revision
    assert snapshot.execution_plan is not None


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


@pytest.mark.parametrize("linked", [True, False], ids=["worktree", "clone"])
def test_adoption_reserves_source_http_port_before_auxiliary_start(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    linked: bool,
) -> None:
    from odoo_instance_sdk.internal.address import AddressState

    monkeypatch.setattr("odoo_instance_sdk.internal.port_allocation._HTTP_RANGE_START", 8000)
    monkeypatch.setattr("odoo_instance_sdk.internal.port_allocation._HTTP_RANGE_END", 9000)
    busy: set[int] = set()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.port_allocation.probe_address",
        lambda _host, port: AddressState.OCCUPIED if port in busy else AddressState.FREE,
    )
    checkout = _external_checkout(project_manifest, tmp_path, linked=linked)
    command, _, _ = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch
    )
    busy.add(8069)
    environment = command.run()  # type: ignore[attr-defined]
    assert environment.state is EnvironmentState.READY
    assert environment.http_port != 8069
