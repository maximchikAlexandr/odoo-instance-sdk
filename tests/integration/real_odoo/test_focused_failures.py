"""Focused public-boundary failure and recovery scenarios for the full E2E tier."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.models import BackupState
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.project import TestInstanceProjectConfig as _TestInstanceConfig
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
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
from .pins import E2E_PINS

pytestmark = [pytest.mark.real_odoo, pytest.mark.e2e_full, pytest.mark.serial]

_BACKUP_ID = "00000000-0000-0000-0000-000000000007"


def _record(record_property: object, evidence: str, value: object = "passed") -> None:
    getattr(record_property, "__call__")(
        evidence.lower().replace("-", "_"), json.dumps(value, default=str, sort_keys=True)
    )


def _project(runtime: E2ERuntime, root: Path, *, source: SourceBackupPlan | None = None) -> Path:
    """Create a project rooted in the pinned Odoo checkout."""
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
    subprocess.run(
        ["git", "-C", str(root), "checkout", "--detach", E2E_PINS.odoo_source_commit],
        check=True,
        capture_output=True,
    )
    relative = next(
        relative for relative in ("odoo-bin", "odoo/odoo-bin") if (root / relative).is_file()
    )
    config = ProjectConfig(
        repository_root=root,
        odoo_bin=root / relative,
        python=sys.executable,
        source_config=Path(".odcli/odoo.conf"),
        default_source_database=runtime.topology.target_sentinel_database,
        test_instance=(
            _TestInstanceConfig(base_url=source.endpoint, database=source.database)
            if source is not None
            else None
        ),
    )
    manifest = root / ".odcli" / "project.toml"
    manifest.parent.mkdir(mode=0o700, exist_ok=True)
    local_config = manifest.parent / "odoo.conf"
    shutil.copy2(runtime.config_file, local_config)
    logfile = root / f"odoo-{runtime.run_id}.log"
    logfile.write_text(
        "INFO focused leaf completed; secret=redacted\n",
        encoding="utf-8",
    )
    with local_config.open("a", encoding="utf-8") as stream:
        stream.write(f"logfile = {logfile}\n")
    local_config.chmod(0o600)
    manifest.write_text(config.to_manifest(), encoding="utf-8")
    manifest.chmod(0o600)
    return root


def _failure_text(result: object) -> str:
    return "\n".join(
        str(getattr(result, field, "")) for field in ("stdout", "stderr", "output", "exception")
    )


def _observe_failure(
    result: object,
    *,
    runtime: E2ERuntime,
    evidence: FailureEvidence,
    name: str,
    argv: object = (),
) -> tuple[dict[str, Any] | None, tuple[Path, ...]]:
    """Audit every real CLI failure across output, exception, and artifacts."""
    text = _failure_text(result)
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


def _invoke_case(  # noqa: C901
    case: PublicLeafCase,
    *,
    project: Path,
    runtime: E2ERuntime,
    evidence: FailureEvidence,
    record_property: object,
) -> None:
    """Execute one canonical leaf through Click and assert its real contract."""
    args = list(case.args)
    if "demo" in args:
        args[args.index("demo")] = runtime.topology.target_sentinel_database
    if case.requires_dry_run and "--dry-run" not in args:
        args.append("--dry-run")
    if case.path == ("logs",):
        args = ["logs", "--tail", "1"]
    if case.path == ("db", "drop"):
        args = ["db", "drop", runtime.topology.target_sentinel_database, "--yes"]
    if case.path == ("backup", "delete"):
        backup_path = runtime.artifact_root / f"leaf-{runtime.run_id}.zip"
        with zipfile.ZipFile(backup_path, "w") as archive:
            archive.writestr("manifest.json", '{"db_name": "demo"}')
            archive.writestr("dump.sql", "-- database: demo\n")
            archive.writestr("filestore/demo/blob", b"fixture")
        _seed_backup(Path(runtime.environment["ODCLI_E2E_CATALOG"]), backup_path)
        args = ["backup", "delete", _BACKUP_ID, "--yes"]
    machine = case.classification not in {"native-passthrough", "jsonl-stream"}
    if machine:
        args.extend(("--format", "json"))
    environment = {
        **runtime.environment,
        "ODCLI_E2E_KEEP_FAILED": "1",
        "ODCLI_TEST_MASTER_PASSWORD": evidence.secret_canary,
    }
    document: dict[str, Any] | None
    if case.path == ("backup", "delete"):
        first = CliRunner().invoke(cli, ["--project", str(project), *args], env=environment)
        second = CliRunner().invoke(cli, ["--project", str(project), *args], env=environment)
        assert first.exit_code == second.exit_code == 0
        first_document = json.loads(first.stdout)
        second_document = json.loads(second.stdout)
        assert first_document["ok"] is True and second_document["ok"] is True
        assert first_document["result"]["already_deleted"] is False
        assert second_document["result"]["already_deleted"] is True
        assert _catalog_state(Path(environment["ODCLI_E2E_CATALOG"])) is BackupState.DELETED
        assert not Path(runtime.artifact_root / f"leaf-{runtime.run_id}.zip").exists()
        result, document = first, first_document
    elif case.path in {("db", "reset-admin-password"), ("shell",)}:
        if case.path == ("shell",):
            args.append("--format")
            args.append("json")
        first = CliRunner().invoke(cli, ["--project", str(project), *args], env=environment)
        second = CliRunner().invoke(cli, ["--project", str(project), *args], env=environment)
        assert first.exit_code == second.exit_code == 0, first.output
        document = json.loads(first.stdout)
        assert document["ok"] is True and json.loads(second.stdout) == document
        actual_args = ["db", "reset-admin-password", "--format", "json"]
        actual_input = None
        if case.path == ("shell",):
            actual_args = ["shell", "--format", "json"]
            actual_input = "exit()\n"
        actual = CliRunner().invoke(
            cli,
            ["--project", str(project), *actual_args],
            env=environment,
            input=actual_input,
        )
        assert actual.exit_code == 0, actual.output
        result = actual
    elif case.path == ("db", "drop"):
        result = CliRunner().invoke(
            cli,
            ["--project", str(project), *args, "--format", "json"],
            env=environment,
        )
        assert result.exit_code != 0
        document, _ = _observe_failure(
            result, runtime=runtime, evidence=evidence, name="leaf-db-drop", argv=args
        )
        assert document is not None
        assert document["error"]["code"] == "db_drop_failed"
    elif case.path == ("exec",):
        success_args = ["exec", "-", "--format", "json"]
        success = CliRunner().invoke(
            cli,
            ["--project", str(project), *success_args],
            env=environment,
            input="print('focused exec')\n",
        )
        assert success.exit_code == 0, success.output
        success_document = json.loads(success.stdout)
        assert success_document["ok"] is True
        failure = CliRunner().invoke(
            cli,
            ["--project", str(project), *success_args],
            env=environment,
            input="raise RuntimeError('focused exec failure')\n",
        )
        assert failure.exit_code != 0
        document, _ = _observe_failure(
            failure, runtime=runtime, evidence=evidence, name="leaf-exec", argv=success_args
        )
        assert document is not None and document["error"]["code"] == "exec_user_code_failed"
        result = failure
    else:
        result = CliRunner().invoke(
            cli,
            ["--project", str(project), *args],
            env=environment,
            input="raise RuntimeError('focused leaf failure')\n",
        )
        if case.path == ("psql",):
            assert result.exit_code == 0, result.output
            document = None
        elif case.path == ("logs",):
            assert result.exit_code == 0, result.output
            assert "redacted" in result.stdout
            document = None
        else:
            assert result.exit_code == 0, result.output
            document = json.loads(result.stdout)
            assert document["ok"] is True
    assert_secret_free(
        {"argv": args, "machine_output": result.stdout, "pytest_output": result.output},
        evidence.secret_canary,
    )
    for evidence_id in case.e2e_evidence:
        _record(record_property, evidence_id, {"path": case.path, "exit_code": result.exit_code})


def _seed_backup(
    db_path: Path,
    archive_path: Path,
    *,
    backup_id: str = _BACKUP_ID,
    database: str = "demo",
) -> None:
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    catalog = BackupCatalog(db_path=db_path)
    catalog.start_download(
        backup_id,
        "http://127.0.0.1:8069",
        database,
        "zip",
        True,
        archive_path,
    )
    catalog.success_download(backup_id, archive_path.name, archive_path.stat().st_size, digest)
    catalog.close()


def _catalog_state(db_path: Path, backup_id: str = _BACKUP_ID) -> BackupState:
    catalog = BackupCatalog(db_path=db_path)
    row = catalog.get_by_id(backup_id)
    catalog.close()
    assert row is not None
    return BackupState(str(row["state"]))


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
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    variant: str,
    record_property: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / f"{variant}.zip"
    write_archive_variant(path, variant)  # type: ignore[arg-type]
    catalog_path = tmp_path / "catalog.sqlite3"
    _seed_backup(catalog_path, path)
    monkeypatch.setattr("odoo_instance_sdk.cli.get_catalog_path", lambda **_: catalog_path)
    result = CliRunner().invoke(cli, ["backup", "validate", _BACKUP_ID, "--format", "json"])
    if variant == "incompatible":
        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        assert document["ok"] is True
        assert document["result"]["db_name"] == "not-the-catalogue-database"
        assert document["result"]["db_name"] != "demo"
        files = write_failure_evidence(
            failure_evidence, logs={"archive-incompatible": result.stdout}
        )
        assert_secret_free(files, failure_evidence.secret_canary)
        project = _project(
            target_runtime, tmp_path / f"incompatible-restore-{target_runtime.run_id}"
        )
        restore = CliRunner().invoke(
            cli,
            [
                "--project",
                str(project),
                "db",
                "restore",
                _BACKUP_ID,
                "--target",
                target_runtime.topology.target_sentinel_database,
                "--yes",
                "--format",
                "json",
            ],
            env={
                **target_runtime.environment,
                "ODCLI_E2E_CATALOG": str(catalog_path),
            },
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
    target_runtime: E2ERuntime,
    failure_evidence: FailureEvidence,
    record_property: object,
) -> None:
    archive_path = tmp_path / "restore.zip"
    shutil.copy2(source_backup.path, archive_path)
    catalog_path = tmp_path / "catalog.sqlite3"
    success_id = "00000000-0000-0000-0000-000000000008"
    _seed_backup(
        catalog_path,
        archive_path,
        backup_id=success_id,
        database=target_runtime.topology.source_database,
    )
    project = _project(target_runtime, tmp_path / f"restore-project-{target_runtime.run_id}")
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
    )


def test_sigint_timeout_and_partial_publication_recover_without_leaks(
    target_runtime: E2ERuntime,
    source_backup: ArchiveIdentity,
    failure_evidence: FailureEvidence,
    resource_ledger: ResourceLedger,
    record_property: object,
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
    invalid_path = target_runtime.artifact_root / f"recovery-invalid-{run_id}.zip"
    write_archive_variant(invalid_path, "truncated")
    invalid_id = "00000000-0000-0000-0000-000000000010"
    _seed_backup(
        catalog_path,
        invalid_path,
        backup_id=invalid_id,
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
    partial_project = _project(target_runtime, target_runtime.root / f"partial-{run_id}")
    partial_result = CliRunner().invoke(
        cli,
        [
            "--project",
            str(partial_project),
            "db",
            "restore",
            invalid_id,
            "--target",
            f"odcli_partial_{run_id.replace('-', '')[:20]}",
            "--yes",
            "--format",
            "json",
        ],
        env=environment,
    )
    assert partial_result.exit_code != 0
    partial_document, _ = _observe_failure(
        partial_result,
        runtime=target_runtime,
        evidence=failure_evidence,
        name="recovery-partial-public-restore",
    )
    assert partial_document is not None
    assert partial_document["error"]["code"] == "db_restore_failed"
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
        [command, "--project", str(project), "run", "--format", "json"],
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
    with pytest.raises(subprocess.TimeoutExpired):
        timeout_process.communicate(timeout=0.1)
    os.killpg(timeout_process.pid, signal.SIGTERM)
    timeout_stdout, timeout_stderr = timeout_process.communicate(timeout=30)
    assert timeout_process.returncode is not None
    assert_secret_free(
        {"argv": command, "machine_output": timeout_stdout, "pytest_output": timeout_stderr},
        failure_evidence.secret_canary,
    )
    _record(
        record_property,
        "E2E-REC-02",
        {"exit_code": timeout_process.returncode, "stderr": timeout_stderr[-1024:]},
    )

    root = target_runtime.root / f"publication-{run_id}"
    scenario_ledger = ResourceLedger(run_id)
    database = f"odcli_partial_{run_id.replace('-', '')[:20]}"
    database_result = ComposeLifecycle(
        target_runtime.compose_file, target_runtime.topology.project_name
    ).run("exec", "-T", "target_postgres", "createdb", "-U", "odoo", database, timeout=30.0)
    assert database_result.returncode == 0, database_result.stderr
    filestore = root / "filestore" / database
    filestore.mkdir(mode=0o700, parents=True)

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
    scenario_ledger.record("filestore", f"{run_id}-filestore", lambda: shutil.rmtree(root))
    scenario_ledger.record(
        "cleanup",
        f"{run_id}-injected-cleanup",
        lambda: (_ for _ in ()).throw(RuntimeError("cleanup failure")),
    )
    observation = run_recovery_action(
        lambda: (_ for _ in ()).throw(RuntimeError(partial_document["error"]["message"])),
        ledger=scenario_ledger,
    )
    assert str(observation.primary_error) == partial_document["error"]["message"]
    assert observation.cleanup_errors == ("cleanup failure",)
    assert not root.exists()
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
        logs={"recovery-interrupt": stdout, "recovery-timeout": timeout_stdout},
    )
    assert_secret_free(files, failure_evidence.secret_canary)


def test_failed_debug_retention_contains_only_sanitized_files(
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
    project = _project(target_runtime, target_runtime.root / f"retention-{target_runtime.run_id}")
    result = CliRunner().invoke(
        cli,
        ["--project", str(project), "db", "drop", "missing", "--format", "json"],
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
