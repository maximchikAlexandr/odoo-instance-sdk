from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.project_filesystem_purge import (
    FilesystemPurgeAdapter,
    FilesystemPurgeTarget,
)
from odoo_instance_sdk.models.project_removal import ProjectResourceDecision


def _decision(identity: str, resource_type: str = "config") -> ProjectResourceDecision:
    return ProjectResourceDecision(
        identity=identity,
        resource_type=resource_type,
        action="delete",
        reason="contained_exclusive_sdk_resource",
        target="<local>/artifact",
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
