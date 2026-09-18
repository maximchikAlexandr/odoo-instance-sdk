"""Shared evidence and catalog helpers for focused real-Odoo leaves."""

from __future__ import annotations  # noqa: I001 -- keep real-Odoo project fixture aliases grouped; remove when Ruff supports grouped aliases.

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import chdir
from pathlib import Path
from typing import Any

import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.models import BackupState, StartConfig
from odoo_instance_sdk.project import (
    ProjectConfig,
    TestInstanceProjectConfig as _TestInstanceConfig,
)
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from scripts.real_odoo_bootstrap import PYTHON_RESOLUTION_LOCK
from scripts.real_odoo_pins import E2E_PINS

from .archive import SourceBackupPlan
from .cleanup import FailureEvidence, compose_down, replace_http_port
from .compose import ComposeLifecycle, reserve_ports
from .conftest import E2ERuntime
from .failures import assert_secret_free, write_failure_evidence
from .test_critical_path import _remove_owned_fixture_tree

BACKUP_ID = "00000000-0000-0000-0000-000000000007"


def copy_catalog_snapshot(source: Path, destination: Path) -> None:
    """Take a transactionally consistent snapshot, including SQLite WAL state."""
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    source_uri = f"file:{source}?mode=ro"
    with (
        sqlite3.connect(source_uri, uri=True) as source_connection,
        sqlite3.connect(temporary) as destination_connection,
    ):
        source_connection.backup(destination_connection)
    temporary.replace(destination)
    destination.chmod(0o600)


def registered_worktree(catalog_path: Path, project: Path) -> Path:
    """Return the active checkout recorded for a focused project."""
    selector = project / ".odcli" / "e2e-environment-id"
    if not selector.is_file():
        raise AssertionError(f"focused project has no environment selector: {selector}")
    environment_id = selector.read_text(encoding="ascii").strip()
    catalog = BackupCatalog(db_path=catalog_path)
    try:
        row = next(
            (
                item
                for item in catalog.list_environments(include_removed=True)
                if str(item["id"]) == environment_id
            ),
            None,
        )
    finally:
        catalog.close()
    if row is None or str(row["state"]) == "removed":
        raise AssertionError(
            f"isolated catalog does not contain an active environment: {environment_id}"
        )
    worktree = Path(str(row["worktree_path"])).resolve()
    if not worktree.is_dir():
        raise AssertionError(f"registered environment worktree is unavailable: {worktree}")
    return worktree


def assert_project_state_preflight(project: Path, catalog_path: Path) -> str:
    """Fail fast when a focused leaf lacks the state its public path requires."""
    config = ProjectConfig.load(project)
    if config.python is None:
        raise AssertionError("state preflight: registered project manifest lacks python")
    python = Path(config.python)
    if not python.is_absolute():
        python = project / python
    if not python.is_file():
        raise AssertionError(f"state preflight: registered python is unavailable: {python}")

    selector = project / ".odcli" / "e2e-environment-id"
    if not selector.is_file():
        raise AssertionError(f"state preflight: project has no environment selector: {selector}")
    environment_id = selector.read_text(encoding="ascii").strip()
    registered_worktree(catalog_path, project)

    from odoo_instance_sdk.resources.postgres import PostgresCluster

    cluster = PostgresCluster.from_project(project)
    catalog = BackupCatalog(db_path=catalog_path)
    try:
        claim = catalog._get_postgres_cluster(cluster._project_id)
    finally:
        catalog.close()
    if claim is None or claim.state != "active":
        raise AssertionError(
            "state preflight: isolated catalog lacks active postgres attachment "
            f"claim: {cluster._project_id}"
        )
    return environment_id


_copy_catalog_snapshot = copy_catalog_snapshot
_assert_project_state_preflight = assert_project_state_preflight


def invoke_in_registered_worktree(
    runner: Any,
    cli: Any,
    project: Path,
    catalog_path: Path,
    args: list[str],
    environment: dict[str, str],
    *,
    input: str | None = None,
) -> Any:
    """Invoke a public command from the exact worktree it resolves."""
    with chdir(registered_worktree(catalog_path, project)):
        return runner.invoke(cli, args, env=environment, input=input)


_invoke_in_registered_worktree = invoke_in_registered_worktree


def record(record_property: object, evidence: str, value: object = "passed") -> None:
    getattr(record_property, "__call__")(
        evidence.lower().replace("-", "_"), json.dumps(value, default=str, sort_keys=True)
    )


def observe_failure(
    result: object,
    *,
    runtime: E2ERuntime,
    evidence: FailureEvidence,
    name: str,
    argv: object = (),
) -> tuple[dict[str, Any] | None, tuple[Path, ...]]:
    """Audit every real CLI failure across output, exception, and artifacts."""
    text = "\n".join(
        str(getattr(result, field, "")) for field in ("stdout", "stderr", "output", "exception")
    )
    assert_secret_free(
        {
            "argv": argv,
            "machine_output": text,
            "pytest_output": getattr(result, "output", ""),
            "fingerprint": getattr(result, "fingerprint", ""),
            "exception_graph": getattr(result, "exception", ""),
        },
        evidence.secret_canary,
    )
    files = write_failure_evidence(evidence, logs={name: text})
    assert_secret_free(files, evidence.secret_canary)
    stdout = str(getattr(result, "stdout", ""))
    if not stdout.strip():
        return None, files
    try:
        document = json.loads(stdout)
    except json.JSONDecodeError:
        return None, files
    assert isinstance(document, dict) and document.get("ok") is False
    return document, files


def seed_backup(
    db_path: Path,
    archive_path: Path,
    *,
    backup_id: str = BACKUP_ID,
    database: str = "demo",
    source_base_url: str = "http://127.0.0.1:8069",
) -> None:
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    catalog = BackupCatalog(db_path=db_path)
    try:
        catalog.start_download(backup_id, source_base_url, database, "zip", True, archive_path)
        catalog.success_download(backup_id, archive_path.name, archive_path.stat().st_size, digest)
    finally:
        catalog.close()


def catalog_state(db_path: Path, backup_id: str = BACKUP_ID) -> BackupState:
    catalog = BackupCatalog(db_path=db_path)
    row = catalog.get_by_id(backup_id)
    catalog.close()
    assert row is not None
    return BackupState(str(row["state"]))


def project_filestore(project: Path, database: str) -> Path:
    config = ProjectConfig.load(project)
    source_config = config.source_config
    if source_config is None:
        raise AssertionError(f"project has no generated source config: {project}")
    if not source_config.is_absolute():
        source_config = project / source_config
    start = StartConfig.from_odoo_config(source_config)
    if start.data_dir is None:
        raise AssertionError(f"generated config has no data_dir: {source_config}")
    return Path(start.data_dir) / "filestore" / database


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
    subprocess.run(
        ["git", "-C", str(root), "config", "core.sparseCheckout", "true"],
        check=True,
        capture_output=True,
    )
    sparse_checkout = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--git-path", "info/sparse-checkout"],
        check=True,
        capture_output=True,
        text=True,
    )
    sparse_path = Path(sparse_checkout.stdout.strip())
    if not sparse_path.is_absolute():
        sparse_path = root / sparse_path
    sparse_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    sparse_path.write_text("odoo-bin\nodoo/\naddons/\n", encoding="utf-8")
    # A no-checkout clone leaves the registered repository root empty even
    # though the sparse pattern would populate the later worktree. Materialize
    # the same pinned launcher/package set in the root first so public checkout
    # can resolve its exact source boundary without restoring the full cache.
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "checkout",
            "--detach",
            "--no-recurse-submodules",
            E2E_PINS.odoo_source_commit,
        ],
        check=True,
        capture_output=True,
    )
    # Checkout above already materialized the exact pinned launcher/package.
    relative = next(
        path for path in (Path("odoo-bin"), Path("odoo/odoo-bin")) if (root / path).is_file()
    )
    bootstrap_odoo_bin = root / relative
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
    replace_http_port(bootstrap_config, public_http_port)
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
    # Public module/test leaves resolve modules from the pinned source tree;
    # the disposable target service config intentionally uses container paths.
    config_lines = [
        line
        for line in local_config.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("addons_path")
    ]
    config_lines.append(f"addons_path = {root / 'addons'}")
    local_config.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    fixture_addon = Path(__file__).parents[2] / "fixtures" / "addons" / "odcli_e2e_probe"
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
                python=sys.executable,
                # The focused leaves execute the real Odoo launcher too. Keep
                # that process on the same audited dependency set as the
                # critical path so imports such as Babel are available.
                create_venv=True,
                hash_lock=PYTHON_RESOLUTION_LOCK,
                hash_lock_sha256=E2E_PINS.odoo_python_lock_sha256,
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
    python_root = Path(str(checkout_environment["python_environment_path"]))
    python = python_root if python_root.name == "python" else python_root / "bin" / "python"
    generated_config = Path(str(checkout_environment["generated_config_path"]))
    probe_destination = worktree / "addons" / "odcli_e2e_probe"
    assert not probe_destination.exists() and not probe_destination.is_symlink()
    shutil.copytree(fixture_addon, probe_destination)
    (generated_config.parent / "odoo.log").write_text(
        "INFO focused leaf completed; secret=redacted\n", encoding="utf-8"
    )
    config = msgspec.structs.replace(
        initialized,
        odoo_bin=worktree / relative,
        python=python,
        source_config=generated_config,
    )
    manifest.write_text(config.to_manifest(), encoding="utf-8")

    # Resolve the catalog immediately after checkout, before lifecycle
    # commands can create a second HOME-local catalog.
    project_catalog = _catalog_for_project(runtime, root)
    process_environment["ODCLI_E2E_CATALOG"] = str(project_catalog)

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
    _assert_project_state_preflight(root, project_catalog)

    # Shared projects start with an empty target database.  Initialize Odoo's
    # base registry before any focused leaf snapshots that database or starts
    # an auxiliary restore manager; otherwise real Odoo exits on res.users.
    previous_environment = os.environ.copy()
    os.environ.update(process_environment)
    try:
        client = OdooClient(config=OdooClientConfig(executable="odoo"))
        environment_object = client.environments.get(environment_id)
        instance = client.instance.from_environment(environment_object)
        assert (
            instance.run_foreground(
                args=(
                    "--init=base,odcli_e2e_probe",
                    "--without-demo=all",
                    "--stop-after-init",
                )
            )
            == 0
        )
    finally:
        os.environ.clear()
        os.environ.update(previous_environment)

    _assert_project_state_preflight(root, project_catalog)

    def remove_environment() -> None:
        if probe_destination.exists():
            _remove_owned_fixture_tree(worktree, probe_destination)
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


def _project_database_probe(
    project: Path, database: str, *, home: str
) -> subprocess.CompletedProcess[str]:
    """Probe the SDK-owned PostgreSQL cluster bound to a focused project."""
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    with pytest.MonkeyPatch.context() as paths:
        paths.setenv("HOME", home)
        cluster = PostgresCluster.from_project(project)
        lifecycle = ComposeLifecycle(cluster.compose_file, cluster.compose_project_name)
    return lifecycle.run(
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


def _bind_catalog_path(monkeypatch: pytest.MonkeyPatch, catalog_path: Path) -> None:
    """Bind every already-imported public command catalog provider for a run."""

    def provider(**_: object) -> Path:
        return catalog_path

    for target in (
        "odoo_instance_sdk.cli.get_catalog_path",
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        "odoo_instance_sdk.internal.context.get_catalog_path",
        "odoo_instance_sdk.commands.env.checkout.get_catalog_path",
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
