from __future__ import annotations

import uuid
from pathlib import Path

from odoo_instance_sdk.internal.pg.inventory import DatabaseInventoryItem
from odoo_instance_sdk.internal.resource_inventory import (
    ActiveUse,
    EnvironmentResourceSource,
    FileResourceSource,
    MeasurementCompleteness,
    OwnershipConfidence,
    ProjectResourceSource,
    ResourceType,
    VolumeResourceSource,
    build_resource_inventory,
    collect_resource_inventory,
    stable_resource_identity,
)
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def test_typed_projection_is_frozen_and_redacts_untrusted_path(tmp_path: Path) -> None:
    outside = tmp_path / "private" / "venv"
    outside.mkdir(parents=True)
    result = build_resource_inventory(
        environments=(
            EnvironmentResourceSource(
                environment_id="env-1",
                project_id="project-one",
                name="feature",
                worktree_path=tmp_path / "worktree",
                python_environment_path=outside,
                python_environment_owned=False,
                state="active",
            ),
        ),
        roots=(tmp_path,),
    )
    python = next(item for item in result.resources if item.type.value == "python_environment")
    assert python.ownership_confidence is OwnershipConfidence.SHARED
    assert python.sanitized_path == "<local>/private/venv"
    assert not Path(python.sanitized_path or "").is_absolute()
    try:
        python.name = "changed"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("resource projections must be frozen")


def test_shared_python_and_volume_sources_are_deduplicated() -> None:
    environments = tuple(
        EnvironmentResourceSource(
            environment_id=env,
            project_id="project-one",
            name=env,
            worktree_path=Path(f"/tmp/{env}"),
            python_environment_path=Path("/shared/venv"),
            python_environment_owned=False,
            state="active",
        )
        for env in ("env-a", "env-b")
    )
    volume = VolumeResourceSource(
        project_id="project-one",
        volume_name="odcli_pg_data",
        cluster_id="cluster-1",
        owned=True,
        available=True,
        usage_bytes=4096,
        active=False,
    )
    result = build_resource_inventory(environments=environments, volumes=(volume,))
    python_items = [item for item in result.resources if item.type.value == "python_environment"]
    volume_item = next(item for item in result.resources if item.type.value == "volume")
    assert len(python_items) == 1
    assert set(python_items[0].relationships) == {"environment:env-a", "environment:env-b"}
    assert volume_item.name.startswith("volume:")
    assert volume_item.measured_bytes == 4096
    assert volume_item.reclaimable is False
    assert volume_item.reclaimability_reason == "retained by postgres lifecycle"


def test_doctor_findings_are_read_only_and_cover_leftovers_and_unknown_filestore(
    tmp_path: Path,
) -> None:
    partial = tmp_path / "backup.zip.part"
    partial.write_bytes(b"partial")
    filestore = tmp_path / "filestore" / "db"
    filestore.mkdir(parents=True)
    before = partial.read_bytes()
    result = build_resource_inventory(
        files=(
            FileResourceSource(path=partial, kind="part"),
            FileResourceSource(
                path=filestore,
                kind="filestore",
                ownership=OwnershipConfidence.UNKNOWN,
            ),
        ),
        roots=(tmp_path,),
        database_measurement_complete=False,
        database_measurement_reason="password=secret probe unavailable",
    )
    codes = {finding.code for finding in result.findings}
    assert {
        "crash_left_partial_backup",
        "filestore_ownership_unknown",
        "database_measurement_unavailable",
    } <= codes
    assert partial.read_bytes() == before
    assert "secret" not in " ".join(finding.message for finding in result.findings)
    assert result.complete is False


def test_database_projection_keeps_logical_bytes_distinct_from_reclamation() -> None:
    database = DatabaseInventoryItem(
        cluster="127.0.0.1:5432",
        cluster_id=str(uuid.uuid4()),
        name="demo",
        logical_size_bytes=8192,
        active_sessions=1,
        is_default=True,
        origin="restore",
    )
    result = build_resource_inventory(databases=(database,))
    item = result.resources[0]
    assert item.logical_bytes == 8192
    assert item.measured_bytes is None
    assert item.logical_bytes_semantics == "PostgreSQL logical database size"
    assert item.reclaimable is False


def test_project_edges_use_the_canonical_cluster_qualified_database_identity(
    tmp_path: Path,
) -> None:
    cluster_id = str(uuid.uuid4())
    database = DatabaseInventoryItem(
        cluster="127.0.0.1:5432",
        cluster_id=cluster_id,
        name="demo",
        logical_size_bytes=1,
        active_sessions=0,
        is_default=True,
    )

    result = build_resource_inventory(
        databases=(database,),
        projects=(
            ProjectResourceSource(
                project_id="project-one",
                repository_root=tmp_path,
                runtime_database="demo",
                database_cluster=cluster_id,
            ),
        ),
    )

    project = next(item for item in result.resources if item.type is ResourceType.PROJECT)
    database_item = next(item for item in result.resources if item.type is ResourceType.DATABASE)
    assert project.relationships == (database_item.stable_identity,)


def test_mixed_claim_and_unknown_database_graph_edges_all_resolve(
    tmp_path: Path,
) -> None:
    endpoint = "127.0.0.1:5432"
    claim_id = str(uuid.uuid4())
    proven_database = DatabaseInventoryItem(
        cluster=endpoint,
        cluster_id=claim_id,
        name="restored",
        logical_size_bytes=1,
        active_sessions=0,
        is_default=True,
        origin="restore",
    )
    unknown_database = DatabaseInventoryItem(
        cluster=endpoint,
        cluster_id=None,
        name="external",
        logical_size_bytes=2,
        active_sessions=0,
        is_default=False,
        origin="unknown",
    )
    proven_identity = stable_resource_identity("database", f"{claim_id}\x00restored")
    unknown_identity = stable_resource_identity("database", f"{endpoint}\x00external")

    result = build_resource_inventory(
        databases=(proven_database, unknown_database),
        projects=(
            ProjectResourceSource(
                project_id="project-one",
                repository_root=tmp_path,
                runtime_database="external",
                database_cluster=endpoint,
            ),
        ),
        files=(
            FileResourceSource(
                path=tmp_path / "filestore" / "restored",
                kind="filestore",
                owner_identity=proven_identity,
                ownership=OwnershipConfidence.PROVEN,
            ),
            FileResourceSource(
                path=tmp_path / "filestore" / "external",
                kind="filestore",
                owner_identity=unknown_identity,
                ownership=OwnershipConfidence.UNKNOWN,
            ),
        ),
        volumes=(
            VolumeResourceSource(
                project_id="project-one",
                volume_name="odcli_pg_data",
                cluster_id=claim_id,
                owned=True,
                available=False,
                usage_bytes=None,
            ),
        ),
    )

    identities = {item.stable_identity for item in result.resources}
    assert all(
        relationship in identities
        for item in result.resources
        for relationship in item.relationships
    )
    project = next(item for item in result.resources if item.type is ResourceType.PROJECT)
    filestores = [item for item in result.resources if item.type is ResourceType.FILESTORE]
    assert project.relationships == (unknown_identity,)
    assert {filestore.relationships[0] for filestore in filestores} == {
        proven_identity,
        unknown_identity,
    }
    assert claim_id not in {
        relationship for item in result.resources for relationship in item.relationships
    }


def test_catalogue_source_reads_without_writes(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    backup_id = str(uuid.uuid4())
    path = tmp_path / "backup.zip"
    path.write_bytes(b"backup")
    catalog.start_download(backup_id, "http://example.invalid", "demo", "zip", True, path)
    catalog.success_download(backup_id, "backup.zip", 6, "sha256")
    before = catalog._conn.total_changes
    result = collect_resource_inventory(catalog=catalog, roots=(tmp_path,))
    assert catalog._conn.total_changes == before
    assert any(item.stable_identity == f"backup:{backup_id}" for item in result.resources)
    assert all(not Path(item.sanitized_path or "").is_absolute() for item in result.resources)
    catalog.close()


def test_resource_command_is_read_only_and_discovers_owned_directory_files(
    tmp_path: Path,
) -> None:
    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "unknown.bin").write_bytes(b"unknown")
    (owned / "crash.zip.part").write_bytes(b"partial")
    result = collect_resource_inventory(owned_directories=(owned,), roots=(tmp_path,))
    codes = {finding.code for finding in result.findings}
    assert "unknown_owned_directory_file" in codes
    assert "crash_left_partial_backup" in codes
    assert (owned / "unknown.bin").exists()
    assert (owned / "crash.zip.part").exists()


def test_resource_source_matrix_preserves_project_only_context_and_volume_states(
    tmp_path: Path,
) -> None:
    result = build_resource_inventory(
        projects=(ProjectResourceSource("project-only", tmp_path / "repo"),),
        volumes=(
            VolumeResourceSource(
                project_id="project-only",
                volume_name="owned-stopped",
                cluster_id="cluster-1",
                owned=True,
                available=True,
                usage_bytes=1024,
                active=False,
            ),
            VolumeResourceSource(
                project_id="project-only",
                volume_name="external",
                cluster_id=None,
                owned=False,
                available=True,
                usage_bytes=2048,
                active=None,
                reason="external volume",
            ),
            VolumeResourceSource(
                project_id="project-only",
                volume_name="unknown",
                cluster_id=None,
                owned=False,
                available=False,
                usage_bytes=None,
                active=None,
                reason="volume probe unavailable",
            ),
        ),
        files=(FileResourceSource(tmp_path / "odoo.log", kind="log"),),
        roots=(tmp_path,),
    )

    assert sum(item.type is ResourceType.ENVIRONMENT for item in result.resources) == 0
    project = next(item for item in result.resources if item.type is ResourceType.PROJECT)
    assert project.stable_identity == "project:project-only"
    volumes = [item for item in result.resources if item.type is ResourceType.VOLUME]
    assert sum(item.active_use is ActiveUse.INACTIVE for item in volumes) == 1
    assert sum(item.active_use is ActiveUse.UNKNOWN for item in volumes) == 2
    assert all(item.reclaimable is False for item in volumes)
    assert any(item.completeness is MeasurementCompleteness.PARTIAL for item in volumes)


def test_handled_failed_download_without_partial_file_has_no_leftover_finding(
    tmp_path: Path,
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    backup_id = str(uuid.uuid4())
    catalog.start_download(
        backup_id,
        "http://example.invalid",
        "demo",
        "zip",
        True,
        tmp_path / "download.zip.part",
    )
    catalog.fail_download(backup_id, "transport", "temporary failure")
    result = collect_resource_inventory(
        catalog=catalog,
        roots=(tmp_path,),
        backup_root=tmp_path,
        owned_directories=(tmp_path,),
    )
    catalog.close()

    assert not any(finding.code == "crash_left_partial_backup" for finding in result.findings)
