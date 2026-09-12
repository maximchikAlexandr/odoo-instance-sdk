"""Focused public-boundary failure and recovery scenarios for the full E2E tier."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import replace
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
from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES, PublicLeafCase

from .archive import ArchiveIdentity, SourceBackupPlan
from .cleanup import FailureEvidence, ResourceLedger
from .compose import ComposeLifecycle
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
    catalog_state as _catalog_state,
)
from .focused_support import (
    observe_failure as _observe_failure,
)
from .focused_support import (
    record as _record,
)
from .focused_support import (
    seed_backup as _seed_backup,
)
from .pins import E2E_PINS

pytestmark = [pytest.mark.real_odoo, pytest.mark.e2e_full, pytest.mark.serial]


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
        ["git", "-C", str(root), "checkout", "--detach", E2E_PINS.odoo_source_commit],
        check=True,
        capture_output=True,
    )
    relative = next(
        relative for relative in ("odoo-bin", "odoo/odoo-bin") if (root / relative).is_file()
    )
    runtime.ledger.record(
        "worktree",
        f"{runtime.run_id}-{root.name}",
        lambda: shutil.rmtree(root, ignore_errors=True),
    )
    environment = dict(runtime.environment)
    command = shutil.which("odcli")
    if command is None:
        pytest.fail("odcli executable is required for focused public leaves")
    process_environment = {**os.environ, **environment}
    init = subprocess.run(
        [
            command,
            "init",
            "--project",
            str(root),
            "--no-input",
            "--odoo-bin",
            str(root / relative),
            "--python",
            E2E_PINS.cpython,
            "--config",
            str(runtime.config_file),
            "--database",
            runtime.topology.target_sentinel_database,
            "--postgres",
            "external",
            "--http-port",
            str(runtime.reservations[3].port),
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
    shutil.copy2(runtime.config_file, local_config)
    logfile = root / f"odoo-{runtime.run_id}.log"
    logfile.write_text("INFO focused leaf completed; secret=redacted\n", encoding="utf-8")
    with local_config.open("a", encoding="utf-8") as stream:
        stream.write(f"logfile = {logfile}\n")
    local_config.chmod(0o600)
    manifest.write_text(initialized.to_manifest(), encoding="utf-8")
    manifest.chmod(0o600)

    ticket = f"MYL-{int(runtime.run_id.replace('-', '')[:8], 16) % 100000000}"
    runtime.reservations[3].release()
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
                odoo_bin=root / relative,
                python=E2E_PINS.cpython,
                create_venv=True,
                http_port=runtime.reservations[3].port,
            ),
        )
    finally:
        os.environ.clear()
        os.environ.update(previous_environment)
    checkout_environment = msgspec.to_builtins(checkout_result.environment)
    environment_id = str(checkout_environment["id"])
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
        if removed.returncode != 0:
            raise RuntimeError(removed.stdout + removed.stderr)

    runtime.ledger.record(
        "environment",
        f"{runtime.run_id}-environment-{environment_id}",
        remove_environment,
    )
    return root


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
        "odoo_instance_sdk.internal.port_allocation.get_catalog_path",
        "odoo_instance_sdk.resources.postgres.get_catalog_path",
        "odoo_instance_sdk.resources.monitor.get_catalog_path",
    ):
        monkeypatch.setattr(target, provider)
    from odoo_instance_sdk.commands import backup as backup_commands
    from odoo_instance_sdk.commands import resource as resource_commands

    monkeypatch.setattr(backup_commands._catalog_path_provider, "provider", provider)
    monkeypatch.setattr(resource_commands._catalog_path_provider, "provider", provider)


def test_remote_auth_and_unreachable_source_fail_closed(
    source_backup_plan: SourceBackupPlan,
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
) -> None:
    """The public refresh command must fail before creating a local backup."""
    runtime = target_runtime
    project = _project(
        runtime, runtime.root / f"project-{runtime.run_id}", source=source_backup_plan
    )
    runner = CliRunner()
    wrong_password = failure_evidence.secret_canary
    environment = {
        **runtime.environment,
        "ODCLI_TEST_MASTER_PASSWORD": wrong_password,
        "ODCLI_E2E_KEEP_FAILED": "1",
    }
    auth = runner.invoke(
        cli,
        ["--project", str(project), "db", "refresh", "--format", "json"],
        env=environment,
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
    unreachable = runner.invoke(
        cli,
        ["--project", str(unreachable_project), "db", "refresh", "--format", "json"],
        env=environment,
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
) -> None:
    path = tmp_path / f"{variant}.zip"
    write_archive_variant(path, variant)  # type: ignore[arg-type]
    catalog_path = Path(target_runtime.environment["ODCLI_E2E_CATALOG"])
    _seed_backup(
        catalog_path,
        path,
        database=source_backup_plan.database,
        source_base_url=source_backup_plan.endpoint,
    )
    environment = {
        **target_runtime.environment,
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_TEST_MASTER_PASSWORD": failure_evidence.secret_canary,
    }
    _bind_catalog_path(monkeypatch, catalog_path)
    result = CliRunner().invoke(
        cli,
        ["backup", "validate", _BACKUP_ID, "--format", "json"],
        env=environment,
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
        project = _project(
            target_runtime,
            tmp_path / f"incompatible-restore-{target_runtime.run_id}",
            source=source_backup_plan,
        )
        target = f"odcli_incompatible_{target_runtime.run_id.replace('-', '')[:20]}"
        restore = CliRunner().invoke(
            cli,
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
            env=environment,
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
        assert not (target_runtime.root / "target-data" / "filestore" / target).exists()
        probe = ComposeLifecycle(
            target_runtime.compose_file, target_runtime.topology.project_name
        ).run(
            "exec",
            "-T",
            "target_postgres",
            "psql",
            "-U",
            "odoo",
            "-d",
            "postgres",
            "-At",
            "-c",
            f"SELECT datname FROM pg_database WHERE datname = '{target}';",
            timeout=30.0,
        )
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
) -> None:
    archive_path = tmp_path / "restore.zip"
    shutil.copy2(source_backup.path, archive_path)
    catalog_path = tmp_path / "catalog.sqlite3"
    success_id = "00000000-0000-0000-0000-000000000008"
    _seed_backup(
        catalog_path,
        archive_path,
        backup_id=success_id,
        database=source_backup_plan.database,
        source_base_url=source_backup_plan.endpoint,
    )
    _bind_catalog_path(monkeypatch, catalog_path)
    project = _project(
        target_runtime,
        tmp_path / f"restore-project-{target_runtime.run_id}",
        source=source_backup_plan,
    )
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
        "ODCLI_E2E_CATALOG": str(catalog_path),
        "ODCLI_E2E_KEEP_FAILED": "1",
    }
    successful = CliRunner().invoke(cli, args, env=environment)
    assert successful.exit_code == 0, successful.output
    success_document = json.loads(successful.stdout)
    assert success_document["ok"] is True
    assert success_document["result"]["restored_database"] == target
    database_probe = ComposeLifecycle(
        target_runtime.compose_file, target_runtime.topology.project_name
    ).run(
        "exec",
        "-T",
        "target_postgres",
        "psql",
        "-U",
        "odoo",
        "-d",
        "postgres",
        "-At",
        "-c",
        f"SELECT datname FROM pg_database WHERE datname = '{target}';",
        timeout=30.0,
    )
    assert database_probe.returncode == 0
    assert database_probe.stdout.strip() == target
    assert (target_runtime.root / "target-data" / "filestore" / target).is_dir()

    first = CliRunner().invoke(cli, args, env=environment)
    second = CliRunner().invoke(cli, args, env=environment)
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
    assert (target_runtime.root / "target-data" / "filestore" / target).is_dir()
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
) -> None:
    project = _project(target_runtime, target_runtime.root / f"leaf-{target_runtime.run_id}")
    _invoke_case(
        case,
        project=project,
        runtime=target_runtime,
        evidence=failure_evidence,
        record_property=record_property,
        source_backup=source_backup,
    )


def test_sigint_timeout_and_partial_publication_recover_without_leaks(
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
    target = f"odcli_interrupt_{run_id.replace('-', '')[:20]}"
    command = shutil.which("odcli")
    if command is None:
        pytest.fail("odcli executable is required for public recovery boundaries")
    environment = {
        **os.environ,
        **target_runtime.environment,
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
        cwd=project,
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    resource_ledger.record(
        "process",
        f"{run_id}-odcli-restore-{process.pid}",
        lambda: os.killpg(process.pid, signal.SIGKILL) if process.poll() is None else None,
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
        cwd=project,
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    resource_ledger.record(
        "process",
        f"{run_id}-odcli-timeout-{timeout_process.pid}",
        lambda: (
            os.killpg(timeout_process.pid, signal.SIGKILL)
            if timeout_process.poll() is None
            else None
        ),
    )
    timeout_stdout, timeout_stderr = timeout_process.communicate(timeout=30)
    assert timeout_process.returncode != 0
    timeout_document = json.loads(timeout_stdout)
    assert timeout_document["ok"] is False
    assert timeout_document["error"]["code"] == "db_init_monitoring_failed"
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
    filestore = target_runtime.root / "target-data" / "filestore" / database

    def public_restore_failure() -> None:
        failed = CliRunner().invoke(cli, partial_args, env=environment)
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
        result = ComposeLifecycle(
            target_runtime.compose_file, target_runtime.topology.project_name
        ).run("exec", "-T", "target_postgres", "dropdb", "-U", "odoo", database, timeout=30.0)
        if result.returncode != 0:
            raise RuntimeError("database cleanup failed")

    scenario_ledger.record(
        "database",
        f"{run_id}-{database}",
        drop_database,
    )
    scenario_ledger.record("filestore", f"{run_id}-filestore", lambda: shutil.rmtree(filestore))

    def delete_catalog_record() -> None:
        deleted = CliRunner().invoke(
            cli,
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
            env=environment,
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
    database_probe = ComposeLifecycle(
        target_runtime.compose_file, target_runtime.topology.project_name
    ).run(
        "exec",
        "-T",
        "target_postgres",
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
    result = CliRunner().invoke(
        cli,
        ["--project", str(project), "db", "refresh", "--format", "json"],
        env=environment,
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
