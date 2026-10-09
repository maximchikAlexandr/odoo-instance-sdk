from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import odoo_instance_sdk.commands.publication as publication_commands
import odoo_instance_sdk.resources.instance.runtime_identity as runtime_identity
import odoo_instance_sdk.resources.publication as publication_resource
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.exceptions import ConfigError, PublicationError
from odoo_instance_sdk.internal.paths import get_user_root
from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, RecordingExecutor
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
        basic_auth_password_hash="$2a$10$abcdefghijklmnopqrstuu",
        panel_host_label="panel",
        trusted_proxy_addresses=("127.0.0.1",),
    )


def _client() -> OdooClient:
    return OdooClient(config=OdooClientConfig(executable="odoo"))


def _process(step: PreparedStep, *, returncode: int = 0) -> ProcessResult:
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
            basic_auth_password_hash="$2a$10$abcdefghijklmnopqrstuu",
            trusted_proxy_addresses=("127.0.0.1",),
        )


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
    assert "basic_auth" in route_text
    assert "/websocket" in route_text
    assert "project-abc-123.example.test" in route_text
    assert "panel.example.test" in render_panel_route("http://127.0.0.1:9000", settings)
    assert second.status == "already_published"
    assert removed.status == "unpublished"
    assert absent.status == "already_absent"


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

    def fail_reload(step: PreparedStep) -> ProcessResult:
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
        publication_commands.PublicationSettings,
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
        runtime=runtime,
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
        runtime=runtime,
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
        runtime=runtime,
    )

    with pytest.raises(PublicationError, match="runtime health probe failed"):
        _client().publication.publish_command(target, settings=settings).run()

    assert not settings.owned_route_file.exists()


def test_two_concurrent_publications_retain_both_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(publication_resource, "_assert_runtime_ready", lambda _target: None)
    settings = _settings()
    client = _client()
    executor = RecordingExecutor()

    original_decode = publication_resource._decode_routes

    def delayed_decode(raw: bytes) -> list[object]:
        time.sleep(0.05)
        return original_decode(raw)

    monkeypatch.setattr(publication_resource, "_decode_routes", delayed_decode)
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

    def reject_candidate(step: PreparedStep) -> ProcessResult:
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
    "command", [publication_commands.publish_cli, publication_commands.unpublish_cli]
)
def test_publication_cli_help_exposes_exact_selectors(command: object) -> None:
    result = CliRunner().invoke(command, ["--help"])

    assert result.exit_code == 0
    assert "--project" in result.output
    assert "--env" in result.output


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
