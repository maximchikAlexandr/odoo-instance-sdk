from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import httpx
import pytest

from odoo_instance_sdk.internal.paths import get_catalog_path
from odoo_instance_sdk.internal.project_manifest import write_manifest
from odoo_instance_sdk.models import BackupFormat
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.resources.instance import OdooInstance


def test_backup_audit_row_precedes_http_request(instance: OdooInstance, tmp_path: Path) -> None:
    """The download audit is durable before the remote request begins."""
    calls: list[str] = []
    response = MagicMock(spec=httpx.Response)
    response.headers = {"content-disposition": 'attachment; filename="snapshot.zip"'}
    response.raise_for_status.return_value = None
    response.iter_bytes.return_value = [b"snapshot"]

    http = MagicMock(spec=httpx.Client)

    def post(*_args: object, **_kwargs: object) -> MagicMock:
        calls.append("http")
        return response

    http.post.side_effect = post
    http_context = MagicMock()
    http_context.__enter__.return_value = http

    catalog = MagicMock()
    catalog.start_download.side_effect = lambda **_kwargs: calls.append("audit")

    with (
        patch.object(instance, "_client") as client,
        patch("httpx.Client", return_value=http_context),
    ):
        client.config.http_timeout_seconds = 10.0
        client.get_catalog.return_value = catalog
        backup = instance.databases.backup(
            "testdb",
            format=BackupFormat.ZIP,
            destination=tmp_path,
        )

    assert calls == ["audit", "http"]
    assert backup.database_name == "testdb"
    catalog.start_download.assert_called_once()
    catalog.success_download.assert_called_once()


def test_manifest_replace_failure_keeps_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manifest publication is a replacement, never a partial write."""
    original = ProjectConfig(repository_root=tmp_path, default_source_database="old")
    write_manifest(tmp_path, original)
    manifest = tmp_path / ".odcli" / "project.toml"
    previous = manifest.read_bytes()

    def fail_replace(source: str, destination: str) -> None:
        assert Path(destination) == manifest
        assert Path(source).parent == manifest.parent
        raise OSError("replacement failed")

    monkeypatch.setattr("odoo_instance_sdk.internal.project_manifest.os.replace", fail_replace)
    replacement = ProjectConfig(repository_root=tmp_path, default_source_database="new")

    with pytest.raises(OSError, match="replacement failed"):
        write_manifest(tmp_path, replacement)

    assert manifest.read_bytes() == previous
    assert list(manifest.parent.glob("project.toml*.tmp")) == []


def test_checkout_dry_run_does_not_initialize_catalog_or_environment(
    env_client: OdooClient, project_manifest: Path, fake_python: Path
) -> None:
    """Planning observes paths without creating durable checkout state."""
    options = EnvironmentCheckoutOptions(python=str(fake_python))
    plan = env_client.environments._plan_checkout(project_manifest, "feat/dry", options=options)

    assert not get_catalog_path().exists()
    assert not plan.worktree.exists()
    assert not plan.generated_config.exists()
    assert env_client.environments.list(project=project_manifest) == []
