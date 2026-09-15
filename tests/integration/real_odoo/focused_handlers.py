"""Focused canonical-leaf handlers grouped by scenario family."""

from __future__ import annotations

import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from click.testing import CliRunner, Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.models import BackupState, StartConfig
from odoo_instance_sdk.project import ProjectConfig
from tests.unit.test_cli_output_modes import PublicLeafCase

from .archive import ArchiveIdentity
from .cleanup import FailureEvidence, terminate_owned_process_group
from .conftest import E2ERuntime
from .failures import assert_secret_free
from .focused_support import (
    BACKUP_ID,
    catalog_state,
    invoke_in_registered_worktree,
    observe_failure,
    project_filestore,
    record,
    registered_worktree,
    seed_backup,
)


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
    selector = state.project / ".odcli" / "e2e-environment-id"
    context = ["--env", selector.read_text(encoding="ascii").strip()] if selector.is_file() else []
    return cast(
        "Result",
        invoke_in_registered_worktree(
            CliRunner(),
            cli,
            state.project,
            Path(state.environment["ODCLI_E2E_CATALOG"]),
            [*context, "--project", str(state.project), *args],
            state.environment,
            input=input,
        ),
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
    assert not (
        state.runtime.artifact_root / f"leaf-{state.runtime.run_id}-{state.case.path[-1]}.zip"
    ).exists()
    return first, first_document


def _reset_or_shell(state: _State) -> tuple[Result, dict[str, Any]]:
    args = [*state.args, "--format", "json"] if state.case.path == ("shell",) else state.args
    first = _invoke(state, args)
    second = _invoke(state, args)
    assert first.exit_code == second.exit_code == 0, first.output
    document = json.loads(first.stdout)
    second_document = json.loads(second.stdout)
    assert document["ok"] is True and second_document["ok"] is True
    # Independent plans have fresh Compose temp filenames and derived fingerprints.
    assert _stable_plan(second_document) == _stable_plan(document)
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
    module_args[module_args.index("test") + 1] = "odcli_e2e_probe"
    tags_index = module_args.index("--test-tags") + 1
    module_args[tags_index] = "/odcli_e2e_probe"
    module = _invoke(state, module_args)
    tags = module_args[tags_index]
    top_level = _invoke(
        state,
        ["test", "odcli_e2e_probe", "--tags", tags, "--allow-empty", "--format", "json"],
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
    first_result = first_document["result"]
    second_result = second_document["result"]
    assert set(first_result) == {"installed", "already_present", "skipped"}
    assert set(second_result) == {"installed", "already_present", "skipped"}
    assert first_result["installed"] == second_result["already_present"]
    assert first_result["already_present"] == second_result["installed"] == []
    assert first_result["skipped"] == second_result["skipped"]
    return first, first_document


def _psql(state: _State) -> tuple[Result, None]:
    dry_run = _invoke(state, state.args)
    assert dry_run.exit_code == 0, dry_run.output
    result = _invoke(state, ["psql", "-c", "SELECT 1"])
    assert result.exit_code == 0, result.output
    return result, None


def _logs(state: _State) -> tuple[Result, None]:
    config = ProjectConfig.load(state.project)
    assert config.source_config is not None
    logfile = StartConfig.from_odoo_config(state.project / config.source_config).logfile
    assert logfile is not None
    worktree = registered_worktree(Path(state.environment["ODCLI_E2E_CATALOG"]), state.project)
    # Runtime initialization and preceding leaves append real Odoo log lines.
    with (worktree / logfile).open("a", encoding="utf-8") as stream:
        stream.write("INFO focused logs probe; secret=redacted\n")
    finite = _invoke(state, ["logs", "--tail", "1"])
    assert finite.exit_code == 0 and "redacted" in finite.stdout
    command = shutil.which("odcli")
    assert command is not None
    followed = subprocess.Popen(
        [
            command,
            "--env",
            (state.project / ".odcli" / "e2e-environment-id").read_text(encoding="ascii").strip(),
            "--project",
            str(state.project),
            "logs",
            "--follow",
            "--tail",
            "1",
        ],
        cwd=state.project,
        env=state.environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert followed.stdout is not None
        with selectors.DefaultSelector() as ready:
            ready.register(followed.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=30), "logs --follow did not become ready"
            first_line = followed.stdout.readline()
        assert "redacted" in first_line
        os.killpg(followed.pid, signal.SIGINT)
        followed_stdout, followed_stderr = followed.communicate(timeout=30)
        followed_stdout = first_line + followed_stdout
    finally:
        terminate_owned_process_group(followed.pid)
        if followed.poll() is None:
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
    backup_path = state.runtime.root / f"drop-{state.runtime.run_id}.zip"
    shutil.copy2(state.source_backup.path, backup_path)
    project_source = ProjectConfig.load(state.project).test_instance
    assert project_source is not None
    seed_backup(
        Path(state.environment["ODCLI_E2E_CATALOG"]),
        backup_path,
        database=project_source.database,
        source_base_url=project_source.base_url,
    )
    restored = _invoke(
        state,
        ["db", "restore", BACKUP_ID, "--target", owned_database, "--yes", "--format", "json"],
    )
    assert restored.exit_code == 0, restored.output
    restored_document = json.loads(restored.stdout)
    assert restored_document["ok"] is True
    assert restored_document["result"]["restored_database"] == owned_database
    # Restore promotes this run-owned database to the project default.
    owned_args = [
        "db",
        state.case.path[-1],
        owned_database,
        "--force-default",
        "--yes",
        "--format",
        "json",
    ]
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
    filestore = project_filestore(state.project, owned_database)
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


def _stable_plan(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_plan(item)
            for key, item in value.items()
            if key not in {"wrapper_nonce", "fingerprint"}
        }
    if isinstance(value, list):
        return [_stable_plan(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"\.compose-[0-9a-f]{32}\.yaml\.tmp", ".compose-<nonce>.yaml.tmp", value)
    return value


HANDLERS: dict[tuple[str, ...], Handler] = {
    ("backup", "delete"): _backup_delete,
    ("backup", "rm"): _backup_delete,
    ("db", "reset-admin-password"): _reset_or_shell,
    ("shell",): _reset_or_shell,
    ("module", "test"): _module_test,
    ("db", "init-monitoring"): _init_monitoring,
    ("psql",): _psql,
    ("logs",): _logs,
    ("db", "drop"): _db_drop,
    ("db", "rm"): _db_drop,
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
    catalog_path: Path | None = None,
) -> None:
    """Execute one canonical leaf through an explicit family handler."""
    args = list(case.args)
    if "demo" in args:
        args[args.index("demo")] = runtime.topology.target_sentinel_database
    if case.requires_dry_run and "--dry-run" not in args:
        args.append("--dry-run")
    if case.path == ("logs",):
        args = ["logs", "--tail", "1"]
    if case.path in {("db", "drop"), ("db", "rm")}:
        args = ["db", "drop", runtime.topology.target_sentinel_database, "--yes"]
    if case.path in {("backup", "delete"), ("backup", "rm")}:
        backup_path = runtime.artifact_root / f"leaf-{runtime.run_id}-{case.path[-1]}.zip"
        with zipfile.ZipFile(backup_path, "w") as archive:
            archive.writestr("manifest.json", '{"db_name": "demo"}')
            archive.writestr("dump.sql", "-- database: demo\n")
            archive.writestr("filestore/demo/blob", b"fixture")
        seed_backup(catalog_path or Path(runtime.environment["ODCLI_E2E_CATALOG"]), backup_path)
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
            **({"HOME": str(catalog_path.parent.parent)} if catalog_path is not None else {}),
            **({"ODCLI_E2E_CATALOG": str(catalog_path)} if catalog_path else {}),
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
