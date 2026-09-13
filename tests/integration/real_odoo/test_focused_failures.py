"""Focused public-boundary failure and recovery scenarios for the full E2E tier."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tarfile
import time
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any

import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.models import BackupState
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.project import TestInstanceProjectConfig as _TestInstanceConfig
from odoo_instance_sdk.resources.database import DatabaseResource
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES, PublicLeafCase

from .archive import ArchiveIdentity, SourceBackupPlan
from .cleanup import (
    FailureEvidence,
    ResourceLedger,
    compose_down,
    terminate_owned_process_group,
)
from .compose import ComposeLifecycle, reserve_ports
from .conftest import E2ERuntime, _finalize
from .failures import (
    assert_secret_free,
    run_recovery_action,
    write_archive_variant,
    write_failure_evidence,
)
from .focused_handlers import invoke_case as _invoke_case
from .focused_support import (
    BACKUP_ID as _BACKUP_ID,
)
from .focused_support import (
    assert_project_state_preflight as _assert_project_state_preflight,
)
from .focused_support import (
    catalog_state as _catalog_state,
)
from .focused_support import (
    copy_catalog_snapshot as _copy_catalog_snapshot,
)
from .focused_support import (
    invoke_in_registered_worktree as _invoke_in_registered_worktree,
)
from .focused_support import (
    observe_failure as _observe_failure,
)
from .focused_support import (
    record as _record,
)
from .focused_support import (
    registered_worktree as _registered_worktree,
)
from .focused_support import (
    seed_backup as _seed_backup,
)
from .pins import E2E_PINS

pytestmark = [pytest.mark.real_odoo, pytest.mark.e2e_full, pytest.mark.serial]


@pytest.fixture(scope="module")
def focused_project(target_runtime: E2ERuntime, source_backup_plan: SourceBackupPlan) -> Path:
    """Reuse one checked-out SDK-owned project across focused leaves."""
    return _project(
        target_runtime,
        target_runtime.root / f"focused-project-{target_runtime.run_id}",
        source=source_backup_plan,
    )


@pytest.fixture(scope="module")
def focused_catalog(target_runtime: E2ERuntime, focused_project: Path) -> Path:
    """Keep a pristine registration/catalog snapshot for each focused leaf."""
    source = _catalog_for_project(target_runtime, focused_project)
    snapshot = target_runtime.root / "focused-catalog-baseline.sqlite3"
    _copy_catalog_snapshot(source, snapshot)
    return snapshot


def _catalog_for_project(runtime: E2ERuntime, project: Path) -> Path:
    """Find the catalog that actually owns a project's registered environment."""
    selector = project / ".odcli" / "e2e-environment-id"
    if not selector.is_file():
        raise AssertionError(f"focused project has no environment selector: {selector}")
    environment_id = selector.read_text(encoding="ascii").strip()
    # Environment checkout may resolve the SDK root through HOME while the
    # process that bootstrapped the project used ODCLI_E2E_CATALOG.  Select
    # the actual catalog containing this checkout instead of snapshotting an
    # empty sibling database; every leaf must start with the active row.
    configured = Path(runtime.environment["ODCLI_E2E_CATALOG"])
    candidates: tuple[Path, ...] = (configured, project / ".odcli" / "catalog.sqlite3")
    candidates += tuple(runtime.root.rglob("catalog.sqlite3"))
    source = next(
        (
            candidate
            for candidate in dict.fromkeys(candidates)
            if candidate.is_file() and _catalog_has_environment(candidate, environment_id)
        ),
        None,
    )
    if source is None:
        raise AssertionError(
            f"project catalog has no active environment {environment_id}: {configured}"
        )
    return source


def _catalog_has_environment(path: Path, environment_id: str) -> bool:
    catalog = BackupCatalog(db_path=path)
    try:
        return any(
            str(row["id"]) == environment_id and str(row["state"]) != "removed"
            for row in catalog.list_environments(include_removed=True)
        )
    finally:
        catalog.close()


def _isolated_catalog(source: Path, root: Path) -> Path:
    """Copy one closed catalog into the HOME-visible SDK root for one leaf."""
    home = root / "home"
    destination = home / ".odcli" / "catalog.sqlite3"
    _copy_catalog_snapshot(source, destination)
    return destination


def _ensure_isolated_environment(catalog_path: Path, project: Path) -> str:
    """Require each leaf to start from the active environment in its catalog."""
    return _assert_project_state_preflight(project, catalog_path)


def _replace_http_port(config: Path, port: int) -> None:
    lines = [
        line
        for line in config.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("http_port")
    ]
    lines.append(f"http_port = {port}")
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _materialize_odoo_bootstrap(repository: Path, root: Path) -> Path:
    """Extract only the pinned Odoo launcher/package for checkout preflight.

    The public checkout later creates the full pinned worktree.  Materializing
    another full source tree here makes the 60-second worktree phase depend on
    Docker Desktop's bind-mount copy speed, so the bootstrap executable is
    deliberately limited to the launcher and its import package.
    """
    relative = next(
        relative
        for relative in (Path("odoo-bin"), Path("odoo") / "odoo-bin")
        if subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "cat-file",
                "-e",
                f"{E2E_PINS.odoo_source_commit}:{relative}",
            ],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    archive = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "archive",
            "--format=tar",
            E2E_PINS.odoo_source_commit,
            "--",
            "odoo-bin",
            "odoo",
        ],
        capture_output=True,
        check=False,
    )
    assert archive.returncode == 0, archive.stderr.decode(errors="replace")
    bootstrap = root / ".odoo-bootstrap"
    bootstrap.mkdir(mode=0o700)
    root_resolved = bootstrap.resolve()
    with tarfile.open(fileobj=BytesIO(archive.stdout), mode="r:") as stream:
        for member in stream:
            destination = (bootstrap / member.name).resolve()
            assert destination.is_relative_to(root_resolved), member.name
            assert not member.issym() and not member.islnk(), member.name
            stream.extract(member, bootstrap, filter="data")
    executable = bootstrap / relative
    assert executable.is_file(), executable
    return executable


def _project(runtime: E2ERuntime, root: Path, *, source: SourceBackupPlan | None = None) -> Path:
    """Create a project using the public pinned source/uv lifecycle."""
    repository_value = os.environ.get("ODCLI_E2E_ODOO_SOURCE_REPO") or os.environ.get(
        "ODCLI_E2E_ODOO_SOURCE_CACHE"
    )
    if not repository_value:
        pytest.fail("ODCLI_E2E_ODOO_SOURCE_REPO is required for focused public leaves")
    repository = Path(repository_value).expanduser().resolve()
    if not repository.is_dir():
        pytest.fail(f"Odoo source cache is not a directory: {repository}")
    subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", str(repository), str(root)],
        check=True,
        capture_output=True,
    )
    # The CI source cache is a bare repository populated with a detached
    # commit (FETCH_HEAD), not a named branch.  A local clone does not
    # advertise that detached object, so fetch the pinned object explicitly
    # before checking it out.  This keeps the focused project on the exact
    # immutable source revision used by the full tier.
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "fetch",
            "--no-tags",
            "--depth=1",
            str(repository),
            E2E_PINS.odoo_source_commit,
        ],
        check=True,
        capture_output=True,
    )
    bootstrap_odoo_bin = _materialize_odoo_bootstrap(repository, root)
    relative = bootstrap_odoo_bin.relative_to(root / ".odoo-bootstrap")
    runtime.ledger.record(
        "worktree",
        f"{runtime.run_id}-{root.name}",
        lambda: shutil.rmtree(root, ignore_errors=True),
    )
    environment = dict(runtime.environment)
    project_postgres = reserve_ports(1)[0]
    project_postgres.release()
    public_http = reserve_ports(1)[0]
    public_http_port = public_http.port
    runtime.ledger.record(
        "port",
        f"{runtime.run_id}-focused-http-{root.name}-{public_http_port}",
        public_http.release,
    )
    public_http.release()
    bootstrap_config = root / "focused-odoo.conf"
    shutil.copy2(runtime.config_file, bootstrap_config)
    _replace_http_port(bootstrap_config, public_http_port)
    command = shutil.which("odcli")
    if command is None:
        pytest.fail("odcli executable is required for focused public leaves")
    process_environment = {**os.environ, **environment}
    project_postgres_port = project_postgres.port
    init = subprocess.run(
        [
            command,
            "init",
            "--project",
            str(root),
            "--no-input",
            "--odoo-bin",
            str(bootstrap_odoo_bin),
            "--python",
            E2E_PINS.cpython,
            "--config",
            str(bootstrap_config),
            "--database",
            runtime.topology.target_sentinel_database,
            "--postgres",
            "compose",
            "--postgres-image",
            E2E_PINS.postgres_image,
            "--postgres-port",
            str(project_postgres.port),
            "--postgres-user",
            "odoo",
            "--http-port",
            str(public_http_port),
            "--format",
            "json",
        ],
        cwd=root,
        env=process_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert init.returncode == 0, init.stdout + init.stderr
    manifest = root / ".odcli" / "project.toml"
    initialized = ProjectConfig.load(root)
    initialized = msgspec.structs.replace(
        initialized,
        source_config=Path(".odcli/odoo.conf"),
        test_instance=(
            _TestInstanceConfig(base_url=source.endpoint, database=source.database)
            if source is not None
            else None
        ),
        default_base_ref=E2E_PINS.odoo_source_commit,
    )
    local_config = root / ".odcli" / "odoo.conf"
    shutil.copy2(bootstrap_config, local_config)
    # Public module/test leaves resolve modules from the pinned source tree;
    # the disposable target service config intentionally uses container paths.
    config_lines = [
        line
        for line in local_config.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("addons_path")
    ]
    config_lines.append(f"addons_path = {root / 'addons'}")
    local_config.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    logfile = root / f"odoo-{runtime.run_id}.log"
    logfile.write_text("INFO focused leaf completed; secret=redacted\n", encoding="utf-8")
    with local_config.open("a", encoding="utf-8") as stream:
        stream.write(f"logfile = {logfile}\n")
    local_config.chmod(0o600)
    manifest.write_text(initialized.to_manifest(), encoding="utf-8")
    manifest.chmod(0o600)

    from odoo_instance_sdk.resources.postgres import PostgresCluster

    project_cluster = PostgresCluster.from_project(root)
    resolved_image = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{index .RepoDigests 0}}",
            E2E_PINS.postgres_image,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert resolved_image.returncode == 0, resolved_image.stderr
    image_digest = resolved_image.stdout.strip()
    assert image_digest.rsplit("@", 1)[-1] == E2E_PINS.postgres_image.rsplit("@", 1)[-1]
    approved = subprocess.run(
        [
            command,
            "--project",
            str(root),
            "postgres",
            "approve-image",
            "--image-digest",
            image_digest,
            "--format",
            "json",
        ],
        cwd=root,
        env=process_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert approved.returncode == 0, approved.stdout + approved.stderr
    runtime.ledger.record(
        "postgres",
        f"{runtime.run_id}-postgres-{root.name}",
        lambda: compose_down(
            project_cluster.compose_file,
            project_cluster.compose_project_name,
            ports=(project_postgres_port,),
        ),
    )

    ticket = f"MYL-{int(runtime.run_id.replace('-', '')[:8], 16) % 100000000}"
    from odoo_instance_sdk import EnvironmentCheckoutOptions, OdooClient, OdooClientConfig

    previous_environment = os.environ.copy()
    os.environ.update(process_environment)
    try:
        checkout_result = OdooClient(
            config=OdooClientConfig(executable="odoo")
        ).environments.checkout_with_plan(
            root,
            ticket,
            options=EnvironmentCheckoutOptions(
                base_ref=E2E_PINS.odoo_source_commit,
                config_path=root / ".odcli" / "odoo.conf",
                source_database=runtime.topology.target_sentinel_database,
                odoo_bin=bootstrap_odoo_bin,
                python=E2E_PINS.cpython,
                create_venv=True,
                http_port=public_http_port,
            ),
        )
    finally:
        os.environ.clear()
        os.environ.update(previous_environment)
    checkout_environment = msgspec.to_builtins(checkout_result.environment)
    environment_id = str(checkout_environment["id"])
    (root / ".odcli" / "e2e-environment-id").write_text(environment_id, encoding="ascii")
    worktree = Path(str(checkout_environment["worktree_path"]))
    python = Path(str(checkout_environment["python_environment_path"])) / "bin" / "python"
    generated_config = Path(str(checkout_environment["generated_config_path"]))
    config = msgspec.structs.replace(
        initialized,
        odoo_bin=worktree / relative,
        python=python,
        source_config=generated_config,
    )
    manifest.write_text(config.to_manifest(), encoding="utf-8")

    # Materialize the project-owned claim before taking the baseline snapshot.
    # Every isolated leaf reuses this exact active environment/volume identity;
    # a catalog row without its attachment claim is not sufficient evidence.
    up = subprocess.run(
        [
            command,
            "--project",
            str(root),
            "postgres",
            "up",
            "--format",
            "json",
        ],
        cwd=root,
        env=process_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert up.returncode == 0, up.stdout + up.stderr
    up_document = json.loads(up.stdout)
    assert up_document["ok"] is True, up_document
    _assert_project_state_preflight(root, Path(process_environment["ODCLI_E2E_CATALOG"]))

    def remove_environment() -> None:
        removed = subprocess.run(
            [
                command,
                "--project",
                str(root),
                "env",
                "remove",
                environment_id,
                "--yes",
                "--format",
                "json",
            ],
            cwd=root,
            env=process_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        output = removed.stdout + removed.stderr
        normalized = output.casefold().replace("_", " ")
        if removed.returncode != 0 and "environment not found" not in normalized:
            raise RuntimeError(removed.stdout + removed.stderr)

    runtime.ledger.record(
        "environment",
        f"{runtime.run_id}-environment-{environment_id}",
        remove_environment,
    )
    return root


def _project_filestore(project: Path, database: str) -> Path:
    """Resolve the filestore from the public project-generated config."""
    config = ProjectConfig.load(project)
    source_config = config.source_config
    if source_config is None:
        pytest.fail(f"project has no generated source config: {project}")
    if not source_config.is_absolute():
        source_config = project / source_config
    from odoo_instance_sdk.models import StartConfig

    start = StartConfig.from_odoo_config(source_config)
    if start.data_dir is None:
        pytest.fail(f"generated config has no data_dir: {source_config}")
    return Path(start.data_dir) / "filestore" / database


def _project_database_probe(project: Path, database: str) -> subprocess.CompletedProcess[str]:
    """Probe the SDK-owned PostgreSQL cluster bound to a focused project."""
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    cluster = PostgresCluster.from_project(project)
    return ComposeLifecycle(cluster.compose_file, cluster.compose_project_name).run(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "odoo",
        "-d",
        "postgres",
        "-At",
        "-c",
        f"SELECT datname FROM pg_database WHERE datname = '{database}';",
        timeout=30.0,
    )


def _failure_text(result: object) -> str:
    return "\n".join(
        str(getattr(result, field, "")) for field in ("stdout", "stderr", "output", "exception")
    )


def _bind_catalog_path(monkeypatch: pytest.MonkeyPatch, catalog_path: Path) -> None:
    """Bind every already-imported public command catalog provider for a run."""

    def provider(**_: object) -> Path:
        return catalog_path

    for target in (
        "odoo_instance_sdk.cli.get_catalog_path",
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        "odoo_instance_sdk.internal.context.get_catalog_path",
        "odoo_instance_sdk.commands.env.get_catalog_path",
    ):
        monkeypatch.setattr(target, provider)
    from odoo_instance_sdk.commands import backup as backup_commands
    from odoo_instance_sdk.commands import resource as resource_commands

    monkeypatch.setattr(backup_commands._catalog_path_provider, "provider", provider)
    monkeypatch.setattr(resource_commands._catalog_path_provider, "provider", provider)


def _approve_project_postgres(project: Path, environment: dict[str, str]) -> None:
    """Reassert the exact pinned image through the public trust command."""
    resolved = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{index .RepoDigests 0}}",
            E2E_PINS.postgres_image,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr
    digest = resolved.stdout.strip()
    assert digest.rsplit("@", 1)[-1] == E2E_PINS.postgres_image.rsplit("@", 1)[-1]
    approved = _invoke_in_registered_worktree(
        CliRunner(),
        cli,
        project,
        Path(environment["ODCLI_E2E_CATALOG"]),
        [
            "--project",
            str(project),
            "postgres",
            "approve-image",
            "--image-digest",
            digest,
            "--format",
            "json",
        ],
        environment,
    )
    assert approved.exit_code == 0, approved.output


def test_remote_auth_and_unreachable_source_fail_closed(
    tmp_path: Path,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
    focused_project: Path,
    focused_catalog: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public refresh command must fail before creating a local backup."""
    runtime = target_runtime
    project = focused_project
    catalog_path = _isolated_catalog(focused_catalog, tmp_path)
    _ensure_isolated_environment(catalog_path, project)
    _bind_catalog_path(monkeypatch, catalog_path)
    runner = CliRunner()
    wrong_password = failure_evidence.secret_canary
    environment = {
        **runtime.environment,
        "HOME": str(catalog_path.parent.parent),
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_TEST_MASTER_PASSWORD": wrong_password,
        "ODCLI_E2E_KEEP_FAILED": "1",
    }
    auth = _invoke_in_registered_worktree(
        runner,
        cli,
        project,
        catalog_path,
        ["--project", str(project), "db", "refresh", "--format", "json"],
        environment,
    )
    assert auth.exit_code != 0
    auth_document, auth_files = _observe_failure(
        auth,
        runtime=runtime,
        evidence=failure_evidence,
        name="auth",
        argv=["db", "refresh", "--format", "json"],
    )
    assert auth_document is not None
    assert auth_document["error"]["code"] == "db_refresh_failed"
    assert not tuple(runtime.artifact_root.glob("*.zip"))
    assert_secret_free(auth_files, wrong_password)
    assert_secret_free(auth_document, wrong_password)
    _record(record_property, "E2E-FC-01", auth_document)
    _record(record_property, "E2E-SEC-01", {"artifacts": len(auth_files), "redacted": True})

    unreachable_plan = replace(
        source_backup_plan,
        endpoint="http://127.0.0.1:1",
        destination=runtime.artifact_root / "unreachable.zip",
    )
    unreachable_project = _project(
        runtime,
        runtime.root / f"unreachable-project-{runtime.run_id}",
        source=unreachable_plan,
    )
    unreachable = _invoke_in_registered_worktree(
        runner,
        cli,
        unreachable_project,
        catalog_path,
        ["--project", str(unreachable_project), "db", "refresh", "--format", "json"],
        environment,
    )
    assert unreachable.exit_code != 0
    network_document, _ = _observe_failure(
        unreachable,
        runtime=runtime,
        evidence=failure_evidence,
        name="unreachable",
        argv=["db", "refresh", "--format", "json"],
    )
    assert network_document is not None
    assert network_document["error"]["code"] == "db_refresh_failed"
    assert not unreachable_plan.destination.exists()
    _record(record_property, "E2E-FC-02", network_document)
    _record(
        record_property, "E2E-SEC-02", {"machine_output": True, "exit_code": unreachable.exit_code}
    )


@pytest.mark.parametrize("variant", ["truncated", "incompatible"])
def test_archive_and_restore_boundaries_publish_no_unowned_state(
    tmp_path: Path,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    variant: str,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
    focused_project: Path,
    focused_catalog: Path,
) -> None:
    path = tmp_path / f"{variant}.zip"
    write_archive_variant(path, variant)  # type: ignore[arg-type]
    catalog_path = _isolated_catalog(focused_catalog, tmp_path)
    _seed_backup(
        catalog_path,
        path,
        database=source_backup_plan.database,
        source_base_url=source_backup_plan.endpoint,
    )
    environment = {
        **target_runtime.environment,
        "HOME": str(catalog_path.parent.parent),
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_TEST_MASTER_PASSWORD": failure_evidence.secret_canary,
    }
    _ensure_isolated_environment(catalog_path, focused_project)
    _approve_project_postgres(focused_project, environment)
    _bind_catalog_path(monkeypatch, catalog_path)
    result = _invoke_in_registered_worktree(
        CliRunner(),
        cli,
        focused_project,
        catalog_path,
        ["backup", "validate", _BACKUP_ID, "--format", "json"],
        environment,
    )
    if variant == "incompatible":
        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        assert document["ok"] is True
        assert document["result"]["db_name"] == "not-the-catalogue-database"
        assert document["result"]["db_name"] != source_backup_plan.database
        files = write_failure_evidence(
            failure_evidence, logs={"archive-incompatible": result.stdout}
        )
        assert_secret_free(files, failure_evidence.secret_canary)
        project = focused_project
        target = f"odcli_incompatible_{target_runtime.run_id.replace('-', '')[:20]}"
        restore = _invoke_in_registered_worktree(
            CliRunner(),
            cli,
            project,
            catalog_path,
            [
                "--project",
                str(project),
                "db",
                "restore",
                _BACKUP_ID,
                "--target",
                target,
                "--yes",
                "--format",
                "json",
            ],
            environment,
        )
        assert restore.exit_code != 0
        restore_document, _ = _observe_failure(
            restore,
            runtime=target_runtime,
            evidence=failure_evidence,
            name="archive-incompatible-restore",
        )
        assert restore_document is not None
        assert restore_document["error"]["code"] == "db_restore_failed"
        assert "database" in restore_document["error"]["message"].lower()
        assert _catalog_state(catalog_path) is BackupState.AVAILABLE
        assert not _project_filestore(project, target).exists()
        probe = _project_database_probe(project, target)
        assert probe.returncode == 0
        assert probe.stdout.strip() != target
    else:
        assert result.exit_code != 0
        document, _ = _observe_failure(
            result, runtime=target_runtime, evidence=failure_evidence, name="archive-truncated"
        )
        assert document is not None
        assert document["error"]["code"] == "backup_validate_invalid"
    assert _catalog_state(catalog_path) is BackupState.AVAILABLE
    assert not (tmp_path / "database").exists()
    assert not (tmp_path / "filestore").exists()
    _record(record_property, "E2E-FC-03" if variant == "truncated" else "E2E-FC-04", document)


def test_catalog_restore_is_exact_and_occupied_or_repeated_targets_fail(
    tmp_path: Path,
    source_backup: ArchiveIdentity,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
    focused_project: Path,
    focused_catalog: Path,
) -> None:
    archive_path = tmp_path / "restore.zip"
    shutil.copy2(source_backup.path, archive_path)
    catalog_path = _isolated_catalog(focused_catalog, tmp_path)
    success_id = "00000000-0000-0000-0000-000000000008"
    _seed_backup(
        catalog_path,
        archive_path,
        backup_id=success_id,
        database=source_backup_plan.database,
        source_base_url=source_backup_plan.endpoint,
    )
    _bind_catalog_path(monkeypatch, catalog_path)
    project = focused_project
    target = f"odcli_restore_{target_runtime.run_id.replace('-', '')[:24]}"
    args = [
        "--project",
        str(project),
        "db",
        "restore",
        success_id,
        "--target",
        target,
        "--yes",
        "--format",
        "json",
    ]
    environment = {
        **target_runtime.environment,
        "HOME": str(catalog_path.parent.parent),
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_E2E_KEEP_FAILED": "1",
    }
    _ensure_isolated_environment(catalog_path, project)
    _approve_project_postgres(project, environment)
    successful = _invoke_in_registered_worktree(
        CliRunner(), cli, project, catalog_path, args, environment
    )
    assert successful.exit_code == 0, successful.output
    success_document = json.loads(successful.stdout)
    assert success_document["ok"] is True
    assert success_document["result"]["restored_database"] == target
    database_probe = _project_database_probe(project, target)
    assert database_probe.returncode == 0
    assert database_probe.stdout.strip() == target
    filestore = _project_filestore(project, target)
    assert filestore.is_dir()

    first = _invoke_in_registered_worktree(
        CliRunner(), cli, project, catalog_path, args, environment
    )
    second = _invoke_in_registered_worktree(
        CliRunner(), cli, project, catalog_path, args, environment
    )
    assert first.exit_code != 0 and second.exit_code != 0
    first_doc, _ = _observe_failure(
        first, runtime=target_runtime, evidence=failure_evidence, name="restore-occupied"
    )
    second_doc, _ = _observe_failure(
        second, runtime=target_runtime, evidence=failure_evidence, name="restore-repeated"
    )
    assert first_doc is not None and second_doc is not None
    assert first_doc["error"]["code"] == "db_restore_failed"
    assert second_doc["error"]["code"] == "db_restore_failed"
    assert _catalog_state(catalog_path, success_id) is BackupState.AVAILABLE
    assert database_probe.stdout.strip() == target
    assert filestore.is_dir()
    _record(
        record_property,
        "E2E-FC-05",
        {"success": success_document, "occupied": first_doc, "repeated": second_doc},
    )


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in PUBLIC_LEAF_CASES
        if case.e2e_disposition == "focused" and case.e2e_evidence != ("E2E-FC-05",)
    ],
    ids=lambda case: ".".join(case.path),
)
def test_remaining_focused_public_leaves_use_canonical_inventory(
    case: PublicLeafCase,
    target_runtime: E2ERuntime,
    source_backup: ArchiveIdentity,
    failure_evidence: FailureEvidence,
    record_property: object,
    focused_project: Path,
    focused_catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = _isolated_catalog(focused_catalog, tmp_path)
    _ensure_isolated_environment(catalog_path, focused_project)
    environment = {
        **target_runtime.environment,
        "HOME": str(catalog_path.parent.parent),
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_E2E_KEEP_FAILED": "1",
        "ODCLI_TEST_MASTER_PASSWORD": failure_evidence.secret_canary,
    }
    _approve_project_postgres(focused_project, environment)
    _bind_catalog_path(monkeypatch, catalog_path)
    _invoke_case(
        case,
        project=focused_project,
        runtime=target_runtime,
        evidence=failure_evidence,
        record_property=record_property,
        source_backup=source_backup,
        catalog_path=catalog_path,
    )


def test_sigint_timeout_and_partial_publication_recover_without_leaks(
    tmp_path: Path,
    target_runtime: E2ERuntime,
    source_backup: ArchiveIdentity,
    failure_evidence: FailureEvidence,
    resource_ledger: ResourceLedger,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = target_runtime.run_id
    archive_path = target_runtime.artifact_root / f"recovery-{run_id}.zip"
    shutil.copy2(source_backup.path, archive_path)
    catalog_path = Path(target_runtime.environment["ODCLI_E2E_CATALOG"])
    backup_id = "00000000-0000-0000-0000-000000000009"
    _seed_backup(
        catalog_path,
        archive_path,
        backup_id=backup_id,
        database=target_runtime.topology.source_database,
    )
    project = _project(target_runtime, target_runtime.root / f"recovery-{run_id}")
    isolated_catalog = _isolated_catalog(_catalog_for_project(target_runtime, project), tmp_path)
    catalog_path = isolated_catalog
    _ensure_isolated_environment(catalog_path, project)
    target = f"odcli_interrupt_{run_id.replace('-', '')[:20]}"
    command = shutil.which("odcli")
    if command is None:
        pytest.fail("odcli executable is required for public recovery boundaries")
    environment = {
        **os.environ,
        **target_runtime.environment,
        "HOME": str(catalog_path.parent.parent),
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_TEST_MASTER_PASSWORD": failure_evidence.secret_canary,
    }
    process = subprocess.Popen(
        [
            command,
            "--project",
            str(project),
            "db",
            "restore",
            backup_id,
            "--target",
            target,
            "--yes",
            "--format",
            "json",
        ],
        cwd=_registered_worktree(catalog_path, project),
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    resource_ledger.record(
        "process",
        f"{run_id}-odcli-restore-{process.pid}",
        lambda: terminate_owned_process_group(process.pid),
    )
    deadline = time.monotonic() + 5
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGINT)
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 130, stderr
    interrupted = json.loads(stdout)
    assert interrupted["error"]["code"] == "db_restore_interrupted"
    assert_secret_free(
        {"argv": command, "machine_output": stdout, "pytest_output": stderr},
        failure_evidence.secret_canary,
    )
    _record(record_property, "E2E-REC-01", interrupted)

    timeout_process = subprocess.Popen(
        [
            command,
            "--project",
            str(project),
            "db",
            "init-monitoring",
            target_runtime.topology.target_sentinel_database,
            "--yes",
            "--timeout",
            "0.001",
            "--format",
            "json",
        ],
        cwd=_registered_worktree(catalog_path, project),
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    resource_ledger.record(
        "process",
        f"{run_id}-odcli-timeout-{timeout_process.pid}",
        lambda: terminate_owned_process_group(timeout_process.pid),
    )
    timeout_stdout, timeout_stderr = timeout_process.communicate(timeout=30)
    assert timeout_process.returncode != 0
    timeout_document = json.loads(timeout_stdout)
    assert timeout_document["ok"] is False
    assert timeout_document["error"]["code"] == "db_init-monitoring_failed"
    assert "timeout" in timeout_document["error"]["message"].lower()
    assert_secret_free(
        {"argv": command, "machine_output": timeout_stdout, "pytest_output": timeout_stderr},
        failure_evidence.secret_canary,
    )
    _record(
        record_property,
        "E2E-REC-02",
        timeout_document,
    )

    partial_project = _project(target_runtime, target_runtime.root / f"partial-{run_id}")
    database = f"odcli_partial_{run_id.replace('-', '')[:20]}"
    partial_args = [
        "--project",
        str(partial_project),
        "db",
        "restore",
        backup_id,
        "--target",
        database,
        "--yes",
        "--format",
        "json",
    ]
    partial_document: dict[str, Any] = {}
    scenario_ledger = ResourceLedger(run_id)
    resource_ledger.record(
        "recovery-scenario",
        f"{run_id}-recovery-scenario",
        scenario_ledger.unwind,
    )

    original_restore = DatabaseResource.restore

    def restore_then_fail(
        resource: DatabaseResource, *restore_args: Any, **restore_kwargs: Any
    ) -> Any:
        original_restore(resource, *restore_args, **restore_kwargs)
        raise RuntimeError("injected restore failure after database and filestore publication")

    monkeypatch.setattr(DatabaseResource, "restore", restore_then_fail)
    filestore = _project_filestore(partial_project, database)

    def public_restore_failure() -> None:
        failed = _invoke_in_registered_worktree(
            CliRunner(), cli, partial_project, catalog_path, partial_args, environment
        )
        assert failed.exit_code != 0
        observed, _ = _observe_failure(
            failed,
            runtime=target_runtime,
            evidence=failure_evidence,
            name="recovery-partial-public-restore",
            argv=partial_args,
        )
        assert observed is not None
        assert filestore.is_dir()
        partial_document.update(observed)
        raise RuntimeError(observed["error"]["message"])

    def drop_database() -> None:
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        cluster = PostgresCluster.from_project(partial_project)
        result = ComposeLifecycle(cluster.compose_file, cluster.compose_project_name).run(
            "exec", "-T", "postgres", "dropdb", "-U", "odoo", database, timeout=30.0
        )
        if result.returncode != 0:
            raise RuntimeError("database cleanup failed")

    scenario_ledger.record(
        "database",
        f"{run_id}-{database}",
        drop_database,
    )
    scenario_ledger.record("filestore", f"{run_id}-filestore", lambda: shutil.rmtree(filestore))

    def delete_catalog_record() -> None:
        deleted = _invoke_in_registered_worktree(
            CliRunner(),
            cli,
            partial_project,
            catalog_path,
            [
                "--project",
                str(partial_project),
                "backup",
                "delete",
                backup_id,
                "--yes",
                "--format",
                "json",
            ],
            environment,
        )
        if deleted.exit_code != 0:
            raise RuntimeError(deleted.output)

    scenario_ledger.record("catalog", f"{run_id}-catalog", delete_catalog_record)
    scenario_ledger.record(
        "cleanup",
        f"{run_id}-injected-cleanup",
        lambda: (_ for _ in ()).throw(RuntimeError("cleanup failure")),
    )
    observation = run_recovery_action(
        public_restore_failure,
        ledger=scenario_ledger,
    )
    assert str(observation.primary_error) == partial_document["error"]["message"]
    assert observation.cleanup_errors == ("cleanup failure",)
    assert not filestore.exists()
    assert _catalog_state(catalog_path, backup_id) is BackupState.DELETED
    assert not archive_path.exists()
    database_probe = _project_database_probe(partial_project, database)
    assert database_probe.returncode == 0
    assert database_probe.stdout.strip() != database
    _record(
        record_property,
        "E2E-REC-03",
        {
            "primary_error": str(observation.primary_error),
            "cleanup_errors": observation.cleanup_errors,
        },
    )
    files = write_failure_evidence(
        failure_evidence,
        logs={
            "recovery-interrupt": stdout,
            "recovery-timeout": timeout_stdout,
            "recovery-partial": json.dumps(partial_document, sort_keys=True),
        },
    )
    assert_secret_free(files, failure_evidence.secret_canary)


def test_failed_debug_retention_contains_only_sanitized_files(
    tmp_path: Path,
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {
        **target_runtime.environment,
        "ODCLI_E2E_KEEP_FAILED": "1",
        "ODCLI_TEST_MASTER_PASSWORD": failure_evidence.secret_canary,
    }
    project = _project(
        target_runtime,
        target_runtime.root / f"retention-{target_runtime.run_id}",
        source=source_backup_plan,
    )
    catalog_path = _isolated_catalog(_catalog_for_project(target_runtime, project), tmp_path)
    _ensure_isolated_environment(catalog_path, project)
    environment["HOME"] = str(catalog_path.parent.parent)
    environment["ODCLI_E2E_CATALOG"] = str(catalog_path)
    result = _invoke_in_registered_worktree(
        CliRunner(),
        cli,
        project,
        catalog_path,
        ["--project", str(project), "db", "refresh", "--format", "json"],
        environment,
    )
    assert result.exit_code != 0
    document, files = _observe_failure(
        result, runtime=target_runtime, evidence=failure_evidence, name="retention"
    )
    assert document is not None
    assert files
    monkeypatch.setenv("ODCLI_E2E_KEEP_FAILED", "1")
    _finalize(target_runtime, RuntimeError(document["error"]["message"]))
    retained = tuple(failure_evidence.root.iterdir())
    assert retained
    assert_secret_free(retained, failure_evidence.secret_canary)
    assert all(path.parent == failure_evidence.root for path in retained)
    assert all(path.suffix in {".log", ".json"} for path in retained)
    assert all(path.stat().st_size <= 2 * 1024 * 1024 for path in files)
    _record(record_property, "E2E-SEC-03", {"artifact_limit_bytes": 2 * 1024 * 1024})
