from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from odoo_instance_sdk.exceptions import EnvironmentConflictError, LockConflictError
from odoo_instance_sdk.internal.address import AddressState
from odoo_instance_sdk.internal.project_init import write_project_generated_config
from odoo_instance_sdk.internal.project_manifest import write_manifest
from odoo_instance_sdk.internal.project_runtime import (
    resolve_project_http_port,
    select_project_http_port,
)
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig


@pytest.mark.parametrize(
    ("preferred", "assigned", "configured", "expected"),
    [
        (8071, 8072, 8073, 8071),
        (None, 8072, 8073, 8072),
        (None, None, 8073, 8073),
        (None, None, None, 8069),
    ],
)
def test_project_http_port_precedence(
    preferred: int | None,
    assigned: int | None,
    configured: int | None,
    expected: int,
) -> None:
    assert resolve_project_http_port(preferred, configured, assigned) == expected


def _project(
    root: Path,
    *,
    source_config: Path | None = None,
    preferred_http_port: int | None = None,
    assigned_http_port: int | None = None,
) -> ProjectConfig:
    (root / ".git").mkdir(exist_ok=True)
    config = ProjectConfig(
        repository_root=root,
        odoo_bin=Path("/usr/bin/odoo"),
        source_config=source_config,
        preferred_http_port=preferred_http_port,
        assigned_http_port=assigned_http_port,
    )
    write_manifest(root, config)
    return config


def test_project_run_persists_fallback_and_reuses_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "odoo.conf").write_text("[options]\nhttp_port = 8069\n")
    _project(root, source_config=Path("odoo.conf"))
    occupied = {8069}
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_init.project_lifecycle_lock",
        lambda _root: nullcontext(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_runtime.probe_address",
        lambda _host, port: AddressState.OCCUPIED if port in occupied else AddressState.FREE,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.port_allocation.probe_address",
        lambda _host, port: AddressState.OCCUPIED if port in occupied else AddressState.FREE,
    )

    def find_port(*_args: object, requested: int | None = None, **_kwargs: object) -> int:
        if requested in occupied:
            raise EnvironmentConflictError("port_in_use", "occupied")
        return requested if requested is not None else 8070

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.port_allocation.find_free_port",
        find_port,
    )

    first = select_project_http_port(ProjectConfig.load(root), None)
    assert (first.port, first.selection_kind, first.fallback_reason) == (
        8070,
        "automatic_fallback",
        "occupied",
    )
    assert ProjectConfig.load(root).assigned_http_port == 8070
    manifest_after_assignment = (root / ".odcli" / "project.toml").read_bytes()
    assert (root / "odoo.conf").read_text() == "[options]\nhttp_port = 8069\n"

    occupied.clear()
    second = select_project_http_port(ProjectConfig.load(root), None)
    assert (second.port, second.selection_kind, second.fallback_reason) == (
        8070,
        "automatic_reused",
        None,
    )
    assert (root / ".odcli" / "project.toml").read_bytes() == manifest_after_assignment


def test_explicit_project_pin_fails_closed_without_manifest_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    config = _project(root, preferred_http_port=8069, assigned_http_port=8070)
    original = (root / ".odcli" / "project.toml").read_bytes()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_init.project_lifecycle_lock",
        lambda _root: nullcontext(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.project_runtime.probe_address",
        lambda _host, _port: AddressState.OCCUPIED,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.port_allocation.probe_address",
        lambda _host, _port: AddressState.OCCUPIED,
    )

    with pytest.raises(
        EnvironmentConflictError, match=r"Explicit project HTTP endpoint 127\.0\.0\.1:8069"
    ):
        select_project_http_port(config, None)
    assert (root / ".odcli" / "project.toml").read_bytes() == original


def test_concurrent_project_port_selection_fails_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.project_init import project_lifecycle_lock

    root = tmp_path / "project"
    root.mkdir()
    config = _project(root)
    original = (root / ".odcli" / "project.toml").read_bytes()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_locks_dir",
        lambda **_kwargs: tmp_path / "locks",
    )

    with project_lifecycle_lock(root), pytest.raises(LockConflictError):
        select_project_http_port(config, None)
    assert (root / ".odcli" / "project.toml").read_bytes() == original


def test_assigned_port_is_written_to_owned_config_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "odoo.conf").write_text("[options]\nhttp_port = 8069\ncustom_option = retained\n")
    source_bytes = (root / "odoo.conf").read_bytes()
    (root / ".git").mkdir()
    config = ProjectConfig(
        repository_root=root,
        source_config=Path("odoo.conf"),
        assigned_http_port=8070,
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=5468),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
        staticmethod(lambda _root: SimpleNamespace(endpoint_host="127.0.0.1", endpoint_port=5468)),
    )

    write_project_generated_config(
        root, config, validate_target=False, ensure_password=False, password="test-secret"
    )

    generated = root / ".odcli" / "odoo.conf"
    assert "http_port = 8070" in generated.read_text()
    assert "custom_option = retained" in generated.read_text()
    assert (root / "odoo.conf").read_bytes() == source_bytes
