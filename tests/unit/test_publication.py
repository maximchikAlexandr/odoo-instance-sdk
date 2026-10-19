from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from click.testing import CliRunner
from msgspec.structs import replace

import odoo_instance_sdk.commands.publication as publication_commands
import odoo_instance_sdk.internal.publication_routes as publication_routes
import odoo_instance_sdk.resources.instance.runtime_identity as runtime_identity
import odoo_instance_sdk.resources.publication as publication_resource
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.exceptions import ConfigError, PublicationError
from odoo_instance_sdk.internal.paths import get_user_root
from odoo_instance_sdk.internal.proc import PreparedProcess, ProcessResult, RecordingExecutor
from odoo_instance_sdk.resources.publication import (
    PublicationSettings,
    PublicationTarget,
    render_panel_route,
)


def _settings() -> PublicationSettings:
    root = get_user_root()
    return PublicationSettings(
        domain_suffix="example.test",
        caddy_executable="caddy",
        caddy_control_endpoint="http://127.0.0.1:2019",
        owned_route_file=root / "routes.caddy",
        basic_auth_username="odoo",
        basic_auth_password_hash="$2a$10$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        panel_host_label="panel",
        trusted_proxy_addresses=("127.0.0.1",),
    )


def _client() -> OdooClient:
    return OdooClient(config=OdooClientConfig(executable="odoo"))


def _process(step: PreparedProcess, *, returncode: int = 0) -> ProcessResult:
    return ProcessResult(
        argv=step.argv,
        returncode=returncode,
        stdout="",
        stderr="",
        duration=0.0,
        cwd=None,
        environment=(),
    )


def test_settings_reject_plaintext_and_non_loopback_control(tmp_path: Path) -> None:
    root = tmp_path / ".odcli"
    with pytest.raises(ConfigError):
        PublicationSettings(
            domain_suffix="example.test",
            caddy_executable="caddy",
            caddy_control_endpoint="http://127.0.0.1:2019",
            owned_route_file=root / "routes.caddy",
            basic_auth_username="odoo",
            basic_auth_password_hash="plaintext",
            trusted_proxy_addresses=("127.0.0.1",),
        )
    with pytest.raises(ConfigError):
        PublicationSettings(
            domain_suffix="*.example.test",
            caddy_executable="caddy",
            caddy_control_endpoint="http://0.0.0.0:2019",
            owned_route_file=root / "routes.caddy",
            basic_auth_username="odoo",
            basic_auth_password_hash="$2a$10$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            trusted_proxy_addresses=("127.0.0.1",),
        )

    for field, value in (
        ("basic_auth_username", "odoo\n}\nattacker.example {"),
        (
            "basic_auth_password_hash",
            "$2a$10$aaaaaaaaaaaaaaaaaaaaaa\n}\nattacker.example {",
        ),
    ):
        values = {
            "domain_suffix": "example.test",
            "caddy_executable": "caddy",
            "caddy_control_endpoint": "http://127.0.0.1:2019",
            "owned_route_file": root / "routes.caddy",
            "basic_auth_username": "odoo",
            "basic_auth_password_hash": "$2a$10$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "trusted_proxy_addresses": ("127.0.0.1",),
        }
        values[field] = value
        with pytest.raises(ConfigError):
            PublicationSettings(**cast("dict[str, Any]", values))


@pytest.mark.parametrize("address", ["127.0.0.1", "::1"])
def test_settings_accept_loopback_trusted_proxy(address: str) -> None:
    settings = replace(_settings(), trusted_proxy_addresses=(address,))
    assert settings.trusted_proxy_addresses == (address,)


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "::",
        "224.0.0.1",
        "ff02::1",
        "240.0.0.1",
        "100::",
        "::1%lo0\n    respond 200",
        "fe80::1%lo0\n    respond 200",
    ],
)
def test_settings_reject_unsafe_trusted_proxy(address: str) -> None:
    with pytest.raises(ConfigError, match=r"must be (?:an unscoped|a specific) IP"):
        replace(_settings(), trusted_proxy_addresses=(address,))


def test_publish_is_stable_and_unpublish_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(publication_resource, "_assert_runtime_ready", lambda _target: None)
    settings = _settings()
    target = PublicationTarget(
        owner_kind="project",
        owner_id="project_abc_123",
        project_id="project_abc_123",
        local_endpoint="http://127.0.0.1:8069",
    )
    executor = RecordingExecutor()
    client = _client()

    first = client.publication.publish_command(target, settings=settings, executor=executor).run()
    second = client.publication.publish_command(target, settings=settings, executor=executor).run()
    route_text = settings.owned_route_file.read_text()
    removed = client.publication.unpublish_command(
        ("project", target.owner_id), settings=settings, executor=executor
    ).run()
    absent = client.publication.unpublish_command(
        ("project", target.owner_id), settings=settings, executor=executor
    ).run()

    assert first.external_url == "https://project-abc-123.example.test"
    assert "basic_auth bcrypt" in route_text
    assert "/websocket" in route_text
    assert "project-abc-123.example.test" in route_text
    assert "panel.example.test" in render_panel_route("http://127.0.0.1:9000", settings)
    assert second.status == "already_published"
    assert removed.status == "unpublished"
    assert absent.status == "already_absent"
    reloads = [step for step in executor.executed if "reload" in step.step_id]
    assert reloads
    for step in reloads:
        assert step.argv[step.argv.index("--address") + 1] == "127.0.0.1:2019"


def test_upstream_http_routes_reload_existing_caddy_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(publication_resource, "_assert_runtime_ready", lambda _target: None)
    base = _settings()
    root_config = tmp_path / "Caddyfile"
    root_config.write_text(f"import {base.owned_route_file}\n")
    settings = PublicationSettings(
        domain_suffix=base.domain_suffix,
        caddy_executable=base.caddy_executable,
        caddy_control_endpoint=base.caddy_control_endpoint,
        owned_route_file=base.owned_route_file,
        basic_auth_username=base.basic_auth_username,
        basic_auth_password_hash=("$argon2id$v=19$m=47104,t=1,p=1$" + "a" * 22 + "$" + "a" * 43),
        trusted_proxy_addresses=base.trusted_proxy_addresses,
        caddy_reload_config=root_config,
        upstream_http_port=18081,
        upstream_bind_address="192.168.100.110",
    )
    executor = RecordingExecutor()
    target = PublicationTarget(
        owner_kind="project",
        owner_id="project_abc",
        project_id="project_abc",
        local_endpoint="http://127.0.0.1:8069",
    )
    _client().publication.publish_command(target, settings=settings, executor=executor).run()
    route_text = settings.owned_route_file.read_text()
    assert "http://project-abc.example.test:18081 {" in route_text
    assert "bind 192.168.100.110" in route_text
    assert "http://panel.example.test:18081 {" in render_panel_route(
        "http://127.0.0.1:8123", settings
    )
    assert "basic_auth argon2id" in render_panel_route("http://127.0.0.1:8123", settings)
    project_block = route_text.split("http://project-abc.example.test:18081 {\n", 1)[1].split(
        "\n}\n", 1
    )[0]
    matcher = "@odcli_trusted_proxy remote_ip 127.0.0.1"
    handle = "handle @odcli_trusted_proxy {"
    backend = "reverse_proxy http://127.0.0.1:8069"
    fallback = "respond 403"
    assert matcher in project_block
    assert handle in project_block
    assert backend in project_block
    assert fallback in project_block
    assert project_block.index(matcher) < project_block.index(handle)
    assert project_block.index(handle) < project_block.index(backend)
    handle_end = project_block.index("\n    }\n    respond 403")
    assert project_block.index(backend) < handle_end < project_block.index(fallback)
    assert "reverse_proxy" not in project_block[handle_end:]
    reloads = [step for step in executor.executed if step.step_id.endswith("caddy.reload")]
    assert reloads
    assert all(step.argv[step.argv.index("--config") + 1] == str(root_config) for step in reloads)


def test_missing_existing_caddy_config_fails_with_bounded_error(tmp_path: Path) -> None:
    base = _settings()
    with pytest.raises(ConfigError, match="caddy_reload_config is unavailable"):
        PublicationSettings(
            domain_suffix=base.domain_suffix,
            caddy_executable=base.caddy_executable,
            caddy_control_endpoint=base.caddy_control_endpoint,
            owned_route_file=base.owned_route_file,
            basic_auth_username=base.basic_auth_username,
            basic_auth_password_hash=base.basic_auth_password_hash,
            trusted_proxy_addresses=base.trusted_proxy_addresses,
            caddy_reload_config=tmp_path / "missing.caddy",
            upstream_http_port=18081,
            upstream_bind_address="127.0.0.1",
        )


def test_non_file_existing_caddy_config_fails_with_bounded_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    base = _settings()
    root_config = tmp_path / "caddy-directory"
    root_config.mkdir()
    with pytest.raises(ConfigError, match="caddy_reload_config must be a file"):
        PublicationSettings(
            domain_suffix=base.domain_suffix,
            caddy_executable=base.caddy_executable,
            caddy_control_endpoint=base.caddy_control_endpoint,
            owned_route_file=base.owned_route_file,
            basic_auth_username=base.basic_auth_username,
            basic_auth_password_hash=base.basic_auth_password_hash,
            trusted_proxy_addresses=base.trusted_proxy_addresses,
            caddy_reload_config=root_config,
            upstream_http_port=18081,
            upstream_bind_address="127.0.0.1",
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"upstream_http_port": None}, "port and bind address must be paired"),
        ({"upstream_bind_address": None}, "port and bind address must be paired"),
        ({"upstream_http_port": 0}, "upstream_http_port is invalid"),
        ({"upstream_http_port": 65536}, "upstream_http_port is invalid"),
        ({"upstream_bind_address": "not-an-ip"}, "upstream_bind_address must be an IP"),
        ({"upstream_bind_address": "0.0.0.0"}, "upstream_bind_address is unsafe"),
    ],
)
def test_upstream_settings_validate_pair_range_and_bind_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    message: str,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    base = _settings()
    root_config = tmp_path / "Caddyfile"
    root_config.write_text(f"import {base.owned_route_file}\n")
    values: dict[str, object] = {
        "domain_suffix": base.domain_suffix,
        "caddy_executable": base.caddy_executable,
        "caddy_control_endpoint": base.caddy_control_endpoint,
        "owned_route_file": base.owned_route_file,
        "basic_auth_username": base.basic_auth_username,
        "basic_auth_password_hash": base.basic_auth_password_hash,
        "trusted_proxy_addresses": base.trusted_proxy_addresses,
        "caddy_reload_config": root_config,
        "upstream_http_port": 18081,
        "upstream_bind_address": "127.0.0.1",
    }
    values.update(overrides)

    with pytest.raises(ConfigError, match=message):
        PublicationSettings(**cast("dict[str, Any]", values))


def test_upstream_settings_require_exact_owned_route_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    base = _settings()
    root_config = tmp_path / "Caddyfile"
    root_config.write_text("http://:18081 { respond 404 }\n")

    with pytest.raises(ConfigError, match="must import owned_route_file"):
        PublicationSettings(
            domain_suffix=base.domain_suffix,
            caddy_executable=base.caddy_executable,
            caddy_control_endpoint=base.caddy_control_endpoint,
            owned_route_file=base.owned_route_file,
            basic_auth_username=base.basic_auth_username,
            basic_auth_password_hash=base.basic_auth_password_hash,
            trusted_proxy_addresses=base.trusted_proxy_addresses,
            caddy_reload_config=root_config,
            upstream_http_port=18081,
            upstream_bind_address="127.0.0.1",
        )


def test_root_import_proof_handles_relative_and_malformed_imports(tmp_path: Path) -> None:
    route_file = tmp_path / "routes.caddy"
    root_config = tmp_path / "Caddyfile"
    root_config.write_text('import "routes.caddy"\n')
    assert publication_routes.root_imports_owned_route(root_config, route_file)

    root_config.write_text('import "routes.caddy\n')
    assert not publication_routes.root_imports_owned_route(root_config, route_file)

    missing_root = tmp_path / "missing.Caddyfile"
    assert not publication_routes.root_imports_owned_route(missing_root, route_file)


@pytest.mark.parametrize("routes", [None, ["not-a-route"]], ids=["not-a-list", "not-a-dict"])
def test_decode_routes_rejects_invalid_route_payloads(routes: object) -> None:
    encoded = base64.urlsafe_b64encode(json.dumps({"routes": routes}).encode("utf-8")).decode(
        "ascii"
    )
    raw = f"# odcli-routes-json: {encoded}\n".encode()

    with pytest.raises(PublicationError, match="not an OdCLI route file"):
        publication_routes.decode_routes(raw)


def test_odoo_auth_can_be_disabled_without_disabling_panel_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(publication_resource, "_assert_runtime_ready", lambda _target: None)
    base = _settings()
    settings = PublicationSettings(
        domain_suffix=base.domain_suffix,
        caddy_executable=base.caddy_executable,
        caddy_control_endpoint=base.caddy_control_endpoint,
        owned_route_file=base.owned_route_file,
        basic_auth_username=base.basic_auth_username,
        basic_auth_password_hash=base.basic_auth_password_hash,
        trusted_proxy_addresses=base.trusted_proxy_addresses,
        odoo_basic_auth_enabled=False,
    )
    target = PublicationTarget(
        owner_kind="project",
        owner_id="project_abc",
        project_id="project_abc",
        local_endpoint="http://127.0.0.1:8069",
    )
    _client().publication.publish_command(
        target, settings=settings, executor=RecordingExecutor()
    ).run()
    assert "basic_auth" not in settings.owned_route_file.read_text()
    assert "basic_auth bcrypt" in render_panel_route("http://127.0.0.1:8123", settings)


def test_monitor_route_is_reconciled_and_preserved_by_runtime_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(publication_resource, "_assert_runtime_ready", lambda _target: None)
    settings = _settings()
    client = _client()
    executor = RecordingExecutor()

    panel_url = client.publication.monitor_route_command(
        "http://127.0.0.1:8123", settings=settings, executor=executor
    ).run()
    assert panel_url == "https://panel.example.test"
    route_bytes = settings.owned_route_file.read_bytes()
    routes = publication_resource._decode_routes(route_bytes)
    assert [(route.owner_kind, route.local_endpoint) for route in routes] == [
        ("panel", "http://127.0.0.1:8123")
    ]
    assert "panel.example.test" in route_bytes.decode()

    target = PublicationTarget(
        owner_kind="project",
        owner_id="project_abc",
        project_id="project_abc",
        local_endpoint="http://127.0.0.1:8069",
    )
    client.publication.publish_command(target, settings=settings, executor=executor).run()
    routes = publication_resource._decode_routes(settings.owned_route_file.read_bytes())
    assert {(route.owner_kind, route.owner_id) for route in routes} == {
        ("panel", "monitor"),
        ("project", "project_abc"),
    }


@pytest.mark.parametrize("recovery_fails", [False, True], ids=["recovery-ok", "recovery-fails"])
def test_upstream_reload_failure_restores_prior_route_and_checks_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recovery_fails: bool
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(publication_resource, "_assert_runtime_ready", lambda _target: None)
    base = _settings()
    root_config = tmp_path / "Caddyfile"
    root_config.write_text(f"import {base.owned_route_file}\n")
    settings = PublicationSettings(
        domain_suffix=base.domain_suffix,
        caddy_executable=base.caddy_executable,
        caddy_control_endpoint=base.caddy_control_endpoint,
        owned_route_file=base.owned_route_file,
        basic_auth_username=base.basic_auth_username,
        basic_auth_password_hash=base.basic_auth_password_hash,
        trusted_proxy_addresses=base.trusted_proxy_addresses,
        caddy_reload_config=root_config,
        upstream_http_port=18081,
        upstream_bind_address="127.0.0.1",
    )
    client = _client()
    first = PublicationTarget(
        owner_kind="project",
        owner_id="project_a",
        project_id="project_a",
        local_endpoint="http://127.0.0.1:8069",
    )
    second = PublicationTarget(
        owner_kind="environment",
        owner_id="env-b",
        project_id="project_a",
        environment_id="env-b",
        local_endpoint="http://127.0.0.1:8070",
    )
    client.publication.publish_command(first, settings=settings, executor=RecordingExecutor()).run()
    previous = settings.owned_route_file.read_bytes()

    def fail_reload(step: PreparedProcess) -> ProcessResult:
        return _process(
            step,
            returncode=(
                1
                if step.step_id == "publication.publish.caddy.reload"
                or (recovery_fails and step.step_id == "publication.publish.caddy.reload.restore")
                else 0
            ),
        )

    expected_error = "live rollback was not confirmed" if recovery_fails else "rejected"
    failing_executor = RecordingExecutor(result_factory=fail_reload)
    with pytest.raises(PublicationError, match=expected_error):
        client.publication.publish_command(
            second,
            settings=settings,
            executor=failing_executor,
        ).run()

    assert settings.owned_route_file.read_bytes() == previous
    reloads = [step for step in failing_executor.executed if "caddy.reload" in step.step_id]
    assert [step.step_id for step in reloads] == [
        "publication.publish.caddy.reload",
        "publication.publish.caddy.reload.restore",
    ]
    assert all(step.argv[step.argv.index("--config") + 1] == str(root_config) for step in reloads)


def test_reload_failure_restores_prior_route_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(publication_resource, "_assert_runtime_ready", lambda _target: None)
    settings = _settings()
    client = _client()
    first = PublicationTarget(
        owner_kind="project",
        owner_id="project_a",
        project_id="project_a",
        local_endpoint="http://127.0.0.1:8069",
    )
    second = PublicationTarget(
        owner_kind="environment",
        owner_id="env-b",
        project_id="project_a",
        environment_id="env-b",
        local_endpoint="http://127.0.0.1:8070",
    )
    client.publication.publish_command(first, settings=settings, executor=RecordingExecutor()).run()
    previous = settings.owned_route_file.read_bytes()

    def fail_reload(step: PreparedProcess) -> ProcessResult:
        return _process(
            step,
            returncode=1 if step.step_id == "publication.publish.caddy.reload" else 0,
        )

    with pytest.raises(PublicationError, match="rejected"):
        client.publication.publish_command(
            second,
            settings=settings,
            executor=RecordingExecutor(result_factory=fail_reload),
        ).run()

    assert settings.owned_route_file.read_bytes() == previous


def test_unproven_target_is_rejected_before_route_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = _settings()
    target = PublicationTarget(
        owner_kind="project",
        owner_id="project_unproven",
        project_id="project_unproven",
        local_endpoint="http://127.0.0.1:8069",
    )

    with pytest.raises(PublicationError, match="runtime readiness must be proven"):
        _client().publication.publish_command(target, settings=settings).run()

    assert not settings.owned_route_file.exists()


def test_publish_cli_resolves_environment_selector_before_building_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = object()
    selected = object()
    captured: list[object] = []

    class FakePublication:
        def publish_command(self, target: object, *, settings: object) -> SimpleNamespace:
            captured.append(target)
            return SimpleNamespace(plan={}, run=dict)

    class FakeEnvironments:
        def get(self, selector: str) -> object:
            assert selector == "env-name"
            return selected

    fake_client = SimpleNamespace(publication=FakePublication(), environments=FakeEnvironments())
    monkeypatch.setattr(
        publication_resource.PublicationSettings,
        "load",
        classmethod(lambda cls, path=None: settings),
    )

    result = CliRunner().invoke(
        publication_commands.publish_cli,
        ["--env", "env-name", "--dry-run"],
        obj=fake_client,
    )

    assert result.exit_code == 0, result.output
    assert captured == [selected]
    assert "$2a$10$" not in result.output


def test_runtime_target_rejects_stopped_before_route_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = _settings()
    runtime = SimpleNamespace(_read_runtime_identity=lambda: None)
    target = PublicationTarget(
        owner_kind="project",
        owner_id="project_stopped",
        project_id="project_stopped",
        local_endpoint="http://127.0.0.1:8069",
        runtime=cast("Any", runtime),
    )

    with pytest.raises(PublicationError, match="runtime is stopped"):
        _client().publication.publish_command(target, settings=settings).run()

    assert not settings.owned_route_file.exists()


def test_runtime_target_rejects_stale_identity_before_route_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = _settings()
    identity = SimpleNamespace(vanished=False, root_pid=123)

    def reject_identity(_identity: object) -> None:
        raise RuntimeError("foreign process")

    runtime = SimpleNamespace(
        _read_runtime_identity=lambda: identity,
        _validate_runtime_identity=reject_identity,
    )
    target = PublicationTarget(
        owner_kind="project",
        owner_id="project_stale",
        project_id="project_stale",
        local_endpoint="http://127.0.0.1:8069",
        runtime=cast("Any", runtime),
    )

    with pytest.raises(PublicationError, match="runtime identity is not valid"):
        _client().publication.publish_command(target, settings=settings).run()

    assert not settings.owned_route_file.exists()


def test_runtime_target_rejects_unhealthy_backend_before_route_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = _settings()
    identity = SimpleNamespace(vanished=False, root_pid=123)
    runtime = SimpleNamespace(
        _read_runtime_identity=lambda: identity,
        _validate_runtime_identity=lambda _identity: None,
        config=SimpleNamespace(start_config=object(), base_url="http://127.0.0.1:8069"),
    )
    monkeypatch.setattr(runtime_identity, "_socket_owned_by", lambda _config, _pid: True)

    class UnhealthyResponse:
        status_code = 503

        def json(self) -> dict[str, str]:
            return {"status": "fail"}

    class UnhealthyHttp:
        def __enter__(self) -> UnhealthyHttp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, _url: str) -> UnhealthyResponse:
            return UnhealthyResponse()

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.transport.factory.open_odoo_http_client",
        lambda _endpoint, timeout: UnhealthyHttp(),
    )
    target = PublicationTarget(
        owner_kind="project",
        owner_id="project_unhealthy",
        project_id="project_unhealthy",
        local_endpoint="http://127.0.0.1:8069",
        runtime=cast("Any", runtime),
    )

    with pytest.raises(PublicationError, match="runtime health probe failed"):
        _client().publication.publish_command(target, settings=settings).run()

    assert not settings.owned_route_file.exists()


def test_two_concurrent_publications_retain_both_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(publication_resource, "_assert_runtime_ready", lambda _target: None)
    settings = _settings()
    client = _client()
    executor = RecordingExecutor()

    original_decode = publication_routes.decode_routes
    first_decode_started = threading.Event()
    release_first_decode = threading.Event()
    decode_calls = 0
    decode_calls_lock = threading.Lock()

    def gated_decode(raw: bytes) -> list[publication_routes.PublicationRoute]:
        nonlocal decode_calls
        with decode_calls_lock:
            decode_calls += 1
            first = decode_calls == 1
        if first:
            first_decode_started.set()
            assert release_first_decode.wait(timeout=5)
        return original_decode(raw)

    monkeypatch.setattr(publication_routes, "decode_routes", gated_decode)
    start = threading.Barrier(2)
    errors: list[BaseException] = []

    def publish(owner_id: str) -> None:
        try:
            start.wait()
            client.publication.publish_command(
                PublicationTarget(
                    owner_kind="project",
                    owner_id=owner_id,
                    project_id=owner_id,
                    local_endpoint="http://127.0.0.1:8069",
                ),
                settings=settings,
                executor=executor,
            ).run()
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=publish, args=(owner,)) for owner in ("project_one", "project_two")
    ]
    for thread in threads:
        thread.start()
    assert first_decode_started.wait(timeout=5)
    release_first_decode.set()
    for thread in threads:
        thread.join(timeout=5.0)
        assert not thread.is_alive()

    assert errors == []
    route_text = settings.owned_route_file.read_text()
    assert "project-one.example.test" in route_text
    assert "project-two.example.test" in route_text


def test_candidate_validation_failure_preserves_prior_route_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(publication_resource, "_assert_runtime_ready", lambda _target: None)
    settings = _settings()
    client = _client()
    target = PublicationTarget(
        owner_kind="project",
        owner_id="project_validation",
        project_id="project_validation",
        local_endpoint="http://127.0.0.1:8069",
    )
    client.publication.publish_command(
        target, settings=settings, executor=RecordingExecutor()
    ).run()
    previous = settings.owned_route_file.read_bytes()

    def reject_candidate(step: PreparedProcess) -> ProcessResult:
        return _process(
            step,
            returncode=1 if step.step_id.endswith("caddy.validate") else 0,
        )

    replacement = PublicationTarget(
        owner_kind="environment",
        owner_id="env-validation",
        project_id="project_validation",
        environment_id="env-validation",
        local_endpoint="http://127.0.0.1:8070",
    )
    with pytest.raises(PublicationError, match="candidate validation failed"):
        client.publication.publish_command(
            replacement,
            settings=settings,
            executor=RecordingExecutor(result_factory=reject_candidate),
        ).run()

    assert settings.owned_route_file.read_bytes() == previous


@pytest.mark.parametrize(
    ("command", "expected_options"),
    [
        pytest.param(publication_commands.publish_cli, (), id="publish"),
        pytest.param(publication_commands.unpublish_cli, ("--yes",), id="unpublish"),
    ],
)
def test_publication_cli_help_exposes_exact_selectors(
    command: object, expected_options: tuple[str, ...]
) -> None:
    result = CliRunner().invoke(cast("Any", command), ["--help"])

    assert result.exit_code == 0
    assert "--project" in result.output
    assert "--env" in result.output
    for option in expected_options:
        assert option in result.output


def test_unpublish_machine_output_requires_confirmation_and_emits_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = object()

    class FakePublication:
        def unpublish_command(self, target: object, *, settings: object) -> SimpleNamespace:
            assert target == ("environment", "env-name")
            assert settings is not None
            return SimpleNamespace(plan={}, run=lambda: pytest.fail("unpublish executed"))

    fake_client = SimpleNamespace(publication=FakePublication())
    monkeypatch.setattr(
        publication_resource.PublicationSettings,
        "load",
        classmethod(lambda cls, path=None: settings),
    )

    result = CliRunner().invoke(
        publication_commands.unpublish_cli,
        ["--env", "env-name", "--format", "json"],
        obj=fake_client,
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["command"] == "unpublish"
    assert payload["error"]["code"] == "confirmation_required"


def test_unpublish_selector_failure_emits_machine_output_envelope() -> None:
    fake_client = SimpleNamespace(publication=object())

    result = CliRunner().invoke(
        publication_commands.unpublish_cli,
        ["--format", "json"],
        obj=fake_client,
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["command"] == "unpublish"
    assert payload["error"]["code"] == "publication_failed"


def test_route_rendering_is_exact_host_basic_auth_and_secret_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(publication_resource, "_assert_runtime_ready", lambda _target: None)
    settings = _settings()
    client = _client()
    result = client.publication.publish_command(
        PublicationTarget(
            owner_kind="project",
            owner_id="project_exact_host",
            project_id="project_exact_host",
            local_endpoint="http://127.0.0.1:8069",
        ),
        settings=settings,
        executor=RecordingExecutor(),
    ).run()
    route_text = settings.owned_route_file.read_text()

    assert result.external_url == "https://project-exact-host.example.test"
    assert "project-exact-host.example.test {" in route_text
    assert "basic_auth" in route_text
    assert "attacker.example.test" not in route_text
    assert settings.basic_auth_password_hash in route_text
    assert "plaintext-password" not in route_text
    assert "plaintext-password" not in repr(settings)


@pytest.mark.parametrize(
    "payload",
    [
        "odoo\n}\nattacker.example {",
        "$2a$10$aaaaaaaaaaaaaaaaaaaaaa\n}\nattacker.example {",
        "$2a$10$aaaaaaaaaaaaaaaaaaaaaa} attacker.example {xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    ],
)
def test_publication_rejects_caddy_injection_before_route_mutation(
    tmp_path: Path, payload: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    prior = tmp_path / "prior.caddy"
    prior.write_bytes(b"prior route bytes\n")
    field = "basic_auth_username" if payload.startswith("odoo") else "basic_auth_password_hash"
    values = {
        "domain_suffix": "example.test",
        "caddy_executable": "caddy",
        "caddy_control_endpoint": "http://127.0.0.1:2019",
        "owned_route_file": prior,
        "basic_auth_username": "odoo",
        "basic_auth_password_hash": "$2a$10$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "trusted_proxy_addresses": ("127.0.0.1",),
    }
    values[field] = payload
    with pytest.raises(ConfigError):
        PublicationSettings(**cast("dict[str, Any]", values))
    assert prior.read_bytes() == b"prior route bytes\n"


def test_route_validation_rejects_external_caddy_injection() -> None:
    route = publication_routes.PublicationRoute(
        owner_kind="project",
        owner_id="project-safe",
        project_id="project-safe",
        environment_id=None,
        local_endpoint="http://127.0.0.1:8069",
        external_url="https://safe.example.test\n}\nattacker.example {",
        route_identity="0123456789abcdef",
    )

    with pytest.raises(PublicationError, match="external_url"):
        publication_routes.validate_route(route)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("owner_kind", "unknown", "invalid owner kind"),
        ("owner_id", "owner\nunsafe", "unsafe owner_id"),
        ("external_url", "https://bad_host.example.test", "unsafe external_url"),
    ],
)
def test_route_validation_rejects_identity_and_host_boundaries(
    field: str, value: str, message: str
) -> None:
    values: dict[str, object] = {
        "owner_kind": "project",
        "owner_id": "project-safe",
        "project_id": "project-safe",
        "environment_id": None,
        "local_endpoint": "http://127.0.0.1:8069",
        "external_url": "https://safe.example.test",
        "route_identity": "0123456789abcdef",
    }
    values[field] = value
    route = publication_routes.PublicationRoute(**cast("dict[str, Any]", values))

    with pytest.raises(PublicationError, match=message):
        publication_routes.validate_route(route)
