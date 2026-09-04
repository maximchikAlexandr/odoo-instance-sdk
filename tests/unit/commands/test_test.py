from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

from click.testing import CliRunner

from odoo_instance_sdk import OdooClient
from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.context import ResolvedContext, RuntimeSource
from odoo_instance_sdk.commands.test import (
    project_execution_result,
    resolve_module_test_selection,
    run_module_tests,
)
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.test_selection import _ChangedSelection, _ChangedSelectionError
from odoo_instance_sdk.models import OdooTestResult, OdooTestSpec, StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance

if TYPE_CHECKING:
    import pytest


def _environment(worktree: Path) -> SimpleNamespace:
    return SimpleNamespace(
        id="env-1",
        name="demo",
        worktree_path=str(worktree),
        base_ref="main",
        http_interface="127.0.0.1",
        http_port=18069,
    )


def _instance(worktree: Path) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            start_config=StartConfig(addons_path=["addons"]),
            default_cwd=worktree,
        )
    )


def _resolved_context(source: object, instance: object) -> ResolvedContext:
    return ResolvedContext(
        client=cast("OdooClient", None),
        source=cast("RuntimeSource", source),
        instance=cast("OdooInstance", instance),
        provenance="explicit",
    )


def _addon(worktree: Path, name: str) -> None:
    module = worktree / "addons" / name
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n")
    (module / "tests").mkdir()
    (module / "tests" / "test_order.py").write_text("# test\n")


def _result_command(value: object) -> Command[object]:
    return Command.create(ExecutionPlan(), lambda _context: value)


def test_invalid_combinations_fail_before_environment_resolution(tmp_path: Path) -> None:
    cases = (
        ["test", "sale", "--changed"],
        ["test", "--base", "main"],
        ["test", "--dry-run"],
        ["test", "test_order.py", "--tags", "/sale"],
    )
    with patch("odoo_instance_sdk.commands.test.cli_context.ready_instance") as ready:
        for args in cases:
            result = CliRunner().invoke(cli, args)
            assert result.exit_code == 2, args
            assert result.stdout == "", args
    ready.assert_not_called()


def test_executed_result_contains_only_native_execution_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _addon(tmp_path, "sale")
    monkeypatch.chdir(tmp_path)
    env = _environment(tmp_path)
    instance = _instance(tmp_path)
    typed = OdooTestResult(
        counts={"tests": 2, "successful": 2, "failed": 0, "errors": 0, "skipped": 0},
        failures=False,
        zero_tests=False,
        exit_code=0,
    )
    calls: list[str] = []

    def preflight(*_args: object, **_kwargs: object) -> None:
        calls.append("preflight")

    def runner(*_args: object, **_kwargs: object) -> Command[object]:
        calls.append("runner")
        return _result_command((typed, None))

    with (
        patch(
            "odoo_instance_sdk.commands.test.cli_context.ready_instance",
            return_value=_resolved_context(env, instance),
        ),
        patch("odoo_instance_sdk.commands.test.preflight_installed_modules", side_effect=preflight),
        patch("odoo_instance_sdk.commands.test.run_odoo_tests_command", side_effect=runner),
    ):
        result = CliRunner().invoke(
            cli, ["test", "sale", "--tags", ":TestSale.test_order", "--format", "json"]
        )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    payload = document["result"]
    assert payload["modules"] == ["sale"]
    assert payload["test_tags"] == ":TestSale.test_order"
    assert payload["counts"] == typed.counts
    assert payload["failures"] is False
    assert payload["zero_tests"] is False
    assert calls == ["preflight", "runner"]


def test_changed_dry_run_omits_execution_fields_and_does_not_run(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    instance = _instance(tmp_path)
    plan = SimpleNamespace(
        base_source="explicit",
        requested_base="main",
        resolved_base="base-sha",
        merge_base="merge-sha",
        head="head-sha",
        changed_files=("addons/sale/tests/test_order.py",),
        modules=("sale",),
        ignored_paths=(),
        unmapped_paths=(),
    )
    with (
        patch(
            "odoo_instance_sdk.commands.test.cli_context.ready_instance",
            return_value=_resolved_context(env, instance),
        ),
        patch("odoo_instance_sdk.commands.test.resolve_changed_selection", return_value=plan),
        patch("odoo_instance_sdk.commands.test.preflight_installed_modules") as preflight,
        patch(
            "odoo_instance_sdk.commands.test.run_odoo_tests_command",
            return_value=_result_command((None, None)),
        ) as runner,
    ):
        result = CliRunner().invoke(cli, ["test", "--changed", "--dry-run", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)["result"]
    assert payload["dry_run"] is True
    assert payload["modules"] == ["sale"]
    assert payload["base_source"] == "explicit"
    for key in ("test_tags", "reload_tests", "allow_empty", "counts", "failures", "zero_tests"):
        assert key not in payload
    preflight.assert_not_called()
    runner.assert_called_once()


def test_changed_execution_uses_default_tags_and_preflight_before_runner(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    instance = _instance(tmp_path)
    plan = SimpleNamespace(
        base_source="environment",
        requested_base="main",
        resolved_base="base-sha",
        merge_base="merge-sha",
        head="head-sha",
        changed_files=("addons/stock/__manifest__.py", "addons/sale/__manifest__.py"),
        modules=("sale", "stock"),
        test_tags="/sale,/stock",
        ignored_paths=(),
        unmapped_paths=(),
    )
    typed = OdooTestResult(
        counts={"tests": 2, "successful": 2, "failed": 0, "errors": 0, "skipped": 0},
        failures=False,
        zero_tests=False,
        exit_code=0,
    )
    calls: list[str] = []

    def preflight(*_args: object, **_kwargs: object) -> None:
        calls.append("preflight")

    def runner(*_args: object, **_kwargs: object) -> Command[object]:
        calls.append("runner")
        return _result_command((typed, None))

    with (
        patch(
            "odoo_instance_sdk.commands.test.cli_context.ready_instance",
            return_value=_resolved_context(env, instance),
        ),
        patch("odoo_instance_sdk.commands.test.resolve_changed_selection", return_value=plan),
        patch("odoo_instance_sdk.commands.test.preflight_installed_modules", side_effect=preflight),
        patch(
            "odoo_instance_sdk.commands.test.run_odoo_tests_command", side_effect=runner
        ) as execute,
    ):
        result = CliRunner().invoke(cli, ["test", "--changed", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)["result"]
    assert payload["modules"] == ["sale", "stock"]
    assert payload["test_tags"] == "/sale,/stock"
    assert calls == ["runner"]
    assert execute.call_args.args[1].test_tags == "/sale,/stock"


def test_changed_no_addon_is_successful_noop_without_runner(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    instance = _instance(tmp_path)
    plan = SimpleNamespace(
        base_source="environment",
        requested_base="main",
        resolved_base="base-sha",
        merge_base="merge-sha",
        head="head-sha",
        changed_files=("README.md",),
        modules=(),
        ignored_paths=("README.md",),
        unmapped_paths=(),
    )
    with (
        patch(
            "odoo_instance_sdk.commands.test.cli_context.ready_instance",
            return_value=_resolved_context(env, instance),
        ),
        patch("odoo_instance_sdk.commands.test.resolve_changed_selection", return_value=plan),
        patch("odoo_instance_sdk.commands.test.preflight_installed_modules") as preflight,
        patch("odoo_instance_sdk.commands.test.run_odoo_tests_command") as runner,
    ):
        result = CliRunner().invoke(cli, ["test", "--changed", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)["result"]
    assert payload["reason"] == "no_addon_changes"
    assert payload["modules"] == []
    assert "counts" not in payload
    assert "test_tags" not in payload
    preflight.assert_not_called()
    runner.assert_not_called()


def test_changed_unmapped_path_returns_provenance_and_nonzero(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    instance = _instance(tmp_path)
    plan = SimpleNamespace(
        base_source="explicit",
        requested_base="main",
        resolved_base="base-sha",
        merge_base="merge-sha",
        head="head-sha",
        changed_files=("addons/unsafe/file.py",),
        modules=(),
        ignored_paths=(),
        unmapped_paths=("addons/unsafe/file.py",),
    )
    with (
        patch(
            "odoo_instance_sdk.commands.test.cli_context.ready_instance",
            return_value=_resolved_context(env, instance),
        ),
        patch("odoo_instance_sdk.commands.test.resolve_changed_selection", return_value=plan),
        patch("odoo_instance_sdk.commands.test.preflight_installed_modules") as preflight,
        patch("odoo_instance_sdk.commands.test.run_odoo_tests_command") as runner,
    ):
        result = CliRunner().invoke(cli, ["test", "--changed", "--format", "toon"])

    assert result.exit_code == 1
    assert "addons/unsafe/file.py" in result.stdout
    preflight.assert_not_called()
    runner.assert_not_called()


def test_changed_git_failure_returns_partial_sanitized_provenance(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    instance = _instance(tmp_path)
    plan = _ChangedSelection(
        base_source="explicit",
        requested_base="origin/dev",
        resolved_base=None,
        merge_base=None,
        head=None,
        changed_files=(),
        modules=(),
        ignored_paths=(),
        unmapped_paths=(),
        test_tags=None,
    )
    with (
        patch(
            "odoo_instance_sdk.commands.test.cli_context.ready_instance",
            return_value=_resolved_context(env, instance),
        ),
        patch(
            "odoo_instance_sdk.commands.test.resolve_changed_selection",
            side_effect=_ChangedSelectionError(
                "git failed password='secret' /private/runtime/git.log",
                plan,
            ),
        ),
    ):
        result = CliRunner().invoke(
            cli,
            ["test", "--changed", "--base", "origin/dev", "--format", "json"],
        )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)["result"]
    assert payload["requested_base"] == "origin/dev"
    assert payload["resolved_base"] is None
    assert payload["changed_files"] == []
    assert "secret" not in result.stderr
    assert "/private/runtime/git.log" not in result.stderr


def test_module_alias_deduplicates_and_sorts_before_typed_preflight(tmp_path: Path) -> None:
    _addon(tmp_path, "sale")
    _addon(tmp_path, "stock")
    instance = _instance(tmp_path)
    typed = OdooTestResult(
        counts={"tests": 1, "successful": 1, "failed": 0, "errors": 0, "skipped": 0},
        failures=False,
        zero_tests=False,
        exit_code=0,
    )
    selection = resolve_module_test_selection(
        tmp_path,
        instance.config.start_config,
        ("stock", "sale", "stock"),
        "/sale,/stock",
    )
    executed_spec = OdooTestSpec(modules=("sale", "stock"), test_tags="/sale,/stock")
    with (
        patch("odoo_instance_sdk.commands.test.preflight_installed_modules") as preflight,
        patch(
            "odoo_instance_sdk.commands.test.run_odoo_tests_command",
            return_value=_result_command((typed, None)),
        ) as runner,
    ):
        result, diagnostic = run_module_tests(
            cast("OdooInstance", instance),
            selection,
            executed_spec,
            http_interface="127.0.0.1",
            http_port=18069,
        )

    assert diagnostic is None
    assert result.exit_code == 0
    assert result.counts["tests"] == 1
    preflight.assert_called_once_with(instance, ("sale", "stock"))
    runner_spec = runner.call_args.args[1]
    assert runner_spec is executed_spec
    assert runner_spec.modules == ("sale", "stock")


def test_module_alias_projects_registered_worktree_selection_provenance(tmp_path: Path) -> None:
    _addon(tmp_path, "sale")
    env = _environment(tmp_path)
    instance = _instance(tmp_path)
    typed = OdooTestResult(
        counts={"tests": 1, "successful": 1, "failed": 0, "errors": 0, "skipped": 0},
        failures=False,
        zero_tests=False,
        exit_code=0,
    )
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            return_value=_resolved_context(env, instance),
        ),
        patch(
            "odoo_instance_sdk.cli.module_tests_command",
            return_value=_result_command((typed, None)),
        ) as runner,
        patch(
            "odoo_instance_sdk.cli.project_execution_result",
            wraps=project_execution_result,
        ) as projector,
    ):
        result = CliRunner().invoke(
            cli,
            [
                "module",
                "test",
                "sale",
                "--test-tags",
                "/sale",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)["result"]
    assert payload["selection"] == {
        "kind": "module",
        "value": "sale",
        "module_path": str(tmp_path / "addons" / "sale"),
        "file_path": None,
    }
    runner.assert_called_once()
    executed_spec = runner.call_args.args[1]
    assert executed_spec.modules == ("sale",)
    projector.assert_called_once()
    assert projector.call_args.args[2] is executed_spec
