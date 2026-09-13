"""Focused canonical-leaf handlers grouped by scenario family."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from click.testing import CliRunner, Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.models import BackupState
from tests.unit.test_cli_output_modes import PublicLeafCase

from .archive import ArchiveIdentity
from .cleanup import FailureEvidence
from .conftest import E2ERuntime
from .failures import assert_secret_free
from .focused_support import BACKUP_ID, catalog_state, observe_failure, record, seed_backup


@dataclass(slots=True)
class _State:
    case: PublicLeafCase
    args: list[str]
    project: Path
    runtime: E2ERuntime
    evidence: FailureEvidence
    environment: dict[str, str]
    source_backup: ArchiveIdentity


Handler = Callable[[_State], tuple[Result, dict[str, Any] | None]]


def _invoke(state: _State, args: list[str], *, input: str | None = None) -> Result:
    return CliRunner().invoke(
        cli,
        ["--project", str(state.project), *args],
        env=state.environment,
        input=input,
    )


def _backup_delete(state: _State) -> tuple[Result, dict[str, Any]]:
    first = _invoke(state, state.args)
    second = _invoke(state, state.args)
    assert first.exit_code == second.exit_code == 0
    first_document = json.loads(first.stdout)
    second_document = json.loads(second.stdout)
    assert first_document["ok"] is True and second_document["ok"] is True
    assert first_document["result"]["already_deleted"] is False
    assert second_document["result"]["already_deleted"] is True
    assert catalog_state(Path(state.environment["ODCLI_E2E_CATALOG"])) is BackupState.DELETED
    assert not (state.runtime.artifact_root / f"leaf-{state.runtime.run_id}.zip").exists()
    return first, first_document


def _reset_or_shell(state: _State) -> tuple[Result, dict[str, Any]]:
    args = [*state.args, "--format", "json"] if state.case.path == ("shell",) else state.args
    first = _invoke(state, args)
    second = _invoke(state, args)
    assert first.exit_code == second.exit_code == 0, first.output
    document = json.loads(first.stdout)
    assert document["ok"] is True and json.loads(second.stdout) == document
    actual_args = (
        ["shell"]
        if state.case.path == ("shell",)
        else [
            "db",
            "reset-admin-password",
            "--format",
            "json",
        ]
    )
    actual_input = "exit()\n" if state.case.path == ("shell",) else None
    actual = _invoke(state, actual_args, input=actual_input)
    repeated = _invoke(state, actual_args, input=actual_input)
    assert actual.exit_code == repeated.exit_code == 0, actual.output
    if state.case.path == ("shell",):
        assert "Usage: odcli shell" not in actual.output
    else:
        actual_document = json.loads(actual.stdout)
        repeated_document = json.loads(repeated.stdout)
        assert actual_document["ok"] is True and repeated_document["ok"] is True
        target = state.runtime.topology.target_sentinel_database
        assert actual_document["result"]["database"] == target
        assert repeated_document["result"]["database"] == target
    return actual, document


def _module_test(state: _State) -> tuple[Result, dict[str, Any]]:
    module_args = [item for item in state.args if item != "--dry-run"]
    module = _invoke(state, module_args)
    tags = module_args[module_args.index("--test-tags") + 1]
    top_level = _invoke(
        state,
        ["test", "sale", "--tags", tags, "--allow-empty", "--format", "json"],
    )
    assert module.exit_code == top_level.exit_code == 0, module.output + top_level.output
    module_document = json.loads(module.stdout)
    top_document = json.loads(top_level.stdout)
    assert module_document["ok"] is True and top_document["ok"] is True
    assert module_document["result"]["modules"] == top_document["result"]["modules"]
    return module, module_document


def _init_monitoring(state: _State) -> tuple[Result, dict[str, Any]]:
    args = [item for item in state.args if item != "--dry-run"]
    first = _invoke(state, args)
    second = _invoke(state, args)
    assert first.exit_code == second.exit_code == 0, first.output
    first_document = json.loads(first.stdout)
    second_document = json.loads(second.stdout)
    assert first_document["ok"] is True and second_document["ok"] is True
    assert first_document["result"]["database"] == second_document["result"]["database"]
    return first, first_document


def _psql(state: _State) -> tuple[Result, None]:
    dry_run = _invoke(state, state.args)
    assert dry_run.exit_code == 0, dry_run.output
    result = _invoke(state, ["psql", "-c", "SELECT 1"])
    assert result.exit_code == 0, result.output
    return result, None


def _logs(state: _State) -> tuple[Result, None]:
    finite = _invoke(state, ["logs", "--tail", "1"])
    assert finite.exit_code == 0 and "redacted" in finite.stdout
    command = shutil.which("odcli")
    assert command is not None
    followed = subprocess.Popen(
        [command, "--project", str(state.project), "logs", "--follow", "--tail", "1"],
        cwd=state.project,
        env=state.environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.2)
        os.killpg(followed.pid, signal.SIGINT)
        followed_stdout, followed_stderr = followed.communicate(timeout=30)
    finally:
        if followed.poll() is None:
            os.killpg(followed.pid, signal.SIGKILL)
            followed.communicate(timeout=30)
    assert followed.returncode == 130
    assert "redacted" in followed_stdout
    assert_secret_free(
        {
            "argv": [command, "logs", "--follow"],
            "machine_output": followed_stdout,
            "pytest_output": followed_stderr,
        },
        state.evidence.secret_canary,
    )
    return finite, None


def _db_drop(state: _State) -> tuple[Result, dict[str, Any]]:
    owned_database = f"odcli_drop_{state.runtime.run_id.replace('-', '')[:20]}"
    backup_path = state.runtime.artifact_root / f"drop-{state.runtime.run_id}.zip"
    shutil.copy2(state.source_backup.path, backup_path)
    seed_backup(
        Path(state.environment["ODCLI_E2E_CATALOG"]),
        backup_path,
        database=state.runtime.topology.source_database,
    )
    restored = _invoke(
        state,
        ["db", "restore", BACKUP_ID, "--target", owned_database, "--yes", "--format", "json"],
    )
    assert restored.exit_code == 0, restored.output
    restored_document = json.loads(restored.stdout)
    assert restored_document["ok"] is True
    assert restored_document["result"]["restored_database"] == owned_database
    owned_args = ["db", "drop", owned_database, "--yes", "--format", "json"]
    first = _invoke(state, owned_args)
    assert first.exit_code == 0, first.output
    first_document = json.loads(first.stdout)
    assert first_document["ok"] is True
    repeated = _invoke(state, owned_args)
    assert repeated.exit_code != 0
    repeated_document, _ = observe_failure(
        repeated,
        runtime=state.runtime,
        evidence=state.evidence,
        name="leaf-db-drop-repeat",
        argv=owned_args,
    )
    assert repeated_document is not None
    foreign = _invoke(state, ["db", "drop", "postgres", "--yes", "--format", "json"])
    assert foreign.exit_code != 0
    foreign_document, _ = observe_failure(
        foreign, runtime=state.runtime, evidence=state.evidence, name="leaf-db-drop-foreign"
    )
    assert foreign_document is not None and foreign_document["error"]["code"] == "db_drop_failed"
    filestore = state.runtime.root / "target-data" / "filestore" / owned_database
    if filestore.exists():
        shutil.rmtree(filestore)
    return first, first_document


def _exec(state: _State) -> tuple[Result, dict[str, Any]]:
    args = ["exec", "-", "--format", "json"]
    success = _invoke(state, args, input="print('focused exec')\n")
    assert success.exit_code == 0, success.output
    assert json.loads(success.stdout)["ok"] is True
    failure = _invoke(state, args, input="raise RuntimeError('focused exec failure')\n")
    assert failure.exit_code != 0
    document, _ = observe_failure(
        failure, runtime=state.runtime, evidence=state.evidence, name="leaf-exec", argv=args
    )
    assert document is not None and document["error"]["code"] == "exec_user_code_failed"
    return failure, document


def _default(state: _State) -> tuple[Result, dict[str, Any] | None]:
    result = _invoke(state, state.args, input="raise RuntimeError('focused leaf failure')\n")
    if state.case.path not in {("psql",), ("logs",)}:
        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        assert document["ok"] is True
        return result, document
    return result, None


HANDLERS: dict[tuple[str, ...], Handler] = {
    ("backup", "delete"): _backup_delete,
    ("db", "reset-admin-password"): _reset_or_shell,
    ("shell",): _reset_or_shell,
    ("module", "test"): _module_test,
    ("db", "init-monitoring"): _init_monitoring,
    ("psql",): _psql,
    ("logs",): _logs,
    ("db", "drop"): _db_drop,
    ("exec",): _exec,
}


def invoke_case(
    case: PublicLeafCase,
    *,
    project: Path,
    runtime: E2ERuntime,
    evidence: FailureEvidence,
    record_property: object,
    source_backup: ArchiveIdentity,
) -> None:
    """Execute one canonical leaf through an explicit family handler."""
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
        seed_backup(Path(runtime.environment["ODCLI_E2E_CATALOG"]), backup_path)
        args = ["backup", "delete", BACKUP_ID, "--yes"]
    if case.classification not in {"native-passthrough", "jsonl-stream"}:
        args.extend(("--format", "json"))
    state = _State(
        case,
        args,
        project,
        runtime,
        evidence,
        {
            **runtime.environment,
            "ODCLI_E2E_KEEP_FAILED": "1",
            "ODCLI_TEST_MASTER_PASSWORD": evidence.secret_canary,
        },
        source_backup,
    )
    result, _ = HANDLERS.get(case.path, _default)(state)
    assert_secret_free(
        {"argv": args, "machine_output": result.stdout, "pytest_output": result.output},
        evidence.secret_canary,
    )
    for evidence_id in case.e2e_evidence:
        record(record_property, evidence_id, {"path": case.path, "exit_code": result.exit_code})
