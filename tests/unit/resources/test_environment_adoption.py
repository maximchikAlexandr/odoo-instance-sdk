from __future__ import annotations

import subprocess
import traceback
import uuid
from pathlib import Path
from typing import cast

import pytest

from odoo_instance_sdk.exceptions import EnvironmentConflictError, StalePlanError
from odoo_instance_sdk.internal.git_worktree import GitError
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


@pytest.mark.parametrize("base_kind", ["branch", "pinned", "planning"])
def test_adopt_command_captures_external_checkout_without_git_creation(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    base_kind: str,
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
    if base_kind == "planning":
        subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--allow-empty",
                "-m",
                "planning",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    base_ref = (
        "main"
        if base_kind == "branch"
        else subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    command = env_client.environments.adopt_command(  # type: ignore[attr-defined]
        project_manifest,
        checkout,
        options=EnvironmentCheckoutOptions(
            db_mode=EnvironmentDatabaseMode.COPY,
            base_ref=base_ref,
            remote_name="staging",
            python=str(fake_python),
        ),
    )
    plan = command._private_projection()
    assert plan is not None
    assert plan.provenance.expected_base_ref == base_ref
    assert all(step.step_id != "checkout.worktree" for step in command._prepared().steps)
    assert checkout.is_dir()


def test_adopt_execution_rejects_changed_origin_lineage_before_mutation(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    catalog_path = get_catalog_path(ensure_exists=False)
    catalog_before = catalog_path.read_bytes() if catalog_path.exists() else None
    pinned_base = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    command, captured, restored = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch, base_ref=pinned_base
    )
    plan = command._private_projection()  # type: ignore[attr-defined]
    assert plan is not None
    assert plan.provenance.status.value == "matched"

    unrelated = tmp_path / "unrelated"
    subprocess.run(["git", "init", str(unrelated)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(unrelated),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@test.com",
            "commit",
            "--allow-empty",
            "-m",
            "unrelated",
        ],
        check=True,
        capture_output=True,
    )
    unrelated_head = subprocess.check_output(
        ["git", "-C", str(unrelated), "rev-parse", "HEAD"], text=True
    ).strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "fetch",
            "--no-write-fetch-head",
            str(unrelated),
            unrelated_head,
        ],
        check=True,
        capture_output=True,
    )
    clean_before = subprocess.check_output(
        ["git", "-C", str(checkout), "status", "--porcelain"], text=True
    )
    subprocess.run(
        ["git", "-C", str(checkout), "update-ref", "refs/remotes/origin/main", unrelated_head],
        check=True,
        capture_output=True,
    )
    assert (
        subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        == pinned_base
    )
    assert (
        subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", pinned_base], text=True
        ).strip()
        == pinned_base
    )
    assert (
        subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain"], text=True)
        == clean_before
    )

    with pytest.raises(EnvironmentConflictError, match="backup source branch"):
        command.run()  # type: ignore[attr-defined]

    executor = cast("RecordingExecutor", captured["executor"])
    assert "checkout.validate.git.lineage.origin" in {step.step_id for step in executor.executed}
    assert not restored
    assert (catalog_path.read_bytes() if catalog_path.exists() else None) == catalog_before


def _commit_pinned_change(checkout: Path, relative_path: str) -> str:
    target = checkout / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("pinned descendant\n")
    subprocess.run(
        ["git", "-C", str(checkout), "add", relative_path], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-m", "pinned-descendant"],
        check=True,
        capture_output=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()


def test_adopt_command_accepts_pinned_planning_descendant_of_origin(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    pinned = _commit_pinned_change(checkout, "openspec/pinned-plan.md")
    restore_calls: list[uuid.UUID] = []

    command, _captured, restored = _capture_adoption_command(
        env_client,
        project_manifest,
        checkout,
        fake_python,
        monkeypatch,
        base_ref=pinned,
        restore_observed=restore_calls,
    )
    command.run()  # type: ignore[attr-defined]

    assert restored and restore_calls


def test_adopt_command_rejects_runtime_pinned_descendant_of_origin_before_mutation(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    pinned = _commit_pinned_change(checkout, "runtime/pinned-change.py")
    catalog_path = get_catalog_path(ensure_exists=False)
    catalog_before = catalog_path.read_bytes() if catalog_path.exists() else None
    restore_calls: list[uuid.UUID] = []

    with pytest.raises(EnvironmentConflictError, match="backup source branch"):
        _capture_adoption_command(
            env_client,
            project_manifest,
            checkout,
            fake_python,
            monkeypatch,
            base_ref=pinned,
            restore_observed=restore_calls,
        )

    assert not restore_calls
    assert env_client.environments.list(project=project_manifest) == []  # type: ignore[attr-defined]
    assert (catalog_path.read_bytes() if catalog_path.exists() else None) == catalog_before


def test_adopt_execution_rejects_compatible_origin_advance_before_mutation(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    pinned_base = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    catalog_path = get_catalog_path(ensure_exists=False)
    catalog_before = catalog_path.read_bytes() if catalog_path.exists() else None
    command, captured, restored = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch, base_ref=pinned_base
    )

    subprocess.run(
        ["git", "-C", str(checkout), "checkout", "--detach", "refs/remotes/origin/main"],
        check=True,
        capture_output=True,
    )
    advanced_file = checkout / "runtime" / "origin-advance.py"
    advanced_file.parent.mkdir(parents=True, exist_ok=True)
    advanced_file.write_text("compatible advance\n")
    subprocess.run(
        ["git", "-C", str(checkout), "add", "runtime/origin-advance.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-m", "compatible-origin-advance"],
        check=True,
        capture_output=True,
    )
    advanced_tip = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    subprocess.run(
        ["git", "-C", str(checkout), "update-ref", "refs/remotes/origin/main", advanced_tip],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    assert (
        subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        == pinned_base
    )
    assert not subprocess.check_output(
        ["git", "-C", str(checkout), "status", "--porcelain"], text=True
    )

    with pytest.raises(StalePlanError, match="lineage evidence changed"):
        command.run()  # type: ignore[attr-defined]

    executor = cast("RecordingExecutor", captured["executor"])
    assert "checkout.validate.git.lineage.origin" in {step.step_id for step in executor.executed}
    assert not restored
    assert env_client.environments.list(project=project_manifest) == []  # type: ignore[attr-defined]
    assert (catalog_path.read_bytes() if catalog_path.exists() else None) == catalog_before


def test_adopt_execution_rejects_origin_selection_drift_before_mutation(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    pinned_base = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    catalog_path = get_catalog_path(ensure_exists=False)
    catalog_before = catalog_path.read_bytes() if catalog_path.exists() else None
    command, captured, restored = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch, base_ref=pinned_base
    )
    clean_before = subprocess.check_output(
        ["git", "-C", str(checkout), "status", "--porcelain"], text=True
    )
    subprocess.run(
        ["git", "-C", str(checkout), "update-ref", "-d", "refs/remotes/origin/main"],
        check=True,
        capture_output=True,
    )
    assert (
        subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        == pinned_base
    )
    assert (
        subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain"], text=True)
        == clean_before
    )

    with pytest.raises(StalePlanError, match="lineage evidence changed"):
        command.run()  # type: ignore[attr-defined]

    executor = cast("RecordingExecutor", captured["executor"])
    assert "checkout.validate.git.lineage.local" in {step.step_id for step in executor.executed}
    assert not restored
    assert env_client.environments.list(project=project_manifest) == []  # type: ignore[attr-defined]
    assert (catalog_path.read_bytes() if catalog_path.exists() else None) == catalog_before


def _divergent_pinned_checkout(
    project_manifest: Path,
    tmp_path: Path,
    *,
    tip_source: str,
    pinned_path: str,
    tip_path: str,
) -> tuple[Path, str]:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    fork = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()

    def commit_file(parent: str, relative_path: str, message: str) -> str:
        subprocess.run(
            ["git", "-C", str(checkout), "checkout", "--detach", parent],
            check=True,
            capture_output=True,
        )
        target = checkout / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{message}\n")
        subprocess.run(
            ["git", "-C", str(checkout), "add", relative_path], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(checkout), "commit", "-m", message],
            check=True,
            capture_output=True,
        )
        return subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()

    pinned = commit_file(fork, pinned_path, "pinned-side-change")
    tip = commit_file(fork, tip_path, "tip-side-change")
    subprocess.run(
        ["git", "-C", str(checkout), "update-ref", "refs/remotes/origin/main", tip],
        check=True,
        capture_output=True,
    )
    if tip_source == "local-fallback":
        subprocess.run(
            ["git", "-C", str(checkout), "update-ref", "-d", "refs/remotes/origin/main"],
            check=True,
            capture_output=True,
        )
        local_tip = tip
    else:
        unrelated = tmp_path / "unrelated-local"
        subprocess.run(["git", "init", str(unrelated)], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(unrelated),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--allow-empty",
                "-m",
                "unrelated-local",
            ],
            check=True,
            capture_output=True,
        )
        local_tip = subprocess.check_output(
            ["git", "-C", str(unrelated), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "fetch",
                "--no-write-fetch-head",
                str(unrelated),
                local_tip,
            ],
            check=True,
            capture_output=True,
        )
    subprocess.run(
        ["git", "-C", str(checkout), "update-ref", "refs/heads/main", local_tip],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "checkout", "-b", "adopted-test", pinned],
        check=True,
        capture_output=True,
    )
    return checkout, pinned


@pytest.mark.parametrize("tip_source", ["origin", "local-fallback"])
def test_adopt_command_accepts_pinned_planning_only_divergence(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tip_source: str,
) -> None:
    checkout, pinned = _divergent_pinned_checkout(
        project_manifest,
        tmp_path,
        tip_source=tip_source,
        pinned_path="openspec/pinned-plan.md",
        tip_path="runtime/tip-change.py",
    )
    catalog_path = get_catalog_path(ensure_exists=False)
    catalog_before = catalog_path.read_bytes() if catalog_path.exists() else None
    restore_calls: list[uuid.UUID] = []

    command, captured, restored = _capture_adoption_command(
        env_client,
        project_manifest,
        checkout,
        fake_python,
        monkeypatch,
        base_ref=pinned,
        restore_observed=restore_calls,
    )
    command.run()  # type: ignore[attr-defined]

    executed_ids = {
        step.step_id for step in cast("RecordingExecutor", captured["executor"]).executed
    }
    selected = "origin" if tip_source == "origin" else "local"
    unused = "local" if selected == "origin" else "origin"
    assert f"checkout.validate.git.lineage.{selected}.merge-base" in executed_ids
    assert f"checkout.validate.git.lineage.{unused}.merge-base" not in executed_ids
    assert restored and restore_calls
    assert (catalog_path.read_bytes() if catalog_path.exists() else None) == catalog_before


@pytest.mark.parametrize("tip_source", ["origin", "local-fallback"])
def test_adopt_command_rejects_pinned_runtime_divergence_before_mutation(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tip_source: str,
) -> None:
    checkout, pinned = _divergent_pinned_checkout(
        project_manifest,
        tmp_path,
        tip_source=tip_source,
        pinned_path="runtime/pinned-change.py",
        tip_path="openspec/tip-plan.md",
    )
    catalog_path = get_catalog_path(ensure_exists=False)
    catalog_before = catalog_path.read_bytes() if catalog_path.exists() else None
    restore_calls: list[uuid.UUID] = []

    with pytest.raises(EnvironmentConflictError, match="backup source branch"):
        _capture_adoption_command(
            env_client,
            project_manifest,
            checkout,
            fake_python,
            monkeypatch,
            base_ref=pinned,
            restore_observed=restore_calls,
        )

    assert not restore_calls
    assert env_client.environments.list(project=project_manifest) == []  # type: ignore[attr-defined]
    assert (catalog_path.read_bytes() if catalog_path.exists() else None) == catalog_before


def _record_revision_expression_source(
    checkout: Path, project_manifest: Path, recorded_branch: str
) -> None:
    if recorded_branch == "main~1":
        subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--allow-empty",
                "-m",
                "parent-for-revision-test",
            ],
            check=True,
            capture_output=True,
        )
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text().replace('git_branch = "main"', f'git_branch = "{recorded_branch}"')
    )


@pytest.mark.parametrize("recorded_branch", ["main^{commit}", "main~1", "main..x"])
def test_adopt_command_rejects_revision_expression_as_source_branch(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    recorded_branch: str,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    _record_revision_expression_source(checkout, project_manifest, recorded_branch)
    catalog_path = get_catalog_path(ensure_exists=False)
    catalog_before = catalog_path.read_bytes() if catalog_path.exists() else None

    with pytest.raises((EnvironmentConflictError, GitError)):
        env_client.environments.adopt_command(  # type: ignore[attr-defined]
            project_manifest,
            checkout,
            options=EnvironmentCheckoutOptions(
                db_mode=EnvironmentDatabaseMode.COPY,
                base_ref=recorded_branch,
                remote_name="staging",
                python=str(fake_python),
            ),
        )

    assert env_client.environments.list(project=project_manifest) == []  # type: ignore[attr-defined]
    assert (catalog_path.read_bytes() if catalog_path.exists() else None) == catalog_before


def test_adopt_command_accepts_literal_slash_source_branch(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    base = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    subprocess.run(
        ["git", "-C", str(checkout), "update-ref", "refs/remotes/origin/feature/lineage", base],
        check=True,
        capture_output=True,
    )
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text().replace('git_branch = "main"', 'git_branch = "feature/lineage"')
    )
    command, captured, restored = _capture_adoption_command(
        env_client, project_manifest, checkout, fake_python, monkeypatch, base_ref=base
    )

    command.run()  # type: ignore[attr-defined]

    executed = cast("RecordingExecutor", captured["executor"]).executed
    assert "checkout.validate.git.lineage.branch" in {step.step_id for step in executed}
    assert restored


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
    restore_observed: list[uuid.UUID] | None = None,
    name: str | None = None,
    base_ref: str = "main",
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
            if step.step_id.startswith("checkout.validate.git.lineage."):
                result = subprocess.run(step.argv, check=False, capture_output=True, text=True)
                text = getattr(step, "text", False)
                return ProcessResult(
                    argv=step.argv,
                    returncode=result.returncode,
                    stdout=result.stdout if text else result.stdout.encode(),
                    stderr=result.stderr if text else result.stderr.encode(),
                    duration=0.0,
                    cwd=getattr(step, "cwd", None),
                    environment=getattr(step, "environment", ()),
                )
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
        if restore_observed is not None:
            restore_observed.append(value)
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
            base_ref=base_ref,
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
