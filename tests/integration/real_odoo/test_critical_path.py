"""Source-backed public critical path for the real-Odoo full tier."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import xmlrpc.client
from pathlib import Path
from typing import Any, cast

import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli

from .archive import ArchiveIdentity
from .cleanup import audit_no_leaks, compose_down, write_odoo_config
from .compose import ComposeLifecycle, wait_for_http
from .conftest import E2ERuntime
from .pins import E2E_PINS

pytestmark = [pytest.mark.real_odoo, pytest.mark.e2e_full, pytest.mark.serial]

_PROBE = "odcli_e2e_probe"
_PROBE_MARKER = "ODCLI-E2E-RESTORED"
_ATTACHMENT = b"OdCLI filestore probe\n"


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


def _environment_state(payload: dict[str, Any], environment_id: str) -> dict[str, Any]:
    environments = payload.get("environments")
    assert isinstance(environments, list)
    row = next(
        (
            item
            for item in environments
            if isinstance(item, dict) and item.get("id") == environment_id
        ),
        None,
    )
    assert isinstance(row, dict), payload
    return {
        key: row.get(key)
        for key in (
            "id",
            "name",
            "branch",
            "db_mode",
            "database",
            "lifecycle_state",
            "allocated_http_port",
            "artifacts",
            "runtime",
        )
    }


def _cluster_state(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in ("mode", "owned", "state", "endpoint")}


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


def _xmlrpc_probe(base_url: str, database: str) -> tuple[str, bytes]:
    common = xmlrpc.client.ServerProxy(f"{base_url}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(database, "admin", "admin", {})
    assert isinstance(uid, int) and uid > 0
    models = xmlrpc.client.ServerProxy(f"{base_url}/xmlrpc/2/object", allow_none=True)
    records = cast(
        "list[dict[str, Any]]",
        models.execute_kw(
            database,
            uid,
            "admin",
            "odcli.e2e.probe",
            "search_read",
            [[("marker", "=", _PROBE_MARKER)]],
            {"fields": ["name", "marker"], "limit": 1},
        ),
    )
    assert len(records) == 1
    assert records[0]["name"] == "Pinned Odoo 19 fixture"
    assert records[0]["marker"] == _PROBE_MARKER
    attachments = cast(
        "list[dict[str, Any]]",
        models.execute_kw(
            database,
            uid,
            "admin",
            "ir.attachment",
            "search_read",
            [[("name", "=", "odcli-e2e-attachment.txt"), ("res_model", "=", "odcli.e2e.probe")]],
            {"fields": ["datas", "store_fname"], "limit": 1},
        ),
    )
    assert len(attachments) == 1 and attachments[0]["store_fname"]
    encoded = attachments[0]["datas"]
    if isinstance(encoded, xmlrpc.client.Binary):
        encoded = bytes(encoded.data)
    assert isinstance(encoded, (str, bytes))
    return str(records[0]["name"]), base64.b64decode(encoded)


def _start_owned(instance: Any, runtime: E2ERuntime) -> Any:
    process = instance.start()
    instance.wait_ready(process, timeout=180.0)
    instance._persist_runtime_identity(
        process.pid,
        instance.config.start_config,
        instance.config.default_cwd,
    )
    runtime.ledger.record(
        "process",
        f"{runtime.run_id}-odoo-{process.pid}",
        lambda: (
            instance.stop(process) if instance._client.get_handle(process.id) is not None else None
        ),
    )
    return process


def _stop_owned(instance: Any, process: Any) -> None:
    handle = instance._client.get_handle(process.id)
    if handle is not None and handle.poll() is None:
        instance.stop(process)
    elif handle is not None:
        instance._client.unregister_process(process.id)
    instance._clear_runtime_identity()


@pytest.mark.timeout(600)
def test_source_backed_full_critical_path(  # noqa: C901
    target_runtime: E2ERuntime,
    source_backup: ArchiveIdentity,
    record_property: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove one serial public workflow from pinned source checkout to cleanup."""
    source_repository, odoo_bin_relative = _source_repository()
    runner = CliRunner()
    runtime = target_runtime
    for key, value in runtime.environment.items():
        monkeypatch.setenv(key, value)
    catalogue_path = (runtime.root / "catalog.sqlite3").resolve()

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

    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_data_root", run_data_root)
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_state_root", run_state_root)
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", run_cache_root)
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_locks_dir", run_locks_dir)

    for provider in (
        "odoo_instance_sdk.cli.get_catalog_path",
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        "odoo_instance_sdk.internal.context.get_catalog_path",
        "odoo_instance_sdk.commands.env.get_catalog_path",
        "odoo_instance_sdk.internal.port_allocation.get_catalog_path",
        "odoo_instance_sdk.resources.postgres.get_catalog_path",
        "odoo_instance_sdk.resources.monitor.get_catalog_path",
    ):
        monkeypatch.setattr(provider, run_catalog_path)
    from odoo_instance_sdk.commands import backup as backup_commands
    from odoo_instance_sdk.commands import resource as resource_commands

    monkeypatch.setattr(backup_commands._catalog_path_provider, "provider", run_catalog_path)
    monkeypatch.setattr(resource_commands._catalog_path_provider, "provider", run_catalog_path)
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
        addons_path=(
            Path("/usr/lib/python3/dist-packages/odoo/addons"),
            addon_root,
        ),
    )
    cli_environment = dict(os.environ)
    cli_environment.update(runtime.environment)
    cli_environment.update(
        {
            "ODCLI_TEST_MASTER_PASSWORD": master_password,
            "ODCLI_TEST_INSTANCE_ORIGIN_PINS": (
                f"http://127.0.0.1:{runtime.topology.source_odoo_port}"
            ),
        }
    )
    project = runtime.root / f"project-{runtime.run_id}"
    _clone_pinned_source(source_repository, project)
    runtime.ledger.record(
        "worktree", project.name, lambda: shutil.rmtree(project, ignore_errors=True)
    )

    # The shared target fixture owns a disposable PostgreSQL only to establish
    # the target namespace.  The public project lifecycle owns the cluster used
    # by the critical path from this point onward.
    down = ComposeLifecycle(runtime.compose_file, runtime.topology.project_name).run(
        "down", "--volumes", "--remove-orphans", timeout=60.0
    )
    assert down.returncode == 0, down.stderr

    init = runner.invoke(
        cli,
        [
            "init",
            "--project",
            str(project),
            "--no-input",
            "--odoo-bin",
            str(project / odoo_bin_relative),
            "--config",
            str(runtime.config_file),
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
    assert manifest.is_file()
    from odoo_instance_sdk.project import ProjectConfig, TestInstanceProjectConfig

    initialized = ProjectConfig.load(project)
    initialized = msgspec.structs.replace(
        initialized,
        source_config=Path(".odcli/odoo.conf"),
        test_instance=TestInstanceProjectConfig(
            base_url=f"http://127.0.0.1:{runtime.topology.source_odoo_port}",
            database=runtime.topology.source_database,
            git_branch=E2E_PINS.odoo_source_commit,
        ),
        default_base_ref=E2E_PINS.odoo_source_commit,
    )
    manifest.write_text(initialized.to_manifest(), encoding="utf-8")
    assert ".odcli/odoo.conf" in manifest.read_text(encoding="utf-8")
    _record(record_property, "E2E-CP-01", {"manifest": str(manifest), "postgres": "compose"})

    from odoo_instance_sdk.resources.postgres import PostgresCluster

    cluster = PostgresCluster.from_project(project)
    runtime.ledger.record(
        "postgres",
        f"{runtime.run_id}-sdk-postgres",
        lambda: compose_down(cluster.compose_file, cluster.compose_project_name),
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
    assert status.get("state") == "healthy"
    assert _cluster_state(status) == _cluster_state(repeated_status)
    _record(record_property, "E2E-CP-01", {"status": status, "repeated_status": repeated_status})

    uv_version = subprocess.run(
        ["uv", "--version"], capture_output=True, shell=False, text=True, check=True
    ).stdout.strip()
    assert uv_version.split()[1] == E2E_PINS.uv
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
        str(project / odoo_bin_relative),
        "--python",
        E2E_PINS.cpython,
        "--create-venv",
        "--http-port",
        str(runtime.reservations[3].port),
    )
    environment = checkout["environment"]
    assert isinstance(environment, dict)
    environment_id = str(environment["id"])
    assert environment["state"] == "ready"
    assert environment["branch"] == "MYL-166"
    assert environment.get("python_environment_owned") is True
    assert Path(str(environment["generated_config_path"])).is_file()
    assert Path(str(environment["python_environment_path"])).is_dir()
    assert Path(str(environment["worktree_path"])).is_dir()
    assert (
        subprocess.run(
            ["git", "-C", str(environment["worktree_path"]), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        == E2E_PINS.odoo_source_commit
    )
    python = Path(str(environment["python_environment_path"])) / "bin" / "python"
    assert (
        subprocess.run(
            [str(python), "--version"],
            capture_output=True,
            shell=False,
            text=True,
            check=True,
        ).stdout.strip()
        == f"Python {E2E_PINS.cpython}"
    )
    _record(
        record_property,
        "E2E-CP-02",
        {"commit": E2E_PINS.odoo_source_commit, "environment": environment_id},
    )
    listed = _invoke(runner, project, cli_environment, "env", "list")
    repeated_listed = _invoke(runner, project, cli_environment, "env", "list")
    assert environment_id in json.dumps(listed)
    assert _environment_state(listed, environment_id) == _environment_state(
        repeated_listed, environment_id
    )
    path_result = _invoke(runner, project, cli_environment, "env", "path", environment_id)
    assert path_result["worktree_path"] == environment["worktree_path"]
    _record(record_property, "E2E-CP-03", path_result)
    sync = _invoke(runner, project, cli_environment, "env", "sync", environment_id)
    repeated_sync = _invoke(runner, project, cli_environment, "env", "sync", environment_id)
    assert sync == repeated_sync
    deps = _invoke(runner, project, cli_environment, "--env", environment_id, "deps", "verify")
    assert deps.get("pip_check_ok") is True
    assert deps.get("missing_imports") == []
    _record(record_property, "E2E-CP-04", {"python": E2E_PINS.cpython, "uv": E2E_PINS.uv})

    from odoo_instance_sdk import OdooClient, OdooClientConfig

    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    assert os.environ["ODCLI_E2E_CATALOG"] == runtime.environment["ODCLI_E2E_CATALOG"]
    assert Path(cli_environment["ODCLI_E2E_CATALOG"]).resolve() == catalogue_path
    sdk_catalogue = client.get_catalog().db_path.resolve()
    assert sdk_catalogue == catalogue_path
    backup_catalogue = backup_commands._catalog_path_provider.provider
    resource_catalogue = resource_commands._catalog_path_provider.provider
    assert callable(backup_catalogue) and backup_catalogue().resolve() == catalogue_path
    assert callable(resource_catalogue) and resource_catalogue().resolve() == catalogue_path
    env_obj = client.environments.get(environment_id)
    instance = client.instance.from_environment(env_obj)
    assert (
        instance.run_foreground(args=("--init=base", "--without-demo=all", "--stop-after-init"))
        == 0
    )
    _invoke(runner, project, cli_environment, "--env", environment_id, "module", "list", _PROBE)
    first_process = _start_owned(instance, runtime)
    try:
        wait_for_http(instance.config.base_url + "/web/health", timeout=180.0)
        stop_result = _invoke(runner, project, cli_environment, "--env", environment_id, "stop")
        repeated_stop_result = _invoke(
            runner, project, cli_environment, "--env", environment_id, "stop"
        )
        assert stop_result == {"status": "stopped", "environment_id": environment_id}
        assert repeated_stop_result == {
            "status": "already_stopped",
            "environment_id": environment_id,
        }
    finally:
        _stop_owned(instance, first_process)
    assert (
        instance.run_foreground(
            args=(f"--init={_PROBE}", "--without-demo=all", "--stop-after-init")
        )
        == 0
    )
    modules = _invoke(
        runner, project, cli_environment, "--env", environment_id, "module", "list", _PROBE
    )
    assert _PROBE in json.dumps(modules)
    _record(record_property, "E2E-CP-05", modules)
    module_update = _invoke(
        runner,
        project,
        cli_environment,
        "--env",
        environment_id,
        "module",
        "update",
        _PROBE,
        "--yes",
    )
    module_state = _invoke(
        runner, project, cli_environment, "--env", environment_id, "module", "list", _PROBE
    )
    repeated_module_update = _invoke(
        runner,
        project,
        cli_environment,
        "--env",
        environment_id,
        "module",
        "update",
        _PROBE,
        "--yes",
    )
    repeated_module_state = _invoke(
        runner, project, cli_environment, "--env", environment_id, "module", "list", _PROBE
    )
    assert module_state == repeated_module_state
    _record(
        record_property,
        "E2E-CP-06",
        {
            "first_update": module_update,
            "repeated_update": repeated_module_update,
            "state": module_state,
            "repeated_state": repeated_module_state,
        },
    )
    tests = _invoke(
        runner,
        project,
        cli_environment,
        "--env",
        environment_id,
        "test",
        _PROBE,
        "--tags",
        f"/{_PROBE}",
    )
    assert tests.get("exit_code") == 0
    _record(record_property, "E2E-CP-07", tests)

    second_process = _start_owned(instance, runtime)
    wait_for_http(instance.config.base_url + "/web/health", timeout=180.0)
    refresh = _invoke(
        runner,
        project,
        cli_environment,
        "db",
        "refresh",
        "--restore",
        "--source-branch",
        E2E_PINS.odoo_source_commit,
    )
    restored_database = str(refresh["restored_database"])
    backup = refresh.get("backup")
    assert isinstance(backup, dict) and backup.get("id")
    assert isinstance(backup.get("sha256"), str) and len(backup["sha256"]) == 64
    assert isinstance(backup.get("size_bytes"), int) and backup["size_bytes"] > 0
    assert source_backup.size_bytes > 0 and len(source_backup.sha256) == 64
    _record(record_property, "E2E-CP-08", {"database": restored_database, "backup": backup["id"]})
    backup_id = str(backup["id"])
    _invoke(runner, project, cli_environment, "backup", "list")
    shown = _invoke(runner, project, cli_environment, "backup", "show", backup_id)
    assert str(shown.get("id", shown.get("backup", {}).get("id", ""))) == backup_id
    for key in ("size_bytes", "sha256", "source_base_url", "database_name", "source_git_branch"):
        assert shown.get(key) == backup.get(key), key
    assert backup["source_base_url"] == f"http://127.0.0.1:{runtime.topology.source_odoo_port}"
    assert backup["database_name"] == runtime.topology.source_database
    assert backup["source_git_branch"] == E2E_PINS.odoo_source_commit
    validated = _invoke(runner, project, cli_environment, "backup", "validate", backup_id)
    assert validated
    _record(
        record_property,
        "E2E-CP-09",
        {
            "id": backup_id,
            "size_bytes": backup["size_bytes"],
            "sha256": backup["sha256"],
            "source_base_url": backup["source_base_url"],
            "database_name": backup["database_name"],
            "source_git_branch": backup["source_git_branch"],
            "shown": shown,
        },
    )

    _stop_owned(instance, second_process)
    _switch_database_config(Path(env_obj.generated_config_path), restored_database)
    env_obj = client.environments.get(environment_id)
    instance = client.instance.from_environment(env_obj)
    third_process = _start_owned(instance, runtime)
    wait_for_http(instance.config.base_url + "/web/health", timeout=180.0)
    name, attachment = _xmlrpc_probe(instance.config.base_url, restored_database)
    assert name == "Pinned Odoo 19 fixture"
    assert attachment == _ATTACHMENT
    _record(
        record_property,
        "E2E-CP-10",
        {"database": restored_database, "attachment_bytes": len(attachment)},
    )
    evaluation = _invoke(
        runner,
        project,
        cli_environment,
        "--env",
        environment_id,
        "eval",
        "env['odcli.e2e.probe'].search_count([('marker', '=', 'ODCLI-E2E-RESTORED')])",
    )
    assert "1" in json.dumps(evaluation)
    _record(record_property, "E2E-CP-11", evaluation)
    resources = _invoke(runner, project, cli_environment, "resource", "list")
    resource_doctor = _invoke(runner, project, cli_environment, "resource", "doctor")
    assert isinstance(resources.get("resources"), list)
    assert isinstance(resource_doctor.get("findings"), list)
    databases = _invoke(runner, project, cli_environment, "db", "list")
    assert restored_database in json.dumps(databases)
    _record(
        record_property,
        "E2E-CP-12",
        {"resources": resources, "resource_doctor": resource_doctor, "databases": databases},
    )

    stop_result = _invoke(runner, project, cli_environment, "--env", environment_id, "stop")
    repeated_stop_result = _invoke(
        runner, project, cli_environment, "--env", environment_id, "stop"
    )
    assert stop_result == {"status": "stopped", "environment_id": environment_id}
    assert repeated_stop_result == {
        "status": "already_stopped",
        "environment_id": environment_id,
    }
    _stop_owned(instance, third_process)
    status_after_stop = _invoke(runner, project, cli_environment, "postgres", "status")
    repeated_status_after_stop = _invoke(runner, project, cli_environment, "postgres", "status")
    assert status_after_stop.get("state") == "healthy"
    assert _cluster_state(status_after_stop) == _cluster_state(repeated_status_after_stop)
    _record(
        record_property,
        "E2E-CP-13",
        {"stop": stop_result, "repeated_stop": repeated_stop_result},
    )
    doctor = _invoke(runner, project, cli_environment, "doctor")
    repeated_doctor = _invoke(runner, project, cli_environment, "doctor")
    assert doctor == repeated_doctor
    _record(record_property, "E2E-CP-14", {"doctor": doctor, "repeated": repeated_doctor})

    _invoke(
        runner,
        project,
        cli_environment,
        "db",
        "drop",
        restored_database,
        "--force-default",
        "--yes",
    )
    removed = _invoke(runner, project, cli_environment, "env", "remove", environment_id, "--yes")
    assert removed.get("id") == environment_id
    assert removed.get("state") == "removed"
    removed_again = _invoke(
        runner, project, cli_environment, "env", "remove", environment_id, "--yes"
    )
    assert removed_again == removed
    assert (
        sum(
            1
            for row in client.get_catalog().list_environments(include_removed=True)
            if str(row["id"]) == environment_id
        )
        == 1
    )
    assert not Path(str(environment["worktree_path"])).exists()
    assert not Path(str(environment["generated_config_path"])).exists()
    postgres_stop = _invoke(runner, project, cli_environment, "postgres", "stop")
    repeated_postgres_stop = _invoke(runner, project, cli_environment, "postgres", "stop")
    assert postgres_stop == repeated_postgres_stop == {}

    runtime.ledger.unwind()
    audit = audit_no_leaks(
        runtime.run_id,
        compose_project=runtime.topology.project_name,
        runtime_root=runtime.root,
        ports=(
            runtime.topology.source_postgres_port,
            runtime.topology.target_postgres_port,
            runtime.topology.source_odoo_port,
            runtime.reservations[3].port,
        ),
        catalog_path=catalogue_path,
        filestore_paths=(runtime.root / "source-data", runtime.root / "target-data"),
    )
    assert audit.clean, audit.leaks
    runtime.artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime.artifact_root.chmod(0o700)
    _record(
        record_property,
        "E2E-CP-15",
        {
            "removed": removed,
            "removed_again": removed_again,
            "postgres_stop": postgres_stop,
            "repeated_postgres_stop": repeated_postgres_stop,
            "cleanup_audit": audit.leaks,
        },
    )

    evidence = runtime.artifact_root / "critical-path.json"
    evidence.write_text(
        json.dumps(
            {
                "run_id": runtime.run_id,
                "source_commit": E2E_PINS.odoo_source_commit,
                "python": E2E_PINS.cpython,
                "uv": E2E_PINS.uv,
                "backup": {
                    "id": backup_id,
                    "size_bytes": backup["size_bytes"],
                    "sha256": backup["sha256"],
                    "source_base_url": backup["source_base_url"],
                    "database_name": backup["database_name"],
                    "source_git_branch": backup["source_git_branch"],
                },
                "restored_database": restored_database,
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
    assert evidence.stat().st_size < 2 * 1024 * 1024
