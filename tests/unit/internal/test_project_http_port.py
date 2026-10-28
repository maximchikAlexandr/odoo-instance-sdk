from __future__ import annotations

import socket
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from odoo_instance_sdk.exceptions import EnvironmentConflictError, LockConflictError
from odoo_instance_sdk.internal.address import AddressState
from odoo_instance_sdk.internal.project_init import write_project_generated_config
from odoo_instance_sdk.internal.project_manifest import write_manifest
from odoo_instance_sdk.internal.project_runtime import (
    apply_project_http_port_selection,
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
    original_manifest = (root / ".odcli" / "project.toml").read_bytes()
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

    preview = select_project_http_port(ProjectConfig.load(root), None)
    assert ProjectConfig.load(root).assigned_http_port is None
    assert (root / ".odcli" / "project.toml").read_bytes() == original_manifest
    first = apply_project_http_port_selection(preview, None)
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


def test_stale_assigned_project_port_is_replaced_and_owned_config_reconciled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / ".git").mkdir()
    source_config = root / "odoo.conf"
    source_config.write_text("[options]\nhttp_port = 8069\ncustom_option = retained\n")
    original_source = source_config.read_bytes()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        stale_port = listener.getsockname()[1]
        with socket.socket() as replacement_reserver:
            replacement_reserver.bind(("127.0.0.1", 0))
            replacement_port = int(replacement_reserver.getsockname()[1])
        assert replacement_port != stale_port

        generated = root / ".odcli" / "odoo.conf"
        generated.parent.mkdir()
        generated.write_text(
            f"[options]\nhttp_interface = 127.0.0.1\nhttp_port = {stale_port}\n"
            "admin_passwd = test-admin\ndb_host = 127.0.0.1\ndb_port = 5468\n"
            "db_user = odoo\ndb_password = test-password\n"
        )
        generated.chmod(0o600)
        password_file = root / ".odcli" / "postgres-password"
        password_file.write_text("test-password\n")
        config = ProjectConfig(
            repository_root=root,
            odoo_bin=Path("/usr/bin/odoo"),
            source_config=Path("odoo.conf"),
            assigned_http_port=stale_port,
            postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=5468),
        )
        write_manifest(root, config)

        monkeypatch.setattr(
            "odoo_instance_sdk.internal.project_init.project_lifecycle_lock",
            lambda _root: nullcontext(),
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.project_init.validate_generated_config_target",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
            staticmethod(
                lambda _root: SimpleNamespace(
                    endpoint_host="127.0.0.1",
                    endpoint_port=5468,
                    password_file=password_file,
                )
            ),
        )

        def find_port(
            _kind: str,
            _catalog: object,
            *,
            requested: int | None = None,
            reserved_ports: tuple[int, ...] = (),
            **_kwargs: object,
        ) -> int:
            if requested == stale_port:
                raise EnvironmentConflictError("port_in_use", "occupied")
            if requested == replacement_port:
                return replacement_port
            assert requested is None
            assert reserved_ports == (stale_port,)
            return replacement_port

        monkeypatch.setattr("odoo_instance_sdk.internal.port_allocation.find_free_port", find_port)

        selection = apply_project_http_port_selection(
            select_project_http_port(ProjectConfig.load(root), None), None
        )

        assert (selection.port, selection.selection_kind, selection.fallback_reason) == (
            replacement_port,
            "automatic_fallback",
            "occupied",
        )
        assert ProjectConfig.load(root).assigned_http_port == replacement_port
        assert f"http_port = {replacement_port}" in generated.read_text()
        assert f"http_port = {stale_port}" not in generated.read_text()
        assert source_config.read_bytes() == original_source
        assert listener.fileno() >= 0
        assert listener.getsockname() == ("127.0.0.1", stale_port)
        with socket.create_connection(("127.0.0.1", stale_port), timeout=1):
            connection, _ = listener.accept()
            connection.close()


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

    selection = select_project_http_port(config, None)
    with project_lifecycle_lock(root), pytest.raises(LockConflictError):
        apply_project_http_port_selection(selection, None)
    assert (root / ".odcli" / "project.toml").read_bytes() == original


def test_execution_rejects_project_manifest_changed_after_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from msgspec.structs import replace

    root = tmp_path / "project"
    root.mkdir()
    config = _project(root)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_locks_dir",
        lambda **_kwargs: tmp_path / "locks",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.port_allocation.find_free_port",
        lambda *_args, **_kwargs: 8069,
    )

    selection = select_project_http_port(config, None)
    changed = replace(ProjectConfig.load(root), assigned_http_port=8070)
    write_manifest(root, changed)

    with pytest.raises(EnvironmentConflictError, match="changed after port preview"):
        apply_project_http_port_selection(selection, None)
    assert ProjectConfig.load(root).assigned_http_port == 8070


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


@pytest.mark.parametrize("catalog_mode", ["instance", "file"])
def test_registered_main_project_assignment_is_reserved_without_environments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalog_mode: str
) -> None:
    from odoo_instance_sdk.internal.repo_key import repo_key
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        root.mkdir()
        (root / ".git").mkdir()
        _project(root)
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    try:
        for root in roots:
            project_id = f"project_{repo_key(root.resolve(), (root / '.git').resolve())}"
            catalog._register_project(project_id, root, root / ".git")
        allocator_catalog = catalog if catalog_mode == "instance" else None
        if allocator_catalog is None:
            monkeypatch.setattr(
                "odoo_instance_sdk.internal.port_allocation._paths.get_catalog_path",
                lambda **_kwargs: tmp_path / "catalog.sqlite3",
            )
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.project_runtime.probe_address",
            lambda _host, _port: AddressState.FREE,
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.port_allocation.probe_address",
            lambda _host, _port: AddressState.FREE,
        )

        first_plan = select_project_http_port(ProjectConfig.load(roots[0]), allocator_catalog)
        first = apply_project_http_port_selection(first_plan, allocator_catalog)
        second_plan = select_project_http_port(ProjectConfig.load(roots[1]), allocator_catalog)
        second = apply_project_http_port_selection(second_plan, allocator_catalog)

        assert first.port == 8069
        assert second.port != first.port
        assert ProjectConfig.load(roots[0]).assigned_http_port == 8069
        assert ProjectConfig.load(roots[1]).assigned_http_port == second.port
        assert catalog.list_environments() == []
    finally:
        catalog.close()
