from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from inspect import unwrap
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import msgspec
import pytest

if TYPE_CHECKING:
    from click.testing import Result

from odoo_instance_sdk import (
    OdooClient,
    OdooClientConfig,
    OdooTestProfile,
    OdooTestProfileEntry,
    OdooTestResult,
    OdooTestSpec,
)
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.resources.postgres import PostgresCluster
from tests.conftest import isolated_cli_catalogue
from tests.integration.real_odoo import focused_handlers
from tests.integration.real_odoo.focused_handlers import _stable_plan, _State


def test_real_odoo_catalogue_follows_runtime_home_in_sdk_and_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()
    request = SimpleNamespace(node=SimpleNamespace(get_closest_marker=lambda _: True))
    unwrap(isolated_cli_catalogue)(monkeypatch, tmp_path, request, tmp_path / "unused")
    runtime_home = tmp_path / "runtime" / "home"
    monkeypatch.setenv("HOME", str(runtime_home))
    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    catalog = client.get_catalog()
    try:
        expected = runtime_home / ".odcli" / "catalog.sqlite3"
        assert expected.is_file()
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "from odoo_instance_sdk.internal.paths import get_catalog_path; print(get_catalog_path())",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert Path(child.stdout.strip()) == expected
    finally:
        catalog.close()


@pytest.mark.parametrize("operation", ["shell", "reset"])
def test_focused_plan_comparison_ignores_only_execution_local_values(
    tmp_path: Path,
    operation: str,
) -> None:
    config = tmp_path / "odoo.conf"
    config.write_text("[options]\nadmin_passwd = admin\ndb_name = demo\n", encoding="utf-8")
    instance = OdooClient(config=OdooClientConfig(executable="/usr/bin/odoo")).instance.from_config(
        config,
        base_url="http://127.0.0.1:8069",
    )
    instance._postgres_cluster = PostgresCluster._from_config(
        ProjectConfig(
            repository_root=tmp_path,
            postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=5432),
        ),
        repository_root=tmp_path,
        compose_runner=None,
        project_id="project_probe",
    )
    if operation == "shell":
        plans = [json.loads(msgspec.json.encode(instance.shell_command().plan)) for _ in range(2)]
    else:
        plans = [
            json.loads(
                msgspec.json.encode(
                    instance.databases.reset_admin_password_command(
                        admin_password="focused-secret",
                        provenance="environment",
                    ).plan
                )
            )
            for _ in range(2)
        ]
    first, second = plans
    assert first["fingerprint"] != second["fingerprint"]
    assert _stable_plan(first) == _stable_plan(second)
    second["steps"][0]["argv"][-1] = "different-image"
    assert _stable_plan(first) != _stable_plan(second)


def test_database_probe_uses_cluster_home_and_restores_caller_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from functools import partial

    from tests.integration.real_odoo import focused_support as failures
    from tests.integration.real_odoo.compose import ComposeLifecycle

    # Exercise real HOME resolution, without the offline fixture's fixed SDK paths.
    monkeypatch.undo()
    project = tmp_path / "project"
    (project / ".odcli").mkdir(parents=True)
    config = ProjectConfig(
        repository_root=project,
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=5432),
    )
    (project / ".odcli" / "project.toml").write_text(config.to_manifest(), encoding="utf-8")
    cluster_home = tmp_path / "cluster-home"
    monkeypatch.setenv("HOME", str(cluster_home))
    expected_file = PostgresCluster.from_project(project).compose_file
    caller_home = tmp_path / "caller-home"
    monkeypatch.setenv("HOME", str(caller_home))

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[args.index("--file") + 1] == str(expected_file)
        assert kwargs["cwd"] == expected_file.parent
        assert args[-1] == "SELECT datname FROM pg_database WHERE datname = 'demo';"
        return subprocess.CompletedProcess(args, 0, "demo\n", "")

    monkeypatch.setattr(failures, "ComposeLifecycle", partial(ComposeLifecycle, command_runner=run))
    result = failures._project_database_probe(project, "demo", home=str(cluster_home))

    assert result.returncode == 0
    assert result.stdout == "demo\n"
    assert Path.home() == caller_home


def test_inspect_profile_preserves_fixture_sentinel_and_has_no_http_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, invoked, http_probes = _inspect_profile_harness(
        monkeypatch,
        sentinels=("ODCLI-E2E-RESTORED", "ODCLI-E2E-RESTORED"),
        http_listeners=(False, False),
    )

    focused_handlers._inspect_profile(state)

    assert [args[0] for args in invoked].count("eval") == 2
    assert http_probes == [("127.0.0.1", 8765), ("127.0.0.1", 8765)]


def test_inspect_profile_contract_fails_on_changed_sentinel_or_http_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _invoked, _http_probes = _inspect_profile_harness(
        monkeypatch,
        sentinels=("ODCLI-E2E-RESTORED", "ODCLI-E2E-CHANGED"),
        http_listeners=(False, False),
    )
    with pytest.raises(AssertionError, match="inspection changed the fixture sentinel"):
        focused_handlers._inspect_profile(state)

    state, _invoked, _http_probes = _inspect_profile_harness(
        monkeypatch, sentinels=(), http_listeners=(True,)
    )
    with pytest.raises(AssertionError, match="unexpectedly exposed an HTTP listener"):
        focused_handlers._inspect_profile(state)


def test_python_sdk_docs_document_public_inspection_and_profile_contract() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    sdk_doc = Path("docs/python-sdk.md").read_text(encoding="utf-8")

    assert "odcli test sale --profile" in readme
    assert "docs/python-sdk.md#inspection-and-test-profiling" in readme
    assert 'instance.inspector.model("res.partner")' in sdk_doc
    assert 'instance.inspector.field("res.partner", "email")' in sdk_doc
    assert 'instance.inspector.ref("base.user_admin")' in sdk_doc
    assert 'OdooTestSpec(modules=("sale",), test_tags="/sale", profile=True)' in sdk_doc
    assert (
        " ".join(sdk_doc.split()).find(
            "OdooTestResult.profile` is `None` when profiling was not requested"
        )
        >= 0
    )
    assert "bounded metadata" in sdk_doc
    assert "does not" in sdk_doc and "return record values, execute SQL, write data" in sdk_doc

    example = sdk_doc.split("## Inspection and test profiling", 1)[1]
    example = example.split("```python\n", 1)[1].split("\n```", 1)[0]
    calls: list[tuple[object, ...]] = []

    class Inspector:
        def model(self, name: str) -> str:
            calls.append(("model", name))
            return "model metadata"

        def field(self, model: str, name: str) -> str:
            calls.append(("field", model, name))
            return "field metadata"

        def ref(self, xml_id: str) -> str:
            calls.append(("ref", xml_id))
            return "reference metadata"

    profile = OdooTestProfile(
        total_duration=1.0,
        total_query_count=2,
        entries=(OdooTestProfileEntry(test_id="sale.test_order", duration=1.0, query_count=2),),
    )
    result = OdooTestResult(
        counts={"tests": 1, "successful": 1, "failed": 0, "errors": 0, "skipped": 0},
        failures=False,
        zero_tests=False,
        exit_code=0,
        profile=profile,
    )
    captured: dict[str, object] = {}

    def run_command(_instance: object, spec: object) -> SimpleNamespace:
        captured["spec"] = spec
        return SimpleNamespace(run=lambda: (result, "sanitized diagnostic"))

    output = io.StringIO()
    namespace: dict[str, object] = {
        "instance": SimpleNamespace(inspector=Inspector()),
    }
    with (
        patch("odoo_instance_sdk.OdooClient") as client_factory,
        patch("odoo_instance_sdk.resources.testing.run_odoo_tests_command", run_command),
        redirect_stdout(output),
    ):
        client_factory.return_value.instance.from_config.return_value = namespace["instance"]
        exec(compile(example, "docs/python-sdk.md", "exec"), namespace)

    assert calls == [
        ("model", "res.partner"),
        ("field", "res.partner", "email"),
        ("ref", "base.user_admin"),
    ]
    assert cast("OdooTestSpec", captured["spec"]).profile is True
    assert namespace["result"] is result
    assert "sanitized diagnostic" in output.getvalue()
    assert "1.0 2" in output.getvalue()


def _inspect_profile_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sentinels: tuple[str, ...],
    http_listeners: tuple[bool, ...],
) -> tuple[_State, list[list[str]], list[tuple[str, int]]]:
    expected: dict[tuple[str, ...], dict[str, object]] = {
        ("model", "odcli.e2e.probe"): {"exists": True},
        ("field", "odcli.e2e.probe", "marker"): {"type": "char"},
        ("ref", "odcli_e2e_probe.probe_record"): {"display_name": "Pinned Odoo 19 fixture"},
        ("model", "odcli.missing_probe"): {"exists": False},
        ("ref", "odcli_e2e_probe.missing_probe"): {"exists": False},
    }
    invoked: list[list[str]] = []
    sentinel_values = iter(sentinels)

    def invoke(_state: _State, args: list[str], *, input: str | None = None) -> Result:
        del input
        invoked.append(args)
        if args[0] == "eval":
            document: dict[str, object] = {
                "ok": True,
                "result": {"result": next(sentinel_values)},
            }
        elif args[0] == "inspect":
            document = {"ok": True, "result": expected[tuple(args[1:-2])]}
        else:
            document = {
                "ok": True,
                "result": {
                    "profile": {
                        "entries": [{"duration": 1.0, "query_count": 2}],
                        "total_query_count": 2,
                    }
                },
            }
        return cast(
            "Result",
            SimpleNamespace(exit_code=0, output="", stdout=json.dumps(document)),
        )

    listener_values = iter(http_listeners)
    http_probes: list[tuple[str, int]] = []

    def tcp_ready(host: str, port: int) -> bool:
        http_probes.append((host, port))
        return next(listener_values)

    state = cast(
        "_State",
        SimpleNamespace(
            runtime=SimpleNamespace(reservations=(None, None, None, SimpleNamespace(port=8765)))
        ),
    )
    monkeypatch.setattr(focused_handlers, "_invoke", invoke)
    monkeypatch.setattr(focused_handlers, "_tcp_ready", tcp_ready)
    return state, invoked, http_probes
