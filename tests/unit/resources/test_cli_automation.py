from __future__ import annotations

import ast
import base64
import json
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import pytest

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.address import AddressState
from odoo_instance_sdk.internal.automation import (
    eval_expression,
    exec_script,
    export_translations,
    list_modules,
    plan_module_update,
    run_odoo_tests,
    update_modules,
    verify_deps,
)
from odoo_instance_sdk.internal.server import _build_shell_wrapper, parse_payload
from odoo_instance_sdk.models import CommandResult, OdooTestSpec
from odoo_instance_sdk.resources.environment import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
)

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient
    from odoo_instance_sdk.resources.instance import OdooInstance


def _payload_stdout(payload: dict[str, Any], nonce: str = "deadbeefdeadbeef") -> str:
    marker_open = f"__ODCLI_PAYLOAD__{nonce}__"
    marker_close = f"__END_PAYLOAD__{nonce}__"
    return f"noise\n{marker_open} {json.dumps(payload)} {marker_close}\nmore\n"


def _command_result(value: CommandResult) -> Command[CommandResult]:
    return Command.create(ExecutionPlan(), lambda _context: value)


def _make_instance(tmp_path: Path) -> OdooInstance:
    from odoo_instance_sdk import OdooClient, OdooClientConfig

    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    cfg = tmp_path / "odoo.conf"
    cfg.write_text(
        "[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\n"
        f"addons_path = {tmp_path / 'wt'}\nadmin_passwd = x\n"
    )
    return client.instance.from_config(cfg)


def _stub_run_shell_script(
    payload: dict[str, Any] | None = None,
    *,
    returncode: int = 0,
    stderr_override: str = "",
    stdout_override: str | None = None,
    captured_source: list[str] | None = None,
    captured_argv: list[list[str]] | None = None,
    captured_commit: list[bool] | None = None,
) -> Any:
    def _impl(
        self: Any,
        source: str,
        *,
        argv: tuple[str, ...] = (),
        timeout: float | None = None,
        commit: bool = False,
        **kwargs: Any,
    ) -> Command[CommandResult | Any]:
        if captured_source is not None:
            captured_source.append(source)
        if captured_argv is not None:
            captured_argv.append(list(argv))
        if captured_commit is not None:
            captured_commit.append(commit)
        if stdout_override is not None:
            result = CommandResult(
                args=[],
                returncode=returncode,
                stdout=stdout_override,
                stderr=stderr_override,
                duration=0.0,
            )
        else:
            out = _payload_stdout(payload or {})
            if (
                kwargs.get("result_converter") is not None
                and "_odcli_exports" in source
                and isinstance((payload or {}).get("result"), dict)
            ):
                out = _payload_stdout({"result": [(payload or {})["result"]]})
            result = CommandResult(
                args=[], returncode=returncode, stdout=out, stderr=stderr_override, duration=0.0
            )
        converter = kwargs.get("result_converter")
        value = converter(result) if converter is not None else result
        return Command.create(ExecutionPlan(), lambda _context: value)

    return _impl


class TestEvalScalar:
    def test_eval_returns_scalar(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        with patch.object(
            type(inst),
            "run_shell_script_command",
            _stub_run_shell_script({"ok": True, "commit": False, "result": 2}),
        ):
            outcome = eval_expression(inst, "1+1")
        assert outcome.payload is not None
        assert outcome.payload["result"] == 2


class TestEvalRecordset:
    def test_eval_recordset_summary(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        summary = {"model": "res.users", "ids": [1, 7], "count": 2}
        with patch.object(
            type(inst),
            "run_shell_script_command",
            _stub_run_shell_script({"ok": True, "commit": False, "result": summary}),
        ):
            outcome = eval_expression(inst, "env['res.users'].search([])")
        assert outcome.payload is not None
        assert outcome.payload["result"] == summary


class TestEvalUnknownObject:
    def test_eval_unknown_bounded_repr(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        sanitized = "<SomeObject object at 0x1>"
        with patch.object(
            type(inst),
            "run_shell_script_command",
            _stub_run_shell_script({"ok": True, "commit": False, "result": sanitized}),
        ):
            outcome = eval_expression(inst, "object()")
        assert outcome.payload is not None
        assert outcome.payload["result"] == sanitized


class TestExec:
    def test_exec_file_argv(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        captured: list[list[str]] = []
        script = tmp_path / "script.py"
        script.write_text("print('hi')\n")
        with patch.object(
            type(inst),
            "run_shell_script_command",
            _stub_run_shell_script({"ok": True, "commit": False}, captured_argv=captured),
        ):
            outcome = exec_script(inst, script.read_text(), argv=("arg1", "arg2"))
        assert outcome.returncode == 0
        assert captured[0] == ["arg1", "arg2"]

    def test_exec_stdin_dash(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        captured: list[str] = []
        with patch.object(
            type(inst),
            "run_shell_script_command",
            _stub_run_shell_script({"ok": True, "commit": False}, captured_source=captured),
        ):
            exec_script(inst, "x = 1\n")
        assert captured[0] == "x = 1\n"


class TestModuleList:
    def test_module_list(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        payload = {
            "ok": True,
            "commit": False,
            "result": [
                {"name": "base", "state": "installed", "installed_version": "19.0"},
                {"name": "sale", "state": "installed", "installed_version": "19.0"},
            ],
        }
        with patch.object(type(inst), "run_shell_script_command", _stub_run_shell_script(payload)):
            records = list_modules(inst, state="installed")
        assert [r.name for r in records] == ["base", "sale"]
        assert all(r.state == "installed" for r in records)


class TestModuleUpdate:
    def test_update_does_not_reacquire_its_own_environment_lock(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        from odoo_instance_sdk.internal.locks import environment_lock_path

        inst._artifact_lock_path = environment_lock_path("update-no-self-conflict")
        list_payload = {"result": [{"name": "comerta_base", "state": "installed"}]}
        upgrade_payload = {"result": {"updated": ["comerta_base"]}}
        with (
            patch.object(
                type(inst), "run_shell_script_command", _stub_run_shell_script(list_payload)
            ),
            patch.object(
                type(inst), "_shell_script_command", _stub_run_shell_script(upgrade_payload)
            ),
        ):
            assert update_modules(inst, ("comerta_base",), env_id="update-no-self-conflict").payload

    def test_dry_run_plan(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        payload = {
            "ok": True,
            "commit": False,
            "result": [
                {"name": "comerta_base", "state": "installed"},
            ],
        }
        with patch.object(type(inst), "run_shell_script_command", _stub_run_shell_script(payload)):
            plan = plan_module_update(inst, ("comerta_base",))
        assert plan.modules == ["comerta_base"]
        assert plan.not_installed == []

    def test_yes_executes_upgrade(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        list_payload = {
            "ok": True,
            "commit": False,
            "result": [{"name": "comerta_base", "state": "installed"}],
        }
        upgrade_payload = {
            "ok": True,
            "commit": True,
            "result": {"updated": ["comerta_base"]},
        }
        payloads = iter([list_payload, upgrade_payload])

        def _impl(
            self: Any,
            source: str,
            *,
            argv: tuple[str, ...] = (),
            timeout: float | None = None,
            commit: bool = False,
            **kwargs: Any,
        ) -> Command[CommandResult]:
            result = CommandResult(
                args=[],
                returncode=0,
                stdout=_payload_stdout(next(payloads)),
                stderr="",
                duration=0.0,
            )
            converter = kwargs.get("result_converter")
            value = converter(result) if converter is not None else result
            return Command.create(ExecutionPlan(), lambda _context: value)

        with (
            patch.object(type(inst), "_shell_script_command", _impl),
        ):
            outcome = update_modules(inst, ("comerta_base",), env_id="env-1")
        assert outcome.payload is not None
        payload = cast("dict[str, dict[str, list[str]]]", outcome.payload)
        assert payload["result"]["updated"] == ["comerta_base"]

    def test_not_installed_errors(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        payload = {"ok": True, "commit": False, "result": []}
        with (
            patch.object(type(inst), "_shell_script_command", _stub_run_shell_script(payload)),
            pytest.raises(ConfigError, match="not installed"),
        ):
            update_modules(inst, ("missing_mod",), env_id="env-1")


class TestOdooTestRunner:
    def test_native_counts_and_single_locked_shell_call(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        payload = {
            "ok": True,
            "commit": False,
            "result": {
                "tests": 7,
                "successful": 3,
                "failed": 1,
                "errors": 2,
                "skipped": 1,
            },
        }
        captured_source: list[str] = []
        captured_commit: list[bool] = []
        with (
            patch.object(
                type(inst),
                "_shell_script_command",
                _stub_run_shell_script(
                    payload,
                    captured_source=captured_source,
                    captured_commit=captured_commit,
                ),
            ),
            patch(
                "odoo_instance_sdk.internal.automation.probe_address",
                return_value=AddressState.FREE,
            ),
        ):
            result, diagnostic = run_odoo_tests(
                inst,
                OdooTestSpec(
                    modules=("sale", "stock"),
                    test_tags="/sale,/stock",
                    reload_tests=True,
                ),
                http_interface="127.0.0.1",
                http_port=18080,
            )

        assert diagnostic is None
        assert result.counts == {
            "tests": 7,
            "successful": 3,
            "failed": 1,
            "errors": 2,
            "skipped": 1,
        }
        assert result.failures is True
        assert result.zero_tests is False
        assert result.exit_code == 1
        assert len(captured_source) == 1
        assert captured_commit == [False]
        assert captured_source[0].count("_odcli_run_tests(") == 1
        assert "_odcli_config['workers'] = 0" in captured_source[0]
        calls = [
            node
            for node in ast.walk(ast.parse(captured_source[0]))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_odcli_run_tests"
        ]
        assert len(calls) == 1
        arguments = {
            keyword.arg: ast.literal_eval(keyword.value)
            for keyword in calls[0].keywords
            if keyword.arg is not None
        }
        assert arguments == {
            "modules": ("sale", "stock"),
            "test_tags": "/sale,/stock",
            "reload_tests": True,
        }
        assert type(arguments["modules"]) is tuple
        assert type(arguments["test_tags"]) is str
        assert type(arguments["reload_tests"]) is bool

    @pytest.mark.parametrize("allow_empty, expected_exit", [(False, 1), (True, 0)])
    def test_native_zero_tests_respects_allow_empty(
        self, tmp_path: Path, allow_empty: bool, expected_exit: int
    ) -> None:
        inst = _make_instance(tmp_path)
        payload = {"result": {"tests": 0, "successful": 0, "failed": 0, "errors": 0, "skipped": 0}}
        with (
            patch.object(type(inst), "_shell_script_command", _stub_run_shell_script(payload)),
            patch(
                "odoo_instance_sdk.internal.automation.probe_address",
                return_value=AddressState.FREE,
            ),
        ):
            result, diagnostic = run_odoo_tests(
                inst,
                OdooTestSpec(modules=("sale",), test_tags="/sale", allow_empty=allow_empty),
                http_interface="127.0.0.1",
                http_port=18081,
            )
        assert result.zero_tests is True
        assert result.exit_code == expected_exit
        assert diagnostic is None

    def test_process_failure_returns_sanitized_stderr_diagnostic(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        with (
            patch.object(
                type(inst),
                "_shell_script_command",
                _stub_run_shell_script(
                    returncode=2,
                    stderr_override="password='secret' token=hidden /private/runtime/odoo.log",
                ),
            ),
            patch(
                "odoo_instance_sdk.internal.automation.probe_address",
                return_value=AddressState.FREE,
            ),
        ):
            result, diagnostic = run_odoo_tests(
                inst,
                OdooTestSpec(modules=("sale",), test_tags="/sale"),
                http_interface="127.0.0.1",
                http_port=18082,
            )
        assert result.exit_code == 1
        assert diagnostic is not None
        assert "secret" not in diagnostic
        assert "hidden" not in diagnostic
        assert "/private/runtime/odoo.log" not in diagnostic

    def test_exit_uses_native_counts_not_log_words(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        payload = {"result": {"tests": 2, "successful": 2, "failed": 0, "errors": 0, "skipped": 0}}
        with (
            patch.object(
                type(inst),
                "_shell_script_command",
                _stub_run_shell_script(payload, stderr_override="FAILED error 0 tests"),
            ),
            patch(
                "odoo_instance_sdk.internal.automation.probe_address",
                return_value=AddressState.FREE,
            ),
        ):
            result, diagnostic = run_odoo_tests(
                inst,
                OdooTestSpec(modules=("sale",), test_tags="/sale"),
                http_interface="127.0.0.1",
                http_port=18083,
            )
        assert result.exit_code == 0
        assert result.failures is False
        assert diagnostic is None

    def test_port_conflict_does_not_call_shell(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        with (
            patch.object(type(inst), "_shell_script_command") as runner,
            patch(
                "odoo_instance_sdk.internal.automation.probe_address",
                return_value=AddressState.OCCUPIED,
            ),
            pytest.raises(ConfigError, match="port occupied"),
        ):
            run_odoo_tests(
                inst,
                OdooTestSpec(modules=("sale",), test_tags="/sale"),
                http_interface="127.0.0.1",
                http_port=18084,
            )
        runner.assert_not_called()


class TestTranslationsExport:
    def test_ru_ru_writes_ru_po(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        po_content = b'msgid ""\nmsgstr ""\n'
        payload = {
            "ok": True,
            "commit": False,
            "result": {
                "iso": "ru",
                "filename": "ru.po",
                "data": base64.b64encode(po_content).decode(),
                "module": "comerta_base",
                "installed": True,
                "lang": "ru_RU",
            },
        }
        worktree = tmp_path / "wt"
        (worktree / "comerta_base" / "i18n").mkdir(parents=True)
        (worktree / "comerta_base" / "__manifest__.py").write_text("{}")
        with patch.object(type(inst), "_shell_script_command", _stub_run_shell_script(payload)):
            results = export_translations(
                inst, ("comerta_base",), ("ru_RU",), worktree_root=worktree
            )
        assert len(results) == 1
        r = results[0]
        assert r.actual_filename == "ru.po"
        assert r.requested_lang == "ru_RU"
        assert r.path.name == "ru.po"
        assert r.path.read_bytes() == po_content

    def test_containment_escape_rejected(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        payload = {
            "ok": True,
            "commit": False,
            "result": {
                "iso": "ru",
                "filename": "../../../../../../../etc/passwd",
                "data": base64.b64encode(b"x").decode(),
                "module": "comerta_base",
                "installed": True,
                "lang": "ru_RU",
            },
        }
        worktree = tmp_path / "wt"
        (worktree / "comerta_base" / "i18n").mkdir(parents=True)
        (worktree / "comerta_base" / "__manifest__.py").write_text("{}")
        with (
            patch.object(type(inst), "_shell_script_command", _stub_run_shell_script(payload)),
            pytest.raises(ConfigError, match="unexpected filename"),
        ):
            export_translations(inst, ("comerta_base",), ("ru_RU",), worktree_root=worktree)

    def test_atomic_write_preserves_mode(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        po_content = b"msgid x\n"
        payload = {
            "ok": True,
            "commit": False,
            "result": {
                "iso": "ru",
                "filename": "ru.po",
                "data": base64.b64encode(po_content).decode(),
                "module": "comerta_base",
                "installed": True,
                "lang": "ru_RU",
            },
        }
        worktree = tmp_path / "wt"
        i18n_dir = worktree / "comerta_base" / "i18n"
        i18n_dir.mkdir(parents=True)
        (worktree / "comerta_base" / "__manifest__.py").write_text("{}")
        existing = i18n_dir / "ru.po"
        existing.write_bytes(b"OLD")
        existing.chmod(0o640)
        with patch.object(type(inst), "_shell_script_command", _stub_run_shell_script(payload)):
            results = export_translations(
                inst, ("comerta_base",), ("ru_RU",), worktree_root=worktree
            )
        assert results[0].path.read_bytes() == po_content
        assert not results[0].path.with_suffix(".po.tmp").exists()

    def test_absent_module_is_rejected_before_creating_i18n(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        payload = {
            "result": {
                "iso": "ru",
                "filename": "ru.po",
                "data": base64.b64encode(b"x").decode(),
                "installed": True,
            }
        }
        worktree = tmp_path / "wt"
        worktree.mkdir()
        with (
            patch.object(type(inst), "_shell_script_command", _stub_run_shell_script(payload)),
            pytest.raises(ConfigError, match="absent"),
        ):
            export_translations(inst, ("not_local",), ("ru_RU",), worktree_root=worktree)
        assert not (worktree / "not_local" / "i18n").exists()

    def test_invalid_base64_is_rejected_before_writing(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        payload = {"result": {"iso": "ru", "filename": "ru.po", "data": "%%%", "installed": True}}
        target = tmp_path / "wt" / "comerta_base" / "i18n" / "ru.po"
        target.parent.mkdir(parents=True)
        (target.parent.parent / "__manifest__.py").write_text("{}")
        target.write_bytes(b"old")
        with (
            patch.object(type(inst), "_shell_script_command", _stub_run_shell_script(payload)),
            pytest.raises(ConfigError, match="invalid base64"),
        ):
            export_translations(inst, ("comerta_base",), ("ru_RU",), worktree_root=tmp_path / "wt")
        assert target.read_bytes() == b"old"

    def test_replace_failure_preserves_old_target_and_removes_temp(self, tmp_path: Path) -> None:
        inst = _make_instance(tmp_path)
        payload = {
            "result": {
                "iso": "ru",
                "filename": "ru.po",
                "data": base64.b64encode(b"new").decode(),
                "installed": True,
            }
        }
        target = tmp_path / "wt" / "comerta_base" / "i18n" / "ru.po"
        target.parent.mkdir(parents=True)
        (target.parent.parent / "__manifest__.py").write_text("{}")
        target.write_bytes(b"old")
        with (
            patch.object(type(inst), "_shell_script_command", _stub_run_shell_script(payload)),
            patch("pathlib.Path.replace", side_effect=OSError("replace denied")),
            pytest.raises(OSError, match="replace denied"),
        ):
            export_translations(inst, ("comerta_base",), ("ru_RU",), worktree_root=tmp_path / "wt")
        assert target.read_bytes() == b"old"
        assert not list(target.parent.glob(".ru.po.*.tmp"))


class TestDepsVerify:
    def test_missing_import_reported(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        worktree = tmp_path / "wt"
        mod_dir = worktree / "myaddon"
        mod_dir.mkdir(parents=True)
        (mod_dir / "__manifest__.py").write_text(
            textwrap.dedent("""\
            {
                'name': 'myaddon',
                'external_dependencies': {
                    'python': ['requests'],
                },
            }
            """)
        )
        fake_py = tmp_path / "fakepython"
        fake_py.write_text("#!/bin/sh\nexit 1\n")
        fake_py.chmod(0o755)

        from odoo_instance_sdk.internal.proc import ProcessResult

        def fake_execute(_executor: Any, step: Any) -> ProcessResult:
            rc = 1 if "-c" in step.argv and "import requests" in " ".join(step.argv) else 0
            return ProcessResult(
                argv=step.argv,
                returncode=rc,
                stdout="",
                stderr="",
                duration=0.0,
                cwd=step.cwd,
                environment=step.environment,
            )

        monkeypatch.setattr(
            "odoo_instance_sdk.internal.proc.executor.SubprocessExecutor.execute", fake_execute
        )
        result = verify_deps(recorded_python=fake_py, worktree_root=worktree, uv_executable="uv")
        assert {"module": "myaddon", "import": "requests"} in result.missing_imports


class TestParsePayload:
    def test_auto_detect_nonce(self) -> None:
        stdout = _payload_stdout({"ok": True, "result": 42}, nonce="abcdef1234567890")
        payload = parse_payload(stdout)
        assert payload is not None
        assert payload["result"] == 42

    def test_explicit_nonce(self) -> None:
        stdout = _payload_stdout({"ok": True}, nonce="1234567890abcdef")
        payload = parse_payload(stdout, nonce="1234567890abcdef")
        assert payload is not None
        assert payload["ok"] is True

    def test_no_payload_returns_none(self) -> None:
        assert parse_payload("no markers here") is None

    def test_returns_payload_dict(self) -> None:
        stdout = _payload_stdout({"ok": True, "result": "x"})
        assert parse_payload(stdout) is not None


class TestShellWrapper:
    def test_wrapper_emits_result_when_defined(self) -> None:
        wrapper = _build_shell_wrapper("result = 5\n", [], commit=False, nonce="abc123")
        assert "_odcli_serialize_result" in wrapper
        assert "'result'" in wrapper
        assert "__ODCLI_PAYLOAD__abc123__" in wrapper

    def test_wrapper_injects_source(self) -> None:
        wrapper = _build_shell_wrapper("x = 1\n", ["--y"], commit=True, nonce="z9")
        assert "x = 1" in wrapper
        assert '"--y"' in wrapper
        assert "True" in wrapper


class TestCliEval:
    def test_cli_eval_json(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        from click.testing import CliRunner

        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            odoo_bin=fake_python.parent / "odoo-bin",
            source_database="comerta",
        )
        env = env_client.environments.checkout(project_manifest, "feat/eval-cli", options=opts)
        runner = CliRunner()
        with (
            patch("odoo_instance_sdk.commands.context.OdooClient", return_value=env_client),
            patch(
                "odoo_instance_sdk.resources.instance.OdooInstance.run_shell_script_command"
            ) as mock_run,
        ):
            mock_run.return_value = _command_result(
                CommandResult(
                    args=[],
                    returncode=0,
                    stdout=_payload_stdout({"ok": True, "commit": False, "result": 2}),
                    stderr="",
                    duration=0.0,
                )
            )
            result = runner.invoke(cli, ["--env", str(env.id), "eval", "1+1", "--json"])
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        assert envelope["data"]["result"] == 2


class TestCliModuleUpdate:
    def test_without_yes_errors(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        from click.testing import CliRunner

        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            odoo_bin=fake_python.parent / "odoo-bin",
            source_database="comerta",
        )
        env = env_client.environments.checkout(project_manifest, "feat/modupd-cli", options=opts)
        runner = CliRunner()
        with (
            patch("odoo_instance_sdk.commands.context.OdooClient", return_value=env_client),
            patch(
                "odoo_instance_sdk.resources.instance.OdooInstance.run_shell_script_command"
            ) as mock_run,
        ):
            mock_run.return_value = _command_result(
                CommandResult(
                    args=[],
                    returncode=0,
                    stdout=_payload_stdout(
                        {
                            "ok": True,
                            "commit": False,
                            "result": [{"name": "base", "state": "installed"}],
                        }
                    ),
                    stderr="",
                    duration=0.0,
                )
            )
            result = runner.invoke(cli, ["--env", str(env.id), "module", "update", "base"])
        assert result.exit_code == 1
        assert "--yes" in result.output

    def test_dry_run_plan(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        from click.testing import CliRunner

        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            odoo_bin=fake_python.parent / "odoo-bin",
            source_database="comerta",
        )
        env = env_client.environments.checkout(project_manifest, "feat/modupd-dry", options=opts)
        runner = CliRunner()
        with (
            patch("odoo_instance_sdk.commands.context.OdooClient", return_value=env_client),
            patch(
                "odoo_instance_sdk.resources.instance.OdooInstance.run_shell_script_command"
            ) as mock_run,
        ):
            mock_run.return_value = _command_result(
                CommandResult(
                    args=[],
                    returncode=0,
                    stdout=_payload_stdout(
                        {
                            "ok": True,
                            "commit": False,
                            "result": [{"name": "base", "state": "installed"}],
                        }
                    ),
                    stderr="",
                    duration=0.0,
                )
            )
            result = runner.invoke(
                cli, ["--env", str(env.id), "module", "update", "base", "--dry-run", "--json"]
            )
        assert result.exit_code == 0
        env_json = json.loads(result.output)
        assert any(
            step["step_id"] == "instance.shell_script" for step in env_json["data"]["plan"]["steps"]
        )
        assert env_json["dry_run"] is True


class TestCliExecStdin:
    def test_exec_dash_reads_stdin(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        from click.testing import CliRunner

        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            odoo_bin=fake_python.parent / "odoo-bin",
            source_database="comerta",
        )
        env = env_client.environments.checkout(project_manifest, "feat/exec-cli", options=opts)
        runner = CliRunner()
        with (
            patch("odoo_instance_sdk.commands.context.OdooClient", return_value=env_client),
            patch(
                "odoo_instance_sdk.resources.instance.OdooInstance.run_shell_script_command"
            ) as mock_run,
        ):
            mock_run.return_value = _command_result(
                CommandResult(
                    args=[],
                    returncode=0,
                    stdout=_payload_stdout({"ok": True, "commit": False}),
                    stderr="",
                    duration=0.0,
                )
            )
            result = runner.invoke(
                cli,
                ["--env", str(env.id), "exec", "-", "--", "arg1"],
                input="print('hi')\n",
            )
        assert result.exit_code == 0
        _src, kwargs = mock_run.call_args
        assert _src[0] == "print('hi')\n"
        assert list(kwargs["argv"]) == ["arg1"]


class TestCliModuleList:
    def test_module_list_json(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        from click.testing import CliRunner

        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            odoo_bin=fake_python.parent / "odoo-bin",
            source_database="comerta",
        )
        env = env_client.environments.checkout(project_manifest, "feat/modlist-cli", options=opts)
        runner = CliRunner()
        with (
            patch("odoo_instance_sdk.commands.context.OdooClient", return_value=env_client),
            patch(
                "odoo_instance_sdk.resources.instance.OdooInstance.run_shell_script_command"
            ) as mock_run,
        ):
            mock_run.return_value = _command_result(
                CommandResult(
                    args=[],
                    returncode=0,
                    stdout=_payload_stdout(
                        {
                            "ok": True,
                            "commit": False,
                            "result": [{"name": "base", "state": "installed"}],
                        }
                    ),
                    stderr="",
                    duration=0.0,
                )
            )
            result = runner.invoke(
                cli,
                ["--env", str(env.id), "module", "list", "--state", "installed", "--json"],
            )
        assert result.exit_code == 0
        env_json = json.loads(result.output)
        assert env_json["data"]["modules"][0]["name"] == "base"


class TestNoNewPublicResources:
    def test_no_module_resource(self) -> None:
        from odoo_instance_sdk import resources as r

        assert not hasattr(r, "ModuleResource")

    def test_no_translation_resource(self) -> None:
        from odoo_instance_sdk import resources as r

        assert not hasattr(r, "TranslationResource")

    def test_no_python_resource(self) -> None:
        from odoo_instance_sdk import resources as r

        assert not hasattr(r, "PythonResource")


if __name__ == "__main__":
    pytest.main([__file__])
