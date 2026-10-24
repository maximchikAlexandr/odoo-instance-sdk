from __future__ import annotations

import ast
import json
import os
import sys
import venv
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    PreparedStep,
    ProcessResult,
    RecordingExecutor,
    StepObserver,
    SubprocessExecutor,
)
from odoo_instance_sdk.internal.project_manifest import write_manifest
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.project import (
    PostgresProjectConfig,
    ProjectConfig,
    TestInstanceProjectConfig as _TestInstanceProjectConfig,
)
from odoo_instance_sdk.project_init import init_project, init_project_command
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

# This is deliberately kept next to the focused matrix rather than in the
# private E2E repository.  It makes the 27 delta scenarios auditable even when
# that repository is unavailable to the implementation workspace.
SCENARIO_EVIDENCE: dict[str, str] = {
    "project-init/omitted-venv": "test_integrated_init_omission_preserves_legacy_plan",
    "project-init/fresh-ordered-preparation": "test_integrated_init_captures_manifest_before_shared_preparation",
    "project-init/missing-python": "test_integrated_init_requires_python_before_manifest_mutation",
    "project-init/prepared-interpreter-bootstrap": "test_integrated_init_captures_concrete_bootstrap_interpreter",
    "project-init/empty-requirements": "test_integrated_init_empty_inputs_skip_dependency_steps",
    "project-init/create-or-validation-failure": "test_integrated_init_process_failures_stop_before_persistence",
    "project-init/compile-or-sync-failure": "test_integrated_init_process_failures_stop_before_persistence",
    "project-init/publication-failure": "test_integrated_init_publication_failure_restores_manifest",
    "project-init/bootstrap-failure": "tests/unit/test_init_self_contained.py::test_init_bootstrap_failure_prevents_successful_result",
    "project-init/identical-retry": "tests/unit/test_init_self_contained.py::test_complete_compose_retry_skips_tmp_recreation",
    "project-init/damaged-venv": "tests/unit/project/test_prepare_venv.py::test_prepare_existing_empty_venv_persists_interpreter_after_success",
    "project-init/missing-venv-changed-selector": "test_integrated_init_missing_venv_captures_selector_and_dependencies",
    "project-init/existing-venv-changed-selector": "test_integrated_init_existing_venv_is_not_recreated",
    "project-init/headless-replacement-confirmation": "test_integrated_init_headless_changed_selector_requires_yes",
    "development-environment/missing-venv-created": "test_integrated_init_missing_venv_captures_selector_and_dependencies",
    "development-environment/existing-venv-refreshed": "test_integrated_init_existing_venv_is_not_recreated",
    "development-environment/empty-inputs": "test_integrated_init_empty_inputs_skip_dependency_steps",
    "development-environment/process-failure-state": "test_integrated_init_process_failures_stop_before_persistence",
    "development-environment/persistence-rollback": "test_integrated_init_publication_failure_restores_manifest",
    "development-environment/shared-owner": "test_integrated_init_shares_standalone_preparation_steps",
    "development-environment/dry-run": "test_integrated_init_dry_run_is_inert_in_all_output_modes",
    "cli-odcli/omission-compatibility": "test_integrated_init_omission_preserves_legacy_plan",
    "cli-odcli/delegation": "test_init_help_exposes_opt_in_venv",
    "cli-odcli/dry-run-output": "test_integrated_init_dry_run_is_inert_in_all_output_modes",
    "cli-odcli/existing-refresh": "test_integrated_init_existing_manifest_refreshes",
    "cli-odcli/failure-transports": "test_integrated_init_failure_uses_all_output_modes",
    "cli-odcli/standalone-recovery": "tests/unit/project/test_prepare_venv.py::test_prepare_validates_existing_venv_before_refresh",
}

EXPECTED_SCENARIO_KEYS = frozenset(
    {
        "project-init/omitted-venv",
        "project-init/fresh-ordered-preparation",
        "project-init/missing-python",
        "project-init/prepared-interpreter-bootstrap",
        "project-init/empty-requirements",
        "project-init/create-or-validation-failure",
        "project-init/compile-or-sync-failure",
        "project-init/publication-failure",
        "project-init/bootstrap-failure",
        "project-init/identical-retry",
        "project-init/damaged-venv",
        "project-init/missing-venv-changed-selector",
        "project-init/existing-venv-changed-selector",
        "project-init/headless-replacement-confirmation",
        "development-environment/missing-venv-created",
        "development-environment/existing-venv-refreshed",
        "development-environment/empty-inputs",
        "development-environment/process-failure-state",
        "development-environment/persistence-rollback",
        "development-environment/shared-owner",
        "development-environment/dry-run",
        "cli-odcli/omission-compatibility",
        "cli-odcli/delegation",
        "cli-odcli/dry-run-output",
        "cli-odcli/existing-refresh",
        "cli-odcli/failure-transports",
        "cli-odcli/standalone-recovery",
    }
)


def _venv_interpreter(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _selector_resolves(selector: str) -> bool:
    if "::" not in selector:
        return callable(globals().get(selector))
    path_text, function_name = selector.split("::", 1)
    path = Path(path_text)
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
        for node in ast.walk(tree)
    )


def _project(tmp_path: Path) -> ProjectConfig:
    (tmp_path / ".odcli").mkdir()
    venv = tmp_path / ".venv"
    interpreter = _venv_interpreter(venv)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text(
        "home = /runtime\nimplementation = CPython\nversion_info = 3.12\nuv = 0.13.0\n",
        encoding="utf-8",
    )
    return ProjectConfig(repository_root=tmp_path, python="3.12")


def _bare_project(tmp_path: Path, *, requirements: bool = False) -> ProjectConfig:
    (tmp_path / ".odcli").mkdir()
    if requirements:
        (tmp_path / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")
    return ProjectConfig(
        repository_root=tmp_path,
        python="3.12",
        requirements=("requirements.txt",) if requirements else (),
    )


def _materialize_venv(step: PreparedStep) -> None:
    venv = Path(step.argv[2])
    interpreter = _venv_interpreter(venv)
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.write_text("placeholder\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text(
        "home = /runtime\nimplementation = CPython\nversion_info = 3.12\nuv = 0.13.0\n",
        encoding="utf-8",
    )


def _process_result(step: PreparedStep, *, returncode: int = 0, stderr: str = "") -> ProcessResult:
    return ProcessResult(
        argv=step.argv,
        returncode=returncode,
        stdout="tmp\t101\nodoo\t102\npostgres\t1\n"
        if step.step_id == "init.bootstrap.databases"
        else "",
        stderr=stderr,
        duration=0.0,
        cwd=step.cwd,
        environment=step.environment,
    )


def _fake_preparation_result(
    failed_step: str | None = None,
) -> Callable[[PreparedProcess], ProcessResult]:
    def result(step: PreparedProcess) -> ProcessResult:
        captured = cast("PreparedStep", step)
        if captured.step_id == "prepare-venv.venv":
            _materialize_venv(captured)
        if "compile" in captured.step_id:
            Path(captured.argv[-1]).write_text("prepared-lock\n", encoding="utf-8")
        return _process_result(
            captured,
            returncode=1 if captured.step_id == failed_step else 0,
            stderr=f"{failed_step} failed" if captured.step_id == failed_step else "",
        )

    return result


def _skip_bootstrap_process_steps(context: object, plan: object) -> None:
    context.skip(plan.probe_step.step_id)  # type: ignore[attr-defined]
    if plan.create_step is not None:  # type: ignore[attr-defined]
        context.skip(plan.create_step.step_id)  # type: ignore[attr-defined]
    context.skip(plan.spawn_step.step_id)  # type: ignore[attr-defined]
    context.skip(plan.ready_step.step_id)  # type: ignore[attr-defined]


def test_scenario_evidence_covers_every_delta_case() -> None:
    assert set(SCENARIO_EVIDENCE) == EXPECTED_SCENARIO_KEYS
    assert all(_selector_resolves(selector) for selector in SCENARIO_EVIDENCE.values())


def test_integrated_init_captures_manifest_before_shared_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )

    assert [step.step_id for step in command.plan.steps] == [
        "init",
        "prepare-venv.validate",
        "prepare-venv.origin",
        "prepare-venv.manifest",
    ]
    result = command.run()

    interpreter = str(_venv_interpreter(tmp_path / ".venv").resolve())
    assert result["interpreter"] == interpreter
    assert result["requested_python"] == "3.12"
    assert ProjectConfig.load(tmp_path).python == Path(interpreter)
    assert [step.step_id for step in executor.executed] == ["prepare-venv.validate"]


def test_integrated_init_omission_preserves_legacy_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = init_project_command(
        tmp_path, config, postgres_allocated=False, allow_partial=True, no_input=True
    )

    assert [step.step_id for step in command.plan.steps] == ["init"]
    command.run()
    assert not executor.executed


def test_integrated_init_resolves_remote_database_names_in_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".odcli").mkdir()
    config = ProjectConfig(
        repository_root=tmp_path,
        python="3.12",
        test_instance=_TestInstanceProjectConfig(
            base_url="http://example.test:8069",
            git_branch="main",
        ),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.project_init.fetch_remote_database_names_for_init",
        lambda _config: ["resolved_db"],
    )

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        local_config=True,
        allow_partial=True,
        no_input=True,
    )

    assert command.plan.steps[0].step_id == "init.remote_database_names"
    result = command.run()
    assert result["python"] == "3.12"


def test_integrated_init_missing_venv_captures_selector_and_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _bare_project(tmp_path, requirements=True)
    executor = RecordingExecutor(result_factory=_fake_preparation_result())
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )

    assert [step.step_id for step in command.plan.steps] == [
        "init",
        "prepare-venv.venv",
        "prepare-venv.validate",
        "prepare-venv.dependencies.compile",
        "prepare-venv.dependencies.install",
        "prepare-venv.origin",
        "prepare-venv.manifest",
        "prepare-venv.dependencies.lock",
    ]
    create = next(
        step for step in command.plan.process_steps if step.step_id == "prepare-venv.venv"
    )
    assert create.argv[-2:] == ("--python", "3.12")
    result = command.run()

    assert result["requested_python"] == "3.12"
    assert ProjectConfig.load(tmp_path).python == _venv_interpreter(tmp_path / ".venv").resolve()
    assert (tmp_path / ".odcli" / "requirements.lock").read_text() == "prepared-lock\n"


def test_integrated_init_existing_venv_is_not_recreated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)
    changed = ProjectConfig(repository_root=tmp_path, python="3.13")
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = init_project_command(
        tmp_path,
        changed,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )

    assert "prepare-venv.venv" not in [step.step_id for step in command.plan.steps]
    result = command.run()
    assert result["requested_python"] == "3.13"
    assert result["interpreter"] == str(_venv_interpreter(tmp_path / ".venv").resolve())
    assert [step.step_id for step in executor.executed] == ["prepare-venv.validate"]


def test_integrated_init_empty_inputs_skip_dependency_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )
    assert not any("dependencies" in step.step_id for step in command.plan.steps)
    command.run()
    assert not (tmp_path / ".odcli" / "requirements.lock").exists()


@pytest.mark.parametrize(
    "failed_step",
    [
        "prepare-venv.venv",
        "prepare-venv.validate",
        "prepare-venv.dependencies.compile",
        "prepare-venv.dependencies.install",
    ],
)
def test_integrated_init_process_failures_stop_before_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_step: str
) -> None:
    config = _bare_project(tmp_path, requirements="dependencies" in failed_step)
    catalog_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_: catalog_path
    )
    project_id = f"project_{repo_key(tmp_path, tmp_path / '.git')}"
    catalog = BackupCatalog(db_path=catalog_path)
    catalog._register_project(project_id, tmp_path, tmp_path / ".git")
    assert catalog.forget_project(project_id)
    catalog.close()
    executor = RecordingExecutor(result_factory=_fake_preparation_result(failed_step))
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )
    with pytest.raises(ConfigError):
        command.run()

    assert executor.executed[-1].step_id == failed_step
    catalog = BackupCatalog(db_path=catalog_path)
    assert catalog.get_project(project_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()
    assert not (tmp_path / ".odcli" / "primary-venv-origin.json").exists()
    assert ProjectConfig.load(tmp_path).python == "3.12"


def test_integrated_init_publication_failure_restores_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _project(tmp_path)
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    def fail_publish(_stage: object) -> None:
        raise OSError("injected integrated origin publication failure")

    monkeypatch.setattr("odoo_instance_sdk.internal.primary_venv.publish_origin", fail_publish)
    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )
    with pytest.raises(OSError, match="integrated origin publication"):
        command.run()

    assert ProjectConfig.load(tmp_path).python == "3.12"
    assert not (tmp_path / ".odcli" / "primary-venv-origin.json").exists()


def test_integrated_init_captures_concrete_bootstrap_interpreter(tmp_path: Path) -> None:
    config = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="3.12",
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=55432),
    )
    (tmp_path / ".odcli").mkdir()
    venv = tmp_path / ".venv"
    interpreter = _venv_interpreter(venv)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("version_info = 3.12\n", encoding="utf-8")

    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        local_config=True,
        no_input=True,
        venv=".venv",
    )
    bootstrap = next(
        step for step in command.plan.process_steps if step.step_id == "init.bootstrap.tmp"
    )
    assert bootstrap.argv[0] == str(interpreter.resolve())
    assert bootstrap.argv[0] != "3.12"


def test_integrated_compose_defers_password_and_repairs_under_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.dbprep import bootstrap
    from odoo_instance_sdk.internal.dbprep.bootstrap import BootstrapOutcome
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    (tmp_path / ".odcli").mkdir()
    config = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="3.12",
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=55432),
    )
    generated = tmp_path / ".odcli" / "odoo.conf"
    generated.write_text("[options]\n", encoding="utf-8")
    cluster = PostgresCluster._from_config(config, repository_root=tmp_path, compose_runner=None)
    password_file = cluster.password_file
    password_file.unlink(missing_ok=True)
    monkeypatch.setattr(PostgresCluster, "_ensure_running_impl", lambda *args, **kwargs: None)
    executor = RecordingExecutor(result_factory=_fake_preparation_result())
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    def skip_bootstrap(context: object, plan: object) -> BootstrapOutcome:
        _skip_bootstrap_process_steps(context, plan)
        return BootstrapOutcome.ALREADY_READY

    monkeypatch.setattr(bootstrap, "run_bootstrap_tmp", skip_bootstrap)
    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        local_config=True,
        no_input=True,
        dry_run=False,
        repair_generated_config=True,
        venv=".venv",
    )

    plan_ids = [step.step_id for step in command.plan.steps]
    assert "init.generated-config.repair" in plan_ids
    assert plan_ids.index("init.compose.password") < plan_ids.index("init.generated-config.repair")
    assert plan_ids.index("init.generated-config.repair") < plan_ids.index("init.compose.config")
    assert plan_ids.index("init.compose.config") < plan_ids.index("prepare-venv.validate")
    assert "init.bootstrap.tmp.lock" in plan_ids
    assert plan_ids.index("init.bootstrap.tmp.lock") < plan_ids.index("init.bootstrap.tmp.verify")
    assert not password_file.exists()
    command.run()
    assert generated.is_file()
    assert password_file.is_file()
    assert "deferred-password" not in generated.read_text(encoding="utf-8")


def test_integrated_compose_redacts_materialized_password_from_bootstrap_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.dbprep import bootstrap
    from odoo_instance_sdk.internal.dbprep.bootstrap import BootstrapFailedError, BootstrapOutcome
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    (tmp_path / ".odcli").mkdir()
    venv_path = tmp_path / ".venv"
    venv.EnvBuilder(with_pip=False, clear=True).create(venv_path)
    interpreter = _venv_interpreter(venv_path)
    if os.name != "nt":
        interpreter.unlink()
        interpreter.symlink_to(sys.executable)
    bootstrap_script = tmp_path / "odoo-bin"
    bootstrap_script.write_text(
        "import os, sys\n"
        "value = os.environ['PGPASSWORD']\n"
        "print(value)\n"
        "print(value, file=sys.stderr)\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    config = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=bootstrap_script,
        python="3.12",
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=55432),
    )
    cluster = PostgresCluster._from_config(config, repository_root=tmp_path, compose_runner=None)
    cluster.password_file.unlink(missing_ok=True)
    monkeypatch.setattr(PostgresCluster, "_ensure_running_impl", lambda *args, **kwargs: None)

    class _BootstrapExecutor(RecordingExecutor):
        def __init__(self) -> None:
            super().__init__(result_factory=_fake_preparation_result())
            self._dispatch = {
                "init.bootstrap.tmp": self._execute_real_child,
                "default": self._execute_recorded_step,
            }

        def _execute_real_child(
            self,
            step: PreparedProcess,
            *,
            observer: StepObserver | None = None,
            observe_output: bool = False,
        ) -> ProcessResult:
            return SubprocessExecutor().execute(
                step,
                observer=observer,
                observe_output=observe_output,
            )

        def _execute_recorded_step(
            self,
            step: PreparedProcess,
            *,
            observer: StepObserver | None = None,
            observe_output: bool = False,
        ) -> ProcessResult:
            return cast(
                "ProcessResult",
                RecordingExecutor.execute(
                    self,
                    step,
                    observer=observer,
                    observe_output=observe_output,
                ),
            )

        def execute(
            self,
            step: PreparedProcess,
            *,
            observer: StepObserver | None = None,
            observe_output: bool = False,
        ) -> ProcessResult:
            captured = cast("PreparedStep", step)
            return self._dispatch.get(captured.step_id, self._dispatch["default"])(
                captured,
                observer=observer,
                observe_output=observe_output,
            )

    executor = _BootstrapExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    def inspect_bootstrap(context: object, plan: object) -> BootstrapOutcome:
        assert plan.create_step is not None  # type: ignore[attr-defined]
        result = context.process_prepared(plan.spawn_step)  # type: ignore[attr-defined]
        assert result.returncode == 7
        assert result.stdout.replace(r"\x0d", "\r").splitlines() == ["<redacted>"]
        assert result.stderr.replace(r"\x0d", "\r").splitlines() == ["<redacted>"]
        context.skip(plan.probe_step.step_id)  # type: ignore[attr-defined]
        context.skip(plan.create_step.step_id)  # type: ignore[attr-defined]
        context.skip(plan.ready_step.step_id)  # type: ignore[attr-defined]
        raise BootstrapFailedError(f"bootstrap output: {result.stdout} {result.stderr}")

    monkeypatch.setattr(bootstrap, "run_bootstrap_tmp", inspect_bootstrap)
    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        local_config=True,
        no_input=True,
        venv=".venv",
    )
    with pytest.raises(BootstrapFailedError) as error_info:
        command.run()

    password = cluster.password_file.read_text(encoding="utf-8").strip()
    assert str(error_info.value).replace(r"\x0d", "\r").splitlines() == [
        "bootstrap output: <redacted>",
        " <redacted>",
    ]
    assert password not in str(error_info.value)
    generated = tmp_path / ".odcli" / "odoo.conf"
    assert password
    assert password in generated.read_text(encoding="utf-8")
    assert "deferred-password" not in generated.read_text(encoding="utf-8")


def test_omitted_venv_keeps_legacy_compose_plan_and_capture_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.dbprep import bootstrap
    from odoo_instance_sdk.internal.dbprep.bootstrap import BootstrapOutcome
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    (tmp_path / ".odcli").mkdir()
    config = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="3.12",
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=55432),
    )
    cluster = PostgresCluster._from_config(config, repository_root=tmp_path, compose_runner=None)
    cluster.password_file.unlink(missing_ok=True)
    monkeypatch.setattr(PostgresCluster, "_ensure_running_impl", lambda *args, **kwargs: None)
    executor = RecordingExecutor(result_factory=_fake_preparation_result())
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    def skip_bootstrap(context: object, plan: object) -> BootstrapOutcome:
        assert plan.create_step is None  # type: ignore[attr-defined]
        _skip_bootstrap_process_steps(context, plan)
        return BootstrapOutcome.ALREADY_READY

    monkeypatch.setattr(bootstrap, "run_bootstrap_tmp", skip_bootstrap)
    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        local_config=True,
        no_input=True,
    )

    plan_ids = [step.step_id for step in command.plan.steps]
    assert "init.compose.password" not in plan_ids
    assert "init.compose.config" not in plan_ids
    assert "init.bootstrap.tmp.lock" not in plan_ids
    assert "init.bootstrap.tmp" in plan_ids
    assert cluster.password_file.is_file()
    command.run()
    assert cluster.password_file.is_file()
    assert (tmp_path / ".odcli" / "odoo.conf").is_file()


def test_integrated_compose_writes_real_config_before_venv_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.dbprep import bootstrap
    from odoo_instance_sdk.internal.dbprep.bootstrap import BootstrapOutcome
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    (tmp_path / ".odcli").mkdir()
    config = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="3.12",
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=55432),
    )
    generated = tmp_path / ".odcli" / "odoo.conf"
    generated.write_text("[options]\n", encoding="utf-8")
    cluster = PostgresCluster._from_config(config, repository_root=tmp_path, compose_runner=None)
    cluster.password_file.unlink(missing_ok=True)
    monkeypatch.setattr(PostgresCluster, "_ensure_running_impl", lambda *args, **kwargs: None)

    def skip_bootstrap(context: object, plan: object) -> BootstrapOutcome:
        _skip_bootstrap_process_steps(context, plan)
        return BootstrapOutcome.ALREADY_READY

    monkeypatch.setattr(bootstrap, "run_bootstrap_tmp", skip_bootstrap)
    executor = RecordingExecutor(result_factory=_fake_preparation_result("prepare-venv.validate"))
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)
    command = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        local_config=True,
        no_input=True,
        venv=".venv",
        repair_generated_config=True,
    )
    with pytest.raises(ConfigError, match=r"prepare-venv\.validate failed"):
        command.run()

    password = cluster.password_file.read_text(encoding="utf-8").strip()
    assert password
    assert password in generated.read_text(encoding="utf-8")
    assert "deferred-password" not in generated.read_text(encoding="utf-8")


def test_integrated_init_shares_standalone_preparation_steps(tmp_path: Path) -> None:
    config = _project(tmp_path)
    integrated = init_project_command(
        tmp_path,
        config,
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        venv=".venv",
    )
    from odoo_instance_sdk.project_init import prepare_venv_command

    standalone = prepare_venv_command(config, python="3.12", venv=".venv")
    assert integrated.plan.steps[1:] == standalone.plan.steps


def _read_rich_dry_run(output: str) -> dict[str, bool]:
    return {"dry_run": "dry run" in output.lower() or "dry_run" in output.lower()}


def _read_json_dry_run(output: str) -> dict[str, bool]:
    return {"dry_run": json.loads(output)["dry_run"]}


def _read_toon_dry_run(output: str) -> dict[str, bool]:
    from toon import DecodeOptions, decode

    return {"dry_run": decode(output, DecodeOptions(indent=2, strict=True))["dry_run"]}


@pytest.mark.parametrize(
    ("output_mode", "read_document"),
    [
        ("rich", _read_rich_dry_run),
        ("json", _read_json_dry_run),
        ("toon", _read_toon_dry_run),
    ],
)
def test_integrated_init_dry_run_is_inert_in_all_output_modes(
    tmp_path: Path, output_mode: str, read_document: Callable[[str], dict[str, bool]]
) -> None:
    result = CliRunner().invoke(
        __import__("odoo_instance_sdk.cli", fromlist=["cli"]).cli,
        [
            "init",
            "--no-input",
            "--allow-partial",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "3.12",
            "--venv",
            ".venv",
            "--dry-run",
            "--format",
            output_mode,
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "dry_run" in result.output.lower() or "dry run" in result.output.lower()
    assert not (tmp_path / ".odcli" / "project.toml").exists()
    assert not (tmp_path / ".venv").exists()
    assert read_document(result.output)["dry_run"] is True


def test_integrated_init_existing_manifest_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".odcli").mkdir()
    config = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="3.12",
    )
    write_manifest(tmp_path, config)
    venv = tmp_path / ".venv"
    interpreter = _venv_interpreter(venv)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("version_info = 3.12\n", encoding="utf-8")
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    result = CliRunner().invoke(
        __import__("odoo_instance_sdk.cli", fromlist=["cli"]).cli,
        [
            "init",
            "--no-input",
            "--allow-partial",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "3.12",
            "--venv",
            ".venv",
            "--format",
            "json",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True
    assert [step.step_id for step in executor.executed] == ["prepare-venv.validate"]


def test_integrated_init_headless_changed_selector_requires_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".odcli").mkdir()
    config = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        python="3.12",
    )
    write_manifest(tmp_path, config)
    result = CliRunner().invoke(
        __import__("odoo_instance_sdk.cli", fromlist=["cli"]).cli,
        [
            "init",
            "--no-input",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "3.13",
            "--venv",
            ".venv",
            "--format",
            "json",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "manifest exists and differs" in result.output
    assert ProjectConfig.load(tmp_path).python == "3.12"


@pytest.mark.parametrize("output_mode", ["rich", "json", "toon"])
def test_integrated_init_failure_uses_all_output_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output_mode: str
) -> None:
    executor = RecordingExecutor(result_factory=_fake_preparation_result("prepare-venv.venv"))
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)
    result = CliRunner().invoke(
        __import__("odoo_instance_sdk.cli", fromlist=["cli"]).cli,
        [
            "init",
            "--no-input",
            "--allow-partial",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "3.12",
            "--venv",
            ".venv",
            "--format",
            output_mode,
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "prepare-venv.venv" in result.output
    assert "ok: true" not in result.output.lower()


def test_integrated_init_requires_python_before_manifest_mutation(tmp_path: Path) -> None:
    (tmp_path / ".odcli").mkdir()
    config = ProjectConfig(repository_root=tmp_path)

    with pytest.raises(ConfigError, match="effective Python selector"):
        init_project_command(
            tmp_path,
            config,
            postgres_allocated=False,
            allow_partial=True,
            no_input=True,
            venv=".venv",
        )

    assert not (tmp_path / ".odcli" / "project.toml").exists()


def test_init_help_exposes_opt_in_venv() -> None:
    from click.testing import CliRunner

    from odoo_instance_sdk.cli import cli

    result = CliRunner().invoke(cli, ["init", "--help"])
    assert result.exit_code == 0, result.output
    assert "--venv" in result.output


def test_integrated_init_dry_run_is_inert_and_contains_preparation_plan(tmp_path: Path) -> None:
    import json

    from click.testing import CliRunner

    from odoo_instance_sdk.cli import cli

    result = CliRunner().invoke(
        cli,
        [
            "init",
            "--no-input",
            "--allow-partial",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--python",
            "3.12",
            "--venv",
            ".venv",
            "--dry-run",
            "--format",
            "json",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    plan_ids = [step["step_id"] for step in document["result"]["plan"]["steps"]]
    assert plan_ids[:2] == ["init", "prepare-venv.venv"]
    assert not (tmp_path / ".odcli" / "project.toml").exists()
    assert not (tmp_path / ".venv").exists()


def test_public_init_dry_run_returns_redacted_plan_projection(tmp_path: Path) -> None:
    (tmp_path / ".odcli").mkdir()
    result = init_project(
        tmp_path,
        ProjectConfig(repository_root=tmp_path, python="3.12"),
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        dry_run=True,
        venv=".venv",
    )

    plan = cast("dict[str, object]", result["plan"])
    steps = cast("list[dict[str, str]]", plan["steps"])
    assert [step["step_id"] for step in steps] == [
        "init",
        "prepare-venv.venv",
        "prepare-venv.validate",
        "prepare-venv.origin",
        "prepare-venv.manifest",
    ]
    assert not (tmp_path / ".odcli" / "project.toml").exists()
    assert not (tmp_path / ".venv").exists()


def test_public_init_without_venv_keeps_legacy_dry_run_shape(tmp_path: Path) -> None:
    (tmp_path / ".odcli").mkdir()
    result = init_project(
        tmp_path,
        ProjectConfig(repository_root=tmp_path, python="3.12"),
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
        dry_run=True,
    )

    assert "plan" not in result
    assert result["python"] == "3.12"
