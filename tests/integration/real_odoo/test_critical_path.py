"""Source-backed public critical path for the real-Odoo full tier."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from scripts.real_odoo_bootstrap import PYTHON_RESOLUTION_LOCK
from scripts.real_odoo_pins import E2E_PINS
from tests.fixtures.xmlrpc_probe import xmlrpc_probe

from .archive import ArchiveIdentity
from .cleanup import audit_no_leaks, compose_down, write_odoo_config
from .compose import ComposeLifecycle, wait_for_http
from .conftest import E2ERuntime, odoo_runtime_python

pytestmark = [pytest.mark.real_odoo, pytest.mark.e2e_full, pytest.mark.serial]

_PROBE = "odcli_e2e_probe"
_PROBE_MARKER = "ODCLI-E2E-RESTORED"
_ATTACHMENT = b"OdCLI filestore probe\n"
_CATALOG_PATCH_TARGETS = (
    "odoo_instance_sdk.cli.get_catalog_path",
    "odoo_instance_sdk.internal.paths.get_catalog_path",
    "odoo_instance_sdk.internal.context.get_catalog_path",
    "odoo_instance_sdk.commands.env.checkout.get_catalog_path",
)


def _assert_catalog_patch_targets_importable() -> None:
    for target in _CATALOG_PATCH_TARGETS:
        module_name, _, attribute_name = target.rpartition(".")
        module = importlib.import_module(module_name)
        assert hasattr(module, attribute_name), target


def test_catalog_patch_targets_are_importable() -> None:
    _assert_catalog_patch_targets_importable()


def _source_repository() -> tuple[Path, str]:
    value = os.environ.get("ODCLI_E2E_ODOO_SOURCE_REPO") or os.environ.get(
        "ODCLI_E2E_ODOO_SOURCE_CACHE"
    )
    if not value:
        pytest.fail(
            "required real-Odoo prerequisite is missing: "
            "ODCLI_E2E_ODOO_SOURCE_REPO (pinned bare source cache)"
        )
    repository = Path(value).expanduser().resolve()
    if not repository.is_dir():
        pytest.fail(f"Odoo source cache is not a directory: {repository}")
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "cat-file",
            "-e",
            f"{E2E_PINS.odoo_source_commit}^{{commit}}",
        ],
        capture_output=True,
        shell=False,
        text=True,
        check=False,
    )
    if result.returncode:
        pytest.fail(
            f"Odoo source cache does not contain pinned commit {E2E_PINS.odoo_source_commit}"
        )
    odoo_bin = next(
        (
            relative
            for relative in ("odoo-bin", "odoo/odoo-bin")
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
                shell=False,
                check=False,
            ).returncode
            == 0
        ),
        None,
    )
    if odoo_bin is None:
        pytest.fail(f"Odoo source cache has no odoo-bin: {repository}")
    return repository, odoo_bin


def _clone_pinned_source(repository: Path, destination: Path) -> None:
    clone = subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", str(repository), str(destination)],
        capture_output=True,
        shell=False,
        text=True,
        check=False,
    )
    if clone.returncode:
        raise AssertionError(f"cannot clone pinned Odoo source: {clone.stderr[-4000:]}")
    checkout = subprocess.run(
        ["git", "-C", str(destination), "checkout", "--detach", E2E_PINS.odoo_source_commit],
        capture_output=True,
        shell=False,
        text=True,
        check=False,
    )
    if checkout.returncode:
        raise AssertionError(f"cannot check out pinned Odoo source: {checkout.stderr[-4000:]}")


def _resolved_postgres_digest(image: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", image],
        capture_output=True,
        shell=False,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    resolved = result.stdout.strip()
    assert resolved.rsplit("@", 1)[-1] == image.rsplit("@", 1)[-1]
    return resolved


def _invoke(
    runner: CliRunner,
    project: Path,
    environment: dict[str, str],
    *args: str,
) -> dict[str, Any]:
    result = runner.invoke(
        cli,
        ["--project", str(project), *args, "--format", "json"],
        env=environment,
    )
    assert result.exit_code == 0, result.output
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"{args!r} did not return JSON: {result.stdout!r}") from error
    assert document.get("ok") is True, document
    payload = document.get("result")
    assert isinstance(payload, dict), document
    return payload


def _record(record_property: Any, evidence: str, value: object = "passed") -> None:
    record_property(evidence.lower().replace("-", "_"), json.dumps(value, default=str))


def _worktree_status(worktree: Path) -> tuple[str, ...]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(worktree),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        capture_output=True,
        shell=False,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(f"cannot inspect fixture worktree: {result.stderr[-4000:]}")
    return tuple(line for line in result.stdout.splitlines() if line)


def _remove_owned_fixture_tree(
    worktree: Path, destination: Path
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Remove only the fixture tree that made the registered worktree dirty.

    ``env remove`` must retain its fail-closed dirty-worktree guard.  This
    helper therefore refuses to remove anything when the status contains a
    tracked change, a foreign untracked path, or a symlink escaping the
    registered worktree.  The postcondition is an empty status before the
    public removal command is allowed to run.
    """
    worktree_root = worktree.resolve()
    if destination.is_symlink():
        raise AssertionError(f"fixture destination is a symlink: {destination}")
    try:
        destination_root = destination.resolve(strict=False)
        relative_destination = destination_root.relative_to(worktree_root)
    except ValueError as error:
        raise AssertionError(f"fixture destination escapes worktree: {destination}") from error
    if not destination.is_dir():
        raise AssertionError(f"owned fixture directory is missing: {destination}")

    before = _worktree_status(worktree)
    if not before:
        raise AssertionError(f"owned fixture has no dirty-worktree evidence: {destination}")
    relative = relative_destination.as_posix().rstrip("/")
    for line in before:
        if not line.startswith("?? "):
            raise AssertionError(f"refusing to remove unexpected worktree change: {line}")
        changed_path = line[3:]
        if changed_path != relative and not changed_path.startswith(f"{relative}/"):
            raise AssertionError(f"refusing to remove foreign worktree change: {line}")

    shutil.rmtree(destination)
    after = _worktree_status(worktree)
    if after:
        raise AssertionError(f"fixture cleanup left worktree changes: {after}")
    return before, after


def _environment_state(payload: dict[str, Any], environment_id: str) -> dict[str, Any]:
    rows = payload.get("rows")
    assert isinstance(rows, list)
    row = next(
        (
            item
            for item in rows
            if isinstance(item, dict)
            and item.get("kind") == "environment"
            and item.get("environment_id") == environment_id
        ),
        None,
    )
    assert isinstance(row, dict), payload
    return {
        key: row.get(key)
        for key in (
            "kind",
            "project_id",
            "environment_id",
            "name",
            "worktree_path",
            "db_mode",
            "database",
            "lifecycle_state",
            "git",
            "facts",
        )
    }


def _cluster_state(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in ("mode", "owned", "state", "endpoint")}


def _expected_stop_context(state: Any, environment: dict[str, Any]) -> dict[str, Any]:
    project = next(
        row
        for row in state.catalog.list_projects()
        if Path(str(row["repository_root"])).resolve() == state.project.resolve()
    )
    project_id = str(project["project_id"])
    assert environment.get("project_id") in (None, project_id)
    return {
        "owner_kind": "environment",
        "owner_id": str(environment["id"]),
        "project_id": project_id,
        "environment_id": str(environment["id"]),
        "environment_name": environment["name"],
        "worktree_path": environment["worktree_path"],
    }


def _switch_database_config(path: Path, database: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    changed = {"db_name": False, "dbfilter": False}
    rendered: list[str] = []
    for line in lines:
        key, separator, _value = line.partition("=")
        normalized = key.strip()
        if separator and normalized in changed:
            rendered.append(f"{normalized} = {database}")
            changed[normalized] = True
        else:
            rendered.append(line)
    if not all(changed.values()):
        raise AssertionError(f"generated Odoo config has no database binding: {path}")
    path.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _start_owned(instance: Any, runtime: Any) -> Any:
    owned_children: list[_OwnedProcess] = []

    def cleanup() -> None:
        if owned_children:
            _stop_owned(instance, owned_children[0])
        else:
            instance.stop_environment()

    # Register owner cleanup before launch so any later setup assertion still
    # has a public lifecycle path to stop and reap the process.
    runtime.ledger.record(
        "process",
        f"{runtime.run_id}-odoo",
        cleanup,
    )
    process = instance.run_detached(wait_ready=True, readiness_timeout=180.0)
    reap_result: list[int | None] = []
    reaper = threading.Thread(
        target=_reap_owned_child,
        args=(process.pid, reap_result),
        name=f"reap-odoo-{process.pid}",
        daemon=True,
    )
    owned = _OwnedProcess(process, reaper, reap_result)
    owned_children.append(owned)
    reaper.start()
    return owned


def _reap_owned_child(pid: int, result: list[int | None]) -> None:
    """Reap only the direct child returned by this test's public launch."""
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            waited_pid, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            result.append(None)  # Python's Popen cleanup already reaped this child.
            return
        if waited_pid == pid:
            result.append(status)
            return
        time.sleep(0.02)
    result.append(-1)


@dataclass(slots=True)
class _OwnedProcess:
    process: Any
    reaper: threading.Thread
    reap_result: list[int | None]
    stopped: bool = False


def _stop_owned(instance: Any, owned: _OwnedProcess) -> None:
    if owned.stopped:
        return
    errors: list[BaseException] = []
    try:
        result = instance.stop_environment()
        assert result.status in {"stopped", "already_stopped"}, result
    except BaseException as error:
        errors.append(error)
    try:
        if owned.reaper.ident is None:
            _reap_owned_child(owned.process.pid, owned.reap_result)
        else:
            owned.reaper.join(timeout=35.0)
        assert not owned.reaper.is_alive(), "owned Odoo child reaper did not finish"
        assert owned.reap_result and owned.reap_result[0] != -1, "owned Odoo leader was not reaped"
    except BaseException as error:
        errors.append(error)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup("owned process stop and reap failed", errors)
    owned.stopped = True


@dataclass(slots=True)
class _CriticalPathWorkspace:
    runtime: E2ERuntime
    source_server: E2ERuntime
    source_backup: ArchiveIdentity
    runner: CliRunner
    project: Path
    manifest: Path
    cli_environment: dict[str, str]
    cluster: Any
    status: dict[str, Any]
    repeated_status: dict[str, Any]
    uv_version: str
    checkout: dict[str, Any]
    environment: dict[str, Any]
    environment_id: str
    registered_worktree: Path
    probe_destination: Path
    generated_config: Path
    python: Path
    python_version: str
    source_commit: str
    persisted_python: Path | None
    persisted_odoo_bin: Path | None
    catalog: Any
    client: Any
    env_obj: Any
    instance: Any
    listed: dict[str, Any]
    repeated_listed: dict[str, Any]
    path_result: dict[str, Any]
    sync: dict[str, Any]
    repeated_sync: dict[str, Any]
    deps: dict[str, Any]
    base_init_status: int
    initial_probe_state: dict[str, Any]


def _patch_runtime_environment(runtime: E2ERuntime, runtime_patch: pytest.MonkeyPatch) -> None:
    for key, value in runtime.environment.items():
        runtime_patch.setenv(key, value)


def _bind_runtime_catalog_paths(runtime: E2ERuntime, runtime_patch: pytest.MonkeyPatch) -> None:
    catalogue_path = Path(runtime.environment["ODCLI_E2E_CATALOG"]).resolve()

    def run_catalog_path(*, ensure_exists: bool = True) -> Path:
        if ensure_exists:
            catalogue_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return catalogue_path

    def run_data_root(*, ensure_exists: bool = True) -> Path:
        if ensure_exists:
            runtime.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return runtime.root

    def run_state_root() -> Path:
        path = runtime.root / "state"
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def run_cache_root(*, ensure_exists: bool = True) -> Path:
        path = runtime.root / "cache"
        if ensure_exists:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def run_locks_dir(**_kwargs: object) -> Path:
        path = run_state_root() / "locks"
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    runtime_patch.setattr("odoo_instance_sdk.internal.paths.get_data_root", run_data_root)
    runtime_patch.setattr("odoo_instance_sdk.internal.paths.get_state_root", run_state_root)
    runtime_patch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", run_cache_root)
    runtime_patch.setattr("odoo_instance_sdk.internal.paths.get_locks_dir", run_locks_dir)
    _assert_catalog_patch_targets_importable()
    _patch_catalog_call_sites(runtime_patch, run_catalog_path)
    from odoo_instance_sdk.commands import backup as backup_commands, resource as resource_commands

    runtime_patch.setattr(backup_commands._catalog_path_provider, "provider", run_catalog_path)
    runtime_patch.setattr(resource_commands._catalog_path_provider, "provider", run_catalog_path)


def _patch_catalog_call_sites(runtime_patch: pytest.MonkeyPatch, run_catalog_path: Any) -> None:
    for target in _CATALOG_PATCH_TARGETS:
        runtime_patch.setattr(target, run_catalog_path)


@pytest.fixture(scope="module")
def critical_path_workspace(
    request: pytest.FixtureRequest,
    target_runtime: E2ERuntime,
    source_server: E2ERuntime,
    source_backup: ArchiveIdentity,
) -> Iterator[_CriticalPathWorkspace]:
    """Build the shared pinned project/environment prerequisites for focused nodes."""
    source_repository, odoo_bin_relative = _source_repository()
    runner = CliRunner()
    runtime = target_runtime
    runtime_patch = pytest.MonkeyPatch()
    request.addfinalizer(runtime_patch.undo)
    _patch_runtime_environment(runtime, runtime_patch)
    _bind_runtime_catalog_paths(runtime, runtime_patch)

    master_password = runtime.master_password_file.read_text(encoding="utf-8").strip()
    pg_password = runtime.secret_file.read_text(encoding="utf-8").strip()
    addon_root = Path(__file__).parents[2] / "fixtures" / "addons"
    write_odoo_config(
        runtime.config_file,
        database_host="127.0.0.1",
        database_port=runtime.topology.target_postgres_port,
        database_password=pg_password,
        admin_password=master_password,
        data_dir=runtime.root / "target-data",
        http_interface="127.0.0.1",
        http_port=runtime.reservations[3].port,
        addons_path=(Path("/usr/lib/python3/dist-packages/odoo/addons"), addon_root),
    )
    cli_environment = dict(os.environ)
    cli_environment.update(runtime.environment)
    cli_environment.update(
        {
            "ODCLI_TEST_MASTER_PASSWORD": source_server.master_password_file.read_text(
                encoding="utf-8"
            ).strip(),
            "ODCLI_TEST_INSTANCE_ORIGIN_PINS": (
                f"http://127.0.0.1:{source_server.topology.source_odoo_port}"
            ),
        }
    )
    project = runtime.root / f"project-{runtime.run_id}"
    _clone_pinned_source(source_repository, project)
    runtime.ledger.record(
        "worktree", project.name, lambda: shutil.rmtree(project, ignore_errors=True)
    )
    odoo_bin_path = project / odoo_bin_relative
    odoo_python = odoo_runtime_python(runtime, project, odoo_bin_path)

    down = ComposeLifecycle(runtime.compose_file, runtime.topology.project_name).run(
        "down", "--volumes", "--remove-orphans", timeout=60.0
    )
    assert down.returncode == 0, down.stderr
    bootstrap_config = runtime.root / "critical-path-bootstrap.conf"
    shutil.copy2(runtime.config_file, bootstrap_config)
    with bootstrap_config.open("a", encoding="utf-8") as stream:
        stream.write(f"logfile = {runtime.root / 'odoo.log'}\n")
    bootstrap_config.chmod(0o600)
    init = runner.invoke(
        cli,
        [
            "init",
            "--project",
            str(project),
            "--yes",
            "--allow-partial",
            "--odoo-bin",
            str(odoo_bin_path),
            "--python",
            str(odoo_python),
            "--config",
            str(bootstrap_config),
            "--database",
            runtime.topology.target_sentinel_database,
            "--postgres",
            "compose",
            "--postgres-image",
            E2E_PINS.postgres_image,
            "--postgres-port",
            str(runtime.topology.target_postgres_port),
            "--postgres-user",
            "odoo",
            "--format",
            "json",
        ],
        env=cli_environment,
    )
    assert init.exit_code == 0, init.output
    manifest = project / ".odcli" / "project.toml"
    from odoo_instance_sdk.project import ProjectConfig, TestInstanceProjectConfig

    initialized = ProjectConfig.load(project)
    initialized = msgspec.structs.replace(
        initialized,
        source_config=Path(".odcli/odoo.conf"),
        test_instance=TestInstanceProjectConfig(
            base_url=f"http://127.0.0.1:{source_server.topology.source_odoo_port}",
            database=source_server.topology.source_database,
            git_branch=E2E_PINS.odoo_source_commit,
        ),
        default_base_ref=E2E_PINS.odoo_source_commit,
    )
    manifest.write_text(initialized.to_manifest(), encoding="utf-8")
    assert ".odcli/odoo.conf" in manifest.read_text(encoding="utf-8")

    from odoo_instance_sdk.resources.postgres import PostgresCluster

    cluster = PostgresCluster.from_project(project)

    def release_owned_cluster() -> None:
        last_error: RuntimeError | None = None
        for _attempt in range(2):
            try:
                compose_down(
                    cluster.compose_file,
                    cluster.compose_project_name,
                    ports=(cluster.endpoint_port,),
                )
            except RuntimeError as error:
                last_error = error
            else:
                return
        if last_error is not None:
            raise last_error

    request.addfinalizer(release_owned_cluster)
    runtime.ledger.record(
        "postgres",
        f"{runtime.run_id}-sdk-postgres",
        lambda: compose_down(
            cluster.compose_file,
            cluster.compose_project_name,
            ports=(cluster.endpoint_port,),
        ),
    )
    _invoke(
        runner,
        project,
        cli_environment,
        "postgres",
        "approve-image",
        "--image-digest",
        _resolved_postgres_digest(E2E_PINS.postgres_image),
    )
    assert cluster.compose_file.parent.is_relative_to(runtime.root)
    assert cluster.password_file.is_file()
    _invoke(runner, project, cli_environment, "postgres", "up")
    status = _invoke(runner, project, cli_environment, "postgres", "status")
    repeated_status = _invoke(runner, project, cli_environment, "postgres", "status")

    uv_version = subprocess.run(
        ["uv", "--version"], capture_output=True, shell=False, text=True, check=True
    ).stdout.strip()
    runtime.reservations[3].release()
    checkout = _invoke(
        runner,
        project,
        cli_environment,
        "env",
        "checkout",
        "MYL-166",
        "--base",
        E2E_PINS.odoo_source_commit,
        "--config",
        str(project / ".odcli" / "odoo.conf"),
        "--source-db",
        runtime.topology.target_sentinel_database,
        "--odoo-bin",
        str(odoo_bin_path),
        "--python",
        E2E_PINS.cpython,
        "--create-venv",
        "--hash-lock",
        str(PYTHON_RESOLUTION_LOCK),
        "--hash-lock-sha256",
        E2E_PINS.odoo_python_lock_sha256,
        "--http-port",
        str(runtime.reservations[3].port),
    )
    environment = checkout["environment"]
    assert isinstance(environment, dict)
    environment_id = str(environment["id"])
    registered_worktree = Path(str(environment["worktree_path"]))
    probe_root = registered_worktree / "addons"
    probe_destination = probe_root / _PROBE
    assert not probe_destination.exists() and not probe_destination.is_symlink()
    shutil.copytree(addon_root / _PROBE, probe_destination)
    generated_config = Path(str(environment["generated_config_path"]))
    config_lines = generated_config.read_text(encoding="utf-8").splitlines()
    addons_lines = [line for line in config_lines if line.lstrip().startswith("addons_path")]
    config_lines = [line for line in config_lines if not line.lstrip().startswith("addons_path")]
    configured_addons = addons_lines[0].split("=", 1)[1].strip() if addons_lines else ""
    configured_addons = ",".join(value for value in (configured_addons, str(probe_root)) if value)
    config_lines.append(f"addons_path = {configured_addons}")
    generated_config.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    generated_config.chmod(0o600)
    source_commit = subprocess.run(
        ["git", "-C", str(registered_worktree), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    python = Path(str(environment["python_environment_path"])) / "bin" / "python"
    python_version = subprocess.run(
        [str(python), "--version"], capture_output=True, shell=False, text=True, check=True
    ).stdout.strip()
    registered = ProjectConfig.load(project)
    registered = msgspec.structs.replace(
        registered,
        odoo_bin=registered_worktree / odoo_bin_relative,
        python=python,
        source_config=generated_config,
    )
    manifest.write_text(registered.to_manifest(), encoding="utf-8")
    manifest.chmod(0o600)
    persisted = ProjectConfig.load(project)
    listed = _invoke(runner, project, cli_environment, "env", "list")
    repeated_listed = _invoke(runner, project, cli_environment, "env", "list")
    path_result = _invoke(runner, project, cli_environment, "env", "path", environment_id)
    sync = _invoke(
        runner,
        project,
        cli_environment,
        "env",
        "sync",
        environment_id,
        "--hash-lock",
        str(PYTHON_RESOLUTION_LOCK),
        "--hash-lock-sha256",
        E2E_PINS.odoo_python_lock_sha256,
    )
    repeated_sync = _invoke(
        runner,
        project,
        cli_environment,
        "env",
        "sync",
        environment_id,
        "--hash-lock",
        str(PYTHON_RESOLUTION_LOCK),
        "--hash-lock-sha256",
        E2E_PINS.odoo_python_lock_sha256,
    )
    deps = _invoke(runner, project, cli_environment, "--env", environment_id, "deps", "verify")

    from odoo_instance_sdk import OdooClient, OdooClientConfig

    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    catalog = client.get_catalog()
    env_obj = client.environments.get(environment_id)
    instance = client.instance.from_environment(env_obj)
    base_init_status = instance.run_foreground(
        args=("--init=base", "--without-demo=all", "--stop-after-init")
    )
    initial_probe_state = _invoke(
        runner, project, cli_environment, "--env", environment_id, "module", "list", _PROBE
    )
    yield _CriticalPathWorkspace(
        runtime=runtime,
        source_server=source_server,
        source_backup=source_backup,
        runner=runner,
        project=project,
        manifest=manifest,
        cli_environment=cli_environment,
        cluster=cluster,
        status=status,
        repeated_status=repeated_status,
        uv_version=uv_version,
        checkout=checkout,
        environment=environment,
        environment_id=environment_id,
        registered_worktree=registered_worktree,
        probe_destination=probe_destination,
        generated_config=generated_config,
        python=python,
        python_version=python_version,
        source_commit=source_commit,
        persisted_python=Path(persisted.python) if persisted.python is not None else None,
        persisted_odoo_bin=Path(persisted.odoo_bin) if persisted.odoo_bin is not None else None,
        catalog=catalog,
        client=client,
        env_obj=env_obj,
        instance=instance,
        listed=listed,
        repeated_listed=repeated_listed,
        path_result=path_result,
        sync=sync,
        repeated_sync=repeated_sync,
        deps=deps,
        base_init_status=base_init_status,
        initial_probe_state=initial_probe_state,
    )


@pytest.fixture
def critical_path_case(
    critical_path_workspace: _CriticalPathWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> _CriticalPathWorkspace:
    """Restore each node's database/cwd/env prerequisites without test-order coupling."""
    state = critical_path_workspace
    for key, value in state.runtime.environment.items():
        monkeypatch.setenv(key, value)
    _switch_database_config(state.generated_config, state.runtime.topology.target_sentinel_database)
    monkeypatch.chdir(state.registered_worktree)
    state.env_obj = state.client.environments.get(state.environment_id)
    state.instance = state.client.instance.from_environment(state.env_obj)
    return state


@dataclass(slots=True)
class _RestoredRuntime:
    workspace: _CriticalPathWorkspace
    refresh: dict[str, Any]
    restored_database: str
    backup_id: str
    backup: dict[str, Any]
    backup_list: dict[str, Any]
    shown_backup: dict[str, Any]
    validation: dict[str, Any]
    owned_process: _OwnedProcess


@pytest.fixture
def critical_path_restored_runtime(
    critical_path_case: _CriticalPathWorkspace,
    request: pytest.FixtureRequest,
) -> Iterator[_RestoredRuntime]:
    """Prepare a fresh restored database/runtime for each restore or cleanup node."""
    state = critical_path_case
    instance = state.instance
    first_process = _start_owned(instance, state.runtime)
    request.addfinalizer(lambda: _stop_owned(instance, first_process))
    wait_for_http(instance.config.base_url + "/web/health", timeout=180.0)
    refresh = _invoke(
        state.runner,
        state.project,
        state.cli_environment,
        "db",
        "refresh",
        "--restore",
        "--source-branch",
        E2E_PINS.odoo_source_commit,
    )
    restored_database = str(refresh["restored_database"])
    backup = refresh.get("backup")
    assert isinstance(backup, dict)
    backup_id = str(backup["id"])
    backup_list = _invoke(state.runner, state.project, state.cli_environment, "backup", "list")
    shown_backup = _invoke(
        state.runner, state.project, state.cli_environment, "backup", "show", backup_id
    )
    validation = _invoke(
        state.runner, state.project, state.cli_environment, "backup", "validate", backup_id
    )

    _stop_owned(instance, first_process)
    _switch_database_config(state.generated_config, restored_database)
    state.env_obj = state.client.environments.get(state.environment_id)
    state.instance = state.client.instance.from_environment(state.env_obj)
    owned_process = _start_owned(state.instance, state.runtime)
    restored_instance = state.instance
    request.addfinalizer(lambda: _stop_owned(restored_instance, owned_process))
    wait_for_http(state.instance.config.base_url + "/web/health", timeout=180.0)
    yield _RestoredRuntime(
        workspace=state,
        refresh=refresh,
        restored_database=restored_database,
        backup_id=backup_id,
        backup=backup,
        backup_list=backup_list,
        shown_backup=shown_backup,
        validation=validation,
        owned_process=owned_process,
    )


def test_critical_path_owned_project_and_postgres_are_repeatable(
    critical_path_case: _CriticalPathWorkspace, record_property: Any
) -> None:
    state = critical_path_case
    runtime = state.runtime
    assert state.manifest.is_file()
    assert ".odcli/odoo.conf" in state.manifest.read_text(encoding="utf-8")
    assert state.cluster.compose_file.parent.is_relative_to(runtime.root)
    assert state.cluster.password_file.is_file()
    assert state.status.get("state") == "healthy"
    assert _cluster_state(state.status) == _cluster_state(state.repeated_status)
    assert state.uv_version.split()[1] == E2E_PINS.uv
    _record(
        record_property,
        "E2E-CP-01",
        {
            "manifest": str(state.manifest),
            "status": state.status,
            "repeated_status": state.repeated_status,
        },
    )


def test_critical_path_environment_is_bound_to_pinned_source_and_python(
    critical_path_case: _CriticalPathWorkspace, record_property: Any
) -> None:
    state = critical_path_case
    environment = state.environment
    assert environment["state"] == "ready"
    assert environment["branch"] == "MYL-166"
    assert environment.get("python_environment_owned") is True
    assert Path(str(environment["generated_config_path"])).is_file()
    assert Path(str(environment["python_environment_path"])).is_dir()
    assert Path(str(environment["worktree_path"])).is_dir()
    assert state.source_commit == E2E_PINS.odoo_source_commit
    assert state.python_version == f"Python {E2E_PINS.cpython}"
    assert state.persisted_python == state.python
    assert state.persisted_odoo_bin == state.registered_worktree / "odoo-bin"
    _record(
        record_property,
        "E2E-CP-02",
        {
            "commit": state.source_commit,
            "environment": state.environment_id,
            "python": state.python_version,
        },
    )


def test_critical_path_environment_registration_and_paths_are_stable(
    critical_path_case: _CriticalPathWorkspace, record_property: Any
) -> None:
    state = critical_path_case
    environment = state.environment
    assert state.environment_id in json.dumps(state.listed)
    assert _environment_state(state.listed, state.environment_id) == _environment_state(
        state.repeated_listed, state.environment_id
    )
    assert state.path_result["worktree_path"] == environment["worktree_path"]
    _record(record_property, "E2E-CP-03", state.path_result)


def test_critical_path_hash_locked_dependencies_and_base_runtime(
    critical_path_case: _CriticalPathWorkspace, record_property: Any
) -> None:
    state = critical_path_case
    assert state.sync == state.repeated_sync
    assert state.deps.get("pip_check_ok") is True
    assert state.deps.get("missing_imports") == []
    assert state.base_init_status == 0
    assert (
        Path(state.cli_environment["ODCLI_E2E_CATALOG"]).resolve()
        == state.catalog.db_path.resolve()
    )
    _record(
        record_property,
        "E2E-CP-04",
        {
            "python": E2E_PINS.cpython,
            "uv": E2E_PINS.uv,
            "resolution_lock": E2E_PINS.odoo_python_lock_sha256,
            "dependencies": state.deps,
        },
    )


def test_critical_path_public_stop_and_probe_module_discovery(
    critical_path_case: _CriticalPathWorkspace, record_property: Any
) -> None:
    state = critical_path_case
    instance = state.instance
    owned = _start_owned(instance, state.runtime)
    try:
        wait_for_http(instance.config.base_url + "/web/health", timeout=180.0)
        stop = _invoke(
            state.runner,
            state.project,
            state.cli_environment,
            "--env",
            state.environment_id,
            "stop",
        )
        repeated_stop = _invoke(
            state.runner,
            state.project,
            state.cli_environment,
            "--env",
            state.environment_id,
            "stop",
        )
        stop_context = _expected_stop_context(state, state.environment)
        assert stop == {**stop_context, "status": "stopped"}
        assert repeated_stop == {**stop_context, "status": "already_stopped"}
    finally:
        _stop_owned(instance, owned)

    assert (
        instance.run_foreground(
            args=(f"--init={_PROBE}", "--without-demo=all", "--stop-after-init")
        )
        == 0
    )
    modules = _invoke(
        state.runner,
        state.project,
        state.cli_environment,
        "--env",
        state.environment_id,
        "module",
        "list",
        _PROBE,
    )
    assert _PROBE in json.dumps(modules)
    assert state.initial_probe_state
    _record(
        record_property, "E2E-CP-05", {"stop": stop, "repeat": repeated_stop, "modules": modules}
    )


def test_critical_path_module_update_is_idempotent(
    critical_path_case: _CriticalPathWorkspace, record_property: Any
) -> None:
    state = critical_path_case
    assert (
        state.instance.run_foreground(
            args=(f"--init={_PROBE}", "--without-demo=all", "--stop-after-init")
        )
        == 0
    )
    update = _invoke(
        state.runner,
        state.project,
        state.cli_environment,
        "--env",
        state.environment_id,
        "module",
        "update",
        _PROBE,
        "--yes",
    )
    state_after_update = _invoke(
        state.runner,
        state.project,
        state.cli_environment,
        "--env",
        state.environment_id,
        "module",
        "list",
        _PROBE,
    )
    repeated_update = _invoke(
        state.runner,
        state.project,
        state.cli_environment,
        "--env",
        state.environment_id,
        "module",
        "update",
        _PROBE,
        "--yes",
    )
    repeated_state = _invoke(
        state.runner,
        state.project,
        state.cli_environment,
        "--env",
        state.environment_id,
        "module",
        "list",
        _PROBE,
    )
    assert state_after_update == repeated_state
    _record(
        record_property,
        "E2E-CP-06",
        {"first_update": update, "repeated_update": repeated_update, "state": state_after_update},
    )


def test_critical_path_public_native_module_test_passes(
    critical_path_case: _CriticalPathWorkspace, record_property: Any
) -> None:
    state = critical_path_case
    assert (
        state.instance.run_foreground(
            args=(f"--init={_PROBE}", "--without-demo=all", "--stop-after-init")
        )
        == 0
    )
    result = _invoke(
        state.runner,
        state.project,
        state.cli_environment,
        "--env",
        state.environment_id,
        "test",
        _PROBE,
        "--tags",
        f"/{_PROBE}",
    )
    assert result.get("exit_code") == 0
    _record(record_property, "E2E-CP-07", result)


def test_critical_path_backup_restore_and_restored_runtime(
    critical_path_restored_runtime: _RestoredRuntime,
    record_property: Any,
) -> None:
    case = critical_path_restored_runtime
    state = case.workspace
    source_server = state.source_server
    backup = case.backup
    assert case.restored_database
    assert backup.get("id") == case.backup_id
    assert isinstance(backup.get("sha256"), str) and len(str(backup["sha256"])) == 64
    assert isinstance(backup.get("size_bytes"), int) and int(backup["size_bytes"]) > 0
    assert state.source_backup.size_bytes > 0 and len(state.source_backup.sha256) == 64
    _record(
        record_property,
        "E2E-CP-08",
        {"database": case.restored_database, "backup": case.backup_id},
    )

    assert case.backup_list
    shown = case.shown_backup
    assert str(shown.get("id", shown.get("backup", {}).get("id", ""))) == case.backup_id
    assert shown.get("size_bytes") == backup.get("size_bytes")
    assert shown.get("sha256") == backup.get("sha256")
    assert shown.get("source_base_url") == backup.get("source_base_url")
    assert shown.get("database_name") == backup.get("database_name")
    assert shown.get("source_git_branch") == backup.get("source_git_branch")
    assert (
        backup["source_base_url"] == f"http://127.0.0.1:{source_server.topology.source_odoo_port}"
    )
    assert backup["database_name"] == source_server.topology.source_database
    assert backup["source_git_branch"] == E2E_PINS.odoo_source_commit
    assert case.validation
    _record(record_property, "E2E-CP-09", {**backup, "shown": shown})

    name, attachment = xmlrpc_probe(state.instance.config.base_url, case.restored_database)
    assert name == "Pinned Odoo 19 fixture"
    assert attachment == _ATTACHMENT
    _record(
        record_property,
        "E2E-CP-10",
        {"database": case.restored_database, "attachment_bytes": len(attachment)},
    )
    evaluation = _invoke(
        state.runner,
        state.project,
        state.cli_environment,
        "--env",
        state.environment_id,
        "eval",
        "env['odcli.e2e.probe'].search_count([('marker', '=', 'ODCLI-E2E-RESTORED')])",
    )
    assert "1" in json.dumps(evaluation)
    _record(record_property, "E2E-CP-11", evaluation)
    resources = _invoke(state.runner, state.project, state.cli_environment, "resource", "list")
    resource_doctor = _invoke(
        state.runner, state.project, state.cli_environment, "resource", "doctor"
    )
    databases = _invoke(state.runner, state.project, state.cli_environment, "db", "list")
    assert isinstance(resources.get("resources"), list)
    assert isinstance(resource_doctor.get("findings"), list)
    assert case.restored_database in json.dumps(databases)
    _record(
        record_property,
        "E2E-CP-12",
        {"resources": resources, "resource_doctor": resource_doctor, "databases": databases},
    )


def test_critical_path_restored_runtime_stop_is_repeatable_and_owned(
    critical_path_restored_runtime: _RestoredRuntime, record_property: Any
) -> None:
    case = critical_path_restored_runtime
    state = case.workspace
    environment = _environment_state(
        _invoke(state.runner, state.project, state.cli_environment, "env", "list"),
        state.environment_id,
    )
    stop = _invoke(
        state.runner, state.project, state.cli_environment, "--env", state.environment_id, "stop"
    )
    repeated_stop = _invoke(
        state.runner, state.project, state.cli_environment, "--env", state.environment_id, "stop"
    )
    context = _expected_stop_context(state, environment)
    assert stop == {**context, "status": "stopped"}
    assert repeated_stop == {**context, "status": "already_stopped"}
    _stop_owned(state.instance, case.owned_process)
    status = _invoke(state.runner, state.project, state.cli_environment, "postgres", "status")
    repeated_status = _invoke(
        state.runner, state.project, state.cli_environment, "postgres", "status"
    )
    assert status.get("state") == "healthy"
    assert _cluster_state(status) == _cluster_state(repeated_status)
    _record(record_property, "E2E-CP-13", {"stop": stop, "repeated_stop": repeated_stop})


def test_critical_path_project_doctor_is_repeatable(
    critical_path_case: _CriticalPathWorkspace, record_property: Any
) -> None:
    state = critical_path_case
    doctor = _invoke(state.runner, state.project, state.cli_environment, "doctor")
    repeated_doctor = _invoke(state.runner, state.project, state.cli_environment, "doctor")
    assert doctor == repeated_doctor
    _record(record_property, "E2E-CP-14", {"doctor": doctor, "repeated": repeated_doctor})


def test_critical_path_environment_removal_and_resource_cleanup(
    critical_path_restored_runtime: _RestoredRuntime,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Any,
) -> None:
    case = critical_path_restored_runtime
    state = case.workspace
    stop = _invoke(
        state.runner, state.project, state.cli_environment, "--env", state.environment_id, "stop"
    )
    repeated_stop = _invoke(
        state.runner, state.project, state.cli_environment, "--env", state.environment_id, "stop"
    )
    context = _expected_stop_context(state, state.environment)
    assert stop == {**context, "status": "stopped"}
    assert repeated_stop == {**context, "status": "already_stopped"}
    _stop_owned(state.instance, case.owned_process)

    _invoke(
        state.runner,
        state.project,
        state.cli_environment,
        "db",
        "drop",
        case.restored_database,
        "--force-default",
        "--force-connections",
        "--yes",
    )
    fixture_status_before, fixture_status_after = _remove_owned_fixture_tree(
        state.registered_worktree, state.probe_destination
    )
    removed = _invoke(
        state.runner,
        state.project,
        state.cli_environment,
        "env",
        "remove",
        state.environment_id,
        "--yes",
    )
    assert removed.get("id") == state.environment_id
    assert removed.get("state") == "removed"
    monkeypatch.chdir(state.project)
    removed_again = _invoke(
        state.runner,
        state.project,
        state.cli_environment,
        "env",
        "remove",
        state.environment_id,
        "--yes",
    )
    assert removed_again == removed
    assert (
        sum(
            1
            for row in state.client.get_catalog().list_environments(include_removed=True)
            if str(row["id"]) == state.environment_id
        )
        == 1
    )
    assert not Path(str(state.environment["worktree_path"])).exists()
    assert not Path(str(state.environment["generated_config_path"])).exists()
    postgres_stop = _invoke(state.runner, state.project, state.cli_environment, "postgres", "stop")
    repeated_postgres_stop = _invoke(
        state.runner, state.project, state.cli_environment, "postgres", "stop"
    )
    assert postgres_stop == repeated_postgres_stop == {}
    compose_down(
        state.cluster.compose_file,
        state.cluster.compose_project_name,
        ports=(state.cluster.endpoint_port,),
    )
    audit = audit_no_leaks(state.runtime.run_id, ports=(state.cluster.endpoint_port,))
    assert audit.clean, audit.leaks
    state.runtime.artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    state.runtime.artifact_root.chmod(0o700)
    _record(
        record_property,
        "E2E-CP-15",
        {
            "fixture_tree": {
                "path": str(state.probe_destination),
                "before": fixture_status_before,
                "after": fixture_status_after,
            },
            "removed": removed,
            "removed_again": removed_again,
            "postgres_stop": postgres_stop,
            "repeated_postgres_stop": repeated_postgres_stop,
            "cleanup_audit": audit.leaks,
        },
    )
    evidence = state.runtime.artifact_root / "critical-path.json"
    evidence.write_text(
        json.dumps(
            {
                "run_id": state.runtime.run_id,
                "source_commit": E2E_PINS.odoo_source_commit,
                "python": E2E_PINS.cpython,
                "uv": E2E_PINS.uv,
                "backup": {
                    key: case.backup[key]
                    for key in (
                        "id",
                        "size_bytes",
                        "sha256",
                        "source_base_url",
                        "database_name",
                        "source_git_branch",
                    )
                },
                "restored_database": case.restored_database,
                "cleanup": {"clean": audit.clean, "leaks": audit.leaks},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    evidence.chmod(0o600)
    assert evidence.is_file()
    assert evidence.stat().st_mode & 0o777 == 0o600
    assert 0 < evidence.stat().st_size < 2 * 1024 * 1024
