from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.project_filesystem_purge import (
    FilesystemPurgeAdapter,
    FilesystemPurgeTarget,
    project_owned_paths,
)
from odoo_instance_sdk.internal.project_manifest import default_project_ignore_contents
from odoo_instance_sdk.models.project_removal import ProjectResourceDecision


def _decision(identity: str, resource_type: str = "config") -> ProjectResourceDecision:
    return ProjectResourceDecision(
        identity=identity,
        resource_type=resource_type,
        action="delete",
        reason="contained_exclusive_sdk_resource",
        target="<local>/artifact",
    )


def _git_repo(root: Path) -> None:
    subprocess.run(("git", "init", str(root)), check=True, capture_output=True)


def _git_commit(root: Path, path: Path) -> None:
    subprocess.run(("git", "-C", str(root), "add", str(path)), check=True)
    subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "baseline",
        ),
        check=True,
        capture_output=True,
    )


def test_filesystem_adapter_deletes_only_contained_target(tmp_path: Path) -> None:
    target = tmp_path / ".odcli" / "project.toml"
    target.parent.mkdir()
    target.write_text("[project]\n", encoding="utf-8")
    identity = "config:project"
    command = FilesystemPurgeAdapter(
        (FilesystemPurgeTarget(identity, "project-1", target, tmp_path),)
    ).capture(_decision(identity), project_id="project-1")

    assert command is not None
    command.run()
    assert not target.exists()


def test_project_owned_paths_includes_generated_odoo_log(tmp_path: Path) -> None:
    root = tmp_path / "project"
    target = root / ".odcli" / "odoo.log"
    target.parent.mkdir(parents=True)
    target.write_text("generated log\n", encoding="utf-8")

    assert ("log", target) in project_owned_paths(root)
    identity = "log:project-odoo-log"
    command = FilesystemPurgeAdapter(
        (FilesystemPurgeTarget(identity, "project-1", target, root),)
    ).capture(_decision(identity, "log"), project_id="project-1")

    assert command is not None
    command.run()
    assert not target.exists()


def test_project_ignore_is_owned_only_with_exact_default_content(tmp_path: Path) -> None:
    root = tmp_path / "project"
    target = root / ".odcli" / ".gitignore"
    target.parent.mkdir(parents=True)
    target.write_bytes(default_project_ignore_contents())

    assert ("config", target) in project_owned_paths(root)
    command = FilesystemPurgeAdapter(
        (
            FilesystemPurgeTarget(
                "config:project-ignore",
                "project-1",
                target,
                root,
                expected_content=default_project_ignore_contents(),
            ),
        )
    ).capture(_decision("config:project-ignore"), project_id="project-1")
    assert command is not None
    target.write_text("caller-rule\n.env\nodoo.conf\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="content changed"):
        command.run()
    assert target.read_text(encoding="utf-8") == "caller-rule\n.env\nodoo.conf\n"

    target.write_text("caller-rule\n.env\nodoo.conf\n", encoding="utf-8")
    assert ("config", target) not in project_owned_paths(root)


def test_project_source_refuses_custom_project_ignore_rules(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    target = root / ".odcli" / ".gitignore"
    target.parent.mkdir()
    target.write_text("caller-rule\n.env\nodoo.conf\n", encoding="utf-8")
    command = FilesystemPurgeAdapter(
        (
            FilesystemPurgeTarget(
                "project:project-1",
                "project-1",
                root,
                tmp_path,
                git_common_dir=root / ".git",
            ),
        )
    ).capture(_decision("project:project-1", "source"), project_id="project-1")

    assert command is not None
    with pytest.raises(ConfigError, match="unknown project filesystem content"):
        command.run()
    assert target.read_text(encoding="utf-8") == "caller-rule\n.env\nodoo.conf\n"


def test_filesystem_adapter_refuses_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("keep", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(outside)
    identity = "config:link"
    command = FilesystemPurgeAdapter(
        (FilesystemPurgeTarget(identity, "project-1", link, tmp_path),)
    ).capture(_decision(identity), project_id="project-1")

    assert command is not None
    with pytest.raises(ConfigError, match="symlink"):
        command.run()
    assert outside.exists()


def test_source_adapter_retains_unknown_odcli_content(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    unknown = root / ".odcli" / "unexpected.json"
    unknown.parent.mkdir()
    unknown.write_text("keep", encoding="utf-8")
    identity = "project:project-1"
    decision = _decision(identity, "source")
    command = FilesystemPurgeAdapter(
        (
            FilesystemPurgeTarget(
                identity,
                "project-1",
                root,
                tmp_path,
                git_common_dir=root / ".git",
            ),
        )
    ).capture(decision, project_id="project-1")

    assert command is not None
    with pytest.raises(ConfigError, match="unknown"):
        command.run()
    assert unknown.exists()
    assert root.exists()


def test_source_adapter_retains_nested_symlink_content(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text("keep", encoding="utf-8")
    nested = root / ".odcli" / "logs"
    nested.mkdir(parents=True)
    link = nested / "external"
    link.symlink_to(outside)
    identity = "project:project-1"
    decision = _decision(identity, "source")
    command = FilesystemPurgeAdapter(
        (
            FilesystemPurgeTarget(
                identity,
                "project-1",
                root,
                tmp_path,
                git_common_dir=root / ".git",
            ),
        )
    ).capture(decision, project_id="project-1")

    assert command is not None
    with pytest.raises(ConfigError, match="nested symlink"):
        command.run()
    assert link.is_symlink()
    assert outside.exists()


def test_source_adapter_retains_untracked_content(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _git_repo(root)
    untracked = root / "notes.txt"
    untracked.write_text("keep", encoding="utf-8")
    identity = "project:project-1"
    command = FilesystemPurgeAdapter(
        (FilesystemPurgeTarget(identity, "project-1", root, tmp_path, root / ".git"),)
    ).capture(_decision(identity, "source"), project_id="project-1")

    assert command is not None
    with pytest.raises(ConfigError, match="clean Git"):
        command.run()
    assert untracked.exists()
    assert root.exists()


def test_source_adapter_retains_modified_content(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _git_repo(root)
    tracked = root / "tracked.txt"
    tracked.write_text("baseline", encoding="utf-8")
    _git_commit(root, tracked)
    tracked.write_text("modified", encoding="utf-8")
    identity = "project:project-1"
    command = FilesystemPurgeAdapter(
        (FilesystemPurgeTarget(identity, "project-1", root, tmp_path, root / ".git"),)
    ).capture(_decision(identity, "source"), project_id="project-1")

    assert command is not None
    with pytest.raises(ConfigError, match="clean Git"):
        command.run()
    assert tracked.exists()
    assert root.exists()


def test_source_adapter_rechecks_capture_to_run_drift(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _git_repo(root)
    identity = "project:project-1"
    command = FilesystemPurgeAdapter(
        (FilesystemPurgeTarget(identity, "project-1", root, tmp_path, root / ".git"),)
    ).capture(_decision(identity, "source"), project_id="project-1")

    assert command is not None
    (root / "drift.txt").write_text("changed after capture", encoding="utf-8")
    with pytest.raises(ConfigError, match="clean Git"):
        command.run()
    assert root.exists()


def test_source_adapter_deletes_clean_checkout_recursively(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _git_repo(root)
    (root / ".odcli").mkdir()
    manifest = root / ".odcli" / "project.toml"
    manifest.write_text("[project]\n", encoding="utf-8")
    _git_commit(root, manifest)
    identity = "project:project-1"
    command = FilesystemPurgeAdapter(
        (FilesystemPurgeTarget(identity, "project-1", root, tmp_path, root / ".git"),)
    ).capture(_decision(identity, "source"), project_id="project-1")

    assert command is not None
    command.run()
    assert not root.exists()
